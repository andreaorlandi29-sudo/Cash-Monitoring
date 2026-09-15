"""Telegram bot: the interface for logging movements and answering categorization
questions. Runs via long-polling (no public HTTPS endpoint needed), so it works
unchanged on a laptop, a Raspberry Pi, or any free-tier always-on host.

Commands:
  /start        welcome + shows your chat id (needed to set OWNER_CHAT_ID)
  /help         same as /start -- a reminder of what's available
  Telegram's own "/" command menu also lists all of these with a short
  description (registered once at startup via set_my_commands).
  /saldo        current real balance
  /proiezione [giorni]   projected balance N days out (default 30)
  /categorizza  works through the backlog of uncategorized transactions
                (e.g. after a statement import), one at a time, chaining to
                the next automatically after each answer
  /previsioni   lists pending (unmatched) future projections with their id,
                needed to delete or modify one

Free text:
  "spesa 12,50 Esselunga"        logs an expense
  "entrata 1200 Stipendio"       logs an income
  "satispay spesa 12,50 Bar"     logs a Satispay purchase (see README: this
                                  doesn't move the checking-account balance
                                  yet -- Satispay nets weekly)
  "satispay entrata 20 da Mario" logs a P2P payment received via Satispay
                                  (same reasoning, doesn't land in the
                                  checking account either)
  "previsione spesa 150 il 2026-10-05 Rata condominio"
                                  logs a planned future expense/income for a
                                  known date (one date per message)
  "previsione saldo al 2026-12-01"
                                  projected balance: today's real balance,
                                  plus every logged projection, plus an
                                  automatic Utenze estimate (trailing
                                  monthly average) for any month in between
                                  that has no explicit Utenze projection --
                                  see ledger.forecast_breakdown
  "previsione elimina 3"         deletes pending projection #3 (see
                                  /previsioni for ids)
  "previsione modifica 3 spesa 160 il 2026-10-10 Rata condominio"
                                  replaces projection #3's fields entirely
                                  (not a partial edit)

If none of the above match, and ANTHROPIC_API_KEY is set, the message falls
through to cashmon.bot.nlu: a conversational fallback that asks Claude to
extract the same structured intent from freer Italian phrasing (see
try_conversational_fallback). Without that key, an unmatched message just
gets the rigid-format help text -- the bot still works exactly as before.

If the description doesn't match any categorization rule, the bot asks which
category it is via inline buttons, and remembers the answer for next time.
"""
import logging
from datetime import date, datetime, timedelta

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
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
from cashmon.bot import nlu
from cashmon.bot.entry_parsing import (
    parse_balance_forecast_request,
    parse_entry,
    parse_projection,
    parse_projection_delete,
    parse_projection_modify,
)
from cashmon.bot.formatting import format_eur
from cashmon.categorizer import CATEGORIES, categorize, learn_rule
from cashmon.ledger import (
    add_projection,
    add_transaction,
    balance_at,
    delete_projection,
    forecast_at,
    forecast_breakdown,
    get_account_id,
    list_accounts,
    list_projections,
    total_balance_at,
    update_projection,
)
from cashmon.seed import DEFAULT_ACCOUNT_NAME

BALANCE_FORECAST_CATEGORY = "Utenze"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
        "/proiezione [giorni] - saldo previsto\n"
        "/categorizza - smaltisci le spese senza categoria una alla volta\n"
        "/previsioni - elenca le previsioni in sospeso (con i numeri per modificarle/eliminarle)\n\n"
        "Per registrare un movimento scrivi ad es.:\n"
        "spesa 12,50 Esselunga\n"
        "entrata 1200 Stipendio\n"
        "satispay spesa 12,50 Bar\n"
        "satispay entrata 20 da Mario\n\n"
        "Per una previsione futura:\n"
        "previsione spesa 150 il 2026-10-05 Rata condominio\n"
        "previsione saldo al 2026-12-01\n"
        "previsione elimina 3\n"
        "previsione modifica 3 spesa 160 il 2026-10-10 Rata condominio"
    )


