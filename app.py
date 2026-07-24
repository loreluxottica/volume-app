# app.py
# ─────────────────────────────────────────────────────────────────────────────
# Entry point for the GLI Darwin Intake Databricks App.
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
from datetime import date

import dash
from dash import Input, Output, State, ctx, dcc, html, ALL, Patch

from components.header import render_topbar, render_app_header, _report_week
from components.data_table import render_data_table
from components.landings import render_landings
from data import cache, db
from data.schema import (
    ROWS, COLS_BY_PL, cols_for, na_matrix, SITES,
    DUMMY_SUBCOLS, DUMMY_PARENT,
    LANDINGS_GROUPS, LANDINGS_ALL_ROW_TYPES, LANDINGS_SECOND_OPTIONS,
    LANDINGS_METRICS, LANDINGS_EMEA_METRICS,
    NA_KPI_WHLS_PLANTS, NA_KPI_DSNA_PLANTS,
    cols_below_threshold, wip_ot_below_threshold, incomplete_cells,
    zero_cells_missing_comment, _is_zero_value, parse_num,
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
    title="GLI Darwin Intake",
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
    if state.get("is_viewer"):
        return False                        # VIEW role: read-only everywhere
    if site == GLOBAL_SITE:
        return False
    if state.get("is_admin"):
        return True
    return site in state.get("sites", [])   # plant owners: only granted sites


def _editable_landings_groups(state: dict) -> set[str]:
    """Landings recap columns the user may edit, mapped from their site scope.
    Any-member rule: a group is editable if the user owns at least one of its
    member plants (e.g. NA = ATLANTA + TIJUANA → owning either unlocks NA).
    VIEW role edits nothing; admins edit every group."""
    if state.get("is_viewer"):
        return set()
    if state.get("is_admin"):
        return {gid for gid, _lbl, _members in LANDINGS_GROUPS}
    sites = set(state.get("sites", []))
    return {gid for gid, _lbl, members in LANDINGS_GROUPS if sites & set(members)}


LANDINGS_ROW2_DEFAULT = "actual"


def _landings_row2() -> str:
    """The Landings 2nd row (Actual | Logistics FRC) — a GLOBAL setting, shared by
    everyone and changeable only by an admin.

    SINGLE SOURCE for the whole feature: render, save and the toggle callback must
    all read it from here. It deliberately does NOT live in the dcc.Store —
    render_ui can render from a value but cannot write one back, so a per-session
    copy goes stale for every user who didn't flip the toggle themselves, and a
    Save would then persist the typed values under the wrong row_type.
    """
    try:
        return cache.cached_setting("landings_row2", LANDINGS_ROW2_DEFAULT) \
            or LANDINGS_ROW2_DEFAULT
    except Exception as exc:
        print(f"[warn] could not read the landings_row2 setting: {exc}")
        return LANDINGS_ROW2_DEFAULT


def current_week() -> dict:
    """
    Current open week as {week_id, year}.

    Resolved through cache.cached_current_week() so the worker self-populates
    on first use — it must NOT depend on the bootstrap callback having run
    first (and it stays correct if gunicorn workers are ever scaled above 1,
    since a module global is per-process). Returns {0, 0} if the DB is
    unreachable or has no open week.
    """
    try:
        wk = cache.cached_current_week()
        return {"week_id": int(wk["week_id"]), "year": int(wk["year"])}
    except Exception as exc:  # DB unavailable — app still starts
        print(f"[warn] could not load current week from DB: {exc}")
        return {"week_id": 0, "year": 0}


def _is_delay(state: dict) -> bool:
    """True when the selected week is an earlier (past) week, not the open one."""
    wk = state.get("week_id") or 0
    if wk == 0:
        return False
    open_wk = current_week()
    return (wk, state.get("week_year") or 0) != (open_wk["week_id"], open_wk["year"])


# ── DB ↔ state helpers ────────────────────────────────────────────────────────

def _to_float(s) -> float | None:
    """Form string → DB numeric (empty → None). Comma = decimal separator.
    Delegates to schema.parse_num so payloads, validators and rendering all
    parse identically."""
    return parse_num(s)


def _fmt(v) -> str:
    """DB numeric → display string for a number input (None/NaN → '').
    Rounded to 1 decimal — the UI never shows more (float noise like
    65.33333333333333 from GLOBAL means would leak through otherwise)."""
    if v is None or v != v:
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    f = round(f, 1)
    s = str(int(f)) if f == int(f) else f"{f:.1f}"
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
    week    = state["week_id"]
    year    = state["week_year"]
    user    = state.get("user") or DEV_USER
    col_ids = {c["id"] for c in cols_for(site, pl)}

    try:
        latest = cache.cached_submissions(week, year, site, pl)
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
            drafts = cache.cached_drafts(week, year, site, pl, user)
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
        ext = cache.cached_gli_extract(state["week_id"], state["week_year"])
    except Exception as exc:
        print(f"[warn] get_gli_extract failed: {exc}")
        return False

    sums: dict = {r["id"]: {} for r in ROWS}
    counts: dict = {}   # {cid: n} for wip_ot — a percentage averages, not sums
    for _, r in ext.iterrows():
        if r["product_line"] != pl:
            continue
        rid, cid = r["submission_type"], r["channel"]
        # DONGGUAN Wearables Dummy sub-channels fold into the single `dummy`.
        if cid in DUMMY_SUBCOLS:
            cid = DUMMY_PARENT
        if rid not in sums or cid not in col_ids:
            continue
        v = _to_float(r["value_kpcs"])
        if v is None:
            continue
        sums[rid][cid] = sums[rid].get(cid, 0.0) + v
        if rid == "wip_ot":
            counts[cid] = counts.get(cid, 0) + 1

    # WIP OT % is a percentage: the GLOBAL cell is the mean of the plants
    # that submitted it, not their sum.
    for cid, n in counts.items():
        sums["wip_ot"][cid] = sums["wip_ot"][cid] / n

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


# ── Landings loaders ──────────────────────────────────────────────────────────

_PLANT_TO_GROUP = {p: gid for gid, _lbl, members in LANDINGS_GROUPS for p in members}


def _default_period_keys(state: dict) -> None:
    """Default the Month/Quarter dropdowns to today's month and quarter."""
    land = state["landings"]
    today = date.today()
    year = current_week()["year"] or today.year
    if not land.get("month_key"):
        land["month_key"] = f"{year}-{today.month:02d}"
    if not land.get("quarter_key"):
        land["quarter_key"] = f"{year}-Q{(today.month - 1) // 3 + 1}"


def _load_landings_weekly(state: dict) -> bool:
    """
    Aggregate the year's FRAMES whls_net submissions into the chart series and
    the read-only WK block. Returns False on a DB read error (retried on the
    next visit). Business FRC = mon_frc, Logistics FRC = fri_frc, PY = py row;
    EMEA sub-rows come from the SEDICO-only whls_net_ow_emea channel.
    """
    land = state["landings"]
    if land.get("weekly_loaded"):
        return True
    wk = current_week()
    try:
        df = cache.cached_landings_weekly(wk["year"])
    except Exception as exc:
        print(f"[warn] get_landings_weekly failed: {exc}")
        return False

    chart_py: dict[str, float] = {}
    chart_cy: dict[str, float] = {}
    wk_block: dict = {
        "business":       {"py": {}, "cy": {}},
        "logistics":      {"py": {}, "cy": {}},
        "business_emea":  {},
        "logistics_emea": {},
    }
    cur = wk["week_id"]
    fc_total, fc_has = 0.0, False   # current-week Logistics Forecast (Friday FRC)
    na_val, na_has = 0.0, False     # current-week NA KPI (Friday FRC components)
    for _, r in df.iterrows():
        v = _to_float(r["value_kpcs"])
        if v is None or v != v:   # None or NaN (zero-flagged rows store NULL)
            continue
        w, st, ch, site = int(r["week_id"]), r["submission_type"], r["channel"], r["site"]
        if ch == "whls_net":
            # Solid red Actual line: only completed weeks (1..cur-1).
            if st == "actual" and w < cur:
                chart_cy[str(w)] = chart_cy.get(str(w), 0.0) + v
            # Dashed red Forecast: current-week Friday FRC, summed across plants.
            elif st == "fri_frc" and w == cur:
                fc_total += v; fc_has = True
        # NA KPI — current-week Friday-FRC components (derived, never hardcoded).
        if w == cur and st == "fri_frc":
            if ch == "whls_net" and site in NA_KPI_WHLS_PLANTS:
                na_val += v; na_has = True
            elif ch == "ds_na" and site in NA_KPI_DSNA_PLANTS:
                na_val += v; na_has = True
        if w != cur:
            continue
        if ch == "whls_net":
            gid = _PLANT_TO_GROUP.get(site)
            if not gid:
                continue
            if st == "py":  # same PY reference for both WK rows
                for row in ("business", "logistics"):
                    wk_block[row]["py"][gid] = wk_block[row]["py"].get(gid, 0.0) + v
            elif st == "mon_frc":
                wk_block["business"]["cy"][gid] = wk_block["business"]["cy"].get(gid, 0.0) + v
            elif st == "fri_frc":
                wk_block["logistics"]["cy"][gid] = wk_block["logistics"]["cy"].get(gid, 0.0) + v
        elif ch == "whls_net_ow_emea" and site == "SEDICO":
            if st == "py":
                wk_block["business_emea"]["py"]  = v
                wk_block["logistics_emea"]["py"] = v
            elif st == "mon_frc":
                wk_block["business_emea"]["cy"] = v
            elif st == "fri_frc":
                wk_block["logistics_emea"]["cy"] = v

    # Blue PY line: dedicated weekly-series table + optional per-week manual
    # override (manual wins). Kept separate from the CY (red) series and the WK
    # block so the historical row is never overwritten. PY of a CY year = prior
    # year's data.
    py_year = (wk["year"] or 0) - 1

    def _num(x):  # numeric-or-missing: treats None AND NaN as missing
        return None if x is None or x != x else float(x)

    def _weekly_value(r):
        man, hist = _num(r["value_manual"]), _num(r["value_hist"])
        return man if man is not None else hist   # manual override wins

    try:
        pdf = cache.cached_chart_weekly(py_year)
        for _, r in pdf.iterrows():
            val = _weekly_value(r)
            if val is not None:
                chart_py[str(int(r["week"]))] = val
    except Exception as exc:
        print(f"[warn] get_chart_weekly({py_year}) failed: {exc}")

    # Red CY line, weeks predating go-live: those have no Actual submissions, so
    # fall back to the same weekly-series table for the CURRENT year. Submissions
    # always win — only weeks absent from chart_cy are filled, so the line
    # self-heals as plants submit. `w >= cur` is excluded so the solid Actual line
    # still ends at current-1 and the dashed forecast segment keeps its meaning.
    try:
        cdf = cache.cached_chart_weekly(wk["year"])
        for _, r in cdf.iterrows():
            w = int(r["week"])
            if w >= cur or str(w) in chart_cy:
                continue
            val = _weekly_value(r)
            if val is not None:
                chart_cy[str(w)] = val
    except Exception as exc:
        print(f"[warn] chart_weekly CY fallback failed: {exc}")

    chart_fc = {"week": cur, "value": fc_total} if fc_has else {}
    na_kpi = na_val if na_has else None

    land["weekly"] = {"chart_py": chart_py, "chart_cy": chart_cy,
                      "chart_fc": chart_fc, "na_kpi": na_kpi, "wk": wk_block}
    land["weekly_loaded"] = True
    return True


def _forget_landings_period(state: dict, period_key: str) -> None:
    """Drop a period from the session's loaded list so the next _load_landings_period
    goes back to the DB. These are shared, last-write-wins figures: showing a stale
    snapshot means the next Save writes it back over a colleague's numbers."""
    state["landings"]["loaded_periods"] = [
        k for k in state["landings"]["loaded_periods"] if k != period_key]


def _load_landings_period(state: dict, period_type: str, period_key: str) -> bool:
    """
    Populate state["landings_values"][period_key] from the shared
    landings_entries table. The full metric tree is skeleton-initialised first
    so the clientside nested write never misses a path. Returns False on a DB
    read error (the period is then retried on the next view).
    """
    if period_key in state["landings"]["loaded_periods"]:
        return True
    # Skeleton spans every row_type (business + actual + logistics) so the shared
    # second-row toggle can show each one's saved values without a DB refetch.
    skel: dict = {}
    for row_type in LANDINGS_ALL_ROW_TYPES:
        skel[row_type] = {}
        for gid, _l, _m in LANDINGS_GROUPS:
            metrics = LANDINGS_METRICS + (LANDINGS_EMEA_METRICS if gid == "SEDICO" else [])
            skel[row_type][gid] = {m: "" for m in metrics}

    try:
        df = cache.cached_landings_entries(period_key)
    except Exception as exc:
        print(f"[warn] get_landings_entries failed for {period_key}: {exc}")
        return False
    for _, r in df.iterrows():
        rt, gid, m = r["row_type"], r["col_group"], r["metric"]
        if rt in skel and gid in skel[rt] and m in skel[rt][gid]:
            skel[rt][gid][m] = _fmt(r["value_kpcs"])

    state["landings_values"][period_key] = skel
    state["landings"]["loaded_periods"].append(period_key)
    return True


def _load_landings(state: dict) -> bool:
    """Everything the Landings page needs: weekly aggregates + both periods."""
    _default_period_keys(state)
    ok_weekly  = _load_landings_weekly(state)
    ok_month   = _load_landings_period(state, "month", state["landings"]["month_key"])
    ok_quarter = _load_landings_period(state, "quarter", state["landings"]["quarter_key"])
    return ok_weekly and ok_month and ok_quarter


def _row_has_data(state: dict, site: str, pl: str, row_id: str) -> bool:
    """True if the row has at least one non-empty, non-N/A cell."""
    na_cols = na_matrix(site, pl).get(row_id, [])
    vals    = state["values"][site][pl][row_id]
    return any(vals.get(c["id"], "") for c in cols_for(site, pl)
               if c["id"] not in na_cols)


def _db_payload(state: dict, site: str, pl: str, row_id: str):
    """Build (values, zero_flags, comments) for one row, ready for db.py."""
    na_cols = na_matrix(site, pl).get(row_id, [])
    raw     = state["values"][site][pl][row_id]
    zf      = state["zero_flags"][site][pl][row_id]
    values, zero_flags = {}, {}
    for c in cols_for(site, pl):
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
        "week_id":        0,        # selected week (set by bootstrap = open week)
        "week_year":      0,
        "pending_delay":  None,     # {"row": ...} awaiting past-week confirm
        "page":           "entry",  # "entry" (grid) | "landings" (recap page)
        "fri_open":       False,
        "submit_attempted": False,
        "user":           "",      # signed-in email (set by bootstrap)
        "is_admin":       False,
        "is_viewer":      False,   # VIEW role: read-only everywhere (set by bootstrap)
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
        "landings": {           # Landings recap page (read-only weekly data)
            "month_key":      "",   # "2026-06" — defaulted on first visit
            "quarter_key":    "",   # "2026-Q2"
            # NB: the 2nd-row toggle is intentionally NOT here — it is a global
            # setting read through _landings_row2(), not per-session state.
            "loaded_periods": [],   # period_keys fetched from the DB
            "weekly_loaded":  False,
            "weekly":         {},   # {"chart_py": {wk: v}, "chart_cy": {...}, "wk": {...}}
        },
        "landings_values": {},  # {period_key: {row_type: {group: {metric: str}}}} — editable
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
            cols = cols_for(s, pl)
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

_FORM_KEYS = ("values", "fri_comments", "wip_ot_comments", "actual_comments",
              "thu_comments", "landings_values")


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
    # Past-week confirm modal — custom overlay (blur backdrop + app-style card).
    # Kept as a persistent layout element so button n_clicks survive re-renders.
    html.Div(
        id="delay-modal", className="modal-overlay", style={"display": "none"},
        children=[
            html.Div(className="modal-box", children=[
                html.Div("⏱", className="modal-icon"),
                html.Div("Modifying a past week", className="modal-title"),
                html.Div(id="delay-modal-body", className="modal-text"),
                html.Div(className="modal-actions", children=[
                    html.Button("Cancel", id="btn-delay-cancel",
                                className="action-btn btn-save-all", n_clicks=0),
                    html.Button("Confirm delayed edit", id="btn-delay-confirm",
                                className="action-btn btn-submit-all", n_clicks=0),
                ]),
            ]),
        ],
    ),
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
    state["user"]      = user
    state["is_admin"]  = "*" in sites
    state["is_viewer"] = "VIEW" in sites
    state["sites"]     = sorted(s for s in sites if s not in ("*", "VIEW"))
    wk = current_week()
    state["week_id"]   = wk["week_id"]
    state["week_year"] = wk["year"]
    # Non-admins start on their own plant, and never on the admin-only Landings
    # page — this is the one place the reset actually reaches the store (render_ui
    # can only refuse to render it), so a user whose admin rights were revoked
    # doesn't keep a stale page="landings" in their session.
    if not state["is_admin"]:
        state["page"] = "entry"
        if state["sites"]:
            state["site"] = state["sites"][0]

    ok = _load_for_view(state, state["site"], state["pl"])
    state["booted"] = True
    return (_app_part(state), _form_part(state),
            "" if ok else "⚠ Could not load data from the database.")


# ── Change week (back-selector) ───────────────────────────────────────────────
# Switching the selected week wipes the per-(site,pl) slices and reloads the view
# for the new week — one week is held in state at a time, so the nested value
# dicts stay week-agnostic.

_WEEK_RESET_KEYS = (
    "values", "submitted", "drafted", "zero_flags", "fri_comments",
    "global", "global_loaded", "loaded",
    "wip_ot_comments", "wip_ot_open", "actual_comments", "actual_open",
    "thu_comments", "thu_open",
)


@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("form-values", "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("week-select", "value"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def change_week(val, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    if not val:
        return dash.no_update, dash.no_update, dash.no_update
    try:
        year, wk = (int(x) for x in str(val).split("-"))
    except (ValueError, TypeError):
        return dash.no_update, dash.no_update, dash.no_update
    if wk == state.get("week_id") and year == state.get("week_year"):
        return dash.no_update, dash.no_update, dash.no_update

    fresh = _empty_state()
    for k in _WEEK_RESET_KEYS:
        state[k] = fresh[k]
    state["week_id"]   = wk
    state["week_year"] = year

    ok = _load_for_view(state, state["site"], state["pl"])
    rw = _report_week(wk, year)
    toast = (f"Loaded WK {rw} | ISO WK {wk}" if ok
             else "⚠ Could not load data for the selected week.")
    return _app_part(state), _form_part(state), toast


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

    # Landings is admin-only. Last line of defence: a tampered app-state store (or a
    # future entry point that forgets to check) can still carry page="landings", so
    # the renderer itself refuses and falls through to the grid.
    if state.get("page") == "landings" and not state.get("is_admin"):
        state["page"] = "entry"

    if state.get("page") == "landings":
        wk = current_week()
        header = render_app_header(
            current_site=state["site"], current_pl=state["pl"],
            week_id=wk["week_id"], year=wk["year"],
            is_readonly=False, page="landings",
            is_admin=True,   # guarded above — only admins reach this branch
        )
        # The 2nd-row view is a shared, admin-controlled global setting — always
        # render from it so every user sees the same view. Guarded: this is the
        # app's only render callback, so an uncaught DB error here blanks the whole
        # page rather than just degrading the Landings tab.
        row2 = _landings_row2()
        body = render_landings(
            week_id=wk["week_id"], year=wk["year"],
            weekly=state.get("landings", {}).get("weekly", {}),
            landings_values=state.get("landings_values", {}),
            month_key=state["landings"].get("month_key", ""),
            quarter_key=state["landings"].get("quarter_key", ""),
            row2=row2, is_admin=state.get("is_admin", False),
            editable_groups=_editable_landings_groups(state),
        )
        return topbar, header, body

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

    try:
        weeks = cache.cached_weeks().to_dict("records")
    except Exception as exc:
        print(f"[warn] could not load weeks list: {exc}")
        weeks = []
    open_wk = current_week()
    header = render_app_header(
        current_site=site, current_pl=pl,
        week_id=state["week_id"] or open_wk["week_id"],
        year=state["week_year"] or open_wk["year"],
        is_readonly=is_ro,
        weeks=weeks,
        open_week_id=open_wk["week_id"],
        open_year=open_wk["year"],
        is_admin=state.get("is_admin", False),
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
    Input("tab-landings",  "n_clicks"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def switch_pl(n_frames, n_wear, n_landings, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    triggered = ctx.triggered_id
    # Guard: a header re-render re-sets n_clicks=0 on the tab buttons, which
    # Dash can interpret as a prop change and fire this callback spuriously.
    # Only act on a real user click (the triggered tab's n_clicks must be > 0).
    if triggered == "tab-frames" and not n_frames:
        return dash.no_update, dash.no_update, dash.no_update
    if triggered == "tab-wearables" and not n_wear:
        return dash.no_update, dash.no_update, dash.no_update
    if triggered == "tab-landings" and not n_landings:
        return dash.no_update, dash.no_update, dash.no_update

    if triggered == "tab-landings":
        # Admin-only page. The tab isn't rendered for anyone else, so this only
        # fires on a forged click — which is exactly what it is here to stop.
        if not state.get("is_admin"):
            return dash.no_update, dash.no_update, "⚠ Landings is admin-only."
        if state.get("page") == "landings":
            return dash.no_update, dash.no_update, dash.no_update
        state["page"] = "landings"
        ok = _load_landings(state)
        return (_app_part(state), _form_part(state),
                "" if ok else "⚠ Could not load Landings data.")

    new_pl = "FRAMES" if triggered == "tab-frames" else "WEARABLES"
    # Clicking Frames/Wearables while on Landings must switch back even when
    # the product line is unchanged.
    if new_pl == state["pl"] and state.get("page", "entry") == "entry":
        return dash.no_update, dash.no_update, dash.no_update
    state["page"]     = "entry"
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


# ── Landings input callback (CLIENTSIDE) ──────────────────────────────────────
# Same rationale as the row-input callback: typed values land in `form-values`
# synchronously in the browser, so a Save click always sees the latest values.
# The full address (period/row/group/metric) is in the pattern id — no site/pl
# indirection needed.

app.clientside_callback(
    """
    function(values, form_data) {
        if (!form_data) return window.dash_clientside.no_update;
        var trig = window.dash_clientside.callback_context.triggered;
        if (!trig || trig.length === 0) return window.dash_clientside.no_update;
        var nf = JSON.parse(JSON.stringify(form_data));
        for (var i = 0; i < trig.length; i++) {
            var pid = trig[i].prop_id;
            var idStr = pid.substring(0, pid.lastIndexOf('.'));
            var idObj;
            try { idObj = JSON.parse(idStr); } catch (e) { continue; }
            var v = trig[i].value;
            var sv = (v === null || v === undefined) ? "" : String(v);
            try {
                nf.landings_values[idObj.period][idObj.row][idObj.group][idObj.metric] = sv;
            } catch (e) { /* nested path missing — skip */ }
        }
        return nf;
    }
    """,
    Output("form-values", "data", allow_duplicate=True),
    Input({"type": "landings-input", "period": ALL, "row": ALL,
           "group": ALL, "metric": ALL}, "value"),
    State("form-values", "data"),
    prevent_initial_call=True,
)


# ── Landings D% live update (CLIENTSIDE) ──────────────────────────────────────
# When any editable cell changes, recompute: group D%, TOTAL PY/CY, TOTAL D%.
# Group IDs are hardcoded in JS (same order as LANDINGS_GROUPS in schema.py).

app.clientside_callback(
    """
    function(inputValues) {
        var ctx = window.dash_clientside.callback_context;
        if (!ctx.inputs_list || !ctx.inputs_list[0]) return [[], [], []];
        var inputIds = ctx.inputs_list[0];
        var outputsAll = ctx.outputs_list;

        var lookup = {};
        inputIds.forEach(function(item, i) {
            var id = item.id;
            var key = id.period + '|' + id.row + '|' + id.group + '|' + id.metric;
            var v = inputValues[i];
            var num = +v;
            lookup[key] = (v == null || v === '' || isNaN(num)) ? null : num;
        });

        var GROUPS = ["SEDICO", "NA", "LHKS", "SUMARE"];

        function calcD(py, cy) {
            if (py === null || cy === null || py === 0 || isNaN(py) || isNaN(cy))
                return ['', 'recap-d-pct'];
            var d = (cy - py) / py;
            var txt = (d * 100).toFixed(1).replace('.', ',') + '%';
            var cls = d < 0 ? 'recap-d-pct recap-d-neg' : d > 0 ? 'recap-d-pct recap-d-pos' : 'recap-d-pct';
            return [txt, cls];
        }

        function sumGroups(id, pyM, cyM) {
            var py = null, cy = null;
            GROUPS.forEach(function(g) {
                var pv = lookup[id.period+'|'+id.row+'|'+g+'|'+pyM];
                var cv = lookup[id.period+'|'+id.row+'|'+g+'|'+cyM];
                if (pv != null) py = (py || 0) + pv;
                if (cv != null) cy = (cy || 0) + cv;
            });
            return [py, cy];
        }

        function fmtNum(v) {
            if (v == null || isNaN(v)) return '';
            var s = Math.round(v).toString();
            return s.replace(/\\B(?=(\\d{3})+(?!\\d))/g, '.');
        }

        var dpctIds = outputsAll[0];
        var totIds  = outputsAll[1];

        var COLORS = {'neg': '#a32d2d', 'pos': '#3b6d11', 'neu': '#000'};

        var dpctChildren = dpctIds.map(function(item) {
            var id = item.id;
            var isEmea = id.mtype === 'emea';
            var pyM = isEmea ? 'py_emea' : 'py', cyM = isEmea ? 'cy_emea' : 'cy';
            var pairPy, pairCy;
            if (id.group === 'TOTAL') {
                var s = sumGroups(id, pyM, cyM); pairPy = s[0]; pairCy = s[1];
            } else {
                pairPy = lookup[id.period+'|'+id.row+'|'+id.group+'|'+pyM];
                pairCy = lookup[id.period+'|'+id.row+'|'+id.group+'|'+cyM];
            }
            var d = calcD(pairPy, pairCy);
            var txt = d[0], cls = d[1];
            if (!txt) return '';
            var color = cls.indexOf('neg') >= 0 ? COLORS.neg : cls.indexOf('pos') >= 0 ? COLORS.pos : COLORS.neu;
            return {type:'Span', namespace:'dash_html_components',
                    props:{children: txt, style:{color: color}, className:'recap-d-pct'}};
        });

        var totChildren = totIds.map(function(item) {
            var id = item.id;
            var sum = null;
            GROUPS.forEach(function(g) {
                var v = lookup[id.period+'|'+id.row+'|'+g+'|'+id.metric];
                if (v != null) sum = (sum || 0) + v;
            });
            return fmtNum(sum);
        });

        return [dpctChildren, totChildren];
    }
    """,
    [
        Output({"type": "landings-dpct",    "period": ALL, "row": ALL, "group": ALL, "mtype": ALL}, "children"),
        Output({"type": "landings-tot-val", "period": ALL, "row": ALL, "metric": ALL}, "children"),
    ],
    Input({"type": "landings-input", "period": ALL, "row": ALL,
           "group": ALL, "metric": ALL}, "value"),
    prevent_initial_call=True,
)


# ── Landings period dropdowns ─────────────────────────────────────────────────
# Guard: a re-render re-mounts the dropdown and fires the callback with the
# already-selected value — the equality check makes that a no-op.

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("form-values", "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("landings-month-select", "value"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def change_landings_month(month_key, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    if not month_key or month_key == state.get("landings", {}).get("month_key"):
        return dash.no_update, dash.no_update, dash.no_update
    state["landings"]["month_key"] = month_key
    # Picking a period is an explicit "show me this" — always re-read it.
    _forget_landings_period(state, month_key)
    ok = _load_landings_period(state, "month", month_key)
    return (_app_part(state), _form_part(state),
            "" if ok else f"⚠ Could not load {month_key}.")


@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("form-values", "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("landings-quarter-select", "value"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def change_landings_quarter(quarter_key, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    if not quarter_key or quarter_key == state.get("landings", {}).get("quarter_key"):
        return dash.no_update, dash.no_update, dash.no_update
    state["landings"]["quarter_key"] = quarter_key
    _forget_landings_period(state, quarter_key)
    ok = _load_landings_period(state, "quarter", quarter_key)
    return (_app_part(state), _form_part(state),
            "" if ok else f"⚠ Could not load {quarter_key}.")


# ── Landings second-row toggle ────────────────────────────────────────────────
# One shared selector picks the second row (Actual | Logistics FRC) for BOTH the
# Month and Quarter blocks. All row_types are already in landings_values, so this
# only flips the rendered row — no DB refetch. _merge keeps unsaved typed edits.

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("form-values", "data", allow_duplicate=True),
    Input("landings-row2-select", "value"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def change_landings_row2(row2, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    # Admin-only: the toggle is a shared global view. Non-admins can't change it
    # (radio is also disabled client-side; this is the server-side enforcement).
    if not state.get("is_admin"):
        return dash.no_update, dash.no_update
    if not row2 or row2 == _landings_row2():
        return dash.no_update, dash.no_update
    try:
        db.set_setting("landings_row2", row2, state.get("user", ""))
        cache.invalidate_setting("landings_row2")
    except Exception as exc:
        print(f"[warn] set landings_row2 failed: {exc}")
        return dash.no_update, dash.no_update
    # No state write: the re-render reads the setting back through _landings_row2().
    return _app_part(state), _form_part(state)


# ── Landings save ─────────────────────────────────────────────────────────────
# Per-column scope: a user only writes the groups their site access unlocks
# (see _editable_landings_groups). Out-of-scope groups are dropped server-side
# so a forged payload can't overwrite them; within scope it's shared figures,
# last write wins. Groups not written keep their existing DB value.

@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-landings-month-save",   "n_clicks"),
    Input("btn-landings-quarter-save", "n_clicks"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def save_landings(n_month, n_quarter, app_data: dict, form_data: dict):
    state = _merge(app_data, form_data)
    # The page is admin-only, so the write path is too — a non-admin reaching here
    # did so with a hand-crafted payload.
    if not state.get("is_admin"):
        return dash.no_update, "⚠ Landings is admin-only."
    triggered = ctx.triggered_id
    if triggered == "btn-landings-month-save" and not n_month:
        return dash.no_update, dash.no_update
    if triggered == "btn-landings-quarter-save" and not n_quarter:
        return dash.no_update, dash.no_update

    period_type = "month" if triggered == "btn-landings-month-save" else "quarter"
    period_key = state["landings"].get(
        "month_key" if period_type == "month" else "quarter_key")
    period_vals = (state.get("landings_values") or {}).get(period_key) or {}

    allowed = _editable_landings_groups(state)   # EMEA metrics live under SEDICO
    # Persist only Business FRC + the currently-selected second row, so toggling
    # never blanks the other row_type's stored values. Read from the global setting,
    # never from the Store — see _landings_row2().
    keep_rows = {"business_frc", _landings_row2()}
    entries: list[tuple] = []
    for row_type, groups in period_vals.items():
        if row_type not in keep_rows:
            continue
        for gid, metrics in groups.items():
            if gid not in allowed:
                continue                          # out of scope — never persist
            for metric, raw in metrics.items():
                entries.append((row_type, gid, metric, _to_float(raw)))
    if not entries:
        return dash.no_update, "⚠ Nothing to save (or nothing in your scope)."

    try:
        db.save_landings_entries(period_type, period_key, state["user"], entries)
        cache.invalidate_landings_entries(period_key)
    except Exception as exc:
        return dash.no_update, f"⚠ Save failed — {exc}"

    # Mark the period for re-read: the next visit / period switch pulls it back from
    # the DB, so a concurrent write by another user surfaces instead of this
    # session's snapshot being re-saved over it. Not reloaded here — this callback
    # does not output form-values, which is where landings_values lives.
    _forget_landings_period(state, period_key)
    # Re-render so the computed TOTAL / D% cells pick up the saved values.
    return _app_part(state), f"⤓ Landings {period_key} saved"


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
    week = state["week_id"]
    year = state["week_year"]
    try:
        db.save_draft(week, year, site, pl, state["user"],
                      row_id, values, zero_flags, comments)
        cache.invalidate_drafts(week, year, site, pl, state["user"])
    except Exception as exc:
        return dash.no_update, f"⚠ Save failed — {exc}"

    state["drafted"][site][pl][row_id] = True
    return _app_part(state), f"⤓ {row_label} — draft saved"


# ── Submit write helpers (shared by direct + past-week-confirm paths) ─────────
# The write tail of every submit path is factored here so both the direct submit
# (open week) and the confirm-dispatcher (past week, is_delay=True) run identical
# DB writes and state updates.

_SUBMIT_TOAST = {
    "fri_frc": "✓ Friday FRC submitted",
    "wip_ot":  "✓ WIP OT % submitted",
    "actual":  "✓ Actual submitted",
    "thu_frc": "✓ Thursday FRC submitted",
}


def _do_submit(state: dict, site: str, pl: str, row_id: str, is_delay: bool,
               payload: dict | None = None) -> str:
    """Write one submitted row, update state flags, return the toast text.
    `payload` is a click-time snapshot used by the deferred (past-week) path so
    the comment-pruning done during validation is preserved."""
    week = state["week_id"]
    year = state["week_year"]
    user = state["user"]
    if payload is None:
        values, zero_flags, comments = _db_payload(state, site, pl, row_id)
    else:
        values     = payload["values"]
        zero_flags = payload["zero_flags"]
        comments   = payload["comments"]
    db.submit_row(week, year, site, pl, user, row_id,
                  values, zero_flags, comments, is_delay=is_delay)
    db.delete_draft(week, year, site, pl, row_id, user)
    cache.invalidate_submissions(week, year, site, pl)
    cache.invalidate_drafts(week, year, site, pl, user)
    # The server cache is cleared above, but the Landings chart / WK block are
    # gated by a per-session flag — clear it too or this user keeps seeing the
    # pre-submit figures until a reload or a Double Tap.
    state["landings"]["weekly_loaded"] = False
    state["submitted"][site][pl][row_id] = True
    state["drafted"][site][pl][row_id]   = False
    if row_id == "fri_frc":
        state["fri_open"]         = False
        state["submit_attempted"] = False
    elif row_id == "wip_ot":
        state["wip_ot_open"][site][pl] = False
        state["submit_attempted"]      = False
    elif row_id == "actual":
        state["actual_open"][site][pl] = False
        state["submit_attempted"]      = False
    elif row_id == "thu_frc":
        state["thu_open"][site][pl] = False
        state["submit_attempted"]   = False
    if row_id in _SUBMIT_TOAST:
        return _SUBMIT_TOAST[row_id]
    label = next(r["label"] for r in ROWS if r["id"] == row_id)
    return f"✓ {label} submitted"


def _gate_submit(state: dict, site: str, pl: str, row_id: str):
    """Either write now (open week) or stash the pending edit and pop the modal
    (past week). Returns the (app-state, toast) tuple the callback should return."""
    if _is_delay(state):
        values, zero_flags, comments = _db_payload(state, site, pl, row_id)
        state["pending_delay"] = {
            "row": row_id,
            "payload": {"values": values, "zero_flags": zero_flags, "comments": comments},
        }
        return _app_part(state), dash.no_update   # modal carries the message
    try:
        msg = _do_submit(state, site, pl, row_id, is_delay=False)
    except Exception as exc:
        return dash.no_update, f"⚠ Submit failed — {exc}"
    return _app_part(state), msg


def _run_bulk(state: dict, site: str, pl: str, is_save: bool, is_delay: bool) -> str:
    """Save-all / Submit-all over the standard rows; returns the toast text."""
    week = state["week_id"]
    year = state["week_year"]
    user = state["user"]
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
                                na_cols, cols_for(site, pl)):
                errors += 1
                continue

        values, zero_flags, comments = _db_payload(state, site, pl, rid)
        try:
            if is_save:
                db.save_draft(week, year, site, pl, user, rid,
                              values, zero_flags, comments)
                cache.invalidate_drafts(week, year, site, pl, user)
                state["drafted"][site][pl][rid] = True
            else:
                db.submit_row(week, year, site, pl, user, rid,
                              values, zero_flags, comments, is_delay=is_delay)
                db.delete_draft(week, year, site, pl, rid, user)
                cache.invalidate_submissions(week, year, site, pl)
                cache.invalidate_drafts(week, year, site, pl, user)
                state["landings"]["weekly_loaded"] = False   # see _do_submit
                state["submitted"][site][pl][rid] = True
                state["drafted"][site][pl][rid]   = False
            n += 1
        except Exception as exc:
            errors += 1
            print(f"[warn] bulk {'save' if is_save else 'submit'} {rid} failed: {exc}")

    verb = "saved as draft" if is_save else "submitted"
    if n and errors:
        return f"{n} row(s) {verb}, {errors} failed — check and retry"
    if n:
        return f"{'⤓' if is_save else '✓'} {n} row(s) {verb}"
    if errors:
        return f"⚠ All {errors} row(s) failed — check the connection"
    return "No open rows with data to save." if is_save else "No open rows with data."


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
    cols    = cols_for(site, pl)
    blank = incomplete_cells(state["values"][site][pl][row_id],
                             state["zero_flags"][site][pl][row_id], na_cols, cols)
    if blank:
        state["submit_attempted"] = True
        blank_labels = [c["label"] for c in cols if c["id"] in blank]
        return _app_part(state), f"⚠ Fill or confirm zero for all cells: {', '.join(blank_labels)}"

    return _gate_submit(state, site, pl, row_id)


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
    week = state["week_id"]
    year = state["week_year"]
    try:
        db.save_draft(week, year, site, pl, state["user"],
                      "fri_frc", values, zero_flags, comments)
        cache.invalidate_drafts(week, year, site, pl, state["user"])
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
    cols    = cols_for(site, pl)

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

    return _gate_submit(state, site, pl, "fri_frc")


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
    week = state["week_id"]
    year = state["week_year"]
    try:
        db.save_draft(week, year, site, pl, state["user"],
                      "wip_ot", values, zero_flags, comments)
        cache.invalidate_drafts(week, year, site, pl, state["user"])
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
    cols    = cols_for(site, pl)

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

    return _gate_submit(state, site, pl, "wip_ot")


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
    week = state["week_id"]
    year = state["week_year"]
    try:
        db.save_draft(week, year, site, pl, state["user"],
                      "actual", values, zero_flags, comments)
        cache.invalidate_drafts(week, year, site, pl, state["user"])
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
    cols    = cols_for(site, pl)

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

    return _gate_submit(state, site, pl, "actual")


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
    week = state["week_id"]
    year = state["week_year"]
    try:
        db.save_draft(week, year, site, pl, state["user"],
                      "thu_frc", values, zero_flags, comments)
        cache.invalidate_drafts(week, year, site, pl, state["user"])
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
    cols    = cols_for(site, pl)

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

    return _gate_submit(state, site, pl, "thu_frc")


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

    is_save = triggered == "btn-save-all"

    # Submit-all on a past week is gated by the same confirm modal.
    if not is_save and _is_delay(state):
        state["pending_delay"] = {"row": "__bulk__"}
        return _app_part(state), dash.no_update   # modal carries the message

    return _app_part(state), _run_bulk(state, site, pl, is_save, is_delay=False)


# ── Past-week confirm modal ───────────────────────────────────────────────────
# A pending delayed edit lives in state["pending_delay"]. This bridge pops the
# modal whenever one is set, and clears when it is resolved.

@app.callback(
    Output("delay-modal", "style"),
    Output("delay-modal-body", "children"),
    Input("app-state", "data"),
    prevent_initial_call=True,
)
def toggle_delay_modal(app_data: dict):
    app_data = app_data or {}
    if not app_data.get("pending_delay"):
        return {"display": "none"}, dash.no_update
    wk = app_data.get("week_id", 0)
    rw = _report_week(wk, app_data.get("week_year", 0)) if wk else 0
    body = ["This edit targets ", html.Strong(f"WK {rw} | ISO WK {wk}"),
            ", a past week. It will be recorded as a delayed edit "
            "(delay = TRUE) with a timestamp. Continue?"]
    return {"display": "flex"}, body


@app.callback(
    Output("app-state",   "data", allow_duplicate=True),
    Output("toast-store", "data", allow_duplicate=True),
    Input("btn-delay-confirm", "n_clicks"),
    Input("btn-delay-cancel",  "n_clicks"),
    State("app-state", "data"),
    State("form-values", "data"),
    prevent_initial_call=True,
)
def resolve_delay(_confirm, _cancel, app_data: dict, form_data: dict):
    state   = _merge(app_data, form_data)
    pending = state.get("pending_delay")
    trig    = ctx.triggered_id
    state["pending_delay"] = None

    if not pending or trig == "btn-delay-cancel":
        return _app_part(state), ("Delayed edit cancelled." if pending else dash.no_update)

    row      = pending.get("row")
    site, pl = state["site"], state["pl"]
    try:
        if row == "__bulk__":
            msg = _run_bulk(state, site, pl, is_save=False, is_delay=True)
        else:
            msg = _do_submit(state, site, pl, row, is_delay=True,
                             payload=pending.get("payload"))
    except Exception as exc:
        return _app_part(state), f"⚠ Submit failed — {exc}"
    return _app_part(state), msg


# ── CSV export ────────────────────────────────────────────────────────────────
# Downloads the current week's gli_extract, filtered to the selected product
# line (and site, unless GLOBAL is selected). BBP §6.9.
# Deliberately ungated: reads are open to every authenticated user (only edits
# are permission-checked). Note: the GLOBAL export is the RAW per-site extract —
# DONGGUAN's dummy sub-channels stay unfolded — unlike the summed GLOBAL screen.
# Format is it-IT friendly: ';' separator, ',' decimals (opens right in Excel).

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

    week = state["week_id"]
    year = state["week_year"]
    if not week or not year:
        return dash.no_update, "⚠ Not connected to the database — reload the page."
    try:
        df = cache.cached_gli_extract(week, year)
    except Exception as exc:
        return dash.no_update, f"⚠ Export failed — {exc}"

    site, pl = state["site"], state["pl"]
    df = df[df["product_line"] == pl]
    if site != GLOBAL_SITE:
        df = df[df["site"] == site]
    if df.empty:
        return dash.no_update, "No data to export for this week."

    fname = f"volumes_{year}_wk{week}_{site}_{pl}.csv"
    return (dcc.send_data_frame(df.to_csv, fname, index=False, sep=";", decimal=","),
            f"⤓ Exported {fname}")


# ── Double Tap — refresh server cache ─────────────────────────────────────────
# Clears the per-process server cache (data/cache.py) and reloads the view.
# gunicorn runs a single worker (gunicorn.conf.py), so one tap clears it all;
# entries also self-expire on a TTL, so this button is just the instant path.

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
    state["landings"]["weekly_loaded"]  = False
    state["landings"]["loaded_periods"] = []
    if state.get("page") == "landings" and state.get("is_admin"):
        ok = _load_landings(state)
    else:
        state["page"] = "entry"          # admin-only page — never reload it otherwise
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
        // Mirror of data/schema.py parse_num: comma = decimal, dots = thousands.
        function pnum(v) {
            var s = String(v === null || v === undefined ? '' : v).trim().split(' ').join('');
            if (s === '') return NaN;
            if (s.indexOf(',') !== -1) s = s.split('.').join('').replace(',', '.');
            return parseFloat(s);
        }
        var site = app_data.site, pl = app_data.pl;
        var sliceVals = ((form_data.values || {})[site] || {})[pl] || {};
        var mon_frc = sliceVals['mon_frc'] || {};
        var THRESHOLD_ABS = 10000, THRESHOLD_REL = 0.10;
        return ids.map(function(id_obj, i) {
            var cid = id_obj.col;
            var fri = pnum(fri_values[i]);
            if (fri === 0) return {};
            var mon = pnum(mon_frc[cid]);
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
        // Mirror of data/schema.py parse_num: comma = decimal, dots = thousands.
        function pnum(v) {
            var s = String(v === null || v === undefined ? '' : v).trim().split(' ').join('');
            if (s === '') return NaN;
            if (s.indexOf(',') !== -1) s = s.split('.').join('').replace(',', '.');
            return parseFloat(s);
        }
        var THRESHOLD = 90;
        return ids.map(function(id_obj, i) {
            var v = pnum(wip_values[i]);
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
        // Mirror of data/schema.py parse_num: comma = decimal, dots = thousands.
        function pnum(v) {
            var s = String(v === null || v === undefined ? '' : v).trim().split(' ').join('');
            if (s === '') return NaN;
            if (s.indexOf(',') !== -1) s = s.split('.').join('').replace(',', '.');
            return parseFloat(s);
        }
        var site = app_data.site, pl = app_data.pl;
        var sliceVals = ((form_data.values || {})[site] || {})[pl] || {};
        var mon_frc = sliceVals['mon_frc'] || {};
        var THRESHOLD_ABS = 10000, THRESHOLD_REL = 0.10;
        return ids.map(function(id_obj, i) {
            var cid = id_obj.col;
            var act = pnum(actual_values[i]);
            if (act === 0) return {};
            var mon = pnum(mon_frc[cid]);
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
        // Mirror of data/schema.py parse_num: comma = decimal, dots = thousands.
        function pnum(v) {
            var s = String(v === null || v === undefined ? '' : v).trim().split(' ').join('');
            if (s === '') return NaN;
            if (s.indexOf(',') !== -1) s = s.split('.').join('').replace(',', '.');
            return parseFloat(s);
        }
        var site = app_data.site, pl = app_data.pl;
        var sliceVals = ((form_data.values || {})[site] || {})[pl] || {};
        var mon_frc = sliceVals['mon_frc'] || {};
        var THRESHOLD_ABS = 10000, THRESHOLD_REL = 0.10;
        return ids.map(function(id_obj, i) {
            var cid = id_obj.col;
            var thu = pnum(thu_values[i]);
            if (thu === 0) return {};
            var mon = pnum(mon_frc[cid]);
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
    app.run(debug=False, port=8050)
