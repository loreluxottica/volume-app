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
from dash import Input, Output, State, ctx, dcc, html, ALL, Patch

from components.header import render_topbar, render_app_header
from components.data_table import render_data_table
from data import cache, db
from data.schema import (
    ROWS, COLS_BY_PL, na_matrix, SITES,
    cols_below_threshold, wip_ot_below_threshold, incomplete_cells,
    zero_cells_missing_comment, _is_zero_value,
)

# ── App init ──────────────────────────────────────────────────────────────────
EXTERNAL_STYLESHEETS = [
    {
        "href": (
            "https://fonts.googleapis.com/css2?"
            "family=IBM+Plex+Mono:wght@400;500;600"
            "&family=DM+Sans:wght@300;400;500;600"
            "&display=swap"
        ),
        "rel": "stylesheet",
    }
]

app = dash.Dash(
    __name__,
    title="Volumes Data Entry Tool",
    external_stylesheets=EXTERNAL_STYLESHEETS,
    suppress_callback_exceptions=True
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


def current_week() -> dict:
    """
    Current open week as {week_id, year}.

    Resolved through cache.cached_current_week() so every gunicorn worker
    self-populates on first use — it must NOT depend on the bootstrap callback
    having run in this particular worker process (gunicorn runs >1 worker, and
    a module global is per-process). Returns {0, 0} if the DB is unreachable
    or has no open week.
    """
    try:
        wk = cache.cached_current_week()
        return {"week_id": int(wk["week_id"]), "year": int(wk["year"])}
    except Exception as exc:  # DB unavailable — app still starts
        print(f"[warn] could not load current week from DB: {exc}")
        return {"week_id": 0, "year": 0}


# ── DB ↔ state helpers ────────────────────────────────────────────────────────

def _to_float(s) -> float | None:
    """Form string → DB numeric (empty → None). Comma = decimal separator."""
    if isinstance(s, str):
        s = s.strip().replace(" ", "")
        if "," in s:                       # comma=decimal, dots are thousands
            s = s.replace(".", "").replace(",", ".")
        # only dots / digits → leave as-is so point-decimal still parses
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
    s = str(int(f)) if f == int(f) else str(f)
    return s.replace(".", ",")          # decimal comma for display (it-IT)


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
    week    = current_week()["week_id"]
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
        elif rid == "actual":
            _apply_comment(state["actual_comments"][site][pl], cid, r)
        elif rid == "thu_frc":
            _apply_comment(state["thu_comments"][site][pl], cid, r)

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
                elif rid == "actual":
                    _apply_comment(state["actual_comments"][site][pl], cid, r)
                elif rid == "thu_frc":
                    _apply_comment(state["thu_comments"][site][pl], cid, r)

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
        ext = cache.cached_gli_extract(current_week()["week_id"])
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
    elif row_id == "actual":
        for cid in values:
            fc = state.get("actual_comments", {}).get(site, {}).get(pl, {}).get(cid, {})
            comments[cid] = {
                "presets": fc.get("presets", []),
                "others":  fc.get("others", ""),
            }
    elif row_id == "thu_frc":
        for cid in values:
            fc = state.get("thu_comments", {}).get(site, {}).get(pl, {}).get(cid, {})
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
        "actual_comments": {},  # {site: {pl: {col_id: {"presets":[], "others":""}}}}
        "actual_open":    {},   # {site: {pl: bool}}
        "thu_comments":   {},   # {site: {pl: {col_id: {"presets":[], "others":""}}}}
        "thu_open":       {},   # {site: {pl: bool}}
    }
    for s in SITES:
        state["values"][s]           = {}
        state["submitted"][s]        = {}
        state["drafted"][s]          = {}
        state["zero_flags"][s]       = {}
        state["fri_comments"][s]     = {}
        state["wip_ot_comments"][s]  = {}
        state["wip_ot_open"][s]      = {}
        state["actual_comments"][s]  = {}
        state["actual_open"][s]      = {}
        state["thu_comments"][s]     = {}
        state["thu_open"][s]         = {}
        for pl in ["FRAMES", "WEARABLES"]:
            cols = COLS_BY_PL[pl]
            state["values"][s][pl]          = {r["id"]: {c["id"]: "" for c in cols} for r in ROWS}
            state["submitted"][s][pl]       = {r["id"]: False for r in ROWS}
            state["drafted"][s][pl]         = {r["id"]: False for r in ROWS}
            state["zero_flags"][s][pl]      = {r["id"]: {c["id"]: False for c in cols} for r in ROWS}
            state["fri_comments"][s][pl]    = {c["id"]: {"presets": [], "others": ""} for c in cols}
            state["wip_ot_comments"][s][pl] = {c["id"]: {"presets": [], "others": ""} for c in cols}
            state["wip_ot_open"][s][pl]     = False
            state["actual_comments"][s][pl] = {c["id"]: {"presets": [], "others": ""} for c in cols}
            state["actual_open"][s][pl]     = False
            state["thu_comments"][s][pl]    = {c["id"]: {"presets": [], "others": ""} for c in cols}
            state["thu_open"][s][pl]        = False

    return state


# ── Two-store split ───────────────────────────────────────────────────────────
# State is held in two dcc.Stores. `app-state` carries structural state and is
# the Input of render_ui — changing it triggers a full re-render. `form-values`
# carries the raw typed values and is only ever read as State, so a keystroke
# updates it WITHOUT re-rendering (no input churn, no lost characters).
# Callbacks merge the two into one dict, run the existing logic, then split the
# result back — so the DB/state helpers stay unchanged.

_FORM_KEYS = ("values", "fri_comments", "wip_ot_comments", "actual_comments", "thu_comments")


def _form_part(s: dict) -> dict:
    return {k: s[k] for k in _FORM_KEYS}


def _app_part(s: dict) -> dict:
    return {k: v for k, v in s.items() if k not in _FORM_KEYS}


def _merge(app_data: dict, form_data: dict) -> dict:
    return {**app_data, **form_data}


# ── Layout ────────────────────────────────────────────────────────────────────

_INIT_STATE = _empty_state()