@owner_only
async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = context.bot_data["conn"]
    today = date.today().isoformat()
    accounts = list_accounts(conn)
    lines = [f"{acc['name']}: {format_eur(balance_at(conn, today, acc['id']))}" for acc in accounts]
    if len(accounts) > 1:
        lines.append(f"Totale patrimonio: {format_eur(total_balance_at(conn, today))}")
    await update.effective_message.reply_text("\n".join(lines))


@owner_only
async def proiezione(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = context.bot_data["conn"]
    account_id = context.bot_data["checking_account_id"]
    days = 30
    if context.args:
        try:
            days = int(context.args[0])
        except ValueError:
            await update.effective_message.reply_text("Uso: /proiezione [giorni]")
            return
    target_date = (date.today() + timedelta(days=days)).isoformat()
    cents = forecast_at(conn, target_date, account_id)
    await update.effective_message.reply_text(
        f"Saldo previsto tra {days} giorni ({target_date}) sul conto corrente: {format_eur(cents)}"
    )


async def ask_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    transaction_id: int,
    description: str,
    amount_cents: int = None,
    date_str: str = None,
    bulk: bool = False,
) -> None:
    conn = context.bot_data["conn"]
    prefix = f"{format_eur(amount_cents)} del {date_str} — " if amount_cents is not None else ""
    bulk_flag = "1" if bulk else "0"
    keyboard = [
        [InlineKeyboardButton(cat, callback_data=f"cat:{transaction_id}:{cat}:{bulk_flag}")]
        for cat in CATEGORIES
    ]
    message = await update.effective_message.reply_text(
        f'{prefix}che categoria ha "{description}"?', reply_markup=InlineKeyboardMarkup(keyboard)
    )
    conn.execute(
        "INSERT INTO pending_questions (transaction_id, chat_id, message_id) VALUES (?, ?, ?)",
        (transaction_id, update.effective_chat.id, message.message_id),
    )
    conn.commit()


