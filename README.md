# GLI Darwin Intake — Databricks App

App Dash per l'inserimento dei volumi settimanali, deployata come Databricks App.

## Struttura

```
volume-app/
├── app.py              # Entry point — Dash app, layout, callbacks
├── app.yaml            # Config Databricks Apps (comando di avvio: gunicorn)
├── gunicorn.conf.py    # Bind su 0.0.0.0:$DATABRICKS_APP_PORT
├── requirements.txt    # Dipendenze Python
├── assets/
│   └── style.css       # Design system
├── components/
│   ├── header.py       # Topbar + app-header
│   └── data_table.py   # Tabella, summary bar, pannelli Friday/WIP OT%/Actual, legenda
├── data/
│   ├── schema.py       # Colonne, matrice N/A, scadenze, gruppi Landings
│   ├── cache.py        # Cache server-side in-memory con TTL
│   └── db.py           # Lettura/scrittura Lakebase PostgreSQL (psycopg2)
├── migrations/         # DDL versionato — da eseguire su Lakebase prima del deploy
├── scripts/
│   └── run_migration.py  # Esegue un .sql (o una query) su Lakebase
└── README.md
```

> Tutto il contenuto della repo sta nella root: la Databricks App è collegata
> alla repo GitHub via URL (senza sottocartella), quindi deploia da qui.

## Funzionalità

Tabella settimanale: 8 righe di submission × canali, per ogni sito e product
line (Frames / Wearables). Ogni riga si salva come bozza (Save) o si conferma
(Submit) in modo indipendente.

- **Righe a pannello** — `Friday FRC`, `WIP OT %` e `Actual` non si compilano
  inline: un pulsante apre un pannello di data entry con una card per colonna
  (valore + checkbox "Confirm zero" + commento), con Save/Submit propri.
- **Commento obbligatorio sotto soglia** — la sezione commento compare/scompare
  in tempo reale mentre si digita:
  - `Friday FRC` e `Actual`: se lo scostamento vs Monday FRC è ≥ 10000 pcs o ≥ 10%.
  - `WIP OT %`: se il valore è ≤ 90%.
- **Validazione celle vuote** — nelle righe a pannello ogni cella applicabile
  deve avere un valore (anche 0) o essere marcata zero; in mancanza il Submit
  si blocca ed evidenzia le celle incomplete.
- **Zero esplicito** — la checkbox "Confirm zero" distingue lo zero voluto dalla
  cella non compilata. Le celle N/A sono bloccate e tratteggiate.
