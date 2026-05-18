# Volumes Data Entry Tool — Databricks App

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
│   └── data_table.py   # Tabella, summary bar, pannello Friday, legenda
├── data/
│   ├── schema.py       # Colonne, matrice N/A, scadenze
│   └── db.py           # Lettura/scrittura Delta Lake (databricks-sql-connector)
├── scripts/
│   └── deploy-dev.ps1  # Push su GitHub del branch dev
└── README.md
```

> Tutto il contenuto della repo sta nella root: la Databricks App è collegata
> alla repo GitHub via URL (senza sottocartella), quindi deploia da qui.

## Deploy

La Databricks App **`dataretrival`** (ambiente dev) deploia da un **Git folder**
del workspace clonato da questa repo:

- Git folder: `/Workspace/Users/lorenzo.muscillo@luxottica.com/volume-app`
- Branch tracciato: `dev`

Il deploy si lancia con un solo comando, che fa l'intero ciclo
(push GitHub → pull del Git folder → deploy app):

```powershell
.\scripts\deploy-dev.ps1
```

In alternativa, manualmente:

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

# dopo il merge della PR in dev:
git checkout dev
git pull
.\scripts\deploy-dev.ps1            # push + pull Git folder + deploy
```

## Sviluppo locale

```powershell
pip install -r requirements.txt
python app.py        # http://localhost:8050
```

`app.py` legge e scrive su Delta Lake tramite `data/db.py`: la settimana
corrente e i dati di ogni coppia (sito, product line) vengono caricati dal DB
(on-demand, alla prima apertura), e Save / Submit scrivono nelle tabelle
`drafts` / `submissions`. La connessione è lazy: se il DB non è raggiungibile
l'app si avvia comunque, con la griglia vuota.

## Runtime su Databricks Apps

L'app è servita da **gunicorn** (`app:server`, vedi `app.yaml`). La porta è
letta da `DATABRICKS_APP_PORT` con bind `0.0.0.0` in `gunicorn.conf.py`.

`DATABRICKS_HOST` e le credenziali OAuth del service principal sono iniettate
da Databricks Apps; l'auth è risolta da `databricks.sdk.Config` in `db.py`.
`DATABRICKS_HTTP_PATH` è collegato in `app.yaml` alla risorsa SQL Warehouse
dell'app (`valueFrom: sql-warehouse`).

## Test DB connectivity

Per verificare la connessione e la scrittura sul DB puoi usare lo script:

```powershell
python .\scripts\test_db.py
```

Prima di eseguirlo, imposta le variabili di ambiente:

```powershell
$env:DATABRICKS_HOST = "https://<your-databricks-host>"
$env:DATABRICKS_HTTP_PATH = "<your-sql-warehouse-http-path>"
$env:DATABRICKS_TOKEN = "<your-databricks-token>"
$env:TEST_WEEK_ID = "<open-week-id>"
$env:TEST_SITE = "<site>"
$env:TEST_PRODUCT_LINE = "<product-line>"
```

Lo script salva un draft di test nella tabella `drafts` e lo cancella subito dopo.
Se la scrittura fallisce, vedrai l'errore restituito dalla connessione SQL.

## Schema Delta Lake atteso

Tabelle in Unity Catalog sotto `sbx-logistics.volume-data-entry-app`:

```sql
-- `sbx-logistics`.`volume-data-entry-app`.weeks
CREATE TABLE `sbx-logistics`.`volume-data-entry-app`.weeks (
  week_id INT, year INT, created_at TIMESTAMP, is_open BOOLEAN
);

-- `sbx-logistics`.`volume-data-entry-app`.submissions (append-only — mai UPDATE/DELETE)
CREATE TABLE `sbx-logistics`.`volume-data-entry-app`.submissions (
  submission_id STRING, timestamp TIMESTAMP, week_id INT, site STRING,
  product_line STRING, user_id STRING, submission_type STRING, channel STRING,
  value_kpcs DOUBLE, is_zero_flagged BOOLEAN, official_log BOOLEAN,
  comment_preset STRING, comment_other STRING, is_amendment BOOLEAN,
  ref_submission_id STRING
);

-- `sbx-logistics`.`volume-data-entry-app`.drafts (sovrascritta a ogni Save — NON append-only)
CREATE TABLE `sbx-logistics`.`volume-data-entry-app`.drafts (
  draft_id STRING, saved_at TIMESTAMP, week_id INT, site STRING,
  product_line STRING, user_id STRING, submission_type STRING, channel STRING,
  value_kpcs DOUBLE, is_zero_flagged BOOLEAN, comment_preset STRING,
  comment_other STRING
);

-- `sbx-logistics`.`volume-data-entry-app`.app_access (accesso per-utente ai siti)
CREATE TABLE `sbx-logistics`.`volume-data-entry-app`.app_access (
  email STRING, site STRING, added_at TIMESTAMP, added_by STRING
);
```

> `submissions` è append-only: `db.get_latest_submissions` e `get_gli_extract`
> prendono l'ultima riga per chiave con una window function sul `timestamp` —
> niente view, niente flip di `official_log`.

## Gestione accessi

Gli accessi vivono nella tabella `app_access`: una riga per (utente, sito),
gestibile con SQL, **senza redeploy** e senza email nel repo. `site = '*'`
significa admin (tutti i siti); un nome di plant abilita solo quel plant.

```sql
-- admin: accesso a tutti i siti
INSERT INTO `sbx-logistics`.`volume-data-entry-app`.app_access (email, site, added_at, added_by)
VALUES ('nome.cognome@luxottica.com', '*', current_timestamp(), 'lorenzo');

-- owner di un plant: una riga per ogni plant abilitato
INSERT INTO `sbx-logistics`.`volume-data-entry-app`.app_access (email, site, added_at, added_by)
VALUES ('owner.atlanta@luxottica.com', 'ATLANTA', current_timestamp(), 'lorenzo');
```

Revoca: `DELETE FROM ... WHERE email = '...'` (eventualmente `AND site = '...'`).

## Item aperti prima della produzione

- Confermare etichette/colonne Wearables di Dongguan (`repl_el`, `meta`, `dummy`)
- Confermare lo scadenzario per sito (`data/schema.py` — `DEADLINES`)
- Mappare gli utenti non-admin → proprio plant (oggi i non-admin sono limitati
  a `OWN_SITE`; gli admin si gestiscono nella tabella `admins`)
- Creare l'app di produzione