# app.py
# ─────────────────────────────────────────────────────────────────────────────
# Entry point for the Volumes Data Entry Tool Databricks App.
#
# Run locally:
#   pip install -r requirements.txt
#   python app.py            # http://localhost:8050
#
# On Databricks Apps the app is served by gunicorn (`app:server`, see app.yaml);
# DATABRICKS_HOST / OAuth creds / DATABRICKS_HTTP_PATH are provided by the
# platform. The signed-in user is read from the request headers (see
# _current_user); data access is resolved per user (see _can_edit).
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import json

import dash
from dash import Input, Output, State, ctx, dcc, html, ALL

from components.header import render_topbar, render_app_header
from components.data_table import render_data_table
from data import cache, db
from data.schema import (
    ROWS, COLS_BY_PL, na_matrix, SITES,
    cols_below_threshold, wip_ot_below_threshold,
)

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

# ── Identity & access ─────────────────────────────────────────────────────────

OWN_SITE    = "SEDICO"     # site shown on load
GLOBAL_SITE = "GLOBAL"     # pseudo-site: read-only sum of every plant
DEV_USER    = "lorenzo.muscillo@luxottica.com"  # fallback when no identity header

def _load_access() -> dict[str, set[str]]:
    """
    {email: {editable sites}} from the `app_access` DB table. The site '*'
    means every site (admin). Managed with plain SQL (no redeploy, never in
    git). Falls back to full access for the dev user if the table is empty or
    unreadable, so the app always keeps at least one admin.
    """
    try:
        access = cache.cached_access()
    except Exception as exc:
        print(f"[warn] could not load the access list: {exc}")
        access = {}
    return access or {DEV_USER: {"*"}}

# Refreshed from the DB by the bootstrap callback on every page load.
CURRENT_WEEK = {"week_id": 0, "year": 0}


def _current_user() -> str:
    """
    The signed-in user's email — from the Databricks Apps request headers
    (`X-Forwarded-Email`), or a dev fallback when running locally.
    """
    try:
        from flask import request, has_request_context
        if has_request_context():
            email = (request.headers.get("X-Forwarded-Email")
                     or request.headers.get("X-Forwarded-Preferred-Username")
                     or request.headers.get("X-Forwarded-User"))
            if email:
                return email.strip().lower()
    except Exception:
        pass
    return DEV_USER


def _display_name(email: str) -> str:
    """`mario.rossi@x.com` → `Mario Rossi`."""
    local = (email or "").split("@")[0]
    parts = [p for p in local.replace("_", ".").split(".") if p]
    return " ".join(p.capitalize() for p in parts) or "User"


def _can_edit(site: str, state: dict) -> bool:
    """Whether the current user may edit the given site."""
    if site == GLOBAL_SITE:
        return False
    if state.get("is_admin"):
        return True
    return site in state.get("sites", [])   # plant owners: only granted sites


def _load_current_week() -> dict:
    try:
        wk = cache.cached_current_week()
        return {"week_id": int(wk["week_id"]), "year": int(wk["year"])}
    except Exception as exc:  # DB unavailable — app still starts
        print(f"[warn] could not load current week from DB: {exc}")
        return {"week_id": 0, "year": 0}


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


