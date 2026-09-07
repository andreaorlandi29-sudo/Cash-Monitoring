# Cash-Monitoring

Tool personale per tenere sotto controllo il proprio patrimonio: saldo iniziale,
movimenti (entrate/uscite) caricati da estratti conto o da Telegram,
categorizzazione automatica, e proiezioni future che vengono "sovrascritte"
(in realtà: riconciliate) man mano che arrivano i dati consuntivi.

## Come funziona

- **`transactions`**: movimenti reali (da import CSV, da Telegram, saldo iniziale).
- **`projections`**: movimenti attesi futuri. Non vengono mai cancellati: quando
  arriva il movimento reale corrispondente vengono "agganciati" (`matched_transaction_id`)
  e smettono di contribuire alla proiezione, ma restano a disposizione per
  confrontare previsto vs consuntivo.
- **`categorization_rules`**: regole di categorizzazione automatica (per
  sottostringa sulla descrizione normalizzata). Le risposte date via Telegram
  vengono salvate come nuove regole, quindi lo stesso esercente non viene più
  richiesto una seconda volta.
- Ogni importo è salvato come **centesimi interi** (mai float).
- Ogni riga importata ha un **hash di deduplica** (fonte + data + importo +
  descrizione normalizzata): reimportare lo stesso estratto conto è
  innocuo (no-op), non duplica i movimenti.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt   # o requirements.txt per solo runtime
cp .env.example .env
```

Inizializza il database con il saldo di partenza (default 10.000 €, usato qui
come dato di test):

```bash
python -m cashmon.seed --balance 10000 --date 2026-01-01
```

Rilanciabile in sicurezza: non azzera i movimenti già presenti, aggiorna solo
il saldo iniziale e le regole di categorizzazione mancanti.

## Test

```bash
PYTHONPATH=. pytest -q
```

## Importare un estratto conto (CSV)

```bash
python -m cashmon.importers.csv_importer percorso/estratto.csv --profile generic
```

Il formato atteso dal profilo `generic` è un CSV con colonne `data,descrizione,importo`
(data `YYYY-MM-DD`, importo con segno, `.` come separatore decimale) — comodo
per test o per un export già "pulito" a mano.

Per collegare la propria banca reale, aggiungi un nuovo file JSON in
`cashmon/importers/profiles/` che descriva le colonne del proprio estratto
conto, ad esempio con importo unico:

```json
{
  "name": "labanca",
  "delimiter": ";",
  "date_col": "Data operazione",
  "date_format": "%d/%m/%Y",
  "description_col": "Descrizione",
  "amount_mode": "single",
  "amount_col": "Importo",
  "decimal_separator": ",",
  "thousands_separator": "."
}
```

oppure con colonne dare/avere separate:

```json
{
  "name": "altrabanca",
  "delimiter": ",",
  "date_col": "Data",
  "date_format": "%d/%m/%Y",
  "description_col": "Descrizione",
  "amount_mode": "debit_credit",
  "debit_col": "Dare",
  "credit_col": "Avere",
  "decimal_separator": ","
}
```

Poi: `--profile labanca`. Non è ancora incluso un parser PDF: i formati delle
banche italiane sono troppo eterogenei per generalizzare senza un export
reale come riferimento — se vuoi, mandami un estratto conto PDF/CSV reale
(anche con i dati sensibili anonimizzati) e aggiungo il profilo giusto.

## Bot Telegram

1. Crea un bot con [@BotFather](https://t.me/BotFather) su Telegram, copia il
   token in `.env` come `TELEGRAM_BOT_TOKEN`.
2. Avvia il bot e scrivigli `/start`: ti risponderà con il tuo `chat_id`.
3. Copia quel valore in `.env` come `OWNER_CHAT_ID` (il bot ignora chiunque
   altro: è un endpoint pubblico, senza questo controllo chiunque trovi il
   token potrebbe scrivere sul tuo saldo).
4. Riavvia il bot:

```bash
python -m cashmon.bot.telegram_bot
```

Il bot usa il **long polling** (non serve un endpoint HTTPS pubblico): puoi
tenerlo acceso su un Mac, un Raspberry Pi, o un host gratuito che supporti un
processo persistente (es. un free tier con worker sempre attivo — evita i
free tier "serverless" che dormono, altrimenti perdi i messaggi nel
frattempo).

Comandi disponibili:

- `/saldo` — saldo reale attuale
- `/proiezione [giorni]` — saldo previsto tra N giorni (default 30)

Testo libero per registrare un movimento:

```
spesa 12,50 Esselunga
entrata 1200 Stipendio
```

Se la descrizione non corrisponde a nessuna regola, il bot chiede la
categoria con dei bottoni; la risposta viene ricordata per le volte
successive.

## Notifiche di spesa da iPhone

Non incluso in questa prima versione (serve una spesa reale, anonimizzata,
come esempio per capire il formato). La strada più semplice e gratuita è
una **Shortcut iOS** agganciata alle notifiche della tua banca/carta
(automazione "quando ricevo una notifica da app X"), che estrae importo ed
esercente e li invia con una richiesta HTTP al metodo Telegram
`sendMessage` verso il tuo stesso bot, nel formato `spesa <importo> <esercente>`
— così arriva nel bot esattamente come un messaggio scritto a mano, e passa
dallo stesso flusso di categorizzazione.

## Roadmap non ancora implementata

- Parser PDF per estratti conto (serve un esempio reale).
- Ricezione diretta delle notifiche iPhone (Shortcut da costruire insieme).
- Riconciliazione automatica tra movimento provvisorio (da Telegram) e riga
  dell'estratto conto quando arriva (lo schema la supporta già via
  `superseded_by_id`, manca solo il matcher).
- Grafici/riepiloghi periodici.
