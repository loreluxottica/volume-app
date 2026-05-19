# components/data_table.py
# ─────────────────────────────────────────────────────────────────────────────
# Renders the full data entry table:
#   - Summary bar (open / draft / submitted counts)
#   - Header rows (section + column names)
#   - Data rows (standard + Friday FRC with expandable panel)
#   - Legend
#
# All state (form values, draft/submit flags, fri_open) comes in as plain
# Python dicts from the app-level dcc.Store — this component is pure layout.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
from typing import Any

from dash import html, dcc

from data.schema import (
    ROWS, COLS_BY_PL, na_matrix, DEADLINES,
    COMMENT_PRESETS, COMMENT_PRESETS_WIP_OT,
    cols_below_threshold, wip_ot_below_threshold,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _dot(color_var: str) -> html.Span:
    return html.Span(className="dot", style={"background": f"var({color_var})"})


def _dot_color(row_id: str, submitted: dict, drafted: dict) -> str:
    if submitted.get(row_id):
        return "--dot-blue"
    if drafted.get(row_id):
        return "--dot-draft"
    return "--dot-green"


# ── summary bar ───────────────────────────────────────────────────────────────

def render_summary_bar(
    submitted: dict[str, bool],
    drafted: dict[str, bool],
    current_site: str,
    current_pl: str,
) -> html.Div:
    sub   = sum(1 for r in ROWS if submitted.get(r["id"]))
    draft = sum(1 for r in ROWS if not submitted.get(r["id"]) and drafted.get(r["id"]))
    open_ = len(ROWS) - sub - draft

    return html.Div(className="summary-bar", children=[
        html.Div(className="summary-item", children=[_dot("--dot-green"), html.Strong(open_), "\u00a0open"]),
        html.Div(className="summary-item", children=[_dot("--dot-draft"), html.Strong(draft), "\u00a0draft saved"]),
        html.Div(className="summary-item", children=[_dot("--dot-blue"),  html.Strong(sub),   "\u00a0submitted"]),
        html.Div(
            f"{current_site} · {current_pl}",
            style={"marginLeft": "auto", "fontSize": "11px", "color": "var(--text-3)"},
        ),
    ])


# ── table header ──────────────────────────────────────────────────────────────

def render_table_header(cols: list[dict], current_site: str, current_pl: str) -> list:
    pl_label = "Frames" if current_pl == "FRAMES" else "Wearables"
    return [
        html.Tr([
            html.Th("Row / Deadline", className="th-section"),
            html.Th(
                f"{current_site} — {pl_label} Volumes & WIP (Kpcs)",
                className="th-section th-section-center",
                colSpan=len(cols),
            ),
            html.Th("", className="th-section", style={"minWidth": "180px"}),
        ]),
        html.Tr([
            html.Th("Forecast type", className="th-col th-col-left"),
            *[html.Th(c["label"], className="th-col") for c in cols],
            html.Th("", className="th-col", style={"minWidth": "180px"}),
        ]),
    ]


# ── friday panel ──────────────────────────────────────────────────────────────

def render_friday_panel(
    cols: list[dict],
    na_cols: list[str],
    fri_values: dict[str, str],
    mon_values: dict[str, str],
    fri_comments: dict[str, dict],   # {col_id: {presets:[], others:""}}
    zero_flags: dict[str, bool],
    submit_attempted: bool,
) -> html.Div:
    below_ids = set(cols_below_threshold(fri_values, mon_values, na_cols, cols))

    missing = [
        cid for cid in below_ids
        if not (fri_comments.get(cid, {}).get("presets") or fri_comments.get(cid, {}).get("others", "").strip())
    ]
    has_errors = submit_attempted and bool(missing)
    warn_cols  = [c["label"] for c in cols if c["id"] in below_ids]

    cards = []
    for col in cols:
        cid = col["id"]
        if cid in na_cols:
            continue

        is_below  = cid in below_ids
        comment_missing = submit_attempted and is_below and cid in missing
        fc = fri_comments.get(cid, {"presets": [], "others": ""})
        zf = zero_flags.get(cid, False)
        incomplete = submit_attempted and not zf and not str(fri_values.get(cid, "") or "").strip()

        card_cls = "fri-card"
        if incomplete:
            card_cls += " fri-card-error"
        elif is_below:
            card_cls += " fri-card-error" if comment_missing else " fri-card-warn"

        input_cls = "fri-num-input"
        if is_below:
            input_cls += " fri-num-input-below"
        if incomplete:
            input_cls += " fri-num-input-incomplete"

        card_children = [
            # Label row
            html.Div(className="fri-card-label-row", children=[
                html.Span(col["label"], className="fri-card-label"),
                html.Span("▼ threshold", className="below-badge") if is_below else None,
            ]),
            # Mon FRC reference
            html.Div(className="fri-ref-row", children=[
                html.Span("Mon FRC"),
                html.Span(mon_values.get(cid) or "—", className="fri-ref-val"),
            ]),
            # Value input
            dcc.Input(
                id={"type": "fri-input", "col": cid},
                type="number",
                min=0,
                placeholder="—",
                value=fri_values.get(cid) or None,
                disabled=zf,
                className=input_cls,
                debounce=False,
            ),
            # Zero flag checkbox
            html.Div(className="zero-flag-row", children=[
                dcc.Checklist(
                    id={"type": "fri-zero", "col": cid},
                    options=[{"label": " Confirm zero (no shipments)", "value": "zero"}],
                    value=["zero"] if zf else [],
                    className="zero-flag-row",
                ),
            ]),
        ]

        # Comment section — always rendered; shown/hidden by clientside callback
        lbl_cls = "comment-label"
        if comment_missing:
            lbl_cls += " comment-label-error"
        comment_children = [
            html.Div(
                "⚠ Comment required" if comment_missing else "Reason for variance",
                className=lbl_cls,
            ),
            dcc.Checklist(
                id={"type": "fri-presets", "col": cid},
                options=[{"label": p["label"], "value": p["id"]} for p in COMMENT_PRESETS],
                value=fc.get("presets", []),
                className="preset-checklist",
            ),
            dcc.Textarea(
                id={"type": "fri-others", "col": cid},
                placeholder="Others (optional)…",
                value=fc.get("others", ""),
                className="others-input",
            ),
        ]
        card_children.append(html.Div(
            id={"type": "fri-comment-section", "col": cid},
            className="comment-section",
            children=comment_children,
            style={} if is_below else {"display": "none"},
        ))

        cards.append(html.Div(className=card_cls, children=[c for c in card_children if c is not None]))

    warn_text = []
    if warn_cols:
        warn_text = [
            html.Br(),
            html.Span(
                f"⚠ {len(warn_cols)} column(s) below threshold — comment required: {', '.join(warn_cols)}",
                className="fri-warn",
            ),
        ]

    return html.Div(className="fri-panel-wrap", children=[
        html.Div(className="fri-panel-header", children=[
            html.Div(children=[
                html.Div("Friday FRC — data entry", className="fri-panel-title"),
                html.Div(className="fri-panel-sub", children=[
                    "Monday FRC shown as reference. Threshold: diff ≥ 10 Kpcs or ≥ 10% triggers mandatory comment.",
                    *warn_text,
                ]),
            ]),
            html.Div(className="fri-panel-btn-row", children=[
                html.Button(["⤓ ", "Save draft"],
                    id="btn-fri-save", className="action-btn btn-save",
                    style={"minWidth": "110px"}, n_clicks=0),
                html.Button(["↑ ", "Submit"],
                    id="btn-fri-submit", className="action-btn btn-open",
                    style={"minWidth": "110px"}, n_clicks=0),
            ]),
        ]),
        html.Div(className="fri-grid", children=cards),
        html.Div(className="fri-panel-footer", children=[
            html.Span(
                f"⚠ Comment required: {', '.join(c['label'] for c in cols if c['id'] in missing)}",
                className="validation-msg",
            ) if has_errors else None,
            html.Button(["⤓ ", "Save draft"],
                id="btn-fri-save-bottom", className="action-btn btn-save",
                style={"minWidth": "110px"}, n_clicks=0),
            html.Button(["↑ ", "Submit Friday FRC"],
                id="btn-fri-submit-bottom", className="action-btn btn-open",
                style={"minWidth": "110px"}, n_clicks=0),
        ]),
    ])


# ── WIP OT panel ──────────────────────────────────────────────────────────────
# Full data-entry panel — an exact replica of the Friday FRC panel, but the
# threshold is a flat value ≤ 90% (no Monday reference comparison).

def render_wip_ot_panel(
    cols: list[dict],
    na_cols: list[str],
    wip_ot_values: dict[str, str],
    wip_ot_comments: dict[str, dict],   # {col_id: {presets:[], others:""}}
    zero_flags: dict[str, bool],
    submit_attempted: bool,
) -> html.Div:
    below_ids = set(wip_ot_below_threshold(wip_ot_values, na_cols, cols))

    missing = [
        cid for cid in below_ids
        if not (wip_ot_comments.get(cid, {}).get("presets")
                or wip_ot_comments.get(cid, {}).get("others", "").strip())
    ]
    has_errors = submit_attempted and bool(missing)
    warn_cols  = [c["label"] for c in cols if c["id"] in below_ids]

    cards = []
    for col in cols:
        cid = col["id"]
        if cid in na_cols:
            continue

        is_below  = cid in below_ids
        comment_missing = submit_attempted and is_below and cid in missing
        fc = wip_ot_comments.get(cid, {"presets": [], "others": ""})
        zf = zero_flags.get(cid, False)
        incomplete = submit_attempted and not zf and not str(wip_ot_values.get(cid, "") or "").strip()

        card_cls = "fri-card"
        if incomplete:
            card_cls += " fri-card-error"
        elif is_below:
            card_cls += " fri-card-error" if comment_missing else " fri-card-warn"

        input_cls = "fri-num-input"
        if is_below:
            input_cls += " fri-num-input-below"
        if incomplete:
            input_cls += " fri-num-input-incomplete"

        card_children = [
            # Label row
            html.Div(className="fri-card-label-row", children=[
                html.Span(col["label"], className="fri-card-label"),
                html.Span("▼ ≤ 90%", className="below-badge") if is_below else None,
            ]),
            # Value input
            dcc.Input(
                id={"type": "wip-ot-input", "col": cid},
                type="number",
                min=0,
                placeholder="—",
                value=wip_ot_values.get(cid) or None,
                disabled=zf,
                className=input_cls,
                debounce=False,
            ),
            # Zero flag checkbox
            html.Div(className="zero-flag-row", children=[
                dcc.Checklist(
                    id={"type": "wip-ot-zero", "col": cid},
                    options=[{"label": " Confirm zero (no shipments)", "value": "zero"}],
                    value=["zero"] if zf else [],
                    className="zero-flag-row",
                ),
            ]),
        ]

        # Comment section — always rendered; shown/hidden by clientside callback
        lbl_cls = "comment-label"
        if comment_missing:
            lbl_cls += " comment-label-error"
        comment_children = [
            html.Div(
                "⚠ Comment required" if comment_missing else "Reason for WIP OT ≤ 90%",
                className=lbl_cls,
            ),
            dcc.Checklist(
                id={"type": "wip-ot-presets", "col": cid},
                options=[{"label": p["label"], "value": p["id"]} for p in COMMENT_PRESETS_WIP_OT],
                value=fc.get("presets", []),
                className="preset-checklist",
            ),
            dcc.Textarea(
                id={"type": "wip-ot-others", "col": cid},
                placeholder="Others (optional)…",
                value=fc.get("others", ""),
                className="others-input",
            ),
        ]
        card_children.append(html.Div(
            id={"type": "wip-ot-comment-section", "col": cid},
            className="comment-section",
            children=comment_children,
            style={} if is_below else {"display": "none"},
        ))

        cards.append(html.Div(className=card_cls, children=[c for c in card_children if c is not None]))

    warn_text = []
    if warn_cols:
        warn_text = [
            html.Br(),
            html.Span(
                f"⚠ {len(warn_cols)} column(s) ≤ 90% — comment required: {', '.join(warn_cols)}",
                className="fri-warn",
            ),
        ]

    return html.Div(className="fri-panel-wrap", children=[
        html.Div(className="fri-panel-header", children=[
            html.Div(children=[
                html.Div("WIP OT % — data entry", className="fri-panel-title"),
                html.Div(className="fri-panel-sub", children=[
                    "Threshold: a value ≤ 90% triggers a mandatory comment.",
                    *warn_text,
                ]),
            ]),
            html.Div(className="fri-panel-btn-row", children=[
                html.Button(["⤓ ", "Save draft"],
                    id="btn-wip-ot-save", className="action-btn btn-save",
                    style={"minWidth": "110px"}, n_clicks=0),
                html.Button(["↑ ", "Submit"],
                    id="btn-wip-ot-submit", className="action-btn btn-open",
                    style={"minWidth": "110px"}, n_clicks=0),
            ]),
        ]),
        html.Div(className="fri-grid", children=cards),
        html.Div(className="fri-panel-footer", children=[
            html.Span(
                f"⚠ Comment required: {', '.join(c['label'] for c in cols if c['id'] in missing)}",
                className="validation-msg",
            ) if has_errors else None,
            html.Button(["⤓ ", "Save draft"],
                id="btn-wip-ot-save-bottom", className="action-btn btn-save",
                style={"minWidth": "110px"}, n_clicks=0),
            html.Button(["↑ ", "Submit WIP OT %"],
                id="btn-wip-ot-submit-bottom", className="action-btn btn-open",
                style={"minWidth": "110px"}, n_clicks=0),
        ]),
    ])


# ── Actual panel ──────────────────────────────────────────────────────────────
# Full data-entry panel — replica of the Friday FRC panel. Same threshold rule
# (diff ≥ 10 Kpcs or ≥ 10% vs Monday FRC) and same comment pre-sets (BBP §6.3).

def render_actual_panel(
    cols: list[dict],
    na_cols: list[str],
    actual_values: dict[str, str],
    mon_values: dict[str, str],
    actual_comments: dict[str, dict],   # {col_id: {presets:[], others:""}}
    zero_flags: dict[str, bool],
    submit_attempted: bool,
) -> html.Div:
    below_ids = set(cols_below_threshold(actual_values, mon_values, na_cols, cols))

    missing = [
        cid for cid in below_ids
        if not (actual_comments.get(cid, {}).get("presets")
                or actual_comments.get(cid, {}).get("others", "").strip())
    ]
    has_errors = submit_attempted and bool(missing)
    warn_cols  = [c["label"] for c in cols if c["id"] in below_ids]

    cards = []
    for col in cols:
        cid = col["id"]
        if cid in na_cols:
            continue

        is_below  = cid in below_ids
        comment_missing = submit_attempted and is_below and cid in missing
        fc = actual_comments.get(cid, {"presets": [], "others": ""})
        zf = zero_flags.get(cid, False)
        incomplete = submit_attempted and not zf and not str(actual_values.get(cid, "") or "").strip()

        card_cls = "fri-card"
        if incomplete:
            card_cls += " fri-card-error"
        elif is_below:
            card_cls += " fri-card-error" if comment_missing else " fri-card-warn"

        input_cls = "fri-num-input"
        if is_below:
            input_cls += " fri-num-input-below"
        if incomplete:
            input_cls += " fri-num-input-incomplete"

        card_children = [
            # Label row
            html.Div(className="fri-card-label-row", children=[
                html.Span(col["label"], className="fri-card-label"),
                html.Span("▼ threshold", className="below-badge") if is_below else None,
            ]),
            # Mon FRC reference
            html.Div(className="fri-ref-row", children=[
                html.Span("Mon FRC"),
                html.Span(mon_values.get(cid) or "—", className="fri-ref-val"),
            ]),
            # Value input
            dcc.Input(
                id={"type": "actual-input", "col": cid},
                type="number",
                min=0,
                placeholder="—",
                value=actual_values.get(cid) or None,
                disabled=zf,
                className=input_cls,
                debounce=False,
            ),
            # Zero flag checkbox
            html.Div(className="zero-flag-row", children=[
                dcc.Checklist(
                    id={"type": "actual-zero", "col": cid},
                    options=[{"label": " Confirm zero (no shipments)", "value": "zero"}],
                    value=["zero"] if zf else [],
                    className="zero-flag-row",
                ),
            ]),
        ]

        # Comment section — always rendered; shown/hidden by clientside callback
        lbl_cls = "comment-label"
        if comment_missing:
            lbl_cls += " comment-label-error"
        comment_children = [
            html.Div(
                "⚠ Comment required" if comment_missing else "Reason for variance",
                className=lbl_cls,
            ),
            dcc.Checklist(
                id={"type": "actual-presets", "col": cid},
                options=[{"label": p["label"], "value": p["id"]} for p in COMMENT_PRESETS],
                value=fc.get("presets", []),
                className="preset-checklist",
            ),
            dcc.Textarea(
                id={"type": "actual-others", "col": cid},
                placeholder="Others (optional)…",
                value=fc.get("others", ""),
                className="others-input",
            ),
        ]
        card_children.append(html.Div(
            id={"type": "actual-comment-section", "col": cid},
            className="comment-section",
            children=comment_children,
            style={} if is_below else {"display": "none"},
        ))

        cards.append(html.Div(className=card_cls, children=[c for c in card_children if c is not None]))

    warn_text = []
    if warn_cols:
        warn_text = [
            html.Br(),
            html.Span(
                f"⚠ {len(warn_cols)} column(s) below threshold — comment required: {', '.join(warn_cols)}",
                className="fri-warn",
            ),
        ]

    return html.Div(className="fri-panel-wrap", children=[
        html.Div(className="fri-panel-header", children=[
            html.Div(children=[
                html.Div("Actual — data entry", className="fri-panel-title"),
                html.Div(className="fri-panel-sub", children=[
                    "Monday FRC shown as reference. Threshold: diff ≥ 10 Kpcs or ≥ 10% triggers a mandatory comment.",
                    *warn_text,
                ]),
            ]),
            html.Div(className="fri-panel-btn-row", children=[
                html.Button(["⤓ ", "Save draft"],
                    id="btn-actual-save", className="action-btn btn-save",
                    style={"minWidth": "110px"}, n_clicks=0),
                html.Button(["↑ ", "Submit"],
                    id="btn-actual-submit", className="action-btn btn-open",
                    style={"minWidth": "110px"}, n_clicks=0),
            ]),
        ]),
        html.Div(className="fri-grid", children=cards),
        html.Div(className="fri-panel-footer", children=[
            html.Span(
                f"⚠ Comment required: {', '.join(c['label'] for c in cols if c['id'] in missing)}",
                className="validation-msg",
            ) if has_errors else None,
            html.Button(["⤓ ", "Save draft"],
                id="btn-actual-save-bottom", className="action-btn btn-save",
                style={"minWidth": "110px"}, n_clicks=0),
            html.Button(["↑ ", "Submit Actual"],
                id="btn-actual-submit-bottom", className="action-btn btn-open",
                style={"minWidth": "110px"}, n_clicks=0),
        ]),
    ])


# ── Thursday FRC panel ────────────────────────────────────────────────────────
# Full data-entry panel — replica of the Friday FRC panel. Same threshold rule
# (diff ≥ 10 Kpcs or ≥ 10% vs Monday FRC) and same comment pre-sets (BBP §6.3).

def render_thu_panel(
    cols: list[dict],
    na_cols: list[str],
    thu_values: dict[str, str],
    mon_values: dict[str, str],
    thu_comments: dict[str, dict],   # {col_id: {presets:[], others:""}}
    zero_flags: dict[str, bool],
    submit_attempted: bool,
) -> html.Div:
    below_ids = set(cols_below_threshold(thu_values, mon_values, na_cols, cols))

    missing = [
        cid for cid in below_ids
        if not (thu_comments.get(cid, {}).get("presets")
                or thu_comments.get(cid, {}).get("others", "").strip())
    ]
    has_errors = submit_attempted and bool(missing)
    warn_cols  = [c["label"] for c in cols if c["id"] in below_ids]

    cards = []
    for col in cols:
        cid = col["id"]
        if cid in na_cols:
            continue

        is_below  = cid in below_ids
        comment_missing = submit_attempted and is_below and cid in missing
        fc = thu_comments.get(cid, {"presets": [], "others": ""})
        zf = zero_flags.get(cid, False)
        incomplete = submit_attempted and not zf and not str(thu_values.get(cid, "") or "").strip()

        card_cls = "fri-card"
        if incomplete:
            card_cls += " fri-card-error"
        elif is_below:
            card_cls += " fri-card-error" if comment_missing else " fri-card-warn"

        input_cls = "fri-num-input"
        if is_below:
            input_cls += " fri-num-input-below"
        if incomplete:
            input_cls += " fri-num-input-incomplete"

        card_children = [
            # Label row
            html.Div(className="fri-card-label-row", children=[
                html.Span(col["label"], className="fri-card-label"),
                html.Span("▼ threshold", className="below-badge") if is_below else None,
            ]),
            # Mon FRC reference
            html.Div(className="fri-ref-row", children=[
                html.Span("Mon FRC"),
                html.Span(mon_values.get(cid) or "—", className="fri-ref-val"),
            ]),
            # Value input
            dcc.Input(
                id={"type": "thu-input", "col": cid},
                type="number",
                min=0,
                placeholder="—",
                value=thu_values.get(cid) or None,
                disabled=zf,
                className=input_cls,
                debounce=False,
            ),
            # Zero flag checkbox
            html.Div(className="zero-flag-row", children=[
                dcc.Checklist(
                    id={"type": "thu-zero", "col": cid},
                    options=[{"label": " Confirm zero (no shipments)", "value": "zero"}],
                    value=["zero"] if zf else [],
                    className="zero-flag-row",
                ),
            ]),
        ]

        # Comment section — always rendered; shown/hidden by clientside callback
        lbl_cls = "comment-label"
        if comment_missing:
            lbl_cls += " comment-label-error"
        comment_children = [
            html.Div(
                "⚠ Comment required" if comment_missing else "Reason for variance",
                className=lbl_cls,
            ),
            dcc.Checklist(
                id={"type": "thu-presets", "col": cid},
                options=[{"label": p["label"], "value": p["id"]} for p in COMMENT_PRESETS],
                value=fc.get("presets", []),
                className="preset-checklist",
            ),
            dcc.Textarea(
                id={"type": "thu-others", "col": cid},
                placeholder="Others (optional)…",
                value=fc.get("others", ""),
                className="others-input",
            ),
        ]
        card_children.append(html.Div(
            id={"type": "thu-comment-section", "col": cid},
            className="comment-section",
            children=comment_children,
            style={} if is_below else {"display": "none"},
        ))

        cards.append(html.Div(className=card_cls, children=[c for c in card_children if c is not None]))

    warn_text = []
    if warn_cols:
        warn_text = [
            html.Br(),
            html.Span(
                f"⚠ {len(warn_cols)} column(s) below threshold — comment required: {', '.join(warn_cols)}",
                className="fri-warn",
            ),
        ]

    return html.Div(className="fri-panel-wrap", children=[
        html.Div(className="fri-panel-header", children=[
            html.Div(children=[
                html.Div("Thursday FRC — data entry", className="fri-panel-title"),
                html.Div(className="fri-panel-sub", children=[
                    "Monday FRC shown as reference. Threshold: diff ≥ 10 Kpcs or ≥ 10% triggers a mandatory comment.",
                    *warn_text,
                ]),
            ]),
            html.Div(className="fri-panel-btn-row", children=[
                html.Button(["⤓ ", "Save draft"],
                    id="btn-thu-save", className="action-btn btn-save",
                    style={"minWidth": "110px"}, n_clicks=0),
                html.Button(["↑ ", "Submit"],
                    id="btn-thu-submit", className="action-btn btn-open",
                    style={"minWidth": "110px"}, n_clicks=0),
            ]),
        ]),
        html.Div(className="fri-grid", children=cards),
        html.Div(className="fri-panel-footer", children=[
            html.Span(
                f"⚠ Comment required: {', '.join(c['label'] for c in cols if c['id'] in missing)}",
                className="validation-msg",
            ) if has_errors else None,
            html.Button(["⤓ ", "Save draft"],
                id="btn-thu-save-bottom", className="action-btn btn-save",
                style={"minWidth": "110px"}, n_clicks=0),
            html.Button(["↑ ", "Submit Thursday FRC"],
                id="btn-thu-submit-bottom", className="action-btn btn-open",
                style={"minWidth": "110px"}, n_clicks=0),
        ]),
    ])


# ── standard row ──────────────────────────────────────────────────────────────

def render_standard_row(
    row: dict,
    cols: list[dict],
    na_cols: list[str],
    values: dict[str, str],
    zero_flags: dict[str, bool],
    is_submitted: bool,
    is_drafted: bool,
    deadline: str,
    is_readonly: bool,
) -> html.Tr:
    is_ref    = row["is_ref"]
    is_locked = is_ref and not is_submitted
    disabled  = is_submitted or is_locked or is_readonly

    row_cls = "data-row"
    if is_submitted: row_cls += " is-submitted"
    elif is_drafted:  row_cls += " is-draft"
    elif is_locked:   row_cls += " is-locked"

    dot_color_var = _dot_color(row["id"], {row["id"]: is_submitted}, {row["id"]: is_drafted})

    label_cell = html.Td(className="label-cell", children=[
        html.Div(className="row-name-wrap", children=[
            _dot(dot_color_var),
            html.Span(row["label"], className="row-name", style={"marginLeft": "7px"}),
        ]),
        html.Div(deadline, className="deadline-tag"),
        html.Div("Reference — read only", className="ref-tag") if is_ref else None,
    ])

    data_cells = []
    for col in cols:
        cid = col["id"]
        if cid in na_cols:
            data_cells.append(html.Td(className="data-cell data-cell-na"))
            continue

        zf = zero_flags.get(cid, False)
        if zf and not disabled:
            # Clickable — reverts the confirmed zero back to an empty cell.
            data_cells.append(html.Td(className="data-cell", children=[
                html.Div(
                    className="zero-cell",
                    id={"type": "zero-revert", "row": row["id"], "col": cid},
                    n_clicks=0,
                    title="Click to undo zero",
                    style={"cursor": "pointer"},
                    children=[html.Span("ZERO ✓", className="zero-label")],
                )
            ]))
            continue

        input_cls = "num-input"
        if is_ref:
            input_cls += " num-input-ref"

        data_cells.append(html.Td(className="data-cell", children=[
            dcc.Input(
                id={"type": "row-input", "row": row["id"], "col": cid},
                type="number",
                min=0,
                placeholder="—",
                value=values.get(cid) or None,
                disabled=disabled,
                className=input_cls,
                debounce=True,
            ),
        ]))

    # Action cell
    row_id = row["id"]
    if is_submitted:
        action = html.Button(
            ["↩ ", "Change submission"],
            id={"type": "btn-change", "row": row_id},
            className="action-btn btn-submitted",
            n_clicks=0,
        )
    elif is_locked or is_ref:
        action = html.Button(
            ["🔒 ", "Read only"],
            className="action-btn btn-locked",
            disabled=True,
        )
    elif is_readonly:
        action = html.Button(
            ["👁 ", "Read only"],
            className="action-btn btn-locked",
            disabled=True,
        )
    else:
        action = html.Div(className="action-split", children=[
            html.Button(
                ["⤓ ", "Saved" if is_drafted else "Save"],
                id={"type": "btn-save", "row": row_id},
                className=f"action-btn {'btn-draft' if is_drafted else 'btn-save'}",
                n_clicks=0,
            ),
            html.Button(
                ["↑ ", "Submit"],
                id={"type": "btn-submit", "row": row_id},
                className="action-btn btn-open",
                n_clicks=0,
            ),
        ])

    action_cell = html.Td(className="action-cell", children=[action])

    return html.Tr(
        className=row_cls,
        children=[label_cell, *data_cells, action_cell],
    )


# ── friday summary row ────────────────────────────────────────────────────────

def render_friday_row(
    cols: list[dict],
    na_cols: list[str],
    fri_values: dict[str, str],
    mon_values: dict[str, str],
    zero_flags: dict[str, bool],
    is_submitted: bool,
    is_drafted: bool,
    fri_open: bool,
    deadline: str,
    is_readonly: bool,
) -> html.Tr:
    below_ids = set(cols_below_threshold(fri_values, mon_values, na_cols, cols))

    dot_var = _dot_color("fri_frc", {"fri_frc": is_submitted}, {"fri_frc": is_drafted})
    row_cls = "data-row" + (" is-submitted" if is_submitted else " is-draft" if is_drafted else "")

    data_cells = []
    for col in cols:
        cid = col["id"]
        if cid in na_cols:
            data_cells.append(html.Td(className="data-cell data-cell-na"))
            continue

        zf = zero_flags.get(cid, False)
        if zf:
            data_cells.append(html.Td(className="data-cell", children=[
                html.Div(className="zero-cell", children=[html.Span("ZERO ✓", className="zero-label")])
            ]))
            continue

        val = fri_values.get(cid, "")
        is_below = cid in below_ids and not is_submitted
        cell_cls = "data-cell" + (" data-cell-below" if is_below else "")
        disp_cls = "fri-display" + (" fri-display-below" if is_below else " fri-display-empty" if not val else "")
        data_cells.append(html.Td(className=cell_cls, children=[
            html.Span(val or "—", className=disp_cls),
        ]))

    if is_submitted:
        action = html.Button(
            ["↩ ", "Change submission"],
            id="btn-fri-change",
            className="action-btn btn-submitted",
            n_clicks=0,
        )
    elif is_readonly:
        action = html.Button(["👁 ", "Read only"], className="action-btn btn-locked", disabled=True)
    else:
        btn_cls = "action-btn btn-fri" + (" btn-fri-open" if fri_open else "")
        action = html.Button(
            ["▲ Close panel" if fri_open else "▼ Enter Friday FRC"],
            id="btn-fri-toggle",
            className=btn_cls,
            n_clicks=0,
        )

    return html.Tr(className=row_cls, children=[
        html.Td(className="label-cell", children=[
            html.Div(className="row-name-wrap", children=[
                _dot(dot_var),
                html.Span("Friday FRC", className="row-name", style={"marginLeft": "7px"}),
            ]),
            html.Div(deadline, className="deadline-tag"),
        ]),
        *data_cells,
        html.Td(className="action-cell", children=[action]),
    ])


# ── WIP OT summary row ────────────────────────────────────────────────────────
# Mirror of render_friday_row: a display-only row with a single toggle button
# that opens the WIP OT data-entry panel.

def render_wip_ot_row(
    cols: list[dict],
    na_cols: list[str],
    wip_ot_values: dict[str, str],
    zero_flags: dict[str, bool],
    is_submitted: bool,
    is_drafted: bool,
    wip_ot_open: bool,
    deadline: str,
    is_readonly: bool,
) -> html.Tr:
    below_ids = set(wip_ot_below_threshold(wip_ot_values, na_cols, cols))

    dot_var = _dot_color("wip_ot", {"wip_ot": is_submitted}, {"wip_ot": is_drafted})
    row_cls = "data-row" + (" is-submitted" if is_submitted else " is-draft" if is_drafted else "")

    data_cells = []
    for col in cols:
        cid = col["id"]
        if cid in na_cols:
            data_cells.append(html.Td(className="data-cell data-cell-na"))
            continue

        zf = zero_flags.get(cid, False)
        if zf:
            data_cells.append(html.Td(className="data-cell", children=[
                html.Div(className="zero-cell", children=[html.Span("ZERO ✓", className="zero-label")])
            ]))
            continue

        val = wip_ot_values.get(cid, "")
        is_below = cid in below_ids and not is_submitted
        cell_cls = "data-cell" + (" data-cell-below" if is_below else "")
        disp_cls = "fri-display" + (" fri-display-below" if is_below else " fri-display-empty" if not val else "")
        data_cells.append(html.Td(className=cell_cls, children=[
            html.Span(val or "—", className=disp_cls),
        ]))

    if is_submitted:
        action = html.Button(
            ["↩ ", "Change submission"],
            id="btn-wip-ot-change",
            className="action-btn btn-submitted",
            n_clicks=0,
        )
    elif is_readonly:
        action = html.Button(["👁 ", "Read only"], className="action-btn btn-locked", disabled=True)
    else:
        btn_cls = "action-btn btn-fri" + (" btn-fri-open" if wip_ot_open else "")
        action = html.Button(
            ["▲ Close panel" if wip_ot_open else "▼ Enter WIP OT %"],
            id="btn-wip-ot-toggle",
            className=btn_cls,
            n_clicks=0,
        )

    return html.Tr(className=row_cls, children=[
        html.Td(className="label-cell", children=[
            html.Div(className="row-name-wrap", children=[
                _dot(dot_var),
                html.Span("WIP OT %", className="row-name", style={"marginLeft": "7px"}),
            ]),
            html.Div(deadline, className="deadline-tag"),
        ]),
        *data_cells,
        html.Td(className="action-cell", children=[action]),
    ])


# ── Actual summary row ────────────────────────────────────────────────────────
# Mirror of render_friday_row: a display-only row with a toggle button that
# opens the Actual data-entry panel. Threshold compares Actual vs Monday FRC.

def render_actual_row(
    cols: list[dict],
    na_cols: list[str],
    actual_values: dict[str, str],
    mon_values: dict[str, str],
    zero_flags: dict[str, bool],
    is_submitted: bool,
    is_drafted: bool,
    actual_open: bool,
    deadline: str,
    is_readonly: bool,
) -> html.Tr:
    below_ids = set(cols_below_threshold(actual_values, mon_values, na_cols, cols))

    dot_var = _dot_color("actual", {"actual": is_submitted}, {"actual": is_drafted})
    row_cls = "data-row" + (" is-submitted" if is_submitted else " is-draft" if is_drafted else "")

    data_cells = []
    for col in cols:
        cid = col["id"]
        if cid in na_cols:
            data_cells.append(html.Td(className="data-cell data-cell-na"))
            continue

        zf = zero_flags.get(cid, False)
        if zf:
            data_cells.append(html.Td(className="data-cell", children=[
                html.Div(className="zero-cell", children=[html.Span("ZERO ✓", className="zero-label")])
            ]))
            continue

        val = actual_values.get(cid, "")
        is_below = cid in below_ids and not is_submitted
        cell_cls = "data-cell" + (" data-cell-below" if is_below else "")
        disp_cls = "fri-display" + (" fri-display-below" if is_below else " fri-display-empty" if not val else "")
        data_cells.append(html.Td(className=cell_cls, children=[
            html.Span(val or "—", className=disp_cls),
        ]))

    if is_submitted:
        action = html.Button(
            ["↩ ", "Change submission"],
            id="btn-actual-change",
            className="action-btn btn-submitted",
            n_clicks=0,
        )
    elif is_readonly:
        action = html.Button(["👁 ", "Read only"], className="action-btn btn-locked", disabled=True)
    else:
        btn_cls = "action-btn btn-fri" + (" btn-fri-open" if actual_open else "")
        action = html.Button(
            ["▲ Close panel" if actual_open else "▼ Enter Actual"],
            id="btn-actual-toggle",
            className=btn_cls,
            n_clicks=0,
        )

    return html.Tr(className=row_cls, children=[
        html.Td(className="label-cell", children=[
            html.Div(className="row-name-wrap", children=[
                _dot(dot_var),
                html.Span("Actual", className="row-name", style={"marginLeft": "7px"}),
            ]),
            html.Div(deadline, className="deadline-tag"),
        ]),
        *data_cells,
        html.Td(className="action-cell", children=[action]),
    ])


# ── Thursday FRC summary row ──────────────────────────────────────────────────
# Mirror of render_friday_row: a display-only row with a toggle button that
# opens the Thursday FRC data-entry panel. Threshold compares vs Monday FRC.

def render_thu_row(
    cols: list[dict],
    na_cols: list[str],
    thu_values: dict[str, str],
    mon_values: dict[str, str],
    zero_flags: dict[str, bool],
    is_submitted: bool,
    is_drafted: bool,
    thu_open: bool,
    deadline: str,
    is_readonly: bool,
) -> html.Tr:
    below_ids = set(cols_below_threshold(thu_values, mon_values, na_cols, cols))

    dot_var = _dot_color("thu_frc", {"thu_frc": is_submitted}, {"thu_frc": is_drafted})
    row_cls = "data-row" + (" is-submitted" if is_submitted else " is-draft" if is_drafted else "")

    data_cells = []
    for col in cols:
        cid = col["id"]
        if cid in na_cols:
            data_cells.append(html.Td(className="data-cell data-cell-na"))
            continue

        zf = zero_flags.get(cid, False)
        if zf:
            data_cells.append(html.Td(className="data-cell", children=[
                html.Div(className="zero-cell", children=[html.Span("ZERO ✓", className="zero-label")])
            ]))
            continue

        val = thu_values.get(cid, "")
        is_below = cid in below_ids and not is_submitted
        cell_cls = "data-cell" + (" data-cell-below" if is_below else "")
        disp_cls = "fri-display" + (" fri-display-below" if is_below else " fri-display-empty" if not val else "")
        data_cells.append(html.Td(className=cell_cls, children=[
            html.Span(val or "—", className=disp_cls),
        ]))

    if is_submitted:
        action = html.Button(
            ["↩ ", "Change submission"],
            id="btn-thu-change",
            className="action-btn btn-submitted",
            n_clicks=0,
        )
    elif is_readonly:
        action = html.Button(["👁 ", "Read only"], className="action-btn btn-locked", disabled=True)
    else:
        btn_cls = "action-btn btn-fri" + (" btn-fri-open" if thu_open else "")
        action = html.Button(
            ["▲ Close panel" if thu_open else "▼ Enter Thursday FRC"],
            id="btn-thu-toggle",
            className=btn_cls,
            n_clicks=0,
        )

    return html.Tr(className=row_cls, children=[
        html.Td(className="label-cell", children=[
            html.Div(className="row-name-wrap", children=[
                _dot(dot_var),
                html.Span("Thursday FRC", className="row-name", style={"marginLeft": "7px"}),
            ]),
            html.Div(deadline, className="deadline-tag"),
        ]),
        *data_cells,
        html.Td(className="action-cell", children=[action]),
    ])


# ── full table ────────────────────────────────────────────────────────────────

def render_data_table(
    current_site: str,
    current_pl: str,
    form_values: dict[str, dict[str, str]],   # {row_id: {col_id: value}}
    submitted: dict[str, bool],
    drafted: dict[str, bool],
    zero_flags: dict[str, dict[str, bool]],   # {row_id: {col_id: bool}}
    fri_comments: dict[str, dict],
    fri_open: bool,
    submit_attempted: bool,
    is_readonly: bool,
    wip_ot_comments: dict | None = None,      # {col_id: {"presets":[], "others":""}}
    wip_ot_open: bool = False,
    actual_comments: dict | None = None,      # {col_id: {"presets":[], "others":""}}
    actual_open: bool = False,
    thu_comments: dict | None = None,         # {col_id: {"presets":[], "others":""}}
    thu_open: bool = False,
) -> html.Div:
    cols   = COLS_BY_PL[current_pl]
    na_map = na_matrix(current_site, current_pl)
    dl     = DEADLINES.get(current_site, {})   # GLOBAL has no deadlines
    is_global = current_site == "GLOBAL"

    thead_rows = render_table_header(cols, current_site, current_pl)

    tbody_rows = []
    for row in ROWS:
        rid = row["id"]
        na_cols = na_map.get(rid, [])

        if row["is_fri"]:
            tbody_rows.append(render_friday_row(
                cols=cols, na_cols=na_cols,
                fri_values=form_values.get(rid, {}),
                mon_values=form_values.get("mon_frc", {}),
                zero_flags=zero_flags.get(rid, {}),
                is_submitted=submitted.get(rid, False),
                is_drafted=drafted.get(rid, False),
                fri_open=fri_open,
                deadline=dl.get(rid, ""),
                is_readonly=is_readonly,
            ))
            if fri_open and not submitted.get(rid) and not is_readonly:
                tbody_rows.append(html.Tr(html.Td(
                    colSpan=len(cols) + 2,
                    children=render_friday_panel(
                        cols=cols, na_cols=na_cols,
                        fri_values=form_values.get(rid, {}),
                        mon_values=form_values.get("mon_frc", {}),
                        fri_comments=fri_comments,
                        zero_flags=zero_flags.get(rid, {}),
                        submit_attempted=submit_attempted,
                    ),
                )))
        elif rid == "thu_frc":
            tbody_rows.append(render_thu_row(
                cols=cols, na_cols=na_cols,
                thu_values=form_values.get(rid, {}),
                mon_values=form_values.get("mon_frc", {}),
                zero_flags=zero_flags.get(rid, {}),
                is_submitted=submitted.get(rid, False),
                is_drafted=drafted.get(rid, False),
                thu_open=thu_open,
                deadline=dl.get(rid, ""),
                is_readonly=is_readonly,
            ))
            if thu_open and not submitted.get(rid) and not is_readonly:
                tbody_rows.append(html.Tr(html.Td(
                    colSpan=len(cols) + 2,
                    children=render_thu_panel(
                        cols=cols, na_cols=na_cols,
                        thu_values=form_values.get(rid, {}),
                        mon_values=form_values.get("mon_frc", {}),
                        thu_comments=thu_comments or {},
                        zero_flags=zero_flags.get(rid, {}),
                        submit_attempted=submit_attempted,
                    ),
                )))
        elif rid == "wip_ot":
            tbody_rows.append(render_wip_ot_row(
                cols=cols, na_cols=na_cols,
                wip_ot_values=form_values.get(rid, {}),
                zero_flags=zero_flags.get(rid, {}),
                is_submitted=submitted.get(rid, False),
                is_drafted=drafted.get(rid, False),
                wip_ot_open=wip_ot_open,
                deadline=dl.get(rid, ""),
                is_readonly=is_readonly,
            ))
            if wip_ot_open and not submitted.get(rid) and not is_readonly:
                tbody_rows.append(html.Tr(html.Td(
                    colSpan=len(cols) + 2,
                    children=render_wip_ot_panel(
                        cols=cols, na_cols=na_cols,
                        wip_ot_values=form_values.get(rid, {}),
                        wip_ot_comments=wip_ot_comments or {},
                        zero_flags=zero_flags.get(rid, {}),
                        submit_attempted=submit_attempted,
                    ),
                )))
        elif rid == "actual":
            tbody_rows.append(render_actual_row(
                cols=cols, na_cols=na_cols,
                actual_values=form_values.get(rid, {}),
                mon_values=form_values.get("mon_frc", {}),
                zero_flags=zero_flags.get(rid, {}),
                is_submitted=submitted.get(rid, False),
                is_drafted=drafted.get(rid, False),
                actual_open=actual_open,
                deadline=dl.get(rid, ""),
                is_readonly=is_readonly,
            ))
            if actual_open and not submitted.get(rid) and not is_readonly:
                tbody_rows.append(html.Tr(html.Td(
                    colSpan=len(cols) + 2,
                    children=render_actual_panel(
                        cols=cols, na_cols=na_cols,
                        actual_values=form_values.get(rid, {}),
                        mon_values=form_values.get("mon_frc", {}),
                        actual_comments=actual_comments or {},
                        zero_flags=zero_flags.get(rid, {}),
                        submit_attempted=submit_attempted,
                    ),
                )))
        else:
            tbody_rows.append(render_standard_row(
                row=row, cols=cols, na_cols=na_cols,
                values=form_values.get(rid, {}),
                zero_flags=zero_flags.get(rid, {}),
                is_submitted=submitted.get(rid, False),
                is_drafted=drafted.get(rid, False),
                deadline=dl.get(rid, ""),
                is_readonly=is_readonly,
            ))

    # Label and action columns are fixed; data columns share the remaining
    # width equally (table-layout:fixed) — uniform and adaptive to the screen.
    colgroup = html.Colgroup(
        [html.Col(style={"width": "210px"})]
        + [html.Col() for _ in cols]
        + [html.Col(style={"width": "190px"})]
    )

    if is_global:
        summary = html.Div(
            f"GLOBAL · {current_pl} — sum of all plants (read-only)",
            className="summary-bar",
            style={"fontStyle": "italic", "color": "var(--text-2)"},
        )
    else:
        summary = render_summary_bar(submitted, drafted, current_site, current_pl)

    return html.Div(children=[
        summary,
        html.Div(className="table-wrap", children=[
            html.Table(className="vol-table", children=[
                colgroup,
                html.Thead(thead_rows),
                html.Tbody(tbody_rows),
            ]),
        ]),
        _render_legend(),
    ])


def _render_legend() -> html.Div:
    return html.Div(className="legend", children=[
        html.Div(className="legend-item", children=[_dot("--dot-green"), "Open for entry"]),
        html.Div(className="legend-item", children=[_dot("--dot-amber"), "Deadline approaching"]),
        html.Div(className="legend-item", children=[_dot("--dot-draft"), "Draft saved"]),
        html.Div(className="legend-item", children=[_dot("--dot-blue"),  "Submitted"]),
        html.Div(className="legend-item", children=[_dot("--dot-grey"),  "Locked"]),
        html.Div(className="legend-item", style={"marginLeft": "auto"}, children=[
            html.Span(className="na-swatch"), "\u00a0N/A",
        ]),
    ])
