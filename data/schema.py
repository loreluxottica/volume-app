# data/schema.py
# ─────────────────────────────────────────────────────────────────────────────
# Central definitions for columns, rows, NA matrix and deadline schedule.
# All other modules import from here — never hardcode these elsewhere.
# ─────────────────────────────────────────────────────────────────────────────

COLS_FRAMES = [
    {"id": "inbound",     "label": "Inbound"},
    {"id": "rop_std",     "label": "ROP STD"},
    {"id": "rop_samples", "label": "ROP Smp"},
    {"id": "whls_gross",  "label": "WHLS Gross"},
    {"id": "whls_net",    "label": "WHLS Net"},
    {"id": "retail",      "label": "Retail"},
    {"id": "gvi",         "label": "GVI"},
    {"id": "ds_na",       "label": "DS NA"},
    {"id": "ecom",        "label": "eCom"},
    {"id": "sample",      "label": "Sample"},
]

# Wearables uses one global column set. `repl_el` and `meta` are only used by
# DONGGUAN — for every other plant they are marked N/A in the matrix below.
COLS_WEARABLES = [
    {"id": "inb_gtk",    "label": "Inb GTK"},
    {"id": "inb_tri",    "label": "Inb Tri"},
    {"id": "rop_labs",   "label": "ROP/Labs"},
    {"id": "whls_gross", "label": "WHLS Gross"},
    {"id": "whls_net",   "label": "WHLS Net"},
    {"id": "retail",     "label": "Retail"},
    {"id": "ds_na",      "label": "DS NA"},
    {"id": "ecom",       "label": "eCom"},
    {"id": "dummy",      "label": "Dummy"},
    {"id": "repl_el",    "label": "REPL EL"},
    {"id": "meta",       "label": "Meta"},
]

COLS_BY_PL = {
    "FRAMES":    COLS_FRAMES,
    "WEARABLES": COLS_WEARABLES,
}

# DONGGUAN Wearables splits the single `dummy` column into two sub-channels:
# Dummy Repl El + Dummy Local. Same column set otherwise (same order, dummy
# replaced in place). In the GLOBAL summed view the two fold back into `dummy`.
COLS_WEARABLES_DONGGUAN = [
    {"id": "inb_gtk",       "label": "Inb GTK"},
    {"id": "inb_tri",       "label": "Inb Tri"},
    {"id": "rop_labs",      "label": "ROP/Labs"},
    {"id": "whls_gross",    "label": "WHLS Gross"},
    {"id": "whls_net",      "label": "WHLS Net"},
    {"id": "retail",        "label": "Retail"},
    {"id": "ds_na",         "label": "DS NA"},
    {"id": "ecom",          "label": "eCom"},
    {"id": "dummy_repl_el", "label": "Dummy Repl El"},
    {"id": "dummy_local",   "label": "Dummy Local"},
    {"id": "repl_el",       "label": "REPL EL"},
    {"id": "meta",          "label": "Meta"},
]

# Dummy sub-channels fold into this parent column for the GLOBAL sum.
DUMMY_SUBCOLS = ("dummy_repl_el", "dummy_local")
DUMMY_PARENT  = "dummy"


def cols_for(site: str, pl: str) -> list[dict]:
    """Columns for a site + product line. DONGGUAN Wearables splits Dummy into
    Dummy Repl El + Dummy Local; every other view uses the shared set."""
    if pl == "WEARABLES" and site == "DONGGUAN":
        return COLS_WEARABLES_DONGGUAN
    return COLS_BY_PL[pl]

# Rows shared across both product lines
ROWS = [
    {"id": "py",      "label": "PY",           "is_ref": False, "is_fri": False},
    {"id": "siop",    "label": "SIOP",          "is_ref": False, "is_fri": False},
    {"id": "mon_frc", "label": "Monday FRC",    "is_ref": False, "is_fri": False},
    {"id": "thu_frc", "label": "Thursday FRC",  "is_ref": False, "is_fri": False},
    {"id": "fri_frc", "label": "Friday FRC",    "is_ref": False, "is_fri": True},
    {"id": "actual",  "label": "Actual",        "is_ref": False, "is_fri": False},
    {"id": "eow_wip", "label": "EoW WIP",       "is_ref": False, "is_fri": False},
    {"id": "wip_ot",  "label": "WIP OT %",      "is_ref": False, "is_fri": False},
]

