# components/landings.py
# ─────────────────────────────────────────────────────────────────────────────
# Landings recap page: weekly shipped chart + WK / MONTH / QUARTER recap table
# replicating the "Landings siop" Excel report as closely as possible: white
# background, black-bordered boxes, vertical section labels, detached rows.
# WK block is read-only from the submissions DB; MONTH and QUARTER blocks are
# editable, scoped per column group, and persisted in the shared landings_entries
# table. The whole page is admin-only (see app.py) — non-admins never get the tab.
# Controls (period dropdowns + Save) live in a toolbar above the table so the
# table itself stays clean for screenshots.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import plotly.graph_objects as go
from dash import dcc, html

from data.schema import (
    LANDINGS_GROUPS,
    LANDINGS_BUSINESS_ROW,
    LANDINGS_SECOND_OPTIONS,
    LANDINGS_SECOND_LABELS,
    MONTH_LABELS,
    NA_KPI_DSNA_PLANTS,
    NA_KPI_WHLS_PLANTS,
)

_GROUP_IDS = [g[0] for g in LANDINGS_GROUPS]

# Built from the constants the KPI actually sums, so the tooltip cannot drift out
# of step with the calculation the way a hand-written list did.
_NA_KPI_TITLE = (
    "North America — Friday Forecast: "
    + " + ".join(f"{p.title()} WHLS Net" for p in NA_KPI_WHLS_PLANTS)
    + " + "
    + " + ".join(f"{p.title()} DS NA" for p in NA_KPI_DSNA_PLANTS)
)
# Each group column = 1 gap spacer + 3 data cells (PY/CY/D%). The gap gives a
# little breathing room between adjacent groups without breaking each triplet's
# shared box. gutter + label + (1 + len groups) group columns.
_N_COLS = 2 + 4 * (1 + len(_GROUP_IDS))


# ── formatting helpers ────────────────────────────────────────────────────────

def _to_num(raw) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _fmt_val(v: float | None) -> str:
    if v is None:
        return ""
    return f"{v:,.0f}".replace(",", ".")


def _fmt_pct(py: float | None, cy: float | None) -> tuple[str, str]:
    """Return (text, css class) for the D% cell — '-23,8%' style, blank when
    PY is missing or zero."""
    if py is None or cy is None or py == 0:
        return "", "recap-d-pct"
    d = (cy - py) / py
    txt = f"{d * 100:,.1f}".replace(".", ",") + "%"
    cls = "recap-d-pct recap-d-neg" if d < 0 else "recap-d-pct recap-d-pos" if d > 0 else "recap-d-pct"
    return txt, cls


# ── chart ─────────────────────────────────────────────────────────────────────

def _build_chart(chart_py: dict, chart_cy: dict, chart_fc: dict,
                 current_week: int, year: int) -> go.Figure:
    def _series(d: dict) -> tuple[list[int], list[float]]:
        weeks = sorted(int(k) for k in d)
        return weeks, [d[str(w)] if str(w) in d else d.get(w) for w in weeks]

    py_x, py_y = _series(chart_py or {})
    cy_x, cy_y = _series(chart_cy or {})   # solid Actual: weeks 1..current-1

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=py_x, y=py_y, mode="lines", name=str(year - 1),
        line=dict(color="#2e6da4", width=2),
    ))
    fig.add_trace(go.Scatter(
        x=cy_x, y=cy_y, mode="lines", name=str(year),
        line=dict(color="#c0392b", width=2),
    ))

    # Dashed red Logistics-Forecast segment: continues the solid Actual line from
    # its last point to the current-week Friday FRC, ending at the vertical line.
    # Only drawn when the Actual line actually reaches the week before the forecast
    # — otherwise the segment would span every missing week as one straight
    # diagonal and read as a multi-week forecast. A lone marker leaves the gap
    # visible, which is the truth: those weeks have no data.
    fc = chart_fc or {}
    fc_val = fc.get("value")
    fc_week = fc.get("week", current_week)
    if fc_val is not None:
        if cy_x and cy_x[-1] == fc_week - 1:
            fig.add_trace(go.Scatter(
                x=[cy_x[-1], fc_week], y=[cy_y[-1], fc_val], mode="lines",
                name=f"{year} FRC", line=dict(color="#c0392b", width=2, dash="dash"),
                showlegend=False,
            ))
        else:
            fig.add_trace(go.Scatter(
                x=[fc_week], y=[fc_val], mode="markers",
                name=f"{year} FRC",
                marker=dict(color="#c0392b", size=7, symbol="circle-open"),
                showlegend=False,
            ))

    # Black dashed vertical marker: the Actual→Forecast break / forecast end.
    fig.add_vline(x=current_week, line_dash="dash", line_color="#000000", line_width=1)
    fig.update_layout(
        height=280,
        margin=dict(l=50, r=16, t=12, b=28),
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font=dict(family="DM Sans, sans-serif", size=10, color="#333333"),
        legend=dict(orientation="h", yanchor="top", y=-0.14, xanchor="center", x=0.5),
        xaxis=dict(title=None, range=[-0.5, 51.5], dtick=2, tick0=0,
                   gridcolor="rgba(0,0,0,0.06)", zeroline=False, fixedrange=True),
        yaxis=dict(title=None, gridcolor="rgba(0,0,0,0.10)", rangemode="tozero",
                   zeroline=False, fixedrange=True),
        hovermode="x unified",
    )
    return fig