- **Editing settimane passate + flag ritardo** — un selettore di settimana
  nell'header (sotto il dropdown Site) permette di rivedere/correggere settimane
  già chiuse. Al Submit di una settimana passata compare un **modal di conferma
  custom** (backdrop sfocato, card con i design token dell'app) che nomina la
  settimana selezionata; alla conferma la scrittura è marcata `is_delay = TRUE` +
  `delay_timestamp`. I submit della settimana aperta restano `is_delay = FALSE`.
  Il modal fa da gate a tutti i percorsi di Submit, incluso Submit-all.
- **Display in migliaia (Kpcs)** — i valori sono mostrati in migliaia
  (es. `265000` → `265`), senza `,0` finale.
- **Separatore decimale virgola** — input, display e somme della vista GLOBAL
  usano la virgola come separatore decimale.
- **Colonna Dummy separata (Dongguan)** — la colonna Dummy di Dongguan Wearables
  è splittata; header di colonna a capo e colonna azioni ridotta per farci stare
  lo split.
- **Vista GLOBAL** in sola lettura — somma di tutti i plant.
- **Permessi** — lettura su tutti i siti, scrittura solo sul proprio
  (tabella `app_access`).
- **Pagina Landings** (tab "Landings") — **riservata agli admin**: chi non ha
  `'*'` in `app_access` non vede nemmeno il tab. Replica del report Excel
  "Landings siop": grafico Shipped CY vs PY per settimana + tabella recap con
  macro colonne TOTAL / SEDICO / NA (ATL+TIJ) / LHKS (=DONGGUAN) / SUMARE',
  ognuna con PY | CY | D%. La sezione **WK** è read-only dal DB (Business =
  Monday FRC, Logistics = Friday FRC); le sezioni **Month** e **Quarter** sono
  editabili (dropdown periodo + Save) e persistite nella tabella condivisa
  `landings_entries` (ultimo salvataggio vince). TOTAL e D% sono calcolati.
  Le sotto-righe "of which EMEA" (solo SEDICO) leggono il canale Frames
  `whls_net_ow_emea` ("WHLS Net ow EMEA"), compilabile solo da SEDICO e N/A per
  gli altri plant. Dettagli su grafico e permessi: vedi
  [Pagina Landings](#pagina-landings-1).

## Pagina Landings

### Permessi — solo admin

La pagina è accessibile **solo agli admin** (riga `'*'` in `app_access`). Il
gate è su quattro livelli, perché nascondere un bottone non è controllo accessi:

1. `components/header.py` non renderizza il tab `tab-landings` per i non-admin.
2. `switch_pl` rifiuta il cambio pagina (toast "Landings is admin-only") — è
   questo che ferma un click forgiato.
3. `render_ui` riporta `page` a `"entry"` se lo store è manomesso.
4. `bootstrap` azzera `page` al login: un utente a cui vengono revocati i
   diritti admin non resta con una sessione sulla pagina.
   `save_landings` applica lo stesso guard sul percorso di scrittura.

> Il toggle "2nd row" ha ancora il lock per i non-admin (🔒, radio disabilitato).
> Oggi è **irraggiungibile** — solo gli admin vedono la pagina — ed è tenuto
> apposta: torna corretto nel momento in cui l'accesso viene allargato. Non è un
> bug.

### Grafico — da dove arrivano le linee

| Linea | Fonte |
|---|---|
| Blu (PY) | `chart_weekly` dell'anno precedente, `COALESCE(value_manual, value_hist)` |
| Rossa piena (Actual) | somma `whls_net` Frames riga `actual` da `submissions`, fino a week−1; per le settimane senza submission, fallback su `chart_weekly` dell'anno corrente |
| Rossa tratteggiata (Logistics FRC) | Friday FRC della settimana aperta |

Dove una settimana ha submission di Actual, **vincono le submission**;
`chart_weekly` copre solo le settimane scoperte (tipicamente quelle precedenti
alla messa online dell'app). Il segmento tratteggiato viene disegnato solo se
l'ultima Actual è la settimana immediatamente precedente al forecast: altrimenti
resta un marker isolato, così un buco di dati si vede invece di essere nascosto
da una diagonale lunga più settimane.

> **Attenzione al gradino.** Le due serie non misurano la stessa cosa: il totale
> Ship del file sorgente comprende più della somma dei `whls_net` Actual per
> plant (~20% in più sulle settimane 25 e 27 del 2026, dove entrambe le fonti
> sono complete). La linea rossa quindi **scende di circa il 20% nel punto in cui
> la fonte cambia**. È atteso, non un difetto.

### Override manuale del grafico

Non esiste UI: `value_manual` si imposta via SQL (lo statement è anche nei
commenti di `migrations/2026-07-chart-weekly.sql`).

```sql
UPDATE volume_data_entry.chart_weekly
   SET value_manual = 1234567, updated_by = 'nome.cognome@luxottica.com',
       updated_at = now()
 WHERE year = 2025 AND week = 18;
```

Rimettere `value_manual = NULL` per tornare al valore storico. `value_hist` non
va **mai** sovrascritto.

### Job settimanale

`chart_weekly` deve continuare a crescere, altrimenti la linea PY del 2027 avrà
un buco. Serve un job schedulato (accanto a quello che apre la settimana) che
faccia l'upsert della sola settimana appena chiusa: la query è pronta in fondo a
`migrations/2026-07-chart-weekly.sql`. Il guard `HAVING SUM(value_kpcs) IS NOT
NULL` evita che una settimana senza submission azzeri una riga di backfill.

## Migrations

Il DDL vive in `migrations/`, uno script per modifica, e va eseguito su Lakebase
**prima** del deploy della versione che lo usa.

```powershell
$env:DATABRICKS_CONFIG_PROFILE = "luxottica"   # profilo ~/.databrickscfg
$env:LAKEBASE_ROLE = "nome.cognome@luxottica.com"
.\.venv\Scripts\python.exe .\scripts\run_migration.py .\migrations\<file>.sql

# query ad-hoc (utile per verificare)
.\.venv\Scripts\python.exe .\scripts\run_migration.py --sql "SELECT count(*) FROM volume_data_entry.chart_weekly"
```

Lakebase è PostgreSQL, ma la password è un token OAuth che ruota (~1h): non
esiste una connection string statica da tenere in un client, e `psql` non è
installato. `run_migration.py` costruisce l'URL al momento della chiamata, con
due differenze volute rispetto a `data/db.py`:

- il ruolo arriva da `LAKEBASE_ROLE`, non dall'URL. `_build_conn_url` risolve
  l'utente come `cfg.client_id or parsed.username`: con l'auth CLI `client_id` è
  `None`, quindi userebbe lo username dell'URL — il **service principal
  dell'app** — autenticandolo con un token personale.
- `autocommit` è **off**: una transazione per file, così un errore a metà non
  lascia mezzo script applicato (il DDL in Postgres è transazionale).

Se più profili in `~/.databrickscfg` puntano allo stesso host, `Config()` è
ambiguo: passare sempre `--profile` / `DATABRICKS_CONFIG_PROFILE`.

**Grants**: sullo schema `volume_data_entry` esiste una regola
`ALTER DEFAULT PRIVILEGES` che concede automaticamente `SELECT/INSERT/UPDATE/DELETE`
al service principal dell'app su ogni tabella creata dall'owner dello schema. Le
migration contengono comunque un `GRANT` esplicito, così l'accesso dell'app non
dipende in silenzio da chi ha eseguito lo script. **Se la app di produzione è
una App nuova ha un service principal diverso**: vanno aggiornati sia il target
del `GRANT` sia `DATABRICKS_LAKEBASE_URL` in `app.yaml`.

## Deploy

La Databricks App **`dataretrival`** (ambiente dev) deploia da un **Git folder**
del workspace clonato da questa repo:

- Git folder: `/Workspace/Users/lorenzo.muscillo@luxottica.com/volume-app`
- Branch tracciato: `dev`

> **Prima di ogni deploy**: eseguire su Lakebase le migration nuove (vedi
> [Migrations](#migrations)). Il deploy parte da git, quindi una migration non
> committata non arriva da nessuna parte — e una tabella mancante fa fallire
> l'app a runtime, non al deploy.

Il ciclo di deploy (push GitHub → pull del Git folder → deploy app):

```powershell
git push origin dev
databricks repos update /Workspace/Users/lorenzo.muscillo@luxottica.com/volume-app --branch dev
databricks apps deploy dataretrival --source-code-path /Workspace/Users/lorenzo.muscillo@luxottica.com/volume-app
```

Se usi il deploy diretto dalla UI `From Git`, il repository è già nella root, quindi:
- `Git reference` = `main` o `dev` (a seconda del branch che vuoi usare)
- `Reference type` = `Branch`
- `Source code path` = lascia vuoto

Assicurati anche che Databricks abbia una Git credential valida per GitHub (token/PAT con almeno `repo` e, se serve, `read:org`).

App URL: https://dataretrival-8661566820370235.15.azure.databricksapps.com

## Branch strategy

| Branch       | Ruolo                                                      |
|--------------|------------------------------------------------------------|
| `main`       | Riservato a produzione (app prod non ancora creata).       |
| `dev`        | Integrazione — è ciò che si deploia sull'app `dataretrival`.|
| `feature/*`  | Un branch per ogni modifica.                               |

Workflow:

```powershell
git checkout dev
git checkout -b feature/<nome>      # nuova modifica
#  ... lavori, git commit ...
git push -u origin feature/<nome>   # poi Pull Request verso dev su GitHub

# dopo il merge della PR in dev: push, pull del Git folder, deploy
```

## Sviluppo locale

```powershell
pip install -r requirements.txt
python app.py        # http://localhost:8050
```

Per lavorare **offline**, senza toccare Lakebase, impostare `VOLUMES_LOCAL_DB`:
in fondo a `data/db.py` c'è uno swap che ridireziona ogni lettura/scrittura sul
backend SQLite di `data/local_db.py` (file `local_volumes.db`, entrambi
gitignorati). Import lazy e condizionato alla variabile, quindi in produzione il
modulo non viene nemmeno importato.

```powershell
$env:VOLUMES_LOCAL_DB = "1"
python app.py
```

Il DB locale si auto-inizializza con una settimana aperta e la serie reale
`chart_weekly`; le submission partono vuote.

`app.py` legge e scrive tramite `data/db.py`: la settimana
corrente e i dati di ogni coppia (sito, product line) vengono caricati dal DB
(on-demand, alla prima apertura), e Save / Submit scrivono nelle tabelle
`drafts` / `submissions`. La connessione è lazy: se il DB non è raggiungibile
l'app si avvia comunque, con la griglia vuota.

## Runtime su Databricks Apps

L'app è servita da **gunicorn** (`app:server`, vedi `app.yaml`). La porta è
letta da `DATABRICKS_APP_PORT` con bind `0.0.0.0` in `gunicorn.conf.py`.

`DATABRICKS_HOST` e le credenziali OAuth del service principal sono iniettate
da Databricks Apps; l'auth è risolta da `databricks.sdk.Config` in `db.py`, che
usa il token come password PostgreSQL verso l'endpoint indicato da
`DATABRICKS_LAKEBASE_URL` (`app.yaml`).

**Un solo worker** (`workers = 1` in `gunicorn.conf.py`), di proposito: le
callback Dash restituiscono snapshot interi dei `dcc.Store`, quindi worker
concorrenti si sovrascriverebbero a vicenda; inoltre la cache di processo
(`data/cache.py`) e la propagazione di `app_settings` valgono per processo.

## Test DB connectivity

```powershell
$env:DATABRICKS_CONFIG_PROFILE = "luxottica"
$env:LAKEBASE_ROLE = "nome.cognome@luxottica.com"
.\.venv\Scripts\python.exe .\scripts\run_migration.py --sql "SELECT current_user, current_database()"
```

Se la connessione fallisce vedrai l'errore di psycopg2 o del provider di
credenziali. Un `refresh token is invalid` significa che quel profilo va
ri-autenticato con `databricks auth login --profile <nome>`.

## Schema DB

> **Nota**: dal 2026-05 il DB è **Lakebase (PostgreSQL)** — database
> `databricks_postgres`, schema `volume_data_entry` (vedi `data/db.py`).
> Il DDL Delta Lake più in basso è la forma storica delle 4 tabelle originali,
> tenuto come riferimento delle colonne; su Lakebase i tipi sono
> TEXT / DOUBLE PRECISION / TIMESTAMPTZ.

Tabelle: `weeks`, `submissions`, `drafts`, `app_access`, `landings_entries`,
`chart_weekly`, `app_settings`. Il DDL versionato è in `migrations/`.

```sql
-- volume_data_entry.chart_weekly — serie settimanale del grafico Landings,
-- una riga per (year, week). Vale per OGNI anno: year-1 alimenta la linea blu
-- (PY), l'anno corrente fa da fallback per la linea rossa sulle settimane
-- precedenti alla messa online. Valore = COALESCE(value_manual, value_hist);
-- value_hist non si sovrascrive mai.
CREATE TABLE IF NOT EXISTS volume_data_entry.chart_weekly (
  year         INTEGER NOT NULL,
  week         INTEGER NOT NULL,
  value_hist   DOUBLE PRECISION,
  value_manual DOUBLE PRECISION,
  updated_by   TEXT,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (year, week)
);

-- volume_data_entry.app_settings — impostazioni globali key/value condivise da
-- tutti gli utenti. Oggi una sola chiave: 'landings_row2' = 'actual' |
-- 'logistics_frc', la seconda riga dei blocchi Month/Quarter.
CREATE TABLE IF NOT EXISTS volume_data_entry.app_settings (
  key        TEXT PRIMARY KEY,
  value      TEXT,
  updated_by TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

```sql
-- volume_data_entry.landings_entries — valori Month/Quarter della pagina
-- Landings, condivisi tra tutti gli utenti (upsert, ultimo salvataggio vince)
CREATE TABLE IF NOT EXISTS volume_data_entry.landings_entries (
  period_type TEXT NOT NULL CHECK (period_type IN ('month','quarter')),
  period_key  TEXT NOT NULL,   -- '2026-06' | '2026-Q2'
  row_type    TEXT NOT NULL,   -- 'business_frc' | 'actual' | 'logistics_frc'
  col_group   TEXT NOT NULL,   -- 'SEDICO' | 'NA' | 'LHKS' | 'SUMARE'
  metric      TEXT NOT NULL,   -- 'py' | 'cy' | 'py_emea' | 'cy_emea'
  value_kpcs  DOUBLE PRECISION,
  updated_by  TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (period_key, row_type, col_group, metric)
);
```

```sql
-- `sbx-logistics`.`volume-data-entry-app`.weeks
CREATE TABLE `sbx-logistics`.`volume-data-entry-app`.weeks (
  week_id INT, year INT, created_at TIMESTAMP, is_open BOOLEAN
);

-- `sbx-logistics`.`volume-data-entry-app`.submissions
-- (le righe si aggiungono soltanto; submit_row marca official_log=FALSE su
--  quelle superate, mai DELETE)
CREATE TABLE `sbx-logistics`.`volume-data-entry-app`.submissions (
  submission_id STRING, timestamp TIMESTAMP, week_id INT, year INT, site STRING,
  product_line STRING, user_id STRING, submission_type STRING, channel STRING,
  value_kpcs DOUBLE, is_zero_flagged BOOLEAN, official_log BOOLEAN,
  comment_preset STRING, comment_other STRING, is_amendment BOOLEAN,
  ref_submission_id STRING,
  is_delay BOOLEAN DEFAULT FALSE, delay_timestamp TIMESTAMP
)
CLUSTER BY (week_id, site, product_line);

-- `sbx-logistics`.`volume-data-entry-app`.drafts (sovrascritta a ogni Save — NON append-only)
CREATE TABLE `sbx-logistics`.`volume-data-entry-app`.drafts (
  draft_id STRING, saved_at TIMESTAMP, week_id INT, year INT, site STRING,
  product_line STRING, user_id STRING, submission_type STRING, channel STRING,
  value_kpcs DOUBLE, is_zero_flagged BOOLEAN, comment_preset STRING,
  comment_other STRING
)
CLUSTER BY (week_id, site, product_line, user_id);

-- `sbx-logistics`.`volume-data-entry-app`.app_access (accesso per-utente ai siti)
CREATE TABLE `sbx-logistics`.`volume-data-entry-app`.app_access (
  email STRING, site STRING, added_at TIMESTAMP, added_by STRING
);
```

> `submissions` non viene mai cancellata: `submit_row` inserisce le nuove righe
> e poi marca `official_log = FALSE` su quelle precedenti. `get_latest_submissions`
> e `get_gli_extract` leggono la riga autorevole con `WHERE official_log = TRUE`
> (niente view).

> Indice `submissions_year_week_official_idx` su `(year, week_id) WHERE
> official_log` (`migrations/2026-07-submissions-index.sql`): serve
> all'aggregato annuale della pagina Landings, che altrimenti fa una scansione
> completa della tabella su una connessione condivisa da tutti gli utenti
> (gunicorn gira con un solo worker).

> Le colonne `is_delay` / `delay_timestamp` di `submissions` sono aggiunte via
> `ALTER TABLE submissions ADD COLUMN ...` (già applicate su Lakebase); marcano le
> scritture su settimane passate confermate dal modal di ritardo.

> La colonna `year` di `submissions` / `drafts` è aggiunta via
> `migrations/2026-07-add-year.sql`: i numeri di settimana ISO si ripetono ogni
> anno, quindi ogni lettura/scrittura filtra su `(year, week_id)` — eseguire lo
> script su Lakebase PRIMA del deploy di questa versione (vedi le note nello
> script per l'ordine di deploy).

## Gestione accessi

Gli accessi vivono nella tabella `app_access`: una riga per (utente, sito),
gestibile con SQL, **senza redeploy** e senza email nel repo.

| `site` | Significato |
|---|---|
| `'*'` | **admin** — scrive su tutti i plant ed è l'unico che vede la pagina Landings |
| nome plant | owner: scrive solo su quel plant (una riga per plant) |
| `'VIEW'` | sola lettura ovunque; non è admin, quindi niente Landings |

```sql
-- admin: accesso a tutti i siti + pagina Landings
INSERT INTO volume_data_entry.app_access (email, site, added_at, added_by)
VALUES ('nome.cognome@luxottica.com', '*', now(), 'lorenzo');

-- owner di un plant: una riga per ogni plant abilitato
INSERT INTO volume_data_entry.app_access (email, site, added_at, added_by)
VALUES ('owner.atlanta@luxottica.com', 'ATLANTA', now(), 'lorenzo');
```

Revoca: `DELETE FROM ... WHERE email = '...'` (eventualmente `AND site = '...'`).
La lista è in cache con TTL 5 minuti: una modifica via SQL si propaga entro quel
lasso, oppure subito con il bottone "Double Tap".

Se `app_access` è vuota o illeggibile l'app ripiega su un singolo admin di
fallback (`DEV_USER` in `app.py`), così non resta mai senza nessuno che possa
entrare.

## Item aperti prima della produzione

- Confermare etichette/colonne Wearables di Dongguan (`repl_el`, `meta`, `dummy`)
- Scadenzario per sito allineato al BBP v0.6 (`data/schema.py` — `DEADLINES`); conferma finale con MatteB
- Creare l'app di produzione. Prima del go-live servono anche:
  - il **job che apre la settimana** (nulla nell'app chiama `db.create_week`:
    senza settimana aperta `get_current_week()` solleva e l'app parte vuota);
  - il **job settimanale** che appende su `chart_weekly` (vedi Pagina Landings);
  - le righe `app_access` degli utenti reali, con almeno un `'*'`;
  - `workers = 1` in `gunicorn.conf.py` va lasciato com'è: la propagazione delle
    impostazioni globali (`app_settings`) passa dalla cache di processo.