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


def render_app_header(
    current_site: str,
    current_pl: str,
    week_id: int,
    year: int,
    is_readonly: bool,
) -> html.Div:
    now = datetime.now()
    days   = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"]
    months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    today_str = f"{days[now.weekday()]} {now.day} {months[now.month-1]} {now.year}"

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
            ]),

            # Week badge
            html.Div(className="field-group", children=[
                html.Div("Current week", className="field-label"),
                html.Div(className="week-row", children=[
                    html.Span(f"WK {week_id} | ISO WK {week_id}", className="week-badge"),
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

    return html.Div([header, banner])