async def send_next_uncategorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Used by /categorizza and, after each bulk answer, to chain to the next
    uncategorized transaction until the backlog (e.g. from a statement
    import) is empty."""
    conn = context.bot_data["conn"]
    row = conn.execute(
        """
        SELECT id, date, amount_cents, description FROM transactions
        WHERE category IS NULL AND superseded_by_id IS NULL
        ORDER BY date ASC LIMIT 1
        """
    ).fetchone()
    if row is None:
        await update.effective_message.reply_text("Fatto! Nessuna spesa in sospeso da categorizzare.")
        return

    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM transactions WHERE category IS NULL AND superseded_by_id IS NULL"
    ).fetchone()["n"]
    await update.effective_message.reply_text(f"Ne restano {remaining} da categorizzare.")
    await ask_category(
        update, context, row["id"], row["description"], amount_cents=row["amount_cents"], date_str=row["date"], bulk=True
    )


@owner_only
async def categorizza(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_next_uncategorized(update, context)


@owner_only
async def previsioni(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = context.bot_data["conn"]
    rows = list_projections(conn)
    if not rows:
        await update.effective_message.reply_text("Nessuna previsione in sospeso.")
        return

    lines = ["Previsioni in sospeso:"]
    for row in rows:
        category_suffix = f" [{row['category']}]" if row["category"] else ""
        lines.append(f"#{row['id']} - {row['date']}: {format_eur(row['amount_cents'])} - {row['description']}{category_suffix}")
    lines.append("")
    lines.append("Per modificarne una: previsione modifica <numero> spesa|entrata <importo> il <data> <descrizione>")
    lines.append("Per eliminarne una: previsione elimina <numero>")
    await update.effective_message.reply_text("\n".join(lines))


async def send_balance_forecast(update: Update, context: ContextTypes.DEFAULT_TYPE, target_date: str) -> None:
    try:
        datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        await update.effective_message.reply_text(f"Data non valida: {target_date}")
        return

    conn = context.bot_data["conn"]
    account_id = context.bot_data["checking_account_id"]
    result = forecast_breakdown(conn, date.today().isoformat(), target_date, account_id, BALANCE_FORECAST_CATEGORY)

    lines = [f"Saldo oggi: {format_eur(result['balance_today_cents'])}"]
    if result["projections_cents"]:
        lines.append(f"Previsioni inserite: {format_eur(result['projections_cents'])}")
    basis = result["category_avg_basis_months"]
    if result["category_gap_months"] and basis > 0:
        low_confidence = f" (stima su solo {basis} mes{'e' if basis == 1 else 'i'} di storico, poco affidabile)" if basis < 3 else ""
        lines.append(
            f"{result['category']} stimate ({result['category_gap_months']} mesi × "
            f"{format_eur(result['category_avg_cents'])} medi){low_confidence}: "
            f"{format_eur(result['category_estimate_cents'])}"
        )
    elif result["category_gap_months"] and basis == 0:
        lines.append(f"({result['category']}: nessuno storico ancora per stimarla)")
    lines.append(f"Previsione al {target_date}: {format_eur(result['total_cents'])}")
    await update.effective_message.reply_text("\n".join(lines))


async def record_transaction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    description: str,
    amount_cents: int,
    source: str,
    counts_toward_balance: int,
    entry_date: str = None,
) -> None:
    """Shared by the rigid "spesa/entrata" parser and the conversational NLU
    fallback -- both end up creating the same kind of row the same way."""
    conn = context.bot_data["conn"]
    entry_date = entry_date or date.today().isoformat()
    category = categorize(conn, description)
    result = add_transaction(
        conn,
        date=entry_date,
        amount_cents=amount_cents,
        description=description,
        source=source,
        account_id=context.bot_data["checking_account_id"],
        category=category,
        status="confirmed",
        counts_toward_balance=counts_toward_balance,
        dedupe=False,  # interactive entries: two identical small purchases in one day are real, not duplicates
    )

    suffix = "" if counts_toward_balance else " (Satispay, non ancora sul conto)"
    if category:
        await update.effective_message.reply_text(f"Registrato: {format_eur(amount_cents)} - {description} [{category}]{suffix}")
    else:
        await update.effective_message.reply_text(f"Registrato: {format_eur(amount_cents)} - {description}{suffix}")
        await ask_category(update, context, result.transaction_id, description, amount_cents=amount_cents, date_str=entry_date)


RIGID_FORMAT_HELP = (
    'Non ho capito. Usa il formato: "spesa 12,50 Esselunga", "entrata 1200 Stipendio", '
    '"satispay spesa 12,50 Bar" / "satispay entrata 20 da Mario", '
    '"previsione spesa 150 il 2026-10-05 Rata condominio", "previsione saldo al 2026-12-01", '
    '"previsione elimina <numero>" oppure "previsione modifica <numero> spesa|entrata <importo> il <data> <descrizione>". '
    "Usa /previsioni per vedere i numeri. Puoi anche scrivere più liberamente, es. "
    '"ho speso 12 euro da Esselunga" o "quanto avrò a dicembre?".'
)


async def try_conversational_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Last resort when none of the rigid formats match: asks Claude to
    extract the same structured intent from free Italian phrasing. Returns
    True if it handled the message (recognized or not), False if the
    fallback itself is unavailable (no API key, or the call failed) and the
    caller should show the plain rigid-format help instead."""
    if not app_config.ANTHROPIC_API_KEY:
        return False

    try:
        result = nlu.interpret(text, date.today().isoformat())
    except Exception:
        logger.exception("Interpretazione discorsiva del messaggio fallita")
        return False

    if not result.recognized:
        reason = f" ({result.reason_unclear})" if result.reason_unclear else ""
        await update.effective_message.reply_text(f"Non ho capito bene{reason}. Puoi riformulare con più dettagli?")
        return True

    if result.kind == "richiesta_saldo" and result.date:
        await send_balance_forecast(update, context, result.date)
        return True

    if result.kind == "previsione" and result.amount_cents is not None and result.date:
        conn = context.bot_data["conn"]
        description = result.description or "Previsione"
        category = categorize(conn, description)
        add_projection(conn, result.date, result.amount_cents, description, category)
        suffix = f" [{category}]" if category else ""
        await update.effective_message.reply_text(
            f"Previsione registrata: {format_eur(result.amount_cents)} il {result.date} - {description}{suffix}"
        )
        return True

    if result.kind == "movimento" and result.amount_cents is not None:
        description = result.description or "Movimento"
        source = "satispay" if result.is_satispay else "telegram"
        counts_toward_balance = 0 if result.is_satispay else 1
        await record_transaction(
            update, context, description, result.amount_cents, source, counts_toward_balance, entry_date=result.date
        )
        return True

    # Recognized as relevant but missing a piece we can't safely guess (e.g.
    # "previsione" with no date) -- ask rather than silently dropping it.
    await update.effective_message.reply_text("Ho capito di cosa parli ma mi manca un dettaglio (importo o data). Puoi essere più preciso?")
    return True