ROW_IDS   = [r["id"] for r in ROWS]
ROW_BY_ID = {r["id"]: r for r in ROWS}

# ── N/A matrix ────────────────────────────────────────────────────────────────
# Columns not applicable for a given (site, product line, row). N/A cells are
# locked in the UI and stored as NULL in the DB. The matrix is per plant: a
# column can be N/A for every row of one plant yet active in another.
# Edit these dicts directly to change which cells are locked.

NA_FRAMES_BY_SITE: dict[str, dict[str, list[str]]] = {
    "SEDICO": {
        "py":      ["rop_samples"],
        "siop":    ["rop_samples", "whls_net"],
        "mon_frc": ["rop_samples"],
        "thu_frc": ["rop_samples"],
        "fri_frc": ["rop_samples"],
        "actual":  ["rop_samples"],
        "eow_wip": ["rop_samples", "whls_net"],
        "wip_ot":  ["rop_samples", "whls_net"],
    },
    "ATLANTA": {
        "py":      ["rop_samples", "gvi", "ds_na", "sample"],
        "siop":    ["rop_samples", "whls_net", "gvi", "ds_na", "sample"],
        "mon_frc": ["rop_samples", "gvi", "ds_na", "sample"],
        "thu_frc": ["rop_samples", "gvi", "ds_na", "sample"],
        "fri_frc": ["rop_samples", "gvi", "ds_na", "sample"],
        "actual":  ["rop_samples", "gvi", "ds_na", "sample"],
        "eow_wip": ["rop_samples", "whls_net", "gvi", "ds_na", "sample"],
        "wip_ot":  ["rop_samples", "whls_net", "gvi", "ds_na", "sample"],
    },
    "TIJUANA": {
        "py":      ["rop_std","rop_samples", "gvi", "ds_na", "sample"],
        "siop":    ["rop_std","rop_samples", "whls_net", "gvi", "ds_na", "sample"],
        "mon_frc": ["rop_std","rop_samples", "gvi", "ds_na", "sample"],
        "thu_frc": ["rop_std","rop_samples", "gvi", "ds_na", "sample"],
        "fri_frc": ["rop_std","rop_samples", "gvi", "ds_na", "sample"],
        "actual":  ["rop_std","rop_samples", "gvi", "ds_na", "sample"],
        "eow_wip": ["rop_std","rop_samples", "whls_net", "gvi", "ds_na", "sample"],
        "wip_ot":  ["rop_std","rop_samples", "whls_net", "gvi", "ds_na", "sample"],
    },
    "DONGGUAN": {
        "py":      ["sample"],
        "siop":    ["whls_net", "sample"],
        "mon_frc": ["sample"],
        "thu_frc": ["sample"],
        "fri_frc": ["sample"],
        "actual":  ["sample"],
        "eow_wip": ["whls_net", "sample"],
        "wip_ot":  ["whls_net", "sample"],
    },
    "RAYONG": {
        "py":      ["whls_gross", "whls_net", "retail", "gvi", "ecom", "sample"],
        "siop":    ["whls_gross", "whls_net", "retail", "gvi", "ecom", "sample"],
        "mon_frc": ["whls_gross", "whls_net", "retail", "gvi", "ecom", "sample"],
        "thu_frc": ["whls_gross", "whls_net", "retail", "gvi", "ecom", "sample"],
        "fri_frc": ["whls_gross", "whls_net", "retail", "gvi", "ecom", "sample"],
        "actual":  ["whls_gross", "whls_net", "retail", "gvi", "ecom", "sample"],
        "eow_wip": ["whls_gross", "whls_net", "retail", "gvi", "ecom", "sample"],
        "wip_ot":  ["whls_gross", "whls_net", "retail", "gvi", "ecom", "sample"],
    },
    "SUMARE": {
        "py":      ["ds_na", "sample"],
        "siop":    ["gvi", "ds_na", "sample"],
        "mon_frc": ["ds_na", "sample"],
        "thu_frc": ["ds_na", "sample"],
        "fri_frc": ["ds_na", "sample"],
        "actual":  ["ds_na", "sample"],
        "eow_wip": ["whls_net", "ds_na", "sample"],
        "wip_ot":  ["ds_na", "sample"],
    },
}

