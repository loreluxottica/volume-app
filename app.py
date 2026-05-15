# app.py
# ─────────────────────────────────────────────────────────────────────────────
# Entry point for the Volumes Data Entry Tool Databricks App.
#
# Run locally:
#   pip install dash databricks-sql-connector pandas
#   python app.py
#
# Deploy on Databricks Apps:
#   - Set DATABRICKS_HOST, DATABRICKS_HTTP_PATH, DATABRICKS_TOKEN env vars
#     (injected automatically when deployed as a Databricks App)
#   - Upload this folder as the app source
#   - Set app.py as the entry point
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime

import dash
from dash import Input, Output, State, ctx, dcc, html, ALL, MATCH

from components.header import render_topbar, render_app_header
from components.data_table import render_data_table
from data.schema import (
    ROWS, COLS_BY_PL, NA_BY_PL, SITES,
    COMMENT_PRESETS, THRESHOLD_ABS, THRESHOLD_REL,
)
# OWN_SITE is defined as a dev stub below (in production it comes from env).

# ── App init ──────────────────────────────────────────────────────────────────

app = dash.Dash(
    __name__,
    title="Volumes Data Entry Tool",
    external_stylesheets=[
        "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600"
        "&family=DM+Sans:wght@300;400;500;600&display=swap"
    ],
    suppress_callback_exceptions=True,
)

# WSGI entry point for gunicorn on Databricks Apps (`app:server` in app.yaml)
server = app.server

# In production (Databricks Apps) this is set via env; for dev use a stub
OWN_SITE = "SEDICO"

# ── Schema constant ───────────────────────────────────────────────────────────
# Actual week_id comes from DB in production; hardcoded for dev/POC
CURRENT_WEEK = {"week_id": 19, "year": 2026}


def _empty_state() -> dict:
    """
    Return a fresh client-side state dict stored in dcc.Store.
    Structure mirrors the JS state in the HTML mockup.
    """
    state: dict = {
        "site":           OWN_SITE,
        "pl":             "FRAMES",
        "fri_open":       False,
        "submit_attempted": False,
        "values":         {},   # {site: {pl: {row_id: {col_id: value}}}}
        "submitted":      {},   # {site: {pl: {row_id: bool}}}
        "drafted":        {},   # {site: {pl: {row_id: bool}}}
        "zero_flags":     {},   # {site: {pl: {row_id: {col_id: bool}}}}
        "fri_comments":   {},   # {pl: {col_id: {presets:[], others:""}}}
    }
    for s in SITES:
        state["values"][s]     = {}
        state["submitted"][s]  = {}
        state["drafted"][s]    = {}
        state["zero_flags"][s] = {}
        for pl in ["FRAMES", "WEARABLES"]:
            cols = COLS_BY_PL[pl]
            state["values"][s][pl]     = {r["id"]: {c["id"]: "" for c in cols} for r in ROWS}
            state["submitted"][s][pl]  = {r["id"]: False for r in ROWS}
            state["drafted"][s][pl]    = {r["id"]: False for r in ROWS}
            state["zero_flags"][s][pl] = {r["id"]: {c["id"]: False for c in cols} for r in ROWS}

    for pl in ["FRAMES", "WEARABLES"]:
        cols = COLS_BY_PL[pl]
        state["fri_comments"][pl] = {c["id"]: {"presets": [], "others": ""} for c in cols}

    # ── Seed SEDICO FRAMES with demo data (remove in production) ──
    sf = state["values"]["SEDICO"]["FRAMES"]
    sf["py"]      = {"inbound":"1200","rop_std":"349","rop_samples":"","whls_gross":"690","whls_net":"628","retail":"141","gvi":"253","ds_na":"35","ecom":"26","sample":"2"}
    sf["siop"]    = {"inbound":"1275","rop_std":"242","rop_samples":"","whls_gross":"721","whls_net":"","retail":"163","gvi":"214","ds_na":"40","ecom":"14","sample":"2"}
    sf["mon_frc"] = {"inbound":"1307","rop_std":"375","rop_samples":"8","whls_gross":"690","whls_net":"655","retail":"105","gvi":"211","ds_na":"16","ecom":"31","sample":"2"}
    sf["thu_frc"] = {"inbound":"1307","rop_std":"375","rop_samples":"","whls_gross":"690","whls_net":"655","retail":"105","gvi":"211","ds_na":"16","ecom":"31","sample":"2"}
    state["submitted"]["SEDICO"]["FRAMES"]["py"]      = True
    state["submitted"]["SEDICO"]["FRAMES"]["siop"]    = True
    state["submitted"]["SEDICO"]["FRAMES"]["mon_frc"] = True
    state["submitted"]["SEDICO"]["FRAMES"]["thu_frc"] = True

    return state