def _load_slice(state: dict, site: str, pl: str) -> bool:
    """
    Populate state[...][site][pl] from the DB. Returns False if the submissions
    read fails (caller can warn the user); the slice is then left empty and not
    marked loaded, so it is retried the next time it is viewed.
    """
    key = f"{site}|{pl}"
    if key in state["loaded"]:
        return True
    week    = CURRENT_WEEK["week_id"]
    user    = state.get("user") or DEV_USER
    col_ids = {c["id"] for c in COLS_BY_PL[pl]}

    try:
        latest = cache.cached_submissions(week, site, pl)
    except Exception as exc:
        print(f"[warn] get_latest_submissions failed for {key}: {exc}")
        return False

    for _, r in latest.iterrows():
        rid, cid = r["submission_type"], r["channel"]
        if rid not in state["values"][site][pl] or cid not in col_ids:
            continue
        state["values"][site][pl][rid][cid] = _fmt(r["value_kpcs"])
        state["submitted"][site][pl][rid]   = True
        if _truthy(r["is_zero_flagged"]):
            state["zero_flags"][site][pl][rid][cid] = True
        if rid == "fri_frc":
            _apply_comment(state["fri_comments"][site][pl], cid, r)
        elif rid == "wip_ot":
            other = _cell_str(r.get("comment_other"))
            if other and cid in state["wip_ot_comments"][site][pl]:
                state["wip_ot_comments"][site][pl][cid]["others"] = other
            preset = _cell_str(r.get("comment_preset"))
            if preset and cid in state["wip_ot_comments"][site][pl]:
                state["wip_ot_comments"][site][pl][cid]["presets"] = [p for p in preset.split(",") if p]

    # Drafts are loaded for sites the user can edit.
    if _can_edit(site, state):
        try:
            drafts = cache.cached_drafts(week, site, pl, user)
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
                    _apply_comment(state["fri_comments"][site][pl], cid, r)
                elif rid == "wip_ot":
                    other = _cell_str(r.get("comment_other"))
                    if other and cid in state["wip_ot_comments"][site][pl]:
                        state["wip_ot_comments"][site][pl][cid]["others"] = other
                    preset = _cell_str(r.get("comment_preset"))
                    if preset and cid in state["wip_ot_comments"][site][pl]:
                        state["wip_ot_comments"][site][pl][cid]["presets"] = [p for p in preset.split(",") if p]

    state["loaded"].append(key)
    return True


def _load_global(state: dict, pl: str) -> bool:
    """
    Sum every plant's submitted data cell by cell for the GLOBAL view — one
    `get_gli_extract` query instead of a per-plant fan-out. Returns False on a
    DB read error.
    """
    if pl in state["global_loaded"]:
        return True
    col_ids = {c["id"] for c in COLS_BY_PL[pl]}
    try:
        ext = cache.cached_gli_extract(CURRENT_WEEK["week_id"])
    except Exception as exc:
        print(f"[warn] get_gli_extract failed: {exc}")
        return False

    sums: dict = {r["id"]: {} for r in ROWS}
    for _, r in ext.iterrows():
        if r["product_line"] != pl:
            continue
        rid, cid = r["submission_type"], r["channel"]
        if rid not in sums or cid not in col_ids:
            continue
        v = _to_float(r["value_kpcs"])
        if v is None:
            continue
        sums[rid][cid] = sums[rid].get(cid, 0.0) + v

    state["global"][pl] = {
        rid: {cid: _fmt(val) for cid, val in cells.items()}
        for rid, cells in sums.items()
    }
    state["global_loaded"].append(pl)
    return True


def _load_for_view(state: dict, site: str, pl: str) -> bool:
    """Load the DB data needed to render a site view. Returns False on error."""
    if site == GLOBAL_SITE:
        return _load_global(state, pl)
    return _load_slice(state, site, pl)


def _row_has_data(state: dict, site: str, pl: str, row_id: str) -> bool:
    """True if the row has at least one non-empty, non-N/A cell."""
    na_cols = na_matrix(site, pl).get(row_id, [])
    vals    = state["values"][site][pl][row_id]
    return any(vals.get(c["id"], "") for c in COLS_BY_PL[pl]
               if c["id"] not in na_cols)


def _db_payload(state: dict, site: str, pl: str, row_id: str):
    """Build (values, zero_flags, comments) for one row, ready for db.py."""
    na_cols = na_matrix(site, pl).get(row_id, [])
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
        for cid, fc in state["fri_comments"][site][pl].items():
            comments[cid] = {
                "presets": fc.get("presets", []),
                "others":  fc.get("others", ""),
            }
    elif row_id == "wip_ot":
        for cid in values:
            fc = state.get("wip_ot_comments", {}).get(site, {}).get(pl, {}).get(cid, {})
            comments[cid] = {
                "presets": fc.get("presets", []),
                "others":  fc.get("others", ""),
            }
    return values, zero_flags, comments