# Wearables: `repl_el` and `meta` are N/A everywhere except DONGGUAN. A row
# listing all 11 columns means the plant does not enter Wearables for that row.
NA_WEARABLES_BY_SITE: dict[str, dict[str, list[str]]] = {
    "SEDICO": {
        "py":      ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "retail", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "siop":    ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "ds_na", "repl_el", "meta"],
        "mon_frc": ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "ds_na", "repl_el", "meta"],
        "thu_frc": ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "ds_na", "repl_el", "meta"],
        "fri_frc": ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "ds_na", "repl_el", "meta"],
        "actual":  ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "ds_na", "repl_el", "meta"],
        "eow_wip": ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "ds_na", "repl_el", "meta"],
        "wip_ot":  ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "ds_na", "repl_el", "meta"],
    },
    "ATLANTA": {
        "py":      ["inb_gtk", "inb_tri", "whls_gross", "ds_na", "repl_el", "meta"],
        "siop":    ["inb_gtk", "inb_tri", "whls_gross", "ds_na", "repl_el", "meta"],
        "mon_frc": ["inb_gtk", "inb_tri", "whls_gross", "ds_na", "repl_el", "meta"],
        "thu_frc": ["inb_gtk", "inb_tri", "whls_gross", "ds_na", "repl_el", "meta"],
        "fri_frc": ["inb_gtk", "inb_tri", "whls_gross", "ds_na", "repl_el", "meta"],
        "actual":  ["inb_gtk", "inb_tri", "whls_gross", "ds_na", "repl_el", "meta"],
        "eow_wip": ["inb_gtk", "inb_tri", "whls_gross", "ds_na", "repl_el", "meta"],
        "wip_ot":  ["inb_gtk", "inb_tri", "whls_gross", "ds_na", "repl_el", "meta"],
    },
    "TIJUANA": {
        "py":      ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "siop":    ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "mon_frc": ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "thu_frc": ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "fri_frc": ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "actual":  ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "eow_wip": ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "wip_ot":  ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "ds_na", "ecom", "dummy", "repl_el", "meta"],
    },
    "DONGGUAN": {
        "py":      ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "retail", "ds_na", "ecom", "dummy_repl_el", "dummy_local", "repl_el", "meta"],
        "siop":    ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "retail", "ds_na", "ecom", "dummy_repl_el", "dummy_local", "repl_el", "meta"],
        "mon_frc": ["rop_labs", "whls_gross"],
        "thu_frc": ["rop_labs", "whls_gross"],
        "fri_frc": ["rop_labs", "whls_gross"],
        "actual":  ["rop_labs", "whls_gross"],
        "eow_wip": ["rop_labs", "whls_gross"],
        "wip_ot":  ["rop_labs", "whls_gross"],
    },
    "RAYONG": {
        "py":      ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "retail", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "siop":    ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "retail", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "mon_frc": ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "retail", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "thu_frc": ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "retail", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "fri_frc": ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "retail", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "actual":  ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "retail", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "eow_wip": ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "retail", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "wip_ot":  ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "retail", "ds_na", "ecom", "dummy", "repl_el", "meta"],
    },
    "SUMARE": {
        "py":      ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "retail", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "siop":    ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "retail", "ds_na", "ecom", "dummy", "repl_el", "meta"],
        "mon_frc": ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "ds_na", "repl_el", "meta"],
        "thu_frc": ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "ds_na", "repl_el", "meta"],
        "fri_frc": ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "ds_na", "repl_el", "meta"],
        "actual":  ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "ds_na", "repl_el", "meta"],
        "eow_wip": ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "whls_net", "ds_na", "repl_el", "meta"],
        "wip_ot":  ["inb_gtk", "inb_tri", "rop_labs", "whls_gross", "ds_na", "repl_el", "meta"],
    },
}

NA_BY_SITE: dict[str, dict[str, dict[str, list[str]]]] = {
    site: {
        "FRAMES":    NA_FRAMES_BY_SITE[site],
        "WEARABLES": NA_WEARABLES_BY_SITE[site],
    }
    for site in NA_FRAMES_BY_SITE
}