# ── Layout ────────────────────────────────────────────────────────────────────

app.layout = html.Div([
    # Client-side state store (replaces JS module-level vars from the mockup)
    dcc.Store(id="app-state", data=_empty_state()),
    # Toast notification store
    dcc.Store(id="toast-store", data=""),

    # Topbar (static — user identity doesn't change mid-session)
    render_topbar("Lorenzo Muscillo"),

    # Dynamic section — re-rendered on every state change
    html.Div(id="app-header-container"),
    html.Div(id="app-body-container"),

    # Toast notification (injected via clientside callback)
    html.Div(id="toast-container", className="toast-container"),
])


# ── Main render callback ──────────────────────────────────────────────────────
# Re-renders header + body whenever app-state changes.

@app.callback(
    Output("app-header-container", "children"),
    Output("app-body-container",   "children"),
    Input("app-state", "data"),
)
def render_ui(state: dict):
    site      = state["site"]
    pl        = state["pl"]
    is_ro     = site != OWN_SITE
    submitted = state["submitted"][site][pl]
    drafted   = state["drafted"][site][pl]
    values    = state["values"][site][pl]
    zf        = state["zero_flags"][site][pl]
    fc        = state["fri_comments"][pl]
    fri_open  = state["fri_open"]
    sa        = state["submit_attempted"]

    header = render_app_header(
        current_site=site, current_pl=pl,
        week_id=CURRENT_WEEK["week_id"], year=CURRENT_WEEK["year"],
        is_readonly=is_ro,
    )
    body = render_data_table(
        current_site=site, current_pl=pl,
        form_values=values,
        submitted=submitted, drafted=drafted,
        zero_flags=zf, fri_comments=fc,
        fri_open=fri_open, submit_attempted=sa,
        is_readonly=is_ro,
    )
    return header, body


# ── Site selector callback ────────────────────────────────────────────────────

