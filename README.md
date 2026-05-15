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

La Databricks App **`dataretrival`** (ambiente dev) è collegata a questa repo:
`https://github.com/loreluxottica/volume-app.git`.

Il deploy NON passa più dal `databricks sync` locale. Flusso:

1. `git push` delle modifiche su GitHub (vedi `scripts/deploy-dev.ps1`).
2. Nella UI Databricks → App `dataretrival` → **Deploy**, selezionando il
   branch `dev`. Databricks pull il codice dalla repo e riavvia l'app.

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
.\scripts\deploy-dev.ps1            # push di dev su GitHub
# poi: Deploy dalla UI Databricks (branch dev)
```

## Sviluppo locale

```powershell
pip install -r requirements.txt
python app.py        # http://localhost:8050
```

Il layer DB (`data/db.py`) usa connessione lazy: l'app si avvia anche senza un
SQL Warehouse configurato. Attualmente `app.py` **non** chiama `db.py` — gira su
dati stub (i punti di aggancio sono i commenti `# In production:` nei callback).

## Runtime su Databricks Apps

L'app è servita da **gunicorn** (`app:server`, vedi `app.yaml`). La porta è
letta da `DATABRICKS_APP_PORT` con bind `0.0.0.0` in `gunicorn.conf.py`.

`DATABRICKS_HOST` e `DATABRICKS_TOKEN` sono iniettati da Databricks Apps.
Per leggere/scrivere su Delta Lake serve impostare a mano `DATABRICKS_HTTP_PATH`
(HTTP path del SQL Warehouse) nelle env var dell'app — non ancora configurato.

## Schema Delta Lake atteso

Tabelle in Unity Catalog sotto `gli.volumes.*`:

```sql
-- gli.volumes.weeks
CREATE TABLE gli.volumes.weeks (
  week_id INT, year INT, created_at TIMESTAMP, is_open BOOLEAN
);

-- gli.volumes.submissions (append-only — mai UPDATE/DELETE)
CREATE TABLE gli.volumes.submissions (
  submission_id STRING, timestamp TIMESTAMP, week_id INT, site STRING,
  product_line STRING, user_id STRING, submission_type STRING, channel STRING,
  value_kpcs DOUBLE, is_zero_flagged BOOLEAN, official_log BOOLEAN,
  comment_preset STRING, comment_other STRING, is_amendment BOOLEAN,
  ref_submission_id STRING
);

-- gli.volumes.drafts (sovrascritta a ogni Save — NON append-only)
CREATE TABLE gli.volumes.drafts (
  draft_id STRING, saved_at TIMESTAMP, week_id INT, site STRING,
  product_line STRING, user_id STRING, submission_type STRING, channel STRING,
  value_kpcs DOUBLE, is_zero_flagged BOOLEAN, comment_preset STRING,
  comment_other STRING
);

-- gli.volumes.gli_extract (view — ultimo valore ufficiale per chiave)
CREATE VIEW gli.volumes.gli_extract AS
SELECT week_id, site, product_line, submission_type,
       channel, value_kpcs, comment_preset, comment_other
FROM gli.volumes.submissions
WHERE official_log = TRUE;
```

## Item aperti prima della produzione

- Confermare la matrice N/A per sito (`data/schema.py` — `NA_FRAMES`/`NA_WEARABLES`)
- Confermare lo scadenzario per sito (`data/schema.py` — `DEADLINES`)
- Collegare le chiamate `db.py` nei callback di `app.py` (`# In production:`)
- Mappare l'identità utente Databricks → sito (`OWN_SITE` in `app.py`)
- Configurare `DATABRICKS_HTTP_PATH` e l'app di produzione