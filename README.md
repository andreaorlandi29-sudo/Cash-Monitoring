# Cash-Monitoring

Tool personale per tenere sotto controllo il proprio patrimonio: saldo iniziale,
movimenti (entrate/uscite) caricati da estratti conto o da Telegram,
categorizzazione automatica, e proiezioni future che vengono "sovrascritte"
(in realtà: riconciliate) man mano che arrivano i dati consuntivi.

## Come funziona

- **`accounts`**: ogni conto tracciato (conto corrente, un conto deposito
  collegato, ...) con il proprio saldo iniziale. Il "patrimonio" totale è la
  somma del saldo di tutti i conti a una data — non solo il conto corrente.
- **`transactions`**: movimenti reali (da import CSV, da Telegram, saldo iniziale),
  ciascuno legato a un conto (`account_id`).
- **`projections`**: movimenti attesi futuri. Non vengono mai cancellati: quando
  arriva il movimento reale corrispondente vengono "agganciati" (`matched_transaction_id`)
  e smettono di contribuire alla proiezione, ma restano a disposizione per
  confrontare previsto vs consuntivo.
- **`categorization_rules`**: regole di categorizzazione automatica (per
  sottostringa sulla descrizione normalizzata). Le risposte date via Telegram
  vengono salvate come nuove regole, quindi lo stesso esercente non viene più
  richiesto una seconda volta. Se aggiungi una nuova regola (es. un nuovo
  esercente/categoria in `cashmon/categorizer.py`) che avrebbe già dovuto
  matchare movimenti importati in passato, lancia
  `python -m cashmon.recategorize` per applicarla retroattivamente invece di
  ricategorizzare a mano dal bot ogni riga già presente (non tocca mai una
  riga già categorizzata).
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

Inizializza il database con il saldo di partenza del conto corrente (default
10.000 €, usato qui come dato di test):

```bash
python -m cashmon.seed --balance 10000 --date 2026-01-01
```

Rilanciabile in sicurezza: non azzera i movimenti già presenti, aggiorna solo
il saldo iniziale (di quel conto) e le regole di categorizzazione mancanti.

Per tracciare **un conto in più** (es. un conto deposito collegato), lancia
`seed` di nuovo con `--account` e il suo saldo iniziale — il nome del conto è
libero, ma se importi da Findomestic/PDF deve combaciare esattamente con
quello che il parser si aspetta (vedi sotto):

```bash
python -m cashmon.seed --account "Findomestic Conto Deposito" --balance 13340.22 --date 2026-08-03
```

`/saldo` mostrerà da quel momento entrambi i conti più il totale.

**Se il database esiste già** da prima che esistesse il multi-conto: non
serve fare nulla a mano. Al primo avvio (bot o un qualsiasi comando), il
conto che avevi viene rinominato automaticamente "Findomestic Conto
Corrente" con lo stesso saldo e la stessa storia — nessun movimento o
categoria già inserita va perso. Fai comunque una copia di sicurezza prima:
```bash
cp cashmon.db cashmon.backup.db
```
(il nome deve finire in `.db` per restare escluso da git, come già `cashmon.db`).

## Test

```bash
PYTHONPATH=. pytest -q
```

## Importare un estratto conto (CSV)

```bash
python -m cashmon.importers.csv_importer percorso/estratto.csv --profile generic --account "Findomestic Conto Corrente"
```

`--account` è opzionale (default il conto corrente) — usalo per importare su
un conto diverso, che deve già esistere (vedi `cashmon.seed --account` sopra).

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

**Findomestic (conto corrente o conto deposito)**: lo stesso comando
riconosce da solo quale dei due è, leggendo il titolo del PDF ("Estratto
Conto" vs "Estratto Conto Deposito"), e importa sul conto corrispondente —
che deve già esistere con quel nome esatto (`Findomestic Conto Corrente` /
`Findomestic Conto Deposito`), creato con `cashmon.seed --account`. Ogni riga
viene importata come movimento reale, con segno da colonna Uscite/Entrate.
L'accredito stipendio, i bonifici, gli addebiti SDD e l'addebito unico
Nexi/Satispay contano sul saldo del conto su cui compaiono.

Se hai un conto deposito collegato che riceve automaticamente una quota ad
ogni utilizzo della carta di debito ("trasferimento resto"), quelle righe
vengono riconosciute su entrambi gli estratti e categorizzate
**"Trasferimento interno"** — non è spesa, sono soldi tuoi che si spostano
tra due conti tuoi. Non serve "abbinare" manualmente le due righe (uscita sul
corrente, entrata sul deposito): bastano import separati di entrambi gli
estratti, ciascuno sul proprio conto, e la somma tra i conti si annulla da
sola. Lo stesso vale per il grande trasferimento iniziale ("basculamento") con
cui la banca gira periodicamente fondi tra i due conti. Un costo reale
addebitato sul conto deposito (es. "Imposte e Tasse" sugli interessi) **non**
viene toccato da questa regola — resta una spesa vera da categorizzare.

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

### Capire il linguaggio discorsivo (opzionale)

Oltre ai formati rigidi qui sotto, il bot capisce anche frasi più libere —
"ho speso 12 euro da Esselunga stamattina", "domani mi arrivano 1200 di
stipendio", "quanto avrò a dicembre?" — appoggiandosi all'API di Claude
(modulo `cashmon/bot/nlu.py`) **solo quando** il messaggio non combacia con
nessun formato rigido: quelli restano gratuiti e vengono sempre provati
per primi.

Per attivarlo, crea una API key su [console.anthropic.com](https://console.anthropic.com)
(Settings → API Keys) e aggiungila a `.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
```
Richiede un metodo di pagamento collegato all'account (a differenza del bot
Telegram, che è gratis) — controlla i consumi da Settings → Usage le prime
volte. Con il modello di default (`claude-haiku-4-5`, cambiabile con
`NLU_MODEL` in `.env`) il costo per messaggio interpretato è nell'ordine di
frazioni di centesimo, adatto a un uso personale.

**Senza questa chiave il bot funziona esattamente come prima**, semplicemente
senza capire le frasi libere — non è un requisito, è un'aggiunta. Se il
messaggio non è chiaro nemmeno per Claude (manca l'importo, non è chiaro se
spesa o entrata, ecc.), il bot te lo dice e ti chiede di essere più preciso
invece di indovinare.

Comandi disponibili (compaiono anche nel menu "/" di Telegram vicino al
campo di testo, con una breve descrizione — utile se te li dimentichi):

- `/start` o `/help` — richiama in ogni momento questo stesso elenco più i
  formati di testo libero
- `/saldo` — saldo reale di ogni conto tracciato, più il patrimonio totale se
  ne hai più di uno
- `/proiezione [giorni]` — saldo previsto tra N giorni (default 30) sul conto
  corrente
- `/categorizza` — smaltisce le spese senza categoria una alla volta
- `/previsioni` — elenca le previsioni in sospeso

Testo libero per registrare un movimento:

```
spesa 12,50 Esselunga
entrata 1200 Stipendio
satispay spesa 12,50 Bar
satispay entrata 20 da Mario
```

Se la descrizione non corrisponde a nessuna regola, il bot chiede la
categoria con dei bottoni; la risposta viene ricordata per le volte
successive. A differenza di un import da estratto conto, due messaggi
identici (stesso importo, stessa descrizione, stesso giorno — capita spesso
con Satispay, es. due caffè da 1,50 €) vengono registrati entrambi: la
deduplica per hash ha senso solo per un file che potresti reimportare per
sbaglio, non per due spese reali scritte a mano.

### Previsioni future

Per una spesa o un'entrata che sai già che arriverà in una data precisa (una
rata condominiale, uno stipendio con data nota, ecc.):

```
previsione spesa 150 il 2026-10-05 Rata condominio
previsione entrata 1200 il 2026-11-27 Tredicesima
```

Una data per messaggio — per più rate (es. 4 scadenze condominiali), manda 4
messaggi, uno per ciascuna. La previsione resta visibile finché non arriva il
movimento reale corrispondente sull'estratto conto: a quel punto puoi
collegarla manualmente (`match_projection`, non ancora esposto via bot) così
smette di contare due volte, oppure lasciarla — il suo effetto sul saldo
attuale (`/saldo`) è comunque zero, conta solo per `/proiezione` e per
"previsione saldo al ...".

Per chiedere una proiezione a una data specifica, che combina saldo attuale +
tutte le previsioni inserite + una stima automatica delle **Utenze** (bollette
luce/gas/telefono) basata sulla media mensile degli ultimi 6 mesi:

```
previsione saldo al 2026-12-01
```