app.layout = html.Div([
    # Structural state — Input of render_ui (changes trigger a re-render)
    dcc.Store(id="app-state", data=_app_part(_INIT_STATE)),
    # Typed values — read as State only, never triggers a re-render
    dcc.Store(id="form-values", data=_form_part(_INIT_STATE)),
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

    # CSV export sink
    dcc.Download(id="csv-download"),
])


# ── Bootstrap ─────────────────────────────────────────────────────────────────
# Runs once per page load, inside a Flask request context: resolves the current
# week and the signed-in user, then loads the initial view.

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("form-values", "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("boot-trigger", "n_intervals"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def bootstrap(_n, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    if state.get("booted"):
        return dash.no_update, dash.no_update, dash.no_update

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
    return (_app_part(state), _form_part(state),
            "" if ok else "⚠ Could not load data from the database.")


# ── Main render callback ──────────────────────────────────────────────────────

@app.callback(
    Output("topbar-container",     "children"),
    Output("app-header-container", "children"),
    Output("app-body-container",   "children"),
    Input("app-state", "data"),
    State("form-values", "data"),
)
def render_ui(app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
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
        ac, ao    = {}, False
        thc, tho  = {}, False
    else:
        submitted = state["submitted"][site][pl]
        drafted   = state["drafted"][site][pl]
        values    = state["values"][site][pl]
        zf        = state["zero_flags"][site][pl]
        fc        = state["fri_comments"][site][pl]
        woc       = state.get("wip_ot_comments", {}).get(site, {}).get(pl, {})
        woo       = state.get("wip_ot_open", {}).get(site, {}).get(pl, False)
        ac        = state.get("actual_comments", {}).get(site, {}).get(pl, {})
        ao        = state.get("actual_open", {}).get(site, {}).get(pl, False)
        thc       = state.get("thu_comments", {}).get(site, {}).get(pl, {})
        tho       = state.get("thu_open", {}).get(site, {}).get(pl, False)

    header = render_app_header(
        current_site=site, current_pl=pl,
        week_id=current_week()["week_id"], year=current_week()["year"],
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
        actual_comments=ac, actual_open=ao,
        thu_comments=thc, thu_open=tho,
    )
    return topbar, header, body


# ── Site selector callback ────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("form-values", "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("site-select", "value"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def change_site(site: str, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    if not site or site == state["site"]:
        return dash.no_update, dash.no_update, dash.no_update
    state["site"]     = site
    state["fri_open"] = False
    state["submit_attempted"] = False
    ok = _load_for_view(state, site, state["pl"])
    return (_app_part(state), _form_part(state),
            "" if ok else f"⚠ Could not load data for {site}.")


# ── Product line tab callbacks ────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("form-values", "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("tab-frames",    "n_clicks"),
    Input("tab-wearables", "n_clicks"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def switch_pl(n_frames, n_wear, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    triggered = ctx.triggered_id
    # Guard: a header re-render re-sets n_clicks=0 on the tab buttons, which
    # Dash can interpret as a prop change and fire this callback spuriously.
    # Only act on a real user click (the triggered tab's n_clicks must be > 0).
    if triggered == "tab-frames" and not n_frames:
        return dash.no_update, dash.no_update, dash.no_update
    if triggered == "tab-wearables" and not n_wear:
        return dash.no_update, dash.no_update, dash.no_update
    new_pl = "FRAMES" if triggered == "tab-frames" else "WEARABLES"
    if new_pl == state["pl"]:
        return dash.no_update, dash.no_update, dash.no_update
    state["pl"]       = new_pl
    state["fri_open"] = False
    state["submit_attempted"] = False
    ok = _load_for_view(state, state["site"], new_pl)
    return (_app_part(state), _form_part(state),
            "" if ok else "⚠ Could not load data.")


# ── Row-level input callback (CLIENTSIDE) ─────────────────────────────────────
# Same rationale as the panel callback below: writes the typed value into
# `form-values` synchronously in the browser, so a subsequent Save/Submit click
# always sees the latest values. Covers every standard row (mon_frc, thu_frc
# inline cells if any, py, siop, eow_wip) for both Frames and Wearables.

app.clientside_callback(
    """
    function(values, app_data, form_data) {
        if (!app_data || !form_data) return window.dash_clientside.no_update;
        var trig = window.dash_clientside.callback_context.triggered;
        if (!trig || trig.length === 0) return window.dash_clientside.no_update;
        var site = app_data.site, pl = app_data.pl;
        if (!site || !pl) return window.dash_clientside.no_update;
        var nf = JSON.parse(JSON.stringify(form_data));
        for (var i = 0; i < trig.length; i++) {
            var pid = trig[i].prop_id;
            var idStr = pid.substring(0, pid.lastIndexOf('.'));
            var idObj;
            try { idObj = JSON.parse(idStr); } catch (e) { continue; }
            var row_id = idObj.row, cid = idObj.col;
            var v = trig[i].value;
            var sv = (v === null || v === undefined) ? "" : String(v);
            try { nf.values[site][pl][row_id][cid] = sv; } catch (e) { /* skip */ }
        }
        return nf;
    }
    """,
    Output("form-values", "data", allow_duplicate=True),
    Input({"type": "row-input", "row": ALL, "col": ALL}, "value"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)


# ── Panel input callback (CLIENTSIDE) ─────────────────────────────────────────
# Runs in the browser, synchronously: a blur on an input updates `form-values`
# in-place BEFORE any subsequent click event (Save/Submit) is dispatched. This
# eliminates the residual race where the click captured stale form-values State
# because previous blurs' server round-trips had not yet returned. Covers all
# four panel rows (Friday FRC, WIP OT %, Actual, Thursday FRC), both Frames
# and Wearables — the (site, pl) dimensions are read from app-state.

app.clientside_callback(
    """
    function(fri_vals, fri_presets, fri_others, wip_inputs, wip_presets, wip_others, act_inputs, act_presets, act_others, thu_inputs, thu_presets, thu_others, app_data, form_data) {
        if (!app_data || !form_data) return window.dash_clientside.no_update;
        var trig = window.dash_clientside.callback_context.triggered;
        if (!trig || trig.length === 0) return window.dash_clientside.no_update;
        var site = app_data.site, pl = app_data.pl;
        if (!site || !pl) return window.dash_clientside.no_update;
        var nf = JSON.parse(JSON.stringify(form_data));
        for (var i = 0; i < trig.length; i++) {
            var pid = trig[i].prop_id;
            var idStr = pid.substring(0, pid.lastIndexOf('.'));
            var idObj;
            try { idObj = JSON.parse(idStr); } catch (e) { continue; }
            var t = idObj.type, cid = idObj.col;
            var v = trig[i].value;
            var sv = (v === null || v === undefined) ? "" : String(v);
            try {
                if (t === "fri-input")           nf.values[site][pl].fri_frc[cid] = sv;
                else if (t === "fri-presets")    nf.fri_comments[site][pl][cid].presets = v || [];
                else if (t === "fri-others")     nf.fri_comments[site][pl][cid].others = v || "";
                else if (t === "wip-ot-input")   nf.values[site][pl].wip_ot[cid] = sv;
                else if (t === "wip-ot-presets") nf.wip_ot_comments[site][pl][cid].presets = v || [];
                else if (t === "wip-ot-others")  nf.wip_ot_comments[site][pl][cid].others = v || "";
                else if (t === "actual-input")   nf.values[site][pl].actual[cid] = sv;
                else if (t === "actual-presets") nf.actual_comments[site][pl][cid].presets = v || [];
                else if (t === "actual-others")  nf.actual_comments[site][pl][cid].others = v || "";
                else if (t === "thu-input")      nf.values[site][pl].thu_frc[cid] = sv;
                else if (t === "thu-presets")    nf.thu_comments[site][pl][cid].presets = v || [];
                else if (t === "thu-others")     nf.thu_comments[site][pl][cid].others = v || "";
            } catch (e) { /* nested path missing — skip */ }
        }
        return nf;
    }
    """,
    Output("form-values", "data", allow_duplicate=True),
    Input({"type": "fri-input",      "col": ALL}, "value"),
    Input({"type": "fri-presets",    "col": ALL}, "value"),
    Input({"type": "fri-others",     "col": ALL}, "value"),
    Input({"type": "wip-ot-input",   "col": ALL}, "value"),
    Input({"type": "wip-ot-presets", "col": ALL}, "value"),
    Input({"type": "wip-ot-others",  "col": ALL}, "value"),
    Input({"type": "actual-input",   "col": ALL}, "value"),
    Input({"type": "actual-presets", "col": ALL}, "value"),
    Input({"type": "actual-others",  "col": ALL}, "value"),
    Input({"type": "thu-input",      "col": ALL}, "value"),
    Input({"type": "thu-presets",    "col": ALL}, "value"),
    Input({"type": "thu-others",     "col": ALL}, "value"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)


# ── Friday FRC zero-flag callback ─────────────────────────────────────────────
# Toggling "Confirm zero" disables the value input, so this DOES re-render.
# The change-detection guard stops the panel-open mount from re-rendering.

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("form-values", "data", allow_duplicate=True),
    Input({"type": "fri-zero", "col": ALL}, "value"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def update_fri_zero(fri_zeros, app_data: dict, form_data: dict):
    if not ctx.triggered or not _can_edit(app_data["site"], app_data):
        return dash.no_update, dash.no_update
    site, pl = app_data["site"], app_data["pl"]
    app_patch  = Patch()
    form_patch = Patch()
    app_changed  = False
    form_changed = False

    for trigger in ctx.triggered:
        id_dict = json.loads(trigger["prop_id"].split(".")[0])
        if id_dict["type"] != "fri-zero":
            continue
        col_id  = id_dict["col"]
        is_zero = bool(trigger["value"])
        current = (app_data.get("zero_flags", {}).get(site, {}).get(pl, {})
                   .get("fri_frc", {}).get(col_id, False))
        if current != is_zero:
            app_patch["zero_flags"][site][pl]["fri_frc"][col_id] = is_zero
            app_changed = True
            if is_zero:
                form_patch["values"][site][pl]["fri_frc"][col_id] = "0"
                form_changed = True

    if not app_changed:
        return dash.no_update, dash.no_update
    return app_patch, form_patch if form_changed else dash.no_update


# ── WIP OT zero-flag callback ─────────────────────────────────────────────────
# Mirror of update_fri_zero — toggling "Confirm zero" disables the value input.

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("form-values", "data", allow_duplicate=True),
    Input({"type": "wip-ot-zero", "col": ALL}, "value"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def update_wip_ot_zero(wip_zeros, app_data: dict, form_data: dict):
    if not ctx.triggered or not _can_edit(app_data["site"], app_data):
        return dash.no_update, dash.no_update
    site, pl = app_data["site"], app_data["pl"]
    app_patch  = Patch()
    form_patch = Patch()
    app_changed  = False
    form_changed = False

    for trigger in ctx.triggered:
        id_dict = json.loads(trigger["prop_id"].split(".")[0])
        if id_dict["type"] != "wip-ot-zero":
            continue
        col_id  = id_dict["col"]
        is_zero = bool(trigger["value"])
        current = (app_data.get("zero_flags", {}).get(site, {}).get(pl, {})
                   .get("wip_ot", {}).get(col_id, False))
        if current != is_zero:
            app_patch["zero_flags"][site][pl]["wip_ot"][col_id] = is_zero
            app_changed = True
            if is_zero:
                form_patch["values"][site][pl]["wip_ot"][col_id] = "0"
                form_changed = True

    if not app_changed:
        return dash.no_update, dash.no_update
    return app_patch, form_patch if form_changed else dash.no_update


# ── Actual zero-flag callback ─────────────────────────────────────────────────
# Mirror of update_fri_zero — toggling "Confirm zero" disables the value input.

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("form-values", "data", allow_duplicate=True),
    Input({"type": "actual-zero", "col": ALL}, "value"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def update_actual_zero(actual_zeros, app_data: dict, form_data: dict):
    if not ctx.triggered or not _can_edit(app_data["site"], app_data):
        return dash.no_update, dash.no_update
    site, pl = app_data["site"], app_data["pl"]
    app_patch  = Patch()
    form_patch = Patch()
    app_changed  = False
    form_changed = False

    for trigger in ctx.triggered:
        id_dict = json.loads(trigger["prop_id"].split(".")[0])
        if id_dict["type"] != "actual-zero":
            continue
        col_id  = id_dict["col"]
        is_zero = bool(trigger["value"])
        current = (app_data.get("zero_flags", {}).get(site, {}).get(pl, {})
                   .get("actual", {}).get(col_id, False))
        if current != is_zero:
            app_patch["zero_flags"][site][pl]["actual"][col_id] = is_zero
            app_changed = True
            if is_zero:
                form_patch["values"][site][pl]["actual"][col_id] = "0"
                form_changed = True

    if not app_changed:
        return dash.no_update, dash.no_update
    return app_patch, form_patch if form_changed else dash.no_update


# ── Thursday FRC zero-flag callback ───────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("form-values", "data", allow_duplicate=True),
    Input({"type": "thu-zero", "col": ALL}, "value"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def update_thu_zero(thu_zeros, app_data: dict, form_data: dict):
    if not ctx.triggered or not _can_edit(app_data["site"], app_data):
        return dash.no_update, dash.no_update
    site, pl = app_data["site"], app_data["pl"]
    app_patch  = Patch()
    form_patch = Patch()
    app_changed  = False
    form_changed = False

    for trigger in ctx.triggered:
        id_dict = json.loads(trigger["prop_id"].split(".")[0])
        if id_dict["type"] != "thu-zero":
            continue
        col_id  = id_dict["col"]
        is_zero = bool(trigger["value"])
        current = (app_data.get("zero_flags", {}).get(site, {}).get(pl, {})
                   .get("thu_frc", {}).get(col_id, False))
        if current != is_zero:
            app_patch["zero_flags"][site][pl]["thu_frc"][col_id] = is_zero
            app_changed = True
            if is_zero:
                form_patch["values"][site][pl]["thu_frc"][col_id] = "0"
                form_changed = True

    if not app_changed:
        return dash.no_update, dash.no_update
    return app_patch, form_patch if form_changed else dash.no_update


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


# ── Actual panel toggle ───────────────────────────────────────────────────────

@app.callback(
    Output("app-state", "data", allow_duplicate=True),
    Input("btn-actual-toggle", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def toggle_actual_panel(n, state: dict):
    if not n:
        return dash.no_update
    site, pl = state["site"], state["pl"]
    current = state.get("actual_open", {}).get(site, {}).get(pl, False)
    state["actual_open"][site][pl] = not current
    return state


# ── Thursday FRC panel toggle ─────────────────────────────────────────────────

@app.callback(
    Output("app-state", "data", allow_duplicate=True),
    Input("btn-thu-toggle", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def toggle_thu_panel(n, state: dict):
    if not n:
        return dash.no_update
    site, pl = state["site"], state["pl"]
    current = state.get("thu_open", {}).get(site, {}).get(pl, False)
    state["thu_open"][site][pl] = not current
    return state


# ── Save row ──────────────────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input({"type": "btn-save", "row": ALL}, "n_clicks"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def save_row(n_clicks_list, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
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
    week = current_week()["week_id"]
    try:
        db.save_draft(week, site, pl, state["user"],
                      row_id, values, zero_flags, comments)
        cache.invalidate_drafts(week, site, pl, state["user"])
    except Exception as exc:
        return dash.no_update, f"⚠ Save failed — {exc}"

    state["drafted"][site][pl][row_id] = True
    return _app_part(state), f"⤓ {row_label} — draft saved"


# ── Submit row ────────────────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input({"type": "btn-submit", "row": ALL}, "n_clicks"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def submit_row(n_clicks_list, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    triggered = ctx.triggered_id
    if not triggered or not any(n_clicks_list):
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    row_id = triggered["row"]
    if not _row_has_data(state, site, pl, row_id):
        return dash.no_update, "⚠ Enter at least one value before submitting."

    # Every applicable cell must have a value or be zero-flagged (BBP §6.4).
    na_cols = na_matrix(site, pl).get(row_id, [])
    cols    = COLS_BY_PL[pl]
    blank = incomplete_cells(state["values"][site][pl][row_id],
                             state["zero_flags"][site][pl][row_id], na_cols, cols)
    if blank:
        state["submit_attempted"] = True
        blank_labels = [c["label"] for c in cols if c["id"] in blank]
        return _app_part(state), f"⚠ Fill or confirm zero for all cells: {', '.join(blank_labels)}"

    row_label = next(r["label"] for r in ROWS if r["id"] == row_id)
    values, zero_flags, comments = _db_payload(state, site, pl, row_id)
    week = current_week()["week_id"]
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
    return _app_part(state), f"✓ {row_label} submitted"


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
    State("form-values", "data"),
    prevent_initial_call=True,
)
def save_fri(n1, n2, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    if not (n1 or n2):
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    if not _row_has_data(state, site, pl, "fri_frc"):
        return dash.no_update, "⚠ Enter at least one value before saving."

    values, zero_flags, comments = _db_payload(state, site, pl, "fri_frc")
    week = current_week()["week_id"]
    try:
        db.save_draft(week, site, pl, state["user"],
                      "fri_frc", values, zero_flags, comments)
        cache.invalidate_drafts(week, site, pl, state["user"])
    except Exception as exc:
        return dash.no_update, f"⚠ Save failed — {exc}"

    state["drafted"][site][pl]["fri_frc"] = True
    return _app_part(state), "⤓ Friday FRC — draft saved"


# ── Submit Friday FRC ─────────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-fri-submit",        "n_clicks"),
    Input("btn-fri-submit-bottom", "n_clicks"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def submit_fri(n1, n2, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    if not (n1 or n2):
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    na_cols = na_matrix(site, pl).get("fri_frc", [])
    cols    = COLS_BY_PL[pl]

    if not _row_has_data(state, site, pl, "fri_frc"):
        state["submit_attempted"] = True
        return _app_part(state), "⚠ Enter at least one value before submitting."

    vals      = state["values"][site][pl]["fri_frc"]
    mon_vals  = state["values"][site][pl].get("mon_frc", {})
    fc        = state["fri_comments"][site][pl]

    # Every applicable cell must have a value or be zero-flagged (BBP §6.4).
    blank = incomplete_cells(vals, state["zero_flags"][site][pl]["fri_frc"], na_cols, cols)
    if blank:
        state["submit_attempted"] = True
        blank_labels = [c["label"] for c in cols if c["id"] in blank]
        return _app_part(state), f"⚠ Fill or confirm zero for all cells: {', '.join(blank_labels)}"

    # A Friday cell that dropped below threshold vs Monday needs a comment.
    # A zero-confirmed cell also needs a justification comment.
    zf_row = state["zero_flags"][site][pl]["fri_frc"]
    below_ids = cols_below_threshold(vals, mon_vals, na_cols, cols)
    zero_missing = zero_cells_missing_comment(vals, zf_row, fc, na_cols, cols, flag_is_sufficient=True)
    missing_below = [
        cid for cid in below_ids
        if not (fc.get(cid, {}).get("presets") or fc.get(cid, {}).get("others", "").strip())
    ]
    missing = list(dict.fromkeys(missing_below + zero_missing))
    if missing:
        state["submit_attempted"] = True
        missing_labels = [c["label"] for c in cols if c["id"] in missing]
        return _app_part(state), f"⚠ Comment required: {', '.join(missing_labels)}"

    # Drop stale comments for cells no longer below threshold and not zero (flag or typed 0).
    keep = (set(below_ids)
            | {cid for cid in zf_row if zf_row.get(cid)}
            | {c["id"] for c in cols if _is_zero_value(vals.get(c["id"]))})
    state["fri_comments"][site][pl] = {
        cid: fc_entry for cid, fc_entry in state["fri_comments"][site][pl].items()
        if cid in keep
    }

    values, zero_flags, comments = _db_payload(state, site, pl, "fri_frc")
    week = current_week()["week_id"]
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
    return _app_part(state), "✓ Friday FRC submitted"


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


# ── Save WIP OT % ─────────────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-wip-ot-save",        "n_clicks"),
    Input("btn-wip-ot-save-bottom", "n_clicks"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def save_wip_ot(n1, n2, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    if not (n1 or n2):
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    if not _row_has_data(state, site, pl, "wip_ot"):
        return dash.no_update, "⚠ Enter at least one value before saving."

    values, zero_flags, comments = _db_payload(state, site, pl, "wip_ot")
    week = current_week()["week_id"]
    try:
        db.save_draft(week, site, pl, state["user"],
                      "wip_ot", values, zero_flags, comments)
        cache.invalidate_drafts(week, site, pl, state["user"])
    except Exception as exc:
        return dash.no_update, f"⚠ Save failed — {exc}"

    state["drafted"][site][pl]["wip_ot"] = True
    return _app_part(state), "⤓ WIP OT % — draft saved"


# ── Submit WIP OT % ───────────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-wip-ot-submit",        "n_clicks"),
    Input("btn-wip-ot-submit-bottom", "n_clicks"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def submit_wip_ot(n1, n2, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    if not (n1 or n2):
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    na_cols = na_matrix(site, pl).get("wip_ot", [])
    cols    = COLS_BY_PL[pl]

    if not _row_has_data(state, site, pl, "wip_ot"):
        state["submit_attempted"] = True
        return _app_part(state), "⚠ Enter at least one value before submitting."

    vals      = state["values"][site][pl]["wip_ot"]
    woc       = state["wip_ot_comments"][site][pl]

    # Every applicable cell must have a value or be zero-flagged (BBP §6.4).
    blank = incomplete_cells(vals, state["zero_flags"][site][pl]["wip_ot"], na_cols, cols)
    if blank:
        state["submit_attempted"] = True
        blank_labels = [c["label"] for c in cols if c["id"] in blank]
        return _app_part(state), f"⚠ Fill or confirm zero for all cells: {', '.join(blank_labels)}"

    # Any column at or below 90% needs a comment.
    # A zero-confirmed cell also needs a justification comment.
    zf_row = state["zero_flags"][site][pl]["wip_ot"]
    below_ids = wip_ot_below_threshold(vals, na_cols, cols)
    zero_missing = zero_cells_missing_comment(vals, zf_row, woc, na_cols, cols)
    missing_below = [
        cid for cid in below_ids
        if not (woc.get(cid, {}).get("presets") or woc.get(cid, {}).get("others", "").strip())
    ]
    missing = list(dict.fromkeys(missing_below + zero_missing))
    if missing:
        state["submit_attempted"] = True
        missing_labels = [c["label"] for c in cols if c["id"] in missing]
        return _app_part(state), f"⚠ Comment required: {', '.join(missing_labels)}"

    # Drop stale comments for cells no longer below threshold and not zero (flag or typed 0).
    keep = (set(below_ids)
            | {cid for cid in zf_row if zf_row.get(cid)}
            | {c["id"] for c in cols if _is_zero_value(vals.get(c["id"]))})
    state["wip_ot_comments"][site][pl] = {
        cid: fc_entry for cid, fc_entry in state["wip_ot_comments"][site][pl].items()
        if cid in keep
    }

    values, zero_flags, comments = _db_payload(state, site, pl, "wip_ot")
    week = current_week()["week_id"]
    try:
        db.submit_row(week, site, pl, state["user"],
                      "wip_ot", values, zero_flags, comments)
        db.delete_draft(week, site, pl, "wip_ot", state["user"])
        cache.invalidate_submissions(week, site, pl)
        cache.invalidate_drafts(week, site, pl, state["user"])
    except Exception as exc:
        return dash.no_update, f"⚠ Submit failed — {exc}"

    state["submitted"][site][pl]["wip_ot"] = True
    state["drafted"][site][pl]["wip_ot"]   = False
    state["wip_ot_open"][site][pl]         = False
    state["submit_attempted"]              = False
    return _app_part(state), "✓ WIP OT % submitted"


# ── Change WIP OT % submission ────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-wip-ot-change", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def change_wip_ot(n, state: dict):
    if not n:
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    state["submitted"][site][pl]["wip_ot"] = False
    state["wip_ot_open"][site][pl]         = True
    state["submit_attempted"]              = False
    return state, "WIP OT % re-opened for editing."


# ── Save Actual ───────────────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-actual-save",        "n_clicks"),
    Input("btn-actual-save-bottom", "n_clicks"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def save_actual(n1, n2, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    if not (n1 or n2):
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    if not _row_has_data(state, site, pl, "actual"):
        return dash.no_update, "⚠ Enter at least one value before saving."

    values, zero_flags, comments = _db_payload(state, site, pl, "actual")
    week = current_week()["week_id"]
    try:
        db.save_draft(week, site, pl, state["user"],
                      "actual", values, zero_flags, comments)
        cache.invalidate_drafts(week, site, pl, state["user"])
    except Exception as exc:
        return dash.no_update, f"⚠ Save failed — {exc}"

    state["drafted"][site][pl]["actual"] = True
    return _app_part(state), "⤓ Actual — draft saved"


# ── Submit Actual ─────────────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-actual-submit",        "n_clicks"),
    Input("btn-actual-submit-bottom", "n_clicks"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def submit_actual(n1, n2, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    if not (n1 or n2):
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    na_cols = na_matrix(site, pl).get("actual", [])
    cols    = COLS_BY_PL[pl]

    if not _row_has_data(state, site, pl, "actual"):
        state["submit_attempted"] = True
        return _app_part(state), "⚠ Enter at least one value before submitting."

    vals      = state["values"][site][pl]["actual"]
    mon_vals  = state["values"][site][pl].get("mon_frc", {})
    ac        = state["actual_comments"][site][pl]

    # Every applicable cell must have a value or be zero-flagged (BBP §6.4).
    blank = incomplete_cells(vals, state["zero_flags"][site][pl]["actual"], na_cols, cols)
    if blank:
        state["submit_attempted"] = True
        blank_labels = [c["label"] for c in cols if c["id"] in blank]
        return _app_part(state), f"⚠ Fill or confirm zero for all cells: {', '.join(blank_labels)}"

    # An Actual cell that dropped below threshold vs Monday needs a comment.
    # A zero-confirmed cell also needs a justification comment.
    zf_row = state["zero_flags"][site][pl]["actual"]
    below_ids = cols_below_threshold(vals, mon_vals, na_cols, cols)
    zero_missing = zero_cells_missing_comment(vals, zf_row, ac, na_cols, cols, flag_is_sufficient=True)
    missing_below = [
        cid for cid in below_ids
        if not (ac.get(cid, {}).get("presets") or ac.get(cid, {}).get("others", "").strip())
    ]
    missing = list(dict.fromkeys(missing_below + zero_missing))
    if missing:
        state["submit_attempted"] = True
        missing_labels = [c["label"] for c in cols if c["id"] in missing]
        return _app_part(state), f"⚠ Comment required: {', '.join(missing_labels)}"

    # Drop stale comments for cells no longer below threshold and not zero (flag or typed 0).
    keep = (set(below_ids)
            | {cid for cid in zf_row if zf_row.get(cid)}
            | {c["id"] for c in cols if _is_zero_value(vals.get(c["id"]))})
    state["actual_comments"][site][pl] = {
        cid: fc_entry for cid, fc_entry in state["actual_comments"][site][pl].items()
        if cid in keep
    }

    values, zero_flags, comments = _db_payload(state, site, pl, "actual")
    week = current_week()["week_id"]
    try:
        db.submit_row(week, site, pl, state["user"],
                      "actual", values, zero_flags, comments)
        db.delete_draft(week, site, pl, "actual", state["user"])
        cache.invalidate_submissions(week, site, pl)
        cache.invalidate_drafts(week, site, pl, state["user"])
    except Exception as exc:
        return dash.no_update, f"⚠ Submit failed — {exc}"

    state["submitted"][site][pl]["actual"] = True
    state["drafted"][site][pl]["actual"]   = False
    state["actual_open"][site][pl]         = False
    state["submit_attempted"]              = False
    return _app_part(state), "✓ Actual submitted"


# ── Change Actual submission ──────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-actual-change", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def change_actual(n, state: dict):
    if not n:
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    state["submitted"][site][pl]["actual"] = False
    state["actual_open"][site][pl]         = True
    state["submit_attempted"]              = False
    return state, "Actual re-opened for editing."


# ── Save Thursday FRC ─────────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-thu-save",        "n_clicks"),
    Input("btn-thu-save-bottom", "n_clicks"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def save_thu(n1, n2, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    if not (n1 or n2):
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    if not _row_has_data(state, site, pl, "thu_frc"):
        return dash.no_update, "⚠ Enter at least one value before saving."

    values, zero_flags, comments = _db_payload(state, site, pl, "thu_frc")
    week = current_week()["week_id"]
    try:
        db.save_draft(week, site, pl, state["user"],
                      "thu_frc", values, zero_flags, comments)
        cache.invalidate_drafts(week, site, pl, state["user"])
    except Exception as exc:
        return dash.no_update, f"⚠ Save failed — {exc}"

    state["drafted"][site][pl]["thu_frc"] = True
    return _app_part(state), "⤓ Thursday FRC — draft saved"


# ── Submit Thursday FRC ───────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-thu-submit",        "n_clicks"),
    Input("btn-thu-submit-bottom", "n_clicks"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def submit_thu(n1, n2, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    if not (n1 or n2):
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    na_cols = na_matrix(site, pl).get("thu_frc", [])
    cols    = COLS_BY_PL[pl]

    if not _row_has_data(state, site, pl, "thu_frc"):
        state["submit_attempted"] = True
        return _app_part(state), "⚠ Enter at least one value before submitting."

    vals      = state["values"][site][pl]["thu_frc"]
    mon_vals  = state["values"][site][pl].get("mon_frc", {})
    tc        = state["thu_comments"][site][pl]

    # Every applicable cell must have a value or be zero-flagged (BBP §6.4).
    blank = incomplete_cells(vals, state["zero_flags"][site][pl]["thu_frc"], na_cols, cols)
    if blank:
        state["submit_attempted"] = True
        blank_labels = [c["label"] for c in cols if c["id"] in blank]
        return _app_part(state), f"⚠ Fill or confirm zero for all cells: {', '.join(blank_labels)}"

    # A Thursday cell that dropped below threshold vs Monday needs a comment.
    # A zero-confirmed cell also needs a justification comment.
    zf_row = state["zero_flags"][site][pl]["thu_frc"]
    below_ids = cols_below_threshold(vals, mon_vals, na_cols, cols)
    zero_missing = zero_cells_missing_comment(vals, zf_row, tc, na_cols, cols, flag_is_sufficient=True)
    missing_below = [
        cid for cid in below_ids
        if not (tc.get(cid, {}).get("presets") or tc.get(cid, {}).get("others", "").strip())
    ]
    missing = list(dict.fromkeys(missing_below + zero_missing))
    if missing:
        state["submit_attempted"] = True
        missing_labels = [c["label"] for c in cols if c["id"] in missing]
        return _app_part(state), f"⚠ Comment required: {', '.join(missing_labels)}"

    # Drop stale comments for cells no longer below threshold and not zero (flag or typed 0).
    keep = (set(below_ids)
            | {cid for cid in zf_row if zf_row.get(cid)}
            | {c["id"] for c in cols if _is_zero_value(vals.get(c["id"]))})
    state["thu_comments"][site][pl] = {
        cid: fc_entry for cid, fc_entry in state["thu_comments"][site][pl].items()
        if cid in keep
    }

    values, zero_flags, comments = _db_payload(state, site, pl, "thu_frc")
    week = current_week()["week_id"]
    try:
        db.submit_row(week, site, pl, state["user"],
                      "thu_frc", values, zero_flags, comments)
        db.delete_draft(week, site, pl, "thu_frc", state["user"])
        cache.invalidate_submissions(week, site, pl)
        cache.invalidate_drafts(week, site, pl, state["user"])
    except Exception as exc:
        return dash.no_update, f"⚠ Submit failed — {exc}"

    state["submitted"][site][pl]["thu_frc"] = True
    state["drafted"][site][pl]["thu_frc"]   = False
    state["thu_open"][site][pl]             = False
    state["submit_attempted"]               = False
    return _app_part(state), "✓ Thursday FRC submitted"


# ── Change Thursday FRC submission ────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-thu-change", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def change_thu(n, state: dict):
    if not n:
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    state["submitted"][site][pl]["thu_frc"] = False
    state["thu_open"][site][pl]             = True
    state["submit_attempted"]               = False
    return state, "Thursday FRC re-opened for editing."


# ── Undo a confirmed zero (standard-row cell) ─────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("form-values", "data", allow_duplicate=True),
    Input({"type": "zero-revert", "row": ALL, "col": ALL}, "n_clicks"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def revert_zero(n_list, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    triggered = ctx.triggered_id
    if not triggered or not any(n_list):
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, dash.no_update

    rid, cid = triggered["row"], triggered["col"]
    state["zero_flags"][site][pl][rid][cid] = False
    state["values"][site][pl][rid][cid]     = ""
    return _app_part(state), _form_part(state)


# ── Save all / Submit all ─────────────────────────────────────────────────────

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-save-all",   "n_clicks"),
    Input("btn-submit-all", "n_clicks"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def bulk_action(n_save, n_submit, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    triggered = ctx.triggered_id
    if not triggered:
        return dash.no_update, dash.no_update

    site, pl = state["site"], state["pl"]
    if not _can_edit(site, state):
        return dash.no_update, f"⚠ No permission to edit {site}."

    week    = current_week()["week_id"]
    user    = state["user"]
    is_save = triggered == "btn-save-all"
    n, errors = 0, 0

    for row in ROWS:
        rid = row["id"]
        # Panel rows (Friday FRC, Thursday FRC, WIP OT %, Actual) have their own
        # panel save/submit buttons.
        if row["is_fri"] or row["is_ref"] or rid in ("wip_ot", "actual", "thu_frc"):
            continue
        if state["submitted"][site][pl][rid]:
            continue
        if not _row_has_data(state, site, pl, rid):
            continue

        # Submit (not Save): skip rows with blank non-N/A cells (BBP §6.4).
        if not is_save:
            na_cols = na_matrix(site, pl).get(rid, [])
            if incomplete_cells(state["values"][site][pl][rid],
                                state["zero_flags"][site][pl][rid],
                                na_cols, COLS_BY_PL[pl]):
                errors += 1
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

    return _app_part(state), msg


# ── CSV export ────────────────────────────────────────────────────────────────
# Downloads the current week's gli_extract, filtered to the selected product
# line (and site, unless GLOBAL is selected). BBP §6.9.

@app.callback(
    Output("csv-download", "data"),
    Output("toast-store",  "data", allow_duplicate=True),
    Input("btn-export-csv", "n_clicks"),
    State("app-state", "data"),
    prevent_initial_call=True,
)
def export_csv(n, state: dict):
    if not n:
        return dash.no_update, dash.no_update

    week = current_week()["week_id"]
    try:
        df = cache.cached_gli_extract(week)
    except Exception as exc:
        return dash.no_update, f"⚠ Export failed — {exc}"

    site, pl = state["site"], state["pl"]
    df = df[df["product_line"] == pl]
    if site != GLOBAL_SITE:
        df = df[df["site"] == site]
    if df.empty:
        return dash.no_update, "No data to export for this week."

    fname = f"volumes_wk{week}_{site}_{pl}.csv"
    return dcc.send_data_frame(df.to_csv, fname, index=False), f"⤓ Exported {fname}"


# ── Double Tap — refresh server cache ─────────────────────────────────────────
# Server cache (data/cache.py) is per-process; gunicorn runs 2 workers, so each
# click only clears the worker that serves the request. Tap twice for full effect.

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("form-values", "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-refresh-cache", "n_clicks"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def refresh_cache(n, app_data: dict, form_data: dict):
    if not n:
        return dash.no_update, dash.no_update, dash.no_update
    state = _merge(app_data, form_data)
    cache.invalidate_all()
    state["loaded"] = []
    state["global_loaded"] = []
    ok = _load_for_view(state, state["site"], state["pl"])
    msg = "🥤 Cache refreshed" if ok else "⚠ Refresh failed — check DB connection"
    return _app_part(state), _form_part(state), msg


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


# ── Friday FRC comment visibility (clientside) ────────────────────────────────
# Runs entirely in-browser on every fri-input keystroke — no server round-trip.
# Shows/hides each comment section immediately as the user types.

app.clientside_callback(
    """
    function(fri_values, ids, app_data, form_data) {
        if (!app_data || !form_data || !Array.isArray(fri_values))
            return window.dash_clientside.no_update;
        var site = app_data.site, pl = app_data.pl;
        var sliceVals = ((form_data.values || {})[site] || {})[pl] || {};
        var mon_frc = sliceVals['mon_frc'] || {};
        var THRESHOLD_ABS = 10, THRESHOLD_REL = 0.10;
        return ids.map(function(id_obj, i) {
            var cid = id_obj.col;
            var fri = parseFloat(String(fri_values[i] || '').replace(',', '.'));
            if (fri === 0) return {};
            var mon = parseFloat(String(mon_frc[cid] || '').replace(',', '.'));
            if (isNaN(fri) || isNaN(mon) || mon <= 0) return {"display": "none"};
            var diff = mon - fri;
            var below = diff >= THRESHOLD_ABS || diff / mon >= THRESHOLD_REL;
            return below ? {} : {"display": "none"};
        });
    }
    """,
    Output({"type": "fri-comment-section", "col": ALL}, "style"),
    Input({"type": "fri-input", "col": ALL}, "value"),
    State({"type": "fri-input", "col": ALL}, "id"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)


# ── WIP OT comment visibility (clientside) ────────────────────────────────────
# Mirror of the Friday FRC callback: shows/hides each comment section in-browser
# as the user types, with the flat WIP OT threshold (value ≤ 90%).

app.clientside_callback(
    """
    function(wip_values, ids) {
        if (!Array.isArray(wip_values))
            return window.dash_clientside.no_update;
        var THRESHOLD = 90;
        return ids.map(function(id_obj, i) {
            var v = parseFloat(String(wip_values[i] || '').replace(',', '.'));
            if (isNaN(v)) return {"display": "none"};
            return v <= THRESHOLD ? {} : {"display": "none"};
        });
    }
    """,
    Output({"type": "wip-ot-comment-section", "col": ALL}, "style"),
    Input({"type": "wip-ot-input", "col": ALL}, "value"),
    State({"type": "wip-ot-input", "col": ALL}, "id"),
    prevent_initial_call=True,
)


# ── Actual comment visibility (clientside) ────────────────────────────────────
# Mirror of the Friday FRC callback: shows/hides each comment section in-browser
# as the user types, with the diff-vs-Monday-FRC threshold.

app.clientside_callback(
    """
    function(actual_values, ids, app_data, form_data) {
        if (!app_data || !form_data || !Array.isArray(actual_values))
            return window.dash_clientside.no_update;
        var site = app_data.site, pl = app_data.pl;
        var sliceVals = ((form_data.values || {})[site] || {})[pl] || {};
        var mon_frc = sliceVals['mon_frc'] || {};
        var THRESHOLD_ABS = 10, THRESHOLD_REL = 0.10;
        return ids.map(function(id_obj, i) {
            var cid = id_obj.col;
            var act = parseFloat(String(actual_values[i] || '').replace(',', '.'));
            if (act === 0) return {};
            var mon = parseFloat(String(mon_frc[cid] || '').replace(',', '.'));
            if (isNaN(act) || isNaN(mon) || mon <= 0) return {"display": "none"};
            var diff = mon - act;
            var below = diff >= THRESHOLD_ABS || diff / mon >= THRESHOLD_REL;
            return below ? {} : {"display": "none"};
        });
    }
    """,
    Output({"type": "actual-comment-section", "col": ALL}, "style"),
    Input({"type": "actual-input", "col": ALL}, "value"),
    State({"type": "actual-input", "col": ALL}, "id"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)


# ── Thursday FRC comment visibility (clientside) ──────────────────────────────
# Mirror of the Friday FRC callback — diff-vs-Monday-FRC threshold.

app.clientside_callback(
    """
    function(thu_values, ids, app_data, form_data) {
        if (!app_data || !form_data || !Array.isArray(thu_values))
            return window.dash_clientside.no_update;
        var site = app_data.site, pl = app_data.pl;
        var sliceVals = ((form_data.values || {})[site] || {})[pl] || {};
        var mon_frc = sliceVals['mon_frc'] || {};
        var THRESHOLD_ABS = 10, THRESHOLD_REL = 0.10;
        return ids.map(function(id_obj, i) {
            var cid = id_obj.col;
            var thu = parseFloat(String(thu_values[i] || '').replace(',', '.'));
            if (thu === 0) return {};
            var mon = parseFloat(String(mon_frc[cid] || '').replace(',', '.'));
            if (isNaN(thu) || isNaN(mon) || mon <= 0) return {"display": "none"};
            var diff = mon - thu;
            var below = diff >= THRESHOLD_ABS || diff / mon >= THRESHOLD_REL;
            return below ? {} : {"display": "none"};
        });
    }
    """,
    Output({"type": "thu-comment-section", "col": ALL}, "style"),
    Input({"type": "thu-input", "col": ALL}, "value"),
    State({"type": "thu-input", "col": ALL}, "id"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Local dev only. On Databricks Apps the app is served by gunicorn
    # (see app.yaml), which imports `server` above instead of running this.
    app.run(debug=True, port=8050)
