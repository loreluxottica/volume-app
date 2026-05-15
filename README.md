# Volumes App

Volumes Data Entry Tool — Databricks App (Dash). Questa repo contiene il codice
dell'app e gli script di deploy.

## Struttura

```
volumes_app/
├── app/              # Codice dell'app — SOLO questa cartella viene deployata su Databricks
│   ├── app.py
│   ├── app.yaml
│   ├── gunicorn.conf.py
│   ├── requirements.txt
│   ├── assets/  components/  data/
│   └── README.md     # Dettaglio architettura app, schema Delta Lake
├── scripts/
│   └── deploy-dev.ps1
└── README.md         # questo file
```

## Ambienti

| Ambiente | App Databricks | Branch    | Deploy                      |
|----------|----------------|-----------|-----------------------------|
| dev      | `dataretrival` | `dev`     | `.\scripts\deploy-dev.ps1`  |
| prod     | _da creare_    | `main`    | _da configurare_            |

Per ora è attivo solo **dev**. Prod verrà aggiunto creando una App Databricks
dedicata, un `deploy-prod.ps1` e attivando il branch `main`.

## Branch strategy

- **`main`** — riservato a produzione. Non deployato finché prod non è configurato.
- **`dev`** — ramo di integrazione. È ciò che gira sull'app DEV.
- **`feature/*`** — un branch per ogni modifica.

Workflow:

```
git checkout dev
git checkout -b feature/<nome>     # nuova modifica
# ... lavori, commit ...
git push -u origin feature/<nome>  # poi Pull Request verso dev su GitHub
# dopo il merge in dev:
git checkout dev && git pull
.\scripts\deploy-dev.ps1           # pubblica su DEV
```

## Sviluppo locale

```powershell
cd app
pip install -r requirements.txt
python app.py        # http://localhost:8050
```

Il layer DB (`app/data/db.py`) richiede le env var `DATABRICKS_HOST`,
`DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN`. La connessione è lazy: l'app si
avvia anche senza, le query falliscono finché non sono configurate.

## Prerequisiti deploy

- Databricks CLI nel PATH (`databricks --version`)
- Profilo autenticato: `databricks auth login --host https://adb-8661566820370235.15.azuredatabricks.net`