# GLOBAL — read-only view summing every plant cell by cell. It has its own
# N/A matrix (not derived from the plants): in Frames only `whls_net` is N/A,
# for siop / eow_wip / wip_ot; Wearables has no N/A cell.
NA_GLOBAL_FRAMES: dict[str, list[str]] = {
    "py":      [],
    "siop":    ["whls_net"],
    "mon_frc": [],
    "thu_frc": [],
    "fri_frc": [],
    "actual":  [],
    "eow_wip": ["whls_net"],
    "wip_ot":  ["whls_net"],
}
NA_GLOBAL_WEARABLES: dict[str, list[str]] = {
    "py":      [],
    "siop":    [],
    "mon_frc": [],
    "thu_frc": [],
    "fri_frc": [],
    "actual":  [],
    "eow_wip": [],
    "wip_ot":  [],
}
NA_BY_SITE["GLOBAL"] = {
    "FRAMES":    NA_GLOBAL_FRAMES,
    "WEARABLES": NA_GLOBAL_WEARABLES,
}


def na_matrix(site: str, pl: str) -> dict[str, list[str]]:
    """N/A columns per row for a given site + product line ({row_id: [col_id]})."""
    return NA_BY_SITE.get(site, {}).get(pl, {})


# Deadline schedule — local time per site, per submission type
DEADLINES: dict[str, dict[str, str]] = {
    "SEDICO":   {"py": "Thu 11:00", "siop": "Thu 11:00", "mon_frc": "Thu 11:00", "thu_frc": "Thu 11:00", "fri_frc": "Fri 15:00", "actual": "Next Mon 16:00", "eow_wip": "Next Mon 16:00", "wip_ot": "Next Mon 16:00"},
    "ATLANTA":  {"py": "Wed EOD",   "siop": "Wed EOD",   "mon_frc": "Wed EOD",   "thu_frc": "Wed EOD",   "fri_frc": "Fri 09:00", "actual": "Next Mon 10:00", "eow_wip": "Next Mon 10:00", "wip_ot": "Next Mon 10:00"},
    "TIJUANA":  {"py": "Wed EOD",   "siop": "Wed EOD",   "mon_frc": "Wed EOD",   "thu_frc": "Wed EOD",   "fri_frc": "Fri 09:00", "actual": "Next Mon 10:00", "eow_wip": "Next Mon 10:00", "wip_ot": "Next Mon 10:00"},
    "DONGGUAN": {"py": "Thu 18:00", "siop": "Thu 18:00", "mon_frc": "Thu 18:00", "thu_frc": "Thu 18:00", "fri_frc": "Fri EOD",   "actual": "Next Mon 18:00", "eow_wip": "Next Mon 18:00", "wip_ot": "Next Mon 18:00"},
    "RAYONG":   {"py": "Thu 18:00", "siop": "Thu 18:00", "mon_frc": "Thu 18:00", "thu_frc": "Thu 18:00", "fri_frc": "Fri EOD",   "actual": "Next Mon 18:00", "eow_wip": "Next Mon 18:00", "wip_ot": "Next Mon 18:00"},
    "SUMARE":   {"py": "Wed EOD",   "siop": "Wed EOD",   "mon_frc": "Wed EOD",   "thu_frc": "Wed EOD",   "fri_frc": "Fri 10:00", "actual": "Next Mon 11:00", "eow_wip": "Next Mon 11:00", "wip_ot": "Next Mon 11:00"},
}

# Per-(site, product line) deadline overrides — applied on top of DEADLINES.
DEADLINE_OVERRIDES: dict[tuple[str, str], dict[str, str]] = {
    ("SEDICO", "WEARABLES"): {"mon_frc": "Tue 18:00"},
}


def deadlines_for(site: str, pl: str) -> dict[str, str]:
    """Deadline schedule for a site, with product-line overrides merged in."""
    d = dict(DEADLINES.get(site, {}))
    d.update(DEADLINE_OVERRIDES.get((site, pl), {}))
    return d


SITES = list(DEADLINES.keys())

SITE_OWNERS = {
    "SEDICO":   "Ongaro",
    "ATLANTA":  "Brittany",
    "TIJUANA":  "Brittany",
    "DONGGUAN": "Li Niu",
    "RAYONG":   "Jirawat",
    "SUMARE":   "Eduarda",
}

# Comment pre-sets for Friday FRC variance
COMMENT_PRESETS = [
    {"id": "logistics",      "label": "Logistic",            "color": "#0e7c9b", "icon": "dot"},
    {"id": "orders",         "label": "Orders",              "color": "#e573b7", "icon": "dot"},
    {"id": "stock",          "label": "Stock availability",  "color": "#b4b2a9", "icon": "dot"},
    {"id": "prioritization", "label": "Prioritization WHLS", "color": "#000000", "icon": "star"},
    {"id": "force_majeure",  "label": "Force Majeure",       "color": "#000000", "icon": "dot"},
]

