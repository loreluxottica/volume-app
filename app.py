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
from data import db
from data.schema import (
    ROWS, COLS_BY_PL, NA_BY_PL, SITES,
    COMMENT_PRESETS, THRESHOLD_ABS, THRESHOLD_REL,
)
# OWN_SITE / USER_ID are dev stubs below — in production they come from the
# Databricks user identity (see README open items).

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

# DEV STUBS — in production these come from the Databricks user identity.
OWN_SITE  = "SEDICO"
USER_ID   = "dev-user"
USER_NAME = "Lorenzo Muscillo"


# ── Current week ──────────────────────────────────────────────────────────────
# The open week is owned by the DB (weeks table, set by the Tuesday job).

def _load_current_week() -> dict:
    try:
        wk = db.get_current_week()
        return {"week_id": int(wk["week_id"]), "year": int(wk["year"])}
    except Exception as exc:  # DB unavailable — app still starts
        print(f"[warn] could not load current week from DB: {exc}")
        return {"week_id": 0, "year": 0}


CURRENT_WEEK = _load_current_week()


# ── DB ↔ state helpers ────────────────────────────────────────────────────────

def _to_float(s) -> float | None:
    """Form string → DB numeric (empty → None)."""
    if isinstance(s, str):
        s = s.strip()
    if s in (None, ""):
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _fmt(v) -> str:
    """DB numeric → display string for a number input (None/NaN → '')."""
    if v is None or v != v:
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else str(f)


def _truthy(v) -> bool:
    """Bool-ish DB value → bool (None/NaN → False)."""
    return bool(v) if v == v else False


def _cell_str(v) -> str:
    """Text DB value → str (None/NaN → '')."""
    return v if isinstance(v, str) else ""


def _apply_comment(fri_comments_pl: dict, cid: str, row) -> None:
    """Copy a Friday FRC row's stored comment into fri_comments state."""
    if cid not in fri_comments_pl:
        return
    preset = _cell_str(row.get("comment_preset"))
    fri_comments_pl[cid]["presets"] = [p for p in preset.split(",") if p]
    fri_comments_pl[cid]["others"]  = _cell_str(row.get("comment_other"))


def _load_slice(state: dict, site: str, pl: str) -> None:
    """
    Populate state[...][site][pl] from the DB. No-op if the slice is already
    loaded; on a DB read error the slice is left empty and NOT marked loaded
    (so it is retried the next time it is viewed).
    """
    key = f"{site}|{pl}"
    if key in state["loaded"]:
        return
    week    = CURRENT_WEEK["week_id"]
    col_ids = {c["id"] for c in COLS_BY_PL[pl]}
    is_own  = site == OWN_SITE

    try:
        latest = db.get_latest_submissions(week, site, pl)
    except Exception as exc:
        print(f"[warn] get_latest_submissions failed for {key}: {exc}")
        return

    for _, r in latest.iterrows():
        rid, cid = r["submission_type"], r["channel"]
        if rid not in state["values"][site][pl] or cid not in col_ids:
            continue
        state["values"][site][pl][rid][cid] = _fmt(r["value_kpcs"])
        state["submitted"][site][pl][rid]   = True
        if _truthy(r["is_zero_flagged"]):
            state["zero_flags"][site][pl][rid][cid] = True
        if is_own and rid == "fri_frc":
            _apply_comment(state["fri_comments"][pl], cid, r)

    # Drafts exist only for the user's own site.
    if is_own:
        try:
            drafts = db.get_drafts(week, site, pl, USER_ID)
        except Exception as exc:
            print(f"[warn] get_drafts failed for {key}: {exc}")
            drafts = None
        if drafts is not None:
            for _, r in drafts.iterrows():
                rid, cid = r["submission_type"], r["channel"]
                if rid not in state["values"][site][pl] or cid not in col_ids:
                    continue
                if state["submitted"][site][pl].get(rid):
                    continue  # a submitted row wins over a stale draft
                state["values"][site][pl][rid][cid] = _fmt(r["value_kpcs"])
                state["drafted"][site][pl][rid]     = True
                if _truthy(r["is_zero_flagged"]):
                    state["zero_flags"][site][pl][rid][cid] = True
                if rid == "fri_frc":
                    _apply_comment(state["fri_comments"][pl], cid, r)

    state["loaded"].append(key)


