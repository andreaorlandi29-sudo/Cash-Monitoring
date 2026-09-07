"""Telegram bot: the interface for logging movements and answering categorization
questions. Runs via long-polling (no public HTTPS endpoint needed), so it works
unchanged on a laptop, a Raspberry Pi, or any free-tier always-on host.

Commands:
  /start        welcome + shows your chat id (needed to set OWNER_CHAT_ID)
  /saldo        current real balance
  /proiezione [giorni]   projected balance N days out (default 30)

Free text:
  "spesa 12,50 Esselunga"    logs an expense
  "entrata 1200 Stipendio"   logs an income

If the description doesn't match any categorization rule, the bot asks which
category it is via inline buttons, and remembers the answer for next time.
"""
import logging
import re
from datetime import date, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from cashmon import config as app_config
from cashmon import db
from cashmon.bot.formatting import format_eur, parse_amount_to_cents
from cashmon.categorizer import CATEGORIES, categorize, learn_rule
from cashmon.ledger import add_transaction, balance_at, forecast_at

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ENTRY_RE = re.compile(r"^(spesa|entrata)\s+([\d.,]+)\s+(.+)$", re.IGNORECASE)


def owner_only(handler):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if not app_config.OWNER_CHAT_ID:
            await update.effective_message.reply_text(
                "OWNER_CHAT_ID non impostato. Aggiungi questo chat id al file .env:\n"
                f"OWNER_CHAT_ID={chat_id}"
            )
            return
        if str(chat_id) != str(app_config.OWNER_CHAT_ID):
            logger.warning("Rejected message from unauthorized chat_id=%s", chat_id)
            return
        return await handler(update, context)

    return wrapped


@owner_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Ciao! Il tuo chat id è: "
        f"{update.effective_chat.id}\n\n"
        "Comandi:\n"
        "/saldo - saldo attuale\n"
        "/proiezione [giorni] - saldo previsto\n\n"
        "Per registrare un movimento scrivi ad es.:\n"
        "spesa 12,50 Esselunga\n"
        "entrata 1200 Stipendio"
    )


@owner_only
async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = context.bot_data["conn"]
    today = date.today().isoformat()
    cents = balance_at(conn, today)
    await update.effective_message.reply_text(f"Saldo attuale: {format_eur(cents)}")


@owner_only
async def proiezione(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = context.bot_data["conn"]
    days = 30
    if context.args:
        try:
            days = int(context.args[0])
        except ValueError:
            await update.effective_message.reply_text("Uso: /proiezione [giorni]")
            return
    target_date = (date.today() + timedelta(days=days)).isoformat()
    cents = forecast_at(conn, target_date)
    await update.effective_message.reply_text(
        f"Saldo previsto tra {days} giorni ({target_date}): {format_eur(cents)}"
    )


async def ask_category(update: Update, context: ContextTypes.DEFAULT_TYPE, transaction_id: int, description: str) -> None:
    conn = context.bot_data["conn"]
    keyboard = [
        [InlineKeyboardButton(cat, callback_data=f"cat:{transaction_id}:{cat}")]
        for cat in CATEGORIES
    ]
    message = await update.effective_message.reply_text(
        f'Che categoria ha "{description}"?', reply_markup=InlineKeyboardMarkup(keyboard)
    )
    conn.execute(
        "INSERT INTO pending_questions (transaction_id, chat_id, message_id) VALUES (?, ?, ?)",
        (transaction_id, update.effective_chat.id, message.message_id),
    )
    conn.commit()


@owner_only
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = context.bot_data["conn"]
    text = update.effective_message.text or ""
    match = ENTRY_RE.match(text.strip())
    if not match:
        await update.effective_message.reply_text(
            'Non ho capito. Usa il formato: "spesa 12,50 Esselunga" oppure "entrata 1200 Stipendio".'
        )
        return

    kind, amount_raw, description = match.groups()
    try:
        cents = parse_amount_to_cents(amount_raw)
    except ValueError:
        await update.effective_message.reply_text(f"Importo non valido: {amount_raw}")
        return
    if kind.lower() == "spesa":
        cents = -abs(cents)
    else:
        cents = abs(cents)

    category = categorize(conn, description)
    result = add_transaction(
        conn,
        date=date.today().isoformat(),
        amount_cents=cents,
        description=description,
        source="telegram",
        category=category,
        status="confirmed",
    )
    if not result.inserted:
        await update.effective_message.reply_text("Movimento già registrato (ignorato duplicato).")
        return

    if category:
        await update.effective_message.reply_text(
            f"Registrato: {format_eur(cents)} - {description} [{category}]"
        )
    else:
        await update.effective_message.reply_text(f"Registrato: {format_eur(cents)} - {description}")
        await ask_category(update, context, result.transaction_id, description)


@owner_only
async def handle_category_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = context.bot_data["conn"]
    query = update.callback_query
    await query.answer()
    _, transaction_id_raw, category = query.data.split(":", 2)
    transaction_id = int(transaction_id_raw)

    row = conn.execute("SELECT description FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    if row is None:
        await query.edit_message_text("Movimento non trovato (forse eliminato).")
        return

    conn.execute("UPDATE transactions SET category = ? WHERE id = ?", (category, transaction_id))
    conn.execute(
        "UPDATE pending_questions SET resolved_at = datetime('now') WHERE transaction_id = ? AND resolved_at IS NULL",
        (transaction_id,),
    )
    conn.commit()
    learn_rule(conn, row["description"], category)

    await query.edit_message_text(f'"{row["description"]}" categorizzato come {category}.')


def build_application() -> Application:
    if not app_config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN non impostato. Vedi .env.example.")

    conn = db.connect(app_config.DB_PATH)
    db.init_schema(conn)

    application = Application.builder().token(app_config.TELEGRAM_BOT_TOKEN).build()
    application.bot_data["conn"] = conn

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("saldo", saldo))
    application.add_handler(CommandHandler("proiezione", proiezione))
    application.add_handler(CallbackQueryHandler(handle_category_answer, pattern=r"^cat:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return application


def main() -> None:
    application = build_application()
    logger.info("Bot avviato (long polling).")
    application.run_polling()


if __name__ == "__main__":
    main()
