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

Poi: `--profile labanca`.

## Importare un estratto conto (PDF)

Supportati due formati reali, entrambi via `pdfplumber` (nessun OCR: i PDF
devono avere testo selezionabile, non essere scansioni):

```bash
python -m cashmon.importers.pdf_findomestic percorso/estratto_conto.pdf
python -m cashmon.importers.pdf_nexi percorso/estratto_nexi.pdf
```

**Findomestic (conto corrente)**: ogni riga viene importata come movimento
reale, con segno da colonna Uscite/Entrate. L'accredito stipendio, i
bonifici, gli addebiti SDD e l'addebito unico Nexi/Satispay finiscono tutti
qui e contano sul saldo.

**Nexi (carta di credito a saldo)**: la carta NON addebita subito il conto —
Nexi salda l'intero estratto in un unico addebito SDD circa 2 mesi dopo (lo
vedrai comparire più avanti come riga "NEXI PAYMENTS" nell'estratto conto
Findomestic di quel mese, categorizzata automaticamente "Carta di Credito").
Per questo le singole spese della carta vengono importate come dettaglio
"informativo": hanno la categoria e la data reali (utili per capire dove
vanno i soldi) ma **non vengono sommate al saldo** — altrimenti la stessa
spesa verrebbe contata due volte, una all'acquisto e una all'addebito.
Importa comunque entrambi gli estratti (Nexi appena disponibile, Findomestic
del mese in cui arriva l'addebito): il saldo resta corretto in ogni momento,
e quando arriva l'addebito puoi confrontare il totale con quanto già
categorizzato dalle singole spese.

Lo stesso ragionamento vale concettualmente per Satispay (il plafond
settimanale si azzera con un unico addebito sul conto Findomestic ogni
lunedì) ma il collegamento con le notifiche Telegram non è ancora
implementato — vedi Roadmap.

Se in futuro cambia il layout del PDF (nuovo template della banca), l'unico
segnale visibile è che l'import restituisce meno righe del solito o importi
che non tornano: confronta sempre il riepilogo stampato a fine comando con i
totali "Entrate complessive / Uscite complessive" (Findomestic) o "TOTALE
SPESE" (Nexi) stampati sull'estratto conto stesso.

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

- Ricezione diretta delle notifiche iPhone (Shortcut da costruire insieme).
- Riconciliazione Satispay: le spese da notifica (Telegram) dovrebbero essere
  registrate come movimenti reali e "provvisori" (`status='provisional'`), e
  quando arriva l'addebito settimanale sul conto Findomestic vanno collegate
  ad esso con `superseded_by_id` (lo schema lo supporta già, manca solo il
  comando Telegram e il matcher — a differenza di Nexi, qui i soldi escono
  davvero dal conto ogni settimana, non è un plafond prepagato: vedi i
  commenti in `cashmon/categorizer.py` sulle categorie "Carta di Credito" /
  "Satispay").
- Grafici/riepiloghi periodici (nota: un futuro report "spesa per categoria"
  deve escludere le categorie in `TRANSFER_CATEGORIES` — sono liquidazioni di
  spesa già dettagliata altrove, sommarle di nuovo la conterebbe due volte).
- Un nuovo template PDF di Findomestic/Nexi potrebbe rompere silenziosamente
  il parser: confronta sempre i totali importati con quelli stampati
  sull'estratto (vedi sopra) dopo il primo import di un nuovo mese.