# ── table building blocks ─────────────────────────────────────────────────────

def _gap_th() -> html.Th:
    return html.Th("", className="recap-gap-th")


def _gap_td() -> html.Td:
    return html.Td(className="recap-gap-td")


def _recap_header(editable_groups: set[str]) -> html.Thead:
    macro = [
        html.Th("", className="recap-empty-th"),
        html.Th("", className="recap-empty-th"),
        _gap_th(),
        html.Th("TOTAL", colSpan=3, className="recap-macro-th recap-macro-total"),
    ]
    for gid, label, _members in LANDINGS_GROUPS:
        macro.append(_gap_th())
        if gid in editable_groups:
            macro.append(html.Th(label, colSpan=3, className="recap-macro-th"))
        else:
            macro.append(html.Th(
                ["🔒 ", label], colSpan=3,
                className="recap-macro-th recap-macro-locked",
                title="Read only — not in your scope",
            ))
    subs = [html.Th("", className="recap-empty-th"),
            html.Th("", className="recap-empty-th")]
    for _ in range(1 + len(_GROUP_IDS)):
        subs.append(_gap_th())
        subs.extend([
            html.Th("PY",  className="recap-sub-th"),
            html.Th("CY",  className="recap-sub-th recap-sub-cy"),
            html.Th("D%",  className="recap-sub-th"),
        ])
    return html.Thead([html.Tr(macro), html.Tr(subs)])


def _pct_span(py: float | None, cy: float | None, id_dict=None) -> html.Span:
    txt, cls = _fmt_pct(py, cy)
    kw: dict = {"className": cls}
    if id_dict:
        kw["id"] = id_dict
    return html.Span(txt, **kw)


def _triplet(py_child, cy_child, pct_child, act: bool = False,
             cell_title: str | None = None) -> list[html.Td]:
    """Three cells wrapped in one black-bordered box (Excel-like group box)."""
    cy_cls = "recap-td recap-cell-mid" + (" recap-cell-act" if act else "")
    t = {"title": cell_title} if cell_title else {}
    return [
        html.Td(py_child,  className="recap-td recap-cell-first", **t),
        html.Td(cy_child,  className=cy_cls, **t),
        html.Td(pct_child, className="recap-td recap-cell-last", **t),
    ]


def _ro_triplet(py: float | None, cy: float | None, act: bool = False,
                dpct_id=None, tot_py_id=None, tot_cy_id=None) -> list[html.Td]:
    def _vs(v, id_dict):
        kw: dict = {"className": "recap-val"}
        if id_dict:
            kw["id"] = id_dict
        return html.Span(_fmt_val(v), **kw)
    return _triplet(_vs(py, tot_py_id), _vs(cy, tot_cy_id),
                    _pct_span(py, cy, dpct_id), act=act)


def _blank_triplet() -> list[html.Td]:
    return [html.Td(className="recap-blank") for _ in range(3)]


def _input(period_key: str, row_type: str, group: str, metric: str, raw,
           editable: bool = True) -> dcc.Input:
    # type="text" (not "number"): the native number stepper (−/+ buttons) can't
    # be reliably suppressed by CSS across browsers. inputMode keeps the numeric
    # keypad on mobile; all value consumers parse strings (_to_num / clientside +v).
    #
    # Out-of-scope groups render a DISABLED input (not a plain Span) on purpose:
    # its value stays in the landings-input pattern list that feeds the live
    # TOTAL / D% recompute, while disabled = no events = the user can't change it
    # (server-side save also drops out-of-scope groups).
    cls = "num-input recap-input" + ("" if editable else " recap-input-locked")
    return dcc.Input(
        id={"type": "landings-input", "period": period_key,
            "row": row_type, "group": group, "metric": metric},
        type="text", inputMode="numeric", debounce=True,
        value=raw if raw not in ("", None) else None,
        disabled=not editable,
        className=cls,
    )


def _edit_triplet(period_key: str, row_type: str, group: str, values: dict,
                  metrics: tuple[str, str] = ("py", "cy"), act: bool = False,
                  editable: bool = True) -> list[html.Td]:
    py_raw, cy_raw = values.get(metrics[0], ""), values.get(metrics[1], "")
    mtype = "emea" if "emea" in metrics[0] else "main"
    dpct_id = {"type": "landings-dpct", "period": period_key,
               "row": row_type, "group": group, "mtype": mtype}
    return _triplet(
        _input(period_key, row_type, group, metrics[0], py_raw, editable=editable),
        _input(period_key, row_type, group, metrics[1], cy_raw, editable=editable),
        _pct_span(_to_num(py_raw), _to_num(cy_raw), dpct_id), act=act,
        cell_title=None if editable else "Read only — not in your scope",
    )


def _vlabel_td(text: str, n_rows: int) -> html.Td:
    return html.Td(html.Div(text, className="recap-vlabel-text"),
                   rowSpan=n_rows, className="recap-vlabel")


def _label_td(text: str, act: bool = False) -> html.Td:
    cls = "recap-row-label" + (" recap-row-label-act" if act else "")
    return html.Td(text, className=cls)


def _emea_label_td() -> html.Td:
    return html.Td("of which EMEA", className="recap-emea-label")


def _spacer_row() -> html.Tr:
    return html.Tr(html.Td(colSpan=_N_COLS, className="recap-spacer-td"),
                   className="recap-spacer")


def _blank_tail() -> list[html.Td]:
    """Blank cells (gap + triplet) for the non-SEDICO groups of an EMEA row."""
    cells: list[html.Td] = []
    for _ in range(len(_GROUP_IDS) - 1):
        cells.append(_gap_td())
        cells += _blank_triplet()
    return cells


# ── WK block (read-only) ──────────────────────────────────────────────────────

def _wk_row(label: str, per_group: dict, gutter: html.Td | None) -> html.Tr:
    """per_group = {"py": {group_id: v}, "cy": {group_id: v}}"""
    py_g = per_group.get("py", {})
    cy_g = per_group.get("cy", {})

    def _tot(d: dict) -> float | None:
        vals = [v for v in (d.get(g) for g in _GROUP_IDS) if v is not None]
        return sum(vals) if vals else None

    cells: list = [gutter] if gutter is not None else []
    cells.append(_label_td(label))
    cells.append(_gap_td())
    cells += _ro_triplet(_tot(py_g), _tot(cy_g))
    for gid in _GROUP_IDS:
        cells.append(_gap_td())
        cells += _ro_triplet(py_g.get(gid), cy_g.get(gid))
    return html.Tr(cells, className="recap-row")


def _wk_emea_row(emea: dict) -> html.Tr:
    """emea = {"py": v, "cy": v} — boxed cells only under SEDICO, rest blank."""
    cells = [_emea_label_td()]
    cells.append(_gap_td()); cells += _blank_triplet()              # TOTAL
    cells.append(_gap_td()); cells += _ro_triplet(emea.get("py"), emea.get("cy"))  # SEDICO
    cells += _blank_tail()
    return html.Tr(cells, className="recap-row")


def _wk_rows(week_id: int, wk: dict) -> list[html.Tr]:
    return [
        _wk_row(f"BUSINESS FRC WK {week_id}", wk.get("business", {}),
                _vlabel_td(f"WK {week_id}", 4)),
        _wk_emea_row(wk.get("business_emea", {})),
        _wk_row(f"LOGISTICS FRC WK {week_id}", wk.get("logistics", {}), None),
        _wk_emea_row(wk.get("logistics_emea", {})),
    ]


# ── MONTH / QUARTER blocks (editable) ─────────────────────────────────────────

def _period_row(label: str, period_key: str, row_type: str, row_values: dict,
                gutter: html.Td | None, editable_groups: set[str],
                act: bool = False) -> html.Tr:
    """Editable row: TOTAL computed from current group values, groups editable."""
    def _tot(metric: str) -> float | None:
        vals = [_to_num((row_values.get(g) or {}).get(metric)) for g in _GROUP_IDS]
        vals = [v for v in vals if v is not None]
        return sum(vals) if vals else None

    cells: list = [gutter] if gutter is not None else []
    cells.append(_label_td(label, act=act))
    cells.append(_gap_td())
    cells += _ro_triplet(
        _tot("py"), _tot("cy"), act=act,
        dpct_id={"type": "landings-dpct", "period": period_key,
                 "row": row_type, "group": "TOTAL", "mtype": "main"},
        tot_py_id={"type": "landings-tot-val", "period": period_key,
                   "row": row_type, "metric": "py"},
        tot_cy_id={"type": "landings-tot-val", "period": period_key,
                   "row": row_type, "metric": "cy"},
    )
    for gid in _GROUP_IDS:
        cells.append(_gap_td())
        cells += _edit_triplet(period_key, row_type, gid,
                               row_values.get(gid) or {}, act=act,
                               editable=gid in editable_groups)
    return html.Tr(cells, className="recap-row")


def _period_emea_row(period_key: str, row_type: str, sedico_values: dict,
                     editable_groups: set[str], act: bool = False) -> html.Tr:
    cells = [_emea_label_td()]
    cells.append(_gap_td()); cells += _blank_triplet()             # TOTAL
    cells.append(_gap_td())
    cells += _edit_triplet(period_key, row_type, "SEDICO", sedico_values,
                           metrics=("py_emea", "cy_emea"), act=act,
                           editable="SEDICO" in editable_groups)
    cells += _blank_tail()
    return html.Tr(cells, className="recap-row")


def _period_label(period_type: str, period_key: str) -> str:
    if period_type == "month":
        try:
            return MONTH_LABELS[int(period_key.split("-")[1]) - 1]
        except (IndexError, ValueError):
            return period_key
    return period_key.split("-")[-1]  # "Q2"


def _period_rows(period_type: str, period_key: str,
                 rows: list[tuple[str, str]], landings_values: dict,
                 editable_groups: set[str]) -> list[html.Tr]:
    period_values = landings_values.get(period_key) or {}
    suffix = _period_label(period_type, period_key)

    out: list[html.Tr] = []
    for i, (row_type, prefix) in enumerate(rows):
        row_values = period_values.get(row_type) or {}
        act = row_type == "actual"   # gray fill like the Excel ACTUAL row
        gutter = _vlabel_td(suffix, 2 * len(rows)) if i == 0 else None
        out.append(_period_row(f"{prefix} {suffix}", period_key, row_type,
                               row_values, gutter, editable_groups, act=act))
        out.append(_period_emea_row(period_key, row_type,
                                    row_values.get("SEDICO") or {},
                                    editable_groups, act=act))
    return out


# ── toolbar ───────────────────────────────────────────────────────────────────

def _month_options(year: int) -> list[dict]:
    return [{"label": f"{MONTH_LABELS[m - 1]} {year}", "value": f"{year}-{m:02d}"}
            for m in range(1, 13)]


def _quarter_options(year: int) -> list[dict]:
    return [{"label": f"Q{q} {year}", "value": f"{year}-Q{q}"} for q in range(1, 5)]