def _empty_state() -> dict:
    """
    Fresh client-side state for dcc.Store — an empty skeleton. The current
    week, the user identity and the initial slice are loaded by the bootstrap
    callback on page load (which runs inside a request context).
    """
    state: dict = {
        "site":           OWN_SITE,
        "pl":             "FRAMES",
        "fri_open":       False,
        "submit_attempted": False,
        "user":           "",      # signed-in email (set by bootstrap)
        "is_admin":       False,
        "sites":          [],      # sites the user may edit (set by bootstrap)
        "booted":         False,
        "values":         {},   # {site: {pl: {row_id: {col_id: value}}}}
        "submitted":      {},   # {site: {pl: {row_id: bool}}}
        "drafted":        {},   # {site: {pl: {row_id: bool}}}
        "zero_flags":     {},   # {site: {pl: {row_id: {col_id: bool}}}}
        "fri_comments":   {},   # {site: {pl: {col_id: {presets:[], others:""}}}}
        "global":         {"FRAMES": {}, "WEARABLES": {}},  # summed GLOBAL view
        "global_loaded":  [],   # ["FRAMES"/"WEARABLES"] product lines summed
        "loaded":         [],   # ["site|pl", ...] slices fetched from the DB
        "wip_ot_comments": {},  # {site: {pl: {col_id: {"presets":[], "others":""}}}}
        "wip_ot_open":    {},   # {site: {pl: bool}}
    }
    for s in SITES:
        state["values"][s]           = {}
        state["submitted"][s]        = {}
        state["drafted"][s]          = {}
        state["zero_flags"][s]       = {}
        state["fri_comments"][s]     = {}
        state["wip_ot_comments"][s]  = {}
        state["wip_ot_open"][s]      = {}
        for pl in ["FRAMES", "WEARABLES"]:
            cols = COLS_BY_PL[pl]
            state["values"][s][pl]          = {r["id"]: {c["id"]: "" for c in cols} for r in ROWS}
            state["submitted"][s][pl]       = {r["id"]: False for r in ROWS}
            state["drafted"][s][pl]         = {r["id"]: False for r in ROWS}
            state["zero_flags"][s][pl]      = {r["id"]: {c["id"]: False for c in cols} for r in ROWS}
            state["fri_comments"][s][pl]    = {c["id"]: {"presets": [], "others": ""} for c in cols}
            state["wip_ot_comments"][s][pl] = {c["id"]: {"presets": [], "others": ""} for c in cols}
            state["wip_ot_open"][s][pl]     = False

    return state


# ── Layout ────────────────────────────────────────────────────────────────────

app.layout = html.Div([
    # Client-side state store
    dcc.Store(id="app-state", data=_empty_state()),
    # Toast notification store
    dcc.Store(id="toast-store", data=""),
    # Fires once on page load → the bootstrap callback (runs in a request ctx)
    dcc.Interval(id="boot-trigger", interval=200, max_intervals=1),

    # Topbar + dynamic section — re-rendered on every state change
    html.Div(id="topbar-container"),
    html.Div(id="app-header-container"),
    html.Div(id="app-body-container"),

    # Toast notification (injected via clientside callback)
    html.Div(id="toast-container", className="toast-container"),
])


# ── Bootstrap ─────────────────────────────────────────────────────────────────
# Runs once per page load, inside a Flask request context: resolves the current
# week and the signed-in user, then loads the initial view.

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("boot-trigger", "n_intervals"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def bootstrap(_n, state: dict):
    if state.get("booted"):
        return dash.no_update, dash.no_update

    global CURRENT_WEEK
    CURRENT_WEEK = _load_current_week()

    user  = _current_user()
    sites = _load_access().get(user, set())
    state["user"]     = user
    state["is_admin"] = "*" in sites
    state["sites"]    = sorted(s for s in sites if s != "*")
    # Non-admins start on their own plant.
    if not state["is_admin"] and state["sites"]:
        state["site"] = state["sites"][0]

    ok = _load_for_view(state, state["site"], state["pl"])
    state["booted"] = True
    return state, ("" if ok else "⚠ Could not load data from the database.")


# ── Main render callback ──────────────────────────────────────────────────────

@app.callback(
    Output("topbar-container",     "children"),
    Output("app-header-container", "children"),
    Output("app-body-container",   "children"),
    Input("app-state", "data"),
)
def render_ui(state: dict):
    topbar = render_topbar(_display_name(state.get("user", "")))

    if not state.get("booted"):
        loading = html.Div(
            "Loading data…",
            style={"padding": "40px 24px", "color": "var(--text-3)",
                   "fontSize": "13px"},
        )
        return topbar, html.Div(), loading

    site     = state["site"]
    pl       = state["pl"]
    is_ro    = not _can_edit(site, state)
    fri_open = state["fri_open"]
    sa       = state["submit_attempted"]

    if site == GLOBAL_SITE:
        # Read-only summed view — no per-row draft/submit state.
        values    = state["global"].get(pl, {})
        submitted = {r["id"]: False for r in ROWS}
        drafted   = {r["id"]: False for r in ROWS}
        zf, fc, woc, woo = {}, {}, {}, False
    else:
        submitted = state["submitted"][site][pl]
        drafted   = state["drafted"][site][pl]
        values    = state["values"][site][pl]
        zf        = state["zero_flags"][site][pl]
        fc        = state["fri_comments"][site][pl]
        woc       = state.get("wip_ot_comments", {}).get(site, {}).get(pl, {})
        woo       = state.get("wip_ot_open", {}).get(site, {}).get(pl, False)

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
        wip_ot_comments=woc, wip_ot_open=woo,
    )
    return topbar, header, body