Risposta tipo:
```
Saldo oggi: 2.780,64 €
Previsioni inserite: -150,00 €
Utenze stimate (3 mesi × -68,50 € medi): -205,50 €
Previsione al 2026-12-01: 2.424,14 €
```

Il mese di una bolletta Utenze già inserita come previsione esplicita **non**
viene anche stimato dalla media (altrimenti conterebbe due volte) — solo i
mesi ancora "scoperti" ricevono la stima automatica. Se hai meno di 3 mesi di
storico Utenze, il bot te lo segnala esplicitamente ("poco affidabile") invece
di darti un numero con falsa precisione.

**Vedere, modificare o eliminare le previsioni inserite:**

```
/previsioni
```

elenca tutte le previsioni ancora in sospeso con un numero (`#3`, ecc.). Con
quel numero:

```
previsione modifica 3 spesa 160 il 2026-10-10 Rata condominio
previsione elimina 3
```

`modifica` sostituisce **tutti** i campi della previsione (non è una modifica
parziale: riscrivi importo, data e descrizione anche se cambi solo uno dei
tre). Una previsione già "realizzata" (collegata a un movimento reale importato
— vedi sopra) non compare più in `/previsioni` e non può più essere modificata
o eliminata: è storico, non più un piano.

### Satispay: come viene trattato

Satispay non è un conto prepagato che "ricarichi": ogni settimana netta
quanto hai speso meno quanto hai ricevuto da altri utenti, e addebita sul
conto Findomestic solo l'eventuale differenza a tuo debito — se ricevi più
di quanto spendi, l'eccesso resta come credito usabile la settimana dopo
(niente arriva sul conto). Per questo:

- `satispay spesa ...` e `satispay entrata ...` registrano la voce con la
  categoria e la data reali (utili per sapere dove vanno i soldi) ma **non
  toccano il saldo** (`/saldo`) — la logica è la stessa di Nexi: la cifra che
  esce davvero dal conto è solo l'addebito settimanale, che l'import
  dell'estratto Findomestic riconosce già da solo (pattern "SATISPAY",
  categoria automatica "Satispay").
- **Limite consapevole**: `/saldo` mostra il saldo del conto corrente, non
  tutto il tuo patrimonio. Se ricevi 50 € via Satispay e non li spendi,
  quei 50 € sono comunque tuoi ma non compaiono da nessuna parte finché non
  vengono spesi o (mai, in pratica) accreditati sul conto. Se vuoi vedere
  anche il "credito Satispay non ancora liquidato", è calcolabile così e
  potrei aggiungerlo come riga in più su `/saldo` o come comando `/satispay`
  dedicato — dimmelo se ti interessa:
  ```sql
  SELECT COALESCE(SUM(amount_cents), 0)
  FROM transactions
  WHERE source = 'satispay' AND counts_toward_balance = 0;
  -- meno la somma di quanto già addebitato dagli SDD settimanali importati
  -- dall'estratto conto (categoria 'Satispay', counts_toward_balance = 1)
  ```

## Notifiche di spesa da iPhone

Non incluso in questa prima versione (serve una spesa reale, anonimizzata,
come esempio per capire il formato). La strada più semplice e gratuita è
una **Shortcut iOS** agganciata alle notifiche della tua banca/carta/Satispay
(automazione "quando ricevo una notifica da app X"), che estrae importo ed
esercente e li invia con una richiesta HTTP al metodo Telegram
`sendMessage` verso il tuo stesso bot, nel formato `spesa <importo> <esercente>`
(o `satispay spesa <importo> <esercente>` per le notifiche di Satispay) —
così arriva nel bot esattamente come un messaggio scritto a mano, e passa
dallo stesso flusso di categorizzazione.

## Roadmap non ancora implementata

- Ricezione diretta delle notifiche iPhone (Shortcut da costruire insieme).
- Comando (`/satispay` o riga extra su `/saldo`) per vedere il credito
  Satispay non ancora liquidato — vedi "Satispay: come viene trattato" sopra.
- Grafici/riepiloghi periodici (nota: un futuro report "spesa per categoria"
  deve escludere le categorie in `TRANSFER_CATEGORIES` — sono liquidazioni di
  spesa già dettagliata altrove, sommarle di nuovo la conterebbe due volte).
- Un nuovo template PDF di Findomestic/Nexi potrebbe rompere silenziosamente
  il parser: confronta sempre i totali importati con quelli stampati
  sull'estratto (vedi sopra) dopo il primo import di un nuovo mese.