def _db_payload(state: dict, site: str, pl: str, row_id: str):
    """Build (values, zero_flags, comments) for one row, ready for db.py."""
    na_cols = NA_BY_PL[pl].get(row_id, [])
    raw     = state["values"][site][pl][row_id]
    zf      = state["zero_flags"][site][pl][row_id]
    values, zero_flags = {}, {}
    for c in COLS_BY_PL[pl]:
        cid = c["id"]
        if cid in na_cols:
            continue
        values[cid]     = _to_float(raw.get(cid, ""))
        zero_flags[cid] = bool(zf.get(cid, False))
    comments: dict = {}
    if row_id == "fri_frc":
        for cid, fc in state["fri_comments"][pl].items():
            comments[cid] = {
                "presets": fc.get("presets", []),
                "others":  fc.get("others", ""),
            }
    return values, zero_flags, comments


def _empty_state() -> dict:
    """
    Return a fresh client-side state dict stored in dcc.Store, pre-loaded
    with the user's own site (Frames) from the DB.
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
        "loaded":         [],   # ["site|pl", ...] slices fetched from the DB
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

    # Pre-load the default view (own site, Frames) from the DB.
    _load_slice(state, OWN_SITE, "FRAMES")

    return state


# ── Layout ────────────────────────────────────────────────────────────────────

app.layout = html.Div([
    # Client-side state store (replaces JS module-level vars from the mockup)
    dcc.Store(id="app-state", data=_empty_state()),
    # Toast notification store
    dcc.Store(id="toast-store", data=""),

    # Topbar (static — user identity doesn't change mid-session)
    render_topbar(USER_NAME),

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
        _load_slice(state, site, state["pl"])
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
        _load_slice(state, state["site"], new_pl)
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

    row_label = next(r["label"] for r in ROWS if r["id"] == row_id)
    values, zero_flags, comments = _db_payload(state, site, pl, row_id)
    try:
        db.save_draft(CURRENT_WEEK["week_id"], site, pl, USER_ID,
                      row_id, values, zero_flags, comments)
    except Exception as exc:
        return state, f"⚠ Save failed — {exc}"

    state["drafted"][site][pl][row_id] = True
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

    row_label = next(r["label"] for r in ROWS if r["id"] == row_id)
    values, zero_flags, comments = _db_payload(state, site, pl, row_id)
    try:
        db.submit_row(CURRENT_WEEK["week_id"], site, pl, USER_ID,
                      row_id, values, zero_flags, comments)
        db.delete_draft(CURRENT_WEEK["week_id"], site, pl, row_id, USER_ID)
    except Exception as exc:
        return state, f"⚠ Submit failed — {exc}"

    state["submitted"][site][pl][row_id] = True
    state["drafted"][site][pl][row_id]   = False
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

    values, zero_flags, comments = _db_payload(state, site, pl, "fri_frc")
    try:
        db.save_draft(CURRENT_WEEK["week_id"], site, pl, USER_ID,
                      "fri_frc", values, zero_flags, comments)
    except Exception as exc:
        return state, f"⚠ Save failed — {exc}"

    state["drafted"][site][pl]["fri_frc"] = True
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

    values, zero_flags, comments = _db_payload(state, site, pl, "fri_frc")
    try:
        db.submit_row(CURRENT_WEEK["week_id"], site, pl, USER_ID,
                      "fri_frc", values, zero_flags, comments)
        db.delete_draft(CURRENT_WEEK["week_id"], site, pl, "fri_frc", USER_ID)
    except Exception as exc:
        return state, f"⚠ Submit failed — {exc}"

    state["submitted"][site][pl]["fri_frc"] = True
    state["drafted"][site][pl]["fri_frc"]   = False
    state["fri_open"]        = False
    state["submit_attempted"] = False
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
    week = CURRENT_WEEK["week_id"]
    is_save = triggered == "btn-save-all"
    n, errors = 0, 0

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

        values, zero_flags, comments = _db_payload(state, site, pl, rid)
        try:
            if is_save:
                db.save_draft(week, site, pl, USER_ID, rid,
                              values, zero_flags, comments)
                state["drafted"][site][pl][rid] = True
            else:
                db.submit_row(week, site, pl, USER_ID, rid,
                              values, zero_flags, comments)
                db.delete_draft(week, site, pl, rid, USER_ID)
                state["submitted"][site][pl][rid] = True
                state["drafted"][site][pl][rid]   = False
            n += 1
        except Exception as exc:
            errors += 1
            print(f"[warn] bulk {triggered} {rid} failed: {exc}")

    verb = "saved as draft" if is_save else "submitted"
    if n and errors:
        msg = f"{n} row(s) {verb}, {errors} failed — check and retry"
    elif n:
        msg = f"{'⤓' if is_save else '✓'} {n} row(s) {verb}"
    elif errors:
        msg = f"⚠ All {errors} row(s) failed — check the connection"
    else:
        msg = "No open rows with data to save." if is_save else "No open rows with data."

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