# ── Site selector callback ────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("site-select", "value"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def change_site(site: str, state: dict):
    if not site or site == state["site"]:
        return dash.no_update, dash.no_update
    state["site"]     = site
    state["fri_open"] = False
    state["submit_attempted"] = False
    ok = _load_for_view(state, site, state["pl"])
    return state, ("" if ok else f"⚠ Could not load data for {site}.")


# ── Product line tab callbacks ────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("tab-frames",    "n_clicks"),
    Input("tab-wearables", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def switch_pl(n_frames, n_wear, state: dict):
    triggered = ctx.triggered_id
    new_pl = "FRAMES" if triggered == "tab-frames" else "WEARABLES"
    if new_pl == state["pl"]:
        return dash.no_update, dash.no_update
    state["pl"]       = new_pl
    state["fri_open"] = False
    state["submit_attempted"] = False
    ok = _load_for_view(state, state["site"], new_pl)
    return state, ("" if ok else "⚠ Could not load data.")


# ── Row-level input callback ──────────────────────────────────────────────────

@app.callback(
    Output("app-state", "data", allow_duplicate=True),
    Input({"type": "row-input", "row": ALL, "col": ALL}, "value"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def update_row_values(values, state: dict):
    if not ctx.triggered or not _can_edit(state["site"], state):
        return dash.no_update
    site, pl = state["site"], state["pl"]
    for trigger in ctx.triggered:
        prop_id = trigger["prop_id"]
        # Parse pattern-matching id: {"type":"row-input","row":"mon_frc","col":"inbound"}.value
        id_dict = json.loads(prop_id.split(".")[0])
        row_id, col_id = id_dict["row"], id_dict["col"]
        val = trigger["value"]
        state["values"][site][pl][row_id][col_id] = str(val) if val is not None else ""
    return state


# ── Friday FRC input callback ─────────────────────────────────────────────────

@app.callback(
    Output("app-state", "data", allow_duplicate=True),
    Input({"type": "fri-input",      "col": ALL}, "value"),
    Input({"type": "fri-zero",       "col": ALL}, "value"),
    Input({"type": "fri-presets",    "col": ALL}, "value"),
    Input({"type": "fri-others",     "col": ALL}, "value"),
    Input({"type": "wip-ot-presets", "col": ALL}, "value"),
    Input({"type": "wip-ot-others",  "col": ALL}, "value"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def update_fri_values(fri_vals, fri_zeros, fri_presets, fri_others,
                      wip_presets, wip_others, state: dict):
    if not ctx.triggered or not _can_edit(state["site"], state):
        return dash.no_update
    site, pl = state["site"], state["pl"]

    for trigger in ctx.triggered:
        id_dict = json.loads(trigger["prop_id"].split(".")[0])
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
            state["fri_comments"][site][pl][col_id]["presets"] = val or []
        elif t == "fri-others":
            state["fri_comments"][site][pl][col_id]["others"] = val or ""
        elif t == "wip-ot-presets":
            state["wip_ot_comments"][site][pl][col_id]["presets"] = val or []
        elif t == "wip-ot-others":
            state["wip_ot_comments"][site][pl][col_id]["others"] = val or ""

    return state


# ── Friday panel toggle ───────────────────────────────────────────────────────

@app.callback(
    Output("app-state", "data", allow_duplicate=True),
    Input("btn-fri-toggle", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def toggle_fri_panel(n, state: dict):
    if not n:
        return dash.no_update
    state["fri_open"] = not state["fri_open"]
    return state


# ── WIP OT panel toggle ───────────────────────────────────────────────────────

@app.callback(
    Output("app-state", "data", allow_duplicate=True),
    Input("btn-wip-ot-toggle", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def toggle_wip_ot_panel(n, state: dict):
    if not n:
        return dash.no_update
    site, pl = state["site"], state["pl"]
    current = state.get("wip_ot_open", {}).get(site, {}).get(pl, False)
    state["wip_ot_open"][site][pl] = not current
    return state


# ── Save row ──────────────────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input({"type": "btn-save", "row": ALL}, "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def save_row(n_clicks_list, state: dict):
    triggered = ctx.triggered_id
    if not triggered or not any(n_clicks_list):
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    row_id = triggered["row"]
    if not _row_has_data(state, site, pl, row_id):
        return dash.no_update, "⚠ Enter at least one value before saving."

    row_label = next(r["label"] for r in ROWS if r["id"] == row_id)
    values, zero_flags, comments = _db_payload(state, site, pl, row_id)
    week = CURRENT_WEEK["week_id"]
    try:
        db.save_draft(week, site, pl, state["user"],
                      row_id, values, zero_flags, comments)
        cache.invalidate_drafts(week, site, pl, state["user"])
    except Exception as exc:
        return dash.no_update, f"⚠ Save failed — {exc}"

    state["drafted"][site][pl][row_id] = True
    return state, f"⤓ {row_label} — draft saved"


# ── Submit row ────────────────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input({"type": "btn-submit", "row": ALL}, "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def submit_row(n_clicks_list, state: dict):
    triggered = ctx.triggered_id
    if not triggered or not any(n_clicks_list):
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    row_id = triggered["row"]
    if not _row_has_data(state, site, pl, row_id):
        return dash.no_update, "⚠ Enter at least one value before submitting."

    # WIP OT: block submit if any column ≤ 90% and comment missing
    if row_id == "wip_ot":
        na_cols_wip = na_matrix(site, pl).get("wip_ot", [])
        below = wip_ot_below_threshold(state["values"][site][pl]["wip_ot"],
                                       na_cols_wip, COLS_BY_PL[pl])
        if below:
            woc = state.get("wip_ot_comments", {}).get(site, {}).get(pl, {})
            missing_cmt = [
                cid for cid in below
                if not (woc.get(cid, {}).get("presets") or
                        woc.get(cid, {}).get("others", "").strip())
            ]
            if missing_cmt:
                state["submit_attempted"] = True
                state["wip_ot_open"][site][pl] = True
                missing_labels = [c["label"] for c in COLS_BY_PL[pl] if c["id"] in missing_cmt]
                return state, f"⚠ Comment required (WIP OT ≤ 90%): {', '.join(missing_labels)}"

    row_label = next(r["label"] for r in ROWS if r["id"] == row_id)
    values, zero_flags, comments = _db_payload(state, site, pl, row_id)
    week = CURRENT_WEEK["week_id"]
    try:
        db.submit_row(week, site, pl, state["user"],
                      row_id, values, zero_flags, comments)
        db.delete_draft(week, site, pl, row_id, state["user"])
        cache.invalidate_submissions(week, site, pl)
        cache.invalidate_drafts(week, site, pl, state["user"])
    except Exception as exc:
        return dash.no_update, f"⚠ Submit failed — {exc}"

    state["submitted"][site][pl][row_id] = True
    state["drafted"][site][pl][row_id]   = False
    return state, f"✓ {row_label} submitted"


# ── Change submission (re-open) ───────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input({"type": "btn-change", "row": ALL}, "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def change_submission(n_clicks_list, state: dict):
    triggered = ctx.triggered_id
    if not triggered or not any(n_clicks_list):
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    state["submitted"][site][pl][triggered["row"]] = False
    return state, "Row re-opened for editing."


# ── Save Friday FRC ───────────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-fri-save",        "n_clicks"),
    Input("btn-fri-save-bottom", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def save_fri(n1, n2, state: dict):
    if not (n1 or n2):
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    if not _row_has_data(state, site, pl, "fri_frc"):
        return dash.no_update, "⚠ Enter at least one value before saving."

    values, zero_flags, comments = _db_payload(state, site, pl, "fri_frc")
    week = CURRENT_WEEK["week_id"]
    try:
        db.save_draft(week, site, pl, state["user"],
                      "fri_frc", values, zero_flags, comments)
        cache.invalidate_drafts(week, site, pl, state["user"])
    except Exception as exc:
        return dash.no_update, f"⚠ Save failed — {exc}"

    state["drafted"][site][pl]["fri_frc"] = True
    return state, "⤓ Friday FRC — draft saved"


# ── Submit Friday FRC ─────────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-fri-submit",        "n_clicks"),
    Input("btn-fri-submit-bottom", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def submit_fri(n1, n2, state: dict):
    if not (n1 or n2):
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    na_cols = na_matrix(site, pl).get("fri_frc", [])
    cols    = COLS_BY_PL[pl]

    if not _row_has_data(state, site, pl, "fri_frc"):
        state["submit_attempted"] = True
        return state, "⚠ Enter at least one value before submitting."

    # A Friday cell that dropped below threshold vs Monday needs a comment.
    vals      = state["values"][site][pl]["fri_frc"]
    mon_vals  = state["values"][site][pl].get("mon_frc", {})
    fc        = state["fri_comments"][site][pl]
    below_ids = cols_below_threshold(vals, mon_vals, na_cols, cols)
    missing = [
        cid for cid in below_ids
        if not (fc.get(cid, {}).get("presets") or fc.get(cid, {}).get("others", "").strip())
    ]
    if missing:
        state["submit_attempted"] = True
        missing_labels = [c["label"] for c in cols if c["id"] in missing]
        return state, f"⚠ Comment required: {', '.join(missing_labels)}"

    values, zero_flags, comments = _db_payload(state, site, pl, "fri_frc")
    week = CURRENT_WEEK["week_id"]
    try:
        db.submit_row(week, site, pl, state["user"],
                      "fri_frc", values, zero_flags, comments)
        db.delete_draft(week, site, pl, "fri_frc", state["user"])
        cache.invalidate_submissions(week, site, pl)
        cache.invalidate_drafts(week, site, pl, state["user"])
    except Exception as exc:
        return dash.no_update, f"⚠ Submit failed — {exc}"

    state["submitted"][site][pl]["fri_frc"] = True
    state["drafted"][site][pl]["fri_frc"]   = False
    state["fri_open"]         = False
    state["submit_attempted"] = False
    return state, "✓ Friday FRC submitted"


# ── Change Friday submission ──────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-fri-change", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def change_fri(n, state: dict):
    if not n:
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    state["submitted"][site][pl]["fri_frc"] = False
    state["fri_open"]         = True
    state["submit_attempted"] = False
    return state, "Friday FRC re-opened for editing."


# ── Save all / Submit all ─────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-save-all",   "n_clicks"),
    Input("btn-submit-all", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def bulk_action(n_save, n_submit, state: dict):
    triggered = ctx.triggered_id
    if not triggered:
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    week    = CURRENT_WEEK["week_id"]
    user    = state["user"]
    is_save = triggered == "btn-save-all"
    n, errors = 0, 0

    for row in ROWS:
        rid = row["id"]
        if row["is_fri"] or row["is_ref"]:
            continue
        if state["submitted"][site][pl][rid]:
            continue
        if not _row_has_data(state, site, pl, rid):
            continue

        # WIP OT: skip submit (not save) if comment missing for below-threshold cols
        if rid == "wip_ot" and not is_save:
            na_cols_wip = na_matrix(site, pl).get("wip_ot", [])
            below = wip_ot_below_threshold(state["values"][site][pl]["wip_ot"],
                                           na_cols_wip, COLS_BY_PL[pl])
            if below:
                woc = state.get("wip_ot_comments", {}).get(site, {}).get(pl, {})
                missing_cmt = [
                    cid for cid in below
                    if not (woc.get(cid, {}).get("presets") or
                            woc.get(cid, {}).get("others", "").strip())
                ]
                if missing_cmt:
                    state["wip_ot_open"][site][pl] = True
                    errors += 1
                    print(f"[warn] bulk submit wip_ot: comment required for {missing_cmt}")
                    continue

        values, zero_flags, comments = _db_payload(state, site, pl, rid)
        try:
            if is_save:
                db.save_draft(week, site, pl, user, rid,
                              values, zero_flags, comments)
                cache.invalidate_drafts(week, site, pl, user)
                state["drafted"][site][pl][rid] = True
            else:
                db.submit_row(week, site, pl, user, rid,
                              values, zero_flags, comments)
                db.delete_draft(week, site, pl, rid, user)
                cache.invalidate_submissions(week, site, pl)
                cache.invalidate_drafts(week, site, pl, user)
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