@owner_only
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = context.bot_data["conn"]
    text = update.effective_message.text or ""

    forecast_target = parse_balance_forecast_request(text)
    if forecast_target:
        await send_balance_forecast(update, context, forecast_target)
        return

    delete_id = parse_projection_delete(text)
    if delete_id is not None:
        if delete_projection(conn, delete_id):
            await update.effective_message.reply_text(f"Previsione #{delete_id} eliminata.")
        else:
            await update.effective_message.reply_text(f"Previsione #{delete_id} non trovata (o già realizzata).")
        return

    edit = parse_projection_modify(text)
    if edit:
        category = categorize(conn, edit.description)
        if update_projection(conn, edit.projection_id, edit.date, edit.amount_cents, edit.description, category):
            suffix = f" [{category}]" if category else ""
            await update.effective_message.reply_text(
                f"Previsione #{edit.projection_id} aggiornata: {format_eur(edit.amount_cents)} il {edit.date} - "
                f"{edit.description}{suffix}"
            )
        else:
            await update.effective_message.reply_text(f"Previsione #{edit.projection_id} non trovata (o già realizzata).")
        return

    projection = parse_projection(text)
    if projection:
        category = categorize(conn, projection.description)
        add_projection(conn, projection.date, projection.amount_cents, projection.description, category)
        suffix = f" [{category}]" if category else ""
        await update.effective_message.reply_text(
            f"Previsione registrata: {format_eur(projection.amount_cents)} il {projection.date} - "
            f"{projection.description}{suffix}"
        )
        return

    entry = parse_entry(text)
    if entry is not None:
        await record_transaction(update, context, entry.description, entry.amount_cents, entry.source, entry.counts_toward_balance)
        return

    if await try_conversational_fallback(update, context, text):
        return

    await update.effective_message.reply_text(RIGID_FORMAT_HELP)


@owner_only
async def handle_category_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = context.bot_data["conn"]
    query = update.callback_query
    await query.answer()
    _, transaction_id_raw, category, bulk_raw = query.data.split(":", 3)
    transaction_id = int(transaction_id_raw)
    bulk = bulk_raw == "1"

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

    if bulk:
        await send_next_uncategorized(update, context)


def build_application() -> Application:
    if not app_config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN non impostato. Vedi .env.example.")

    conn = db.connect(app_config.DB_PATH)
    db.init_schema(conn)

    application = Application.builder().token(app_config.TELEGRAM_BOT_TOKEN).post_init(_register_bot_commands).build()
    application.bot_data["conn"] = conn
    application.bot_data["checking_account_id"] = get_account_id(conn, DEFAULT_ACCOUNT_NAME)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("saldo", saldo))
    application.add_handler(CommandHandler("proiezione", proiezione))
    application.add_handler(CommandHandler("categorizza", categorizza))
    application.add_handler(CommandHandler("previsioni", previsioni))
    application.add_handler(CallbackQueryHandler(handle_category_answer, pattern=r"^cat:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return application


async def _register_bot_commands(application: Application) -> None:
    """Populates Telegram's own "/" command menu (tap the menu button next to
    the message box) so the available commands are always one tap away,
    without having to remember or re-read /start."""
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Comandi e formati disponibili"),
            BotCommand("help", "Uguale a /start"),
            BotCommand("saldo", "Saldo di ogni conto e patrimonio totale"),
            BotCommand("proiezione", "Saldo previsto tra N giorni (conto corrente)"),
            BotCommand("categorizza", "Categorizza le spese in sospeso"),
            BotCommand("previsioni", "Elenca le previsioni in sospeso"),
        ]
    )


def main() -> None:
    application = build_application()
    logger.info("Bot avviato (long polling).")
    application.run_polling()


if __name__ == "__main__":
    main()