# Comment pre-sets for WIP OT below threshold
COMMENT_PRESETS_WIP_OT = [
    {"id": "production",  "label": "Production delays"},
    {"id": "machine",     "label": "Machine breakdown"},
    {"id": "material",    "label": "Material shortage"},
    {"id": "staffing",    "label": "Staffing / capacity"},
]

# WIP OT threshold: comment mandatory when value <= this percentage
WIP_OT_THRESHOLD = 90


# Threshold rule: comment mandatory if abs diff >= THRESHOLD_ABS OR relative diff >= THRESHOLD_REL
THRESHOLD_ABS = 10      # Kpcs
THRESHOLD_REL = 0.10    # 10%


def cols_below_threshold(fri_values: dict, mon_values: dict,
                         na_cols: list[str], cols: list[dict]) -> list[str]:
    """
    Column ids where fri_frc drops vs mon_frc by >= 10 Kpcs or >= 10%.
    Shared by data_table rendering and submit-time validation — single source.
    """
    result: list[str] = []
    for col in cols:
        cid = col["id"]
        if cid in na_cols:
            continue
        fri_raw = fri_values.get(cid, "")
        mon_raw = mon_values.get(cid, "")
        if fri_raw == "" or mon_raw == "":
            continue
        try:
            fri = float(fri_raw)
            mon = float(mon_raw)
        except (TypeError, ValueError):
            continue
        diff = mon - fri
        if diff >= THRESHOLD_ABS or (mon > 0 and diff / mon >= THRESHOLD_REL):
            result.append(cid)
    return result


def incomplete_cells(values: dict, zero_flags: dict, na_cols: list[str],
                     cols: list[dict]) -> list[str]:
    """
    Column ids that are applicable (non-N/A), not zero-flagged, and left blank.
    A blank applicable cell blocks submission for the panel rows (BBP §6.4).
    Shared by panel rendering and submit-time validation — single source.
    """
    result: list[str] = []
    for col in cols:
        cid = col["id"]
        if cid in na_cols:
            continue
        if zero_flags.get(cid, False):
            continue
        raw = values.get(cid, "")
        if raw is None or str(raw).strip() == "":
            result.append(cid)
    return result


def _is_zero_value(raw) -> bool:
    """True if raw parses to numeric 0 (e.g. '0', '0.0', 0, 0.0). Blank/non-numeric = False."""
    if raw is None:
        return False
    s = str(raw).strip()
    if s == "":
        return False
    try:
        return float(s) == 0.0
    except (TypeError, ValueError):
        return False


def zero_cells_missing_comment(values: dict, zero_flags: dict, comments: dict,
                               na_cols: list[str], cols: list[dict],
                               flag_is_sufficient: bool = False) -> list[str]:
    """
    Column ids that are zero (flag OR typed 0) and have no preset/'others' text.
    A zero entry must be justified — same rule used today for below-threshold cells.
    Shared by panel rendering and submit-time validation — single source.

    flag_is_sufficient=True (shipment FRC panels): a confirm-zero flag alone is
    enough ("No shipments") and never counts as missing; only a typed-0 without
    the flag still needs a comment. WIP OT keeps the stricter default.
    """
    result: list[str] = []
    for col in cols:
        cid = col["id"]
        if cid in na_cols:
            continue
        if flag_is_sufficient and zero_flags.get(cid, False):
            # Confirm-zero flag is sufficient justification ("No shipments");
            # a free-text comment is optional.
            continue
        if not (zero_flags.get(cid, False) or _is_zero_value(values.get(cid))):
            continue
        c = comments.get(cid) or {}
        if c.get("presets") or (c.get("others") or "").strip():
            continue
        result.append(cid)
    return result


def wip_ot_below_threshold(values: dict, na_cols: list[str],
                            cols: list[dict]) -> list[str]:
    """Column ids where WIP OT value <= WIP_OT_THRESHOLD (comment mandatory)."""
    result: list[str] = []
    for col in cols:
        cid = col["id"]
        if cid in na_cols:
            continue
        raw = values.get(cid, "")
        if raw == "":
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v <= WIP_OT_THRESHOLD:
            result.append(cid)
    return result