@app.callback(
    Output("app-state", "data", allow_duplicate=True),
    Input("site-select", "value"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def change_site(site: str, state: dict) -> dict:
    if site and site != state["site"]:
        state = deepcopy(state)
        state["site"]     = site
        state["fri_open"] = False
        state["submit_attempted"] = False
    return state


# ── Product line tab callbacks ────────────────────────────────────────────────

@app.callback(
    Output("app-state", "data", allow_duplicate=True),
    Input("tab-frames",    "n_clicks"),
    Input("tab-wearables", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def switch_pl(n_frames, n_wear, state: dict) -> dict:
    triggered = ctx.triggered_id
    new_pl = "FRAMES" if triggered == "tab-frames" else "WEARABLES"
    if new_pl != state["pl"]:
        state = deepcopy(state)
        state["pl"]       = new_pl
        state["fri_open"] = False
        state["submit_attempted"] = False
    return state


# ── Row-level input callback ──────────────────────────────────────────────────

@app.callback(
    Output("app-state", "data", allow_duplicate=True),
    Input({"type": "row-input", "row": ALL, "col": ALL}, "value"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def update_row_values(values, state: dict) -> dict:
    if not ctx.triggered:
        return dash.no_update
    state = deepcopy(state)
    site, pl = state["site"], state["pl"]
    for trigger in ctx.triggered:
        prop_id = trigger["prop_id"]
        # Parse pattern-matching id: {"type":"row-input","row":"mon_frc","col":"inbound"}.value
        id_part = prop_id.split(".")[0]
        id_dict = json.loads(id_part)
        row_id, col_id = id_dict["row"], id_dict["col"]
        val = trigger["value"]
        state["values"][site][pl][row_id][col_id] = str(val) if val is not None else ""
    return state


# ── Friday FRC input callback ─────────────────────────────────────────────────

@app.callback(
    Output("app-state", "data", allow_duplicate=True),
    Input({"type": "fri-input",   "col": ALL}, "value"),
    Input({"type": "fri-zero",    "col": ALL}, "value"),
    Input({"type": "fri-presets", "col": ALL}, "value"),
    Input({"type": "fri-others",  "col": ALL}, "value"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def update_fri_values(fri_vals, fri_zeros, fri_presets, fri_others, state: dict) -> dict:
    if not ctx.triggered:
        return dash.no_update
    state = deepcopy(state)
    site, pl = state["site"], state["pl"]

    for trigger in ctx.triggered:
        prop_id = trigger["prop_id"]
        id_part = prop_id.split(".")[0]
        id_dict = json.loads(id_part)
        t       = id_dict["type"]
        col_id  = id_dict["col"]
        val     = trigger["value"]

        if t == "fri-input":
            state["values"][site][pl]["fri_frc"][col_id] = str(val) if val is not None else ""
        elif t == "fri-zero":
            is_zero = bool(val)
            state["zero_flags"][site][pl]["fri_frc"][col_id] = is_zero
            if is_zero:
                state["values"][site][pl]["fri_frc"][col_id] = "0"
        elif t == "fri-presets":
            state["fri_comments"][pl][col_id]["presets"] = val or []
        elif t == "fri-others":
            state["fri_comments"][pl][col_id]["others"] = val or ""

    return state


# ── Friday panel toggle ───────────────────────────────────────────────────────

@app.callback(
    Output("app-state", "data", allow_duplicate=True),
    Input("btn-fri-toggle", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def toggle_fri_panel(n, state: dict) -> dict:
    if n:
        state = deepcopy(state)
        state["fri_open"] = not state["fri_open"]
    return state


# ── Save row ──────────────────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data",    allow_duplicate=True),
    Output("toast-store", "data",    allow_duplicate=True),
    Input({"type": "btn-save", "row": ALL}, "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def save_row(n_clicks_list, state: dict):
    triggered = ctx.triggered_id
    if not triggered or not any(n_clicks_list):
        return dash.no_update, dash.no_update

    row_id = triggered["row"]
    state  = deepcopy(state)
    site, pl = state["site"], state["pl"]
    na_cols = NA_BY_PL[pl].get(row_id, [])
    cols    = COLS_BY_PL[pl]
    vals    = state["values"][site][pl][row_id]
    has_data = any(vals.get(c["id"], "") for c in cols if c["id"] not in na_cols)

    if not has_data:
        return state, "⚠ Enter at least one value before saving."

    state["drafted"][site][pl][row_id] = True
    row_label = next(r["label"] for r in ROWS if r["id"] == row_id)

    # In production: call db.save_draft(...) here
    return state, f"⤓ {row_label} — draft saved"


# ── Submit row ────────────────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data",    allow_duplicate=True),
    Output("toast-store", "data",    allow_duplicate=True),
    Input({"type": "btn-submit", "row": ALL}, "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def submit_row(n_clicks_list, state: dict):
    triggered = ctx.triggered_id
    if not triggered or not any(n_clicks_list):
        return dash.no_update, dash.no_update

    row_id = triggered["row"]
    state  = deepcopy(state)
    site, pl = state["site"], state["pl"]
    na_cols  = NA_BY_PL[pl].get(row_id, [])
    cols     = COLS_BY_PL[pl]
    vals     = state["values"][site][pl][row_id]
    has_data = any(vals.get(c["id"], "") for c in cols if c["id"] not in na_cols)

    if not has_data:
        return state, "⚠ Enter at least one value before submitting."

    state["submitted"][site][pl][row_id] = True
    state["drafted"][site][pl][row_id]   = False
    row_label = next(r["label"] for r in ROWS if r["id"] == row_id)

    # In production: call db.submit_row(...) here, then db.delete_draft(...)
    return state, f"✓ {row_label} submitted"


# ── Change submission (re-open) ───────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data",    allow_duplicate=True),
    Output("toast-store", "data",    allow_duplicate=True),
    Input({"type": "btn-change", "row": ALL}, "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def change_submission(n_clicks_list, state: dict):
    triggered = ctx.triggered_id
    if not triggered or not any(n_clicks_list):
        return dash.no_update, dash.no_update

    row_id = triggered["row"]
    state  = deepcopy(state)
    site, pl = state["site"], state["pl"]
    state["submitted"][site][pl][row_id] = False
    return state, "Row re-opened for editing."


# ── Save Friday FRC ───────────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data",    allow_duplicate=True),
    Output("toast-store", "data",    allow_duplicate=True),
    Input("btn-fri-save",        "n_clicks"),
    Input("btn-fri-save-bottom", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def save_fri(n1, n2, state: dict):
    if not (n1 or n2):
        return dash.no_update, dash.no_update

    state  = deepcopy(state)
    site, pl = state["site"], state["pl"]
    na_cols  = NA_BY_PL[pl].get("fri_frc", [])
    cols     = COLS_BY_PL[pl]
    vals     = state["values"][site][pl]["fri_frc"]
    has_data = any(vals.get(c["id"], "") for c in cols if c["id"] not in na_cols)

    if not has_data:
        return state, "⚠ Enter at least one value before saving."

    state["drafted"][site][pl]["fri_frc"] = True
    # In production: call db.save_draft(...) here
    return state, "⤓ Friday FRC — draft saved"


# ── Submit Friday FRC ─────────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data",    allow_duplicate=True),
    Output("toast-store", "data",    allow_duplicate=True),
    Input("btn-fri-submit",        "n_clicks"),
    Input("btn-fri-submit-bottom", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def submit_fri(n1, n2, state: dict):
    if not (n1 or n2):
        return dash.no_update, dash.no_update

    state  = deepcopy(state)
    site, pl = state["site"], state["pl"]
    na_cols  = NA_BY_PL[pl].get("fri_frc", [])
    cols     = COLS_BY_PL[pl]
    vals     = state["values"][site][pl]["fri_frc"]
    mon_vals = state["values"][site][pl].get("mon_frc", {})
    has_data = any(vals.get(c["id"], "") for c in cols if c["id"] not in na_cols)

    if not has_data:
        state["submit_attempted"] = True
        return state, "⚠ Enter at least one value before submitting."

    # Check comment requirements
    fc = state["fri_comments"][pl]
    below_ids: list[str] = []
    for col in cols:
        cid = col["id"]
        if cid in na_cols:
            continue
        try:
            fri = float(vals.get(cid, "") or 0)
            mon = float(mon_vals.get(cid, "") or 0)
        except ValueError:
            continue
        if vals.get(cid, "") == "" or mon_vals.get(cid, "") == "":
            continue
        diff = mon - fri
        if diff >= THRESHOLD_ABS or (mon > 0 and diff / mon >= THRESHOLD_REL):
            below_ids.append(cid)

    missing = [
        cid for cid in below_ids
        if not (fc.get(cid, {}).get("presets") or fc.get(cid, {}).get("others", "").strip())
    ]

    if missing:
        state["submit_attempted"] = True
        missing_labels = [c["label"] for c in cols if c["id"] in missing]
        return state, f"⚠ Comment required: {', '.join(missing_labels)}"

    state["submitted"][site][pl]["fri_frc"] = True
    state["drafted"][site][pl]["fri_frc"]   = False
    state["fri_open"]        = False
    state["submit_attempted"] = False
    # In production: call db.submit_row(...) + db.delete_draft(...) here
    return state, "✓ Friday FRC submitted"


# ── Change Friday submission ──────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data",    allow_duplicate=True),
    Output("toast-store", "data",    allow_duplicate=True),
    Input("btn-fri-change", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def change_fri(n, state: dict):
    if not n:
        return dash.no_update, dash.no_update
    state = deepcopy(state)
    site, pl = state["site"], state["pl"]
    state["submitted"][site][pl]["fri_frc"] = False
    state["fri_open"] = True
    state["submit_attempted"] = False
    return state, "Friday FRC re-opened for editing."


# ── Save all / Submit all ─────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data",    allow_duplicate=True),
    Output("toast-store", "data",    allow_duplicate=True),
    Input("btn-save-all",   "n_clicks"),
    Input("btn-submit-all", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def bulk_action(n_save, n_submit, state: dict):
    triggered = ctx.triggered_id
    if not triggered:
        return dash.no_update, dash.no_update

    state  = deepcopy(state)
    site, pl = state["site"], state["pl"]
    n = 0

    for row in ROWS:
        rid = row["id"]
        if row["is_fri"] or row["is_ref"]:
            continue
        if state["submitted"][site][pl][rid]:
            continue
        na_cols  = NA_BY_PL[pl].get(rid, [])
        cols     = COLS_BY_PL[pl]
        vals     = state["values"][site][pl][rid]
        has_data = any(vals.get(c["id"], "") for c in cols if c["id"] not in na_cols)
        if not has_data:
            continue

        if triggered == "btn-save-all":
            state["drafted"][site][pl][rid] = True
        else:
            state["submitted"][site][pl][rid] = True
            state["drafted"][site][pl][rid]   = False
        n += 1

    if triggered == "btn-save-all":
        msg = f"⤓ {n} row(s) saved as draft" if n else "No open rows with data to save."
    else:
        msg = f"✓ {n} row(s) submitted" if n else "No open rows with data."

    return state, msg


# ── Toast clientside callback ─────────────────────────────────────────────────
# Injects toast DOM element when toast-store updates — pure JS, no round trip.

app.clientside_callback(
    """
    function(msg) {
        if (!msg) return window.dash_clientside.no_update;
        const container = document.getElementById('toast-container');
        if (!container) return window.dash_clientside.no_update;
        const toast = document.createElement('div');
        toast.className = 'toast';
        toast.textContent = msg;
        container.appendChild(toast);
        setTimeout(() => { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 2800);
        return window.dash_clientside.no_update;
    }
    """,
    Output("toast-container", "children"),
    Input("toast-store", "data"),
    prevent_initial_call=True,
)


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Local dev only. On Databricks Apps the app is served by gunicorn
    # (see app.yaml), which imports `server` above instead of running this.
    app.run(debug=True, port=8050)