def _toolbar(year: int, month_key: str, quarter_key: str, row2: str,
             is_admin: bool = False) -> html.Div:
    # 2nd-row toggle is a shared, admin-controlled global view. Non-admins see it
    # disabled (RadioItems has no top-level disabled → disable each option).
    # NB: currently unreachable — the whole page is admin-only, so is_admin is
    # always True here. Kept for the moment access is widened again.
    row2_label = "2nd row:" if is_admin else "🔒 2nd row:"
    row2_title = None if is_admin else "Only an admin can change this view"
    return html.Div([
        html.Span("Month:", className="landings-ctl-label"),
        dcc.Dropdown(id="landings-month-select", options=_month_options(year),
                     value=month_key, clearable=False,
                     className="landings-period-dd"),
        html.Button("Save month", id="btn-landings-month-save", n_clicks=0,
                    className="action-btn btn-save landings-save-btn"),
        html.Span("Quarter:", className="landings-ctl-label landings-ctl-gap"),
        dcc.Dropdown(id="landings-quarter-select", options=_quarter_options(year),
                     value=quarter_key, clearable=False,
                     className="landings-period-dd"),
        html.Button("Save quarter", id="btn-landings-quarter-save", n_clicks=0,
                    className="action-btn btn-save landings-save-btn"),
        # Shared 2nd-row toggle — drives both Month and Quarter blocks.
        html.Span(row2_label, className="landings-ctl-label landings-ctl-gap"),
        html.Div(dcc.RadioItems(
            id="landings-row2-select",
            options=[{"label": lbl.title(), "value": rt, "disabled": not is_admin}
                     for rt, lbl in LANDINGS_SECOND_OPTIONS],
            value=row2, className="landings-row2-radio",
            inputClassName="landings-row2-input", labelClassName="landings-row2-label",
        ), className="landings-row2-wrap" + ("" if is_admin else " is-locked"),
           title=row2_title),
    ], className="landings-toolbar")


# ── page ──────────────────────────────────────────────────────────────────────

def render_landings(week_id: int, year: int, weekly: dict, landings_values: dict,
                    month_key: str, quarter_key: str, row2: str = "actual",
                    is_admin: bool = False,
                    editable_groups: set[str] | None = None) -> html.Div:
    weekly = weekly or {}
    landings_values = landings_values or {}
    # None → full access (keeps callers that don't pass scope working); an empty
    # set means "edit nothing" (VIEW role) and must NOT be treated as full access.
    if editable_groups is None:
        editable_groups = {gid for gid, _l, _m in LANDINGS_GROUPS}

    na_kpi = weekly.get("na_kpi")
    na_text = _fmt_val(na_kpi) if na_kpi is not None else "—"
    chart_card = html.Div([
        html.Div([
            html.Div("Shipped CY vs Shipped PY (by week)",
                     className="landings-chart-title"),
            html.Div([
                html.Span("NA · Friday FRC", className="landings-na-kpi-label"),
                html.Span(na_text, className="landings-na-kpi-val"),
            ], className="landings-na-kpi", title=_NA_KPI_TITLE),
        ], className="landings-chart-head"),
        dcc.Graph(
            id="landings-chart",
            figure=_build_chart(weekly.get("chart_py", {}),
                                weekly.get("chart_cy", {}),
                                weekly.get("chart_fc", {}), week_id, year),
            config={"staticPlot": True},
        ),
    ], className="landings-chart-card")

    # One continuous table — fixed layout: narrow gutter, wide label column,
    # then each group = a narrow gap column + its 3 data columns; the data
    # columns share the remaining width equally.
    group_cols: list = []
    for _ in range(1 + len(_GROUP_IDS)):
        group_cols.append(html.Col(style={"width": "14px"}))   # inter-group gap
        group_cols += [html.Col() for _ in range(3)]
    colgroup = html.Colgroup(
        [html.Col(style={"width": "36px"}), html.Col(style={"width": "215px"})]
        + group_cols
    )

    # Business FRC (always) + the shared-selected second row (Actual|Logistics).
    period_rows = [LANDINGS_BUSINESS_ROW,
                   (row2, LANDINGS_SECOND_LABELS.get(row2, row2.upper()))]

    body_rows: list[html.Tr] = []
    body_rows += _wk_rows(week_id, weekly.get("wk", {}))
    body_rows.append(_spacer_row())
    body_rows += _period_rows("month", month_key, period_rows,
                              landings_values, editable_groups)
    body_rows.append(_spacer_row())
    body_rows += _period_rows("quarter", quarter_key, period_rows,
                              landings_values, editable_groups)

    table = html.Table([colgroup, _recap_header(editable_groups), html.Tbody(body_rows)],
                       className="recap-table")

    return html.Div([
        _toolbar(year, month_key, quarter_key, row2, is_admin),
        chart_card,
        html.Div(table, className="table-wrap"),
    ], className="landings-wrap")
