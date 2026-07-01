# components/header.py
# ─────────────────────────────────────────────────────────────────────────────
# Renders the topbar (brand + user chip) and the app-header (site selector,
# week badge, product line tabs, Save/Submit all buttons).
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
from datetime import datetime

from dash import html, dcc

from data.schema import SITES


def render_topbar(username: str = "Lorenzo Muscillo") -> html.Div:
    initials = "".join(p[0].upper() for p in username.split()[:2])
    return html.Div(className="topbar", children=[
        html.Div(className="topbar-brand", children=[
            html.Span(className="brand-dot"),
            html.Span("GLI Reporting App", className="brand-name"),
            html.Span(className="brand-sep"),
            html.Span("Volumes Data Entry Tool", className="brand-sub"),
        ]),
        html.Div(
            html.Div(className="user-chip", children=[
                html.Div(initials, className="user-avatar"),
                html.Span(username),
            ])
        ),
    ])


def _report_week(week_id: int, year: int) -> int:
    """ISO week stored in DB → displayed report week (WK = ISO WK - 1).
    Week 1 wraps back to the prior year's last ISO week (52 or 53)."""
    if week_id > 1:
        return week_id - 1
    return datetime(year - 1, 12, 28).isocalendar()[1]   # always 52/53


def render_app_header(
    current_site: str,
    current_pl: str,
    week_id: int,
    year: int,
    is_readonly: bool,
    weeks: list[dict] | None = None,
    open_week_id: int | None = None,
) -> html.Div:
    now = datetime.now()
    days   = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]   # now.weekday(): Mon=0
    months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    today_str = f"{days[now.weekday()]} {now.day} {months[now.month-1]} {now.year}"

    iso_week    = week_id
    report_week = _report_week(week_id, year)
    is_past     = open_week_id is not None and week_id != open_week_id

    # Back-selector options: open week + every past week, newest first.
    week_options = []
    for w in (weeks or []):
        wid, wyr = int(w["week_id"]), int(w["year"])
        label = f"WK {_report_week(wid, wyr)} | ISO {wid} · {wyr}"
        if open_week_id is not None and wid == open_week_id:
            label += "  (current)"
        week_options.append({"label": label, "value": f"{wyr}-{wid}"})

    header = html.Div(className="app-header", children=[
        html.Div(className="header-left", children=[

            # Site selector — dcc.Dropdown so the value reaches the callbacks
            # (a plain html.Select does not report its value to Dash).
            # Plant name only; table-level access is enforced backend-side.
            html.Div(className="field-group", children=[
                html.Div("Site", className="field-label"),
                dcc.Dropdown(
                    id="site-select",
                    options=(
                        [{"label": s, "value": s} for s in SITES]
                        + [{"label": "GLOBAL — all plants", "value": "GLOBAL"}]
                    ),
                    value=current_site,
                    clearable=False,
                    searchable=False,
                    className="site-dropdown",
                    style={"width": "240px"},
                ),
                # Week back-selector — stacked directly under the Site dropdown.
                dcc.Dropdown(
                    id="week-select",
                    options=week_options,
                    value=f"{year}-{week_id}",
                    clearable=False,
                    searchable=False,
                    className="week-dropdown",
                    style={"width": "240px", "marginTop": "6px"},
                ),
            ]),

            # Week badge + date
            html.Div(className="field-group", children=[
                html.Div("Week", className="field-label"),
                html.Div(className="week-row", children=[
                    html.Span(
                        f"WK {report_week} | ISO WK {iso_week}",
                        className="week-badge" + (" week-badge-past" if is_past else ""),
                    ),
                    html.Span(today_str, className="today-label"),
                ]),
            ]),

            # Product line tabs
            html.Div(className="field-group", children=[
                html.Div("Product line", className="field-label"),
                html.Div(className="pl-tabs", children=[
                    html.Button(
                        "Frames",
                        id="tab-frames",
                        className=f"pl-tab{'  active' if current_pl == 'FRAMES' else ''}",
                        n_clicks=0,
                    ),
                    html.Button(
                        "Wearables",
                        id="tab-wearables",
                        className=f"pl-tab{'  active' if current_pl == 'WEARABLES' else ''}",
                        n_clicks=0,
                    ),
                ]),
            ]),
        ]),

        # Action buttons (hidden in read-only mode)
        html.Div(
            id="header-actions",
            className="header-actions",
            style={"display": "none" if is_readonly else "flex"},
            children=[
                html.Button(
                    ["⤓ ", "Save all drafts"],
                    id="btn-save-all",
                    className="action-btn btn-save-all",
                    n_clicks=0,
                ),
                html.Button(
                    ["↑ ", "Submit all open"],
                    id="btn-submit-all",
                    className="action-btn btn-submit-all",
                    n_clicks=0,
                ),
            ],
        ),

        # CSV export — a read action, available also in read-only mode
        html.Div(
            className="header-export",
            children=[
                html.Button(
                    ["⤓ ", "Export CSV"],
                    id="btn-export-csv",
                    className="action-btn btn-save-all",
                    n_clicks=0,
                ),
                html.Button(
                    ["⚠ ", "Double Tap ", "🥤"],
                    id="btn-refresh-cache",
                    className="action-btn btn-danger",
                    title="Refresh the server cache (clears cache.py memo).",
                    n_clicks=0,
                ),
            ],
        ),
    ])

    # Read-only banner
    banner = html.Div(
        id="readonly-banner",
        className="readonly-banner",
        style={"display": "flex" if is_readonly else "none"},
        children=[
            "👁 You are viewing ",
            html.Strong(current_site),
            " in read-only mode. Switch to your own site to enter data.",
        ],
    )

    # Past-week banner — shown when editing an earlier (non-open) week.
    past_banner = html.Div(
        id="pastweek-banner",
        className="pastweek-banner",
        style={"display": "flex" if (is_past and not is_readonly) else "none"},
        children=[
            "⏱ You are editing ",
            html.Strong(f"WK {report_week} | ISO WK {iso_week}"),
            " — a past week. Submissions will be flagged as delayed (delay = TRUE).",
        ],
    )

    return html.Div([header, banner, past_banner])
