#!/usr/bin/env python3
"""Unified ChampSim log summarizer – 183-column full schema.

Handles both normal ChampSim and WP ChampSim log formats automatically.
Implements SUMMARY_SPEC.md §16 (full column catalog).

Outputs:
  full_metrics.csv   – all 183 parsed metrics
  summary.csv        – key metrics for daily review
  parse_errors.csv   – files that could not be parsed
  normalized_ipc.csv – IPC normalized to baseline (legacy)
  *.svg / *.png      – charts (legacy, requires matplotlib)
"""

import re
import glob
import os
import math
import csv
import argparse
from collections import defaultdict


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Unified ChampSim log summarizer (normal + WP ChampSim formats)"
    )
    p.add_argument("--glob", default="*.txt", help="Glob pattern for log files")
    p.add_argument("--outdir", default="summary_out", help="Output directory")
    p.add_argument("--baseline", default="latest", help="Label used as normalization baseline")
    p.add_argument(
        "--label-map",
        default="resche2:schedcost_on,resche_:schedcost_off,ChampSim:latest",
        help="Comma-separated substring:label mapping (first match wins)",
    )
    p.add_argument(
        "--img-formats",
        default="svg",
        help="Comma-separated image formats (e.g. 'svg' or 'png,svg'). Default: svg",
    )
    return p.parse_args()


def build_label_map(arg: str):
    out = []
    for pair in arg.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" in pair:
            k, v = pair.split(":", 1)
            out.append((k, v))
        else:
            out.append((pair, pair))
    return out


# ── Format / mode detection (spec §4) ─────────────────────────────────────────
# wp_capable: any WRONG-PATH stats line (always in WP binary, even WP OFF)
# normal:     cpu0->cpu0_ prefix present
_WP_SIG   = re.compile(r"^(?:cpu0_\w+|LLC) WRONG-PATH\s+ACCESS:", re.MULTILINE)
_NORM_SIG = re.compile(r"cpu0->cpu0_")
_WP_MODE  = re.compile(r"Wrong path enabled")

# ── ROI (last occurrence, spec §5) ────────────────────────────────────────────
ROI_RE = re.compile(
    r"CPU\s+\d+\s+cumulative IPC:\s*([\d.]+)\s+"
    r"instructions:\s*(\d+)\s+cycles:\s*(\d+)"
    r"(?:\s+wp_cycles:\s*(\d+))?"
)

# ── Branch ────────────────────────────────────────────────────────────────────
BR_RE    = re.compile(r"Branch Prediction Accuracy:\s*([\d.]+)%\s*MPKI:\s*([\d.]+)")
BR_DJ_RE = re.compile(r"BRANCH_DIRECT_JUMP:\s*([\d.]+)")
BR_I_RE  = re.compile(r"BRANCH_INDIRECT:\s*([\d.]+)")
BR_C_RE  = re.compile(r"BRANCH_CONDITIONAL:\s*([\d.]+)")
BR_DC_RE = re.compile(r"BRANCH_DIRECT_CALL:\s*([\d.]+)")
BR_IC_RE = re.compile(r"BRANCH_INDIRECT_CALL:\s*([\d.]+)")
BR_R_RE  = re.compile(r"BRANCH_RETURN:\s*([\d.]+)")

# ── G2 WP insts (WP binary only, present even when WP OFF) ───────────────────
WP_INSTS_RE = re.compile(
    r"wrong_path_insts:\s*(\d+)\s+wrong_path_insts_skipped:\s*(\d+)"
    r"\s+wrong_path_insts_executed:\s*(\d+)"
)
FOOTPRINT_RE = re.compile(r"instr_foot_print:\s*(\d+)\s+data_foot_print:\s*(\d+)")
ISPF_RE      = re.compile(r"is_prefetch_insts:\s*(\d+)\s+is_prefetch_skipped:\s*(\d+)")

# ── G4 Pipeline / Execute (WP binary only) ────────────────────────────────────
EXEC_WP_CYC_RE  = re.compile(r"Execute Only WP Cycles\s+(\d+)")
EXEC_CP_CYC_RE  = re.compile(r"Execute Only CP Cycles\s+(\d+)")
EXEC_CPWP_RE    = re.compile(r"Execute CP WP Cycles\s+(\d+)")
ROB_FULL_CYC_RE = re.compile(r"ROB Full Cycles\s+(\d+)")
ROB_EMPTY_CYC_RE= re.compile(r"ROB Empty Cycles\s+(\d+)")
ROB_FULL_EVT_RE = re.compile(r"ROB Full Events\s+(\d+)")
ROB_EMPTY_EVT_RE= re.compile(r"ROB Empty Events\s+(\d+)")
RESTEER_EVT_RE  = re.compile(r"Resteer Events\s+(\d+)")
RESTEER_PCT_RE  = re.compile(r"Resteer Penalty\s+([\d.]+)%")
WP_NA_PCT_RE    = re.compile(r"WP Not Available Count\s+\d+\s+Cycles\s+\d+\s+\(([\d.]+)%\)")

# ── G7 DRAM ───────────────────────────────────────────────────────────────────
# ROW_BUFFER_MISS is on the next indented line → \s+ crosses the newline
DRAM_RE = re.compile(
    r"Channel 0 RQ ROW_BUFFER_HIT:\s*(\d+)\s+ROW_BUFFER_MISS:\s*(\d+)"
)

# ── Column schema (SUMMARY_SPEC.md §16) ───────────────────────────────────────
# 29 fields per cache level (l1d/l1i/l2c/llc)
_CACHE_FIELDS = [
    "load_access", "load_hit", "load_miss", "load_mpki",
    "pf_access", "pf_hit", "pf_miss",
    "pf_requested", "pf_issued", "pf_useful", "pf_useless",
    "wp_access", "wp_useful", "wp_fill", "wp_useless",
    "pollution", "pol_wp_fill", "pol_wp_miss", "pol_cp_fill", "pol_cp_miss",
    "data_req", "data_hit", "data_miss", "data_wp_req", "data_wp_hit", "data_wp_miss",
    "miss_lat", "wp_miss_lat", "cp_miss_lat",
]

# 10 fields per TLB (dtlb/itlb/stlb)
_TLB_FIELDS = [
    "access", "hit", "miss", "mpki",
    "wp_access", "wp_useful", "wp_useless",
    "miss_lat", "wp_miss_lat", "cp_miss_lat",
]

FULL_FIELDNAMES = (
    # G1 Identifiers (6)
    ["bench", "config", "file", "log_format", "wp_mode", "parse_warnings"]
    # G2 ROI core + WP insts (11)
    + ["cycles", "inst", "ipc", "wp_cycles",
       "wp_insts_total", "wp_insts_skipped", "wp_insts_executed",
       "instr_footprint", "data_footprint", "is_prefetch_insts", "is_prefetch_skipped"]
    # G3 Branch (8)
    + ["branch_acc_percent", "branch_mpki",
       "br_direct_jump_mpki", "br_indirect_mpki", "br_conditional_mpki",
       "br_direct_call_mpki", "br_indirect_call_mpki", "br_return_mpki"]
    # G4 Pipeline / Execute stats (10, WP binary only)
    + ["exec_only_wp_cycles", "exec_only_cp_cycles", "exec_cp_wp_cycles",
       "rob_full_cycles", "rob_empty_cycles", "rob_full_events", "rob_empty_events",
       "resteer_events", "resteer_penalty_pct", "wp_not_avail_cycles_pct"]
    # G5 Cache × 4 levels (29 × 4 = 116)
    + [f"{lv}_{f}" for lv in ["l1d", "l1i", "l2c", "llc"] for f in _CACHE_FIELDS]
    # G6 TLB × 3 levels (10 × 3 = 30)
    + [f"{tlv}_{f}" for tlv in ["dtlb", "itlb", "stlb"] for f in _TLB_FIELDS]
    # G7 DRAM (2)
    + ["dram_rq_row_hit", "dram_rq_row_miss"]
)

assert len(FULL_FIELDNAMES) == 183, f"Expected 183 columns, got {len(FULL_FIELDNAMES)}"

SUMMARY_FIELDNAMES = [
    # Identifiers
    "bench", "config", "log_format", "wp_mode", "parse_warnings",
    # ROI
    "cycles", "wp_cycles", "inst", "ipc",
    # Branch
    "branch_mpki",
    # LLC
    "llc_load_miss", "llc_load_mpki", "llc_miss_lat",
    "llc_pf_useful", "llc_pf_useless",
    # WP-specific (empty for normal format)
    "llc_wp_access", "llc_wp_useful",
    "llc_pol_cp_miss",
    "l2c_pf_useful", "l2c_pf_useless",
    "l2c_pollution",
]

ERROR_FIELDNAMES = ["file", "bench", "config", "error_code", "detail"]


# ── Prefix mapping ─────────────────────────────────────────────────────────────

def _cache_prefix(lv, fmt):
    """Return the log line prefix for a given cache level and format."""
    if fmt == "normal":
        return {"l1d": "cpu0->cpu0_L1D", "l1i": "cpu0->cpu0_L1I",
                "l2c": "cpu0->cpu0_L2C", "llc": "cpu0->LLC"}[lv]
    else:  # wp_capable
        return {"l1d": "cpu0_L1D", "l1i": "cpu0_L1I",
                "l2c": "cpu0_L2C", "llc": "LLC"}[lv]


def _tlb_prefix(tlv, fmt):
    """Return the log line prefix for a given TLB level and format."""
    if fmt == "normal":
        return {"dtlb": "cpu0->cpu0_DTLB", "itlb": "cpu0->cpu0_ITLB",
                "stlb": "cpu0->cpu0_STLB"}[tlv]
    else:
        return {"dtlb": "cpu0_DTLB", "itlb": "cpu0_ITLB",
                "stlb": "cpu0_STLB"}[tlv]


# ── Helpers ───────────────────────────────────────────────────────────────────

def label_from_name(fname, label_map):
    base = os.path.basename(fname)
    for key, lab in label_map:
        if key and (key in fname or key in base):
            return lab
    return "unknown"


def bench_from_name(fname):
    base = os.path.splitext(os.path.basename(fname))[0]
    base = re.sub(r"_ChampSim.*$", "", base)   # strip binary/job suffix
    base = re.sub(r"^\d+_", "", base)           # strip leading index "00_", "12_"
    base = re.sub(r"\.gz$", "", base)           # strip .gz extension
    return base


def safe_float(s):
    """Parse float; return None for NaN, inf, '-', or unparseable values."""
    try:
        v = float(s)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def last_roi(text):
    """Return the last ROI match (spec §5: use last occurrence)."""
    m = None
    for m in ROI_RE.finditer(text):
        pass
    return m


def mpki_val(miss, inst):
    if miss is None or inst is None or inst == 0:
        return None
    return miss * 1000.0 / inst


def geomean(lst):
    vals = [v for v in lst if v and v > 0]
    if not vals:
        return None
    return math.exp(sum(math.log(x) for x in vals) / len(vals))


def fmt(v, field=""):
    """Format a cell value for CSV output (None → empty string).

    Precision rules:
      *_lat, *_miss_lat  → 4 sig figs (g format)
      *_mpki             → 4 decimal places
      *_percent, *_pct   → 2 decimal places
      ipc                → 6 decimal places
      other floats       → 4 decimal places
    """
    if v is None:
        return ""
    if isinstance(v, float):
        if "_lat" in field:
            return f"{v:.4g}"
        if "_mpki" in field:
            return f"{v:.4f}"
        if "_percent" in field or "_pct" in field:
            return f"{v:.2f}"
        if field == "ipc":
            return f"{v:.6f}"
        return f"{v:.4f}"
    return str(v)


def _getint(m, grp):
    """Safely extract an integer group from a match object."""
    try:
        return int(m.group(grp)) if m else None
    except (IndexError, TypeError):
        return None


def _getfloat(m, grp):
    """Safely extract a safe_float group from a match object."""
    try:
        return safe_float(m.group(grp)) if m else None
    except (IndexError, TypeError):
        return None


# ── Per-level cache parser (returns 29-field dict) ────────────────────────────

def parse_cache_level(text, lv, fmt_str, wp_on, inst):
    """
    Parse all 29 cache-level fields for one level.
    lv:     column prefix (e.g. 'l1d', 'llc')
    fmt_str: 'normal' or 'wp_capable'
    wp_on:  True if wp_mode == 'on'
    inst:   ROI instruction count (for MPKI)
    """
    pfx = _cache_prefix(lv, fmt_str)
    ep  = re.escape(pfx)

    def S(pat, flags=re.MULTILINE):
        return re.search(r"^" + ep + r" " + pat, text, flags)

    # LOAD
    m = S(r"LOAD\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)")
    load_access = _getint(m, 1)
    load_hit    = _getint(m, 2)
    load_miss   = _getint(m, 3)

    # PREFETCH ACCESS
    m = S(r"PREFETCH\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)")
    pf_access = _getint(m, 1)
    pf_hit    = _getint(m, 2)
    pf_miss   = _getint(m, 3)

    # PREFETCH REQUESTED
    m = S(r"PREFETCH REQUESTED:\s*(\d+)\s+ISSUED:\s*(\d+)\s+USEFUL:\s*(\d+)\s+USELESS:\s*(\d+)")
    pf_requested = _getint(m, 1)
    pf_issued    = _getint(m, 2)
    pf_useful    = _getint(m, 3)
    pf_useless   = _getint(m, 4)

    # Miss latency
    if fmt_str == "normal":
        m = S(r"AVERAGE MISS LATENCY:\s*([\S]+) cycles")
    else:
        m = S(r"AVERAGE DATA MISS LATENCY:\s*([\S]+) cycles")
    miss_lat = _getfloat(m, 1)

    # WP-capable-only fields
    if fmt_str == "wp_capable":
        # WRONG-PATH
        m = S(r"WRONG-PATH ACCESS:\s*(\d+)\s+LOAD:\s*\d+\s+USEFULL:\s*(\d+)"
              r"\s+FILL:\s*(\d+)\s+USELESS:\s*(\d+)")
        wp_access  = _getint(m, 1)
        wp_useful  = _getint(m, 2)
        wp_fill    = _getint(m, 3)
        wp_useless = _getint(m, 4)

        # POLLUTION
        m = S(r"POLLUTION:\s*([\S]+)\s+WP_FILL:\s*(\d+)\s+WP_MISS:\s*(\d+)"
              r"\s+CP_FILL:\s*(\d+)\s+CP_MISS:\s*(\d+)")
        pollution   = _getfloat(m, 1)
        pol_wp_fill = _getint(m, 2)
        pol_wp_miss = _getint(m, 3)
        pol_cp_fill = _getint(m, 4)
        pol_cp_miss = _getint(m, 5)

        # DATA REQ
        m = S(r"DATA REQ:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)"
              r"\s+WP_REQ:\s*(\d+)\s+WP_HIT:\s*(\d+)\s+WP_MISS:\s*(\d+)")
        data_req    = _getint(m, 1)
        data_hit    = _getint(m, 2)
        data_miss   = _getint(m, 3)
        data_wp_req = _getint(m, 4)
        data_wp_hit = _getint(m, 5)
        data_wp_miss= _getint(m, 6)

        # WP/CP latency
        m = S(r"AVERAGE WP DATA MISS LATENCY:\s*([\S]+) cycles")
        wp_miss_lat = _getfloat(m, 1)
        m = S(r"AVERAGE CP DATA MISS LATENCY:\s*([\S]+) cycles")
        cp_miss_lat = _getfloat(m, 1)

        # Suppress WP-activity fields when WP is off (spec §16)
        # pollution ratio is 0/0 = undefined when WP OFF → blank
        if not wp_on:
            wp_access = wp_useful = wp_fill = wp_useless = None
            pollution = pol_wp_fill = pol_wp_miss = None
            data_wp_req = data_wp_hit = data_wp_miss = None
    else:
        wp_access = wp_useful = wp_fill = wp_useless = None
        pollution = pol_wp_fill = pol_wp_miss = pol_cp_fill = pol_cp_miss = None
        data_req = data_hit = data_miss = None
        data_wp_req = data_wp_hit = data_wp_miss = None
        wp_miss_lat = cp_miss_lat = None

    return {
        f"{lv}_load_access":  load_access,
        f"{lv}_load_hit":     load_hit,
        f"{lv}_load_miss":    load_miss,
        f"{lv}_load_mpki":    mpki_val(load_miss, inst),
        f"{lv}_pf_access":    pf_access,
        f"{lv}_pf_hit":       pf_hit,
        f"{lv}_pf_miss":      pf_miss,
        f"{lv}_pf_requested": pf_requested,
        f"{lv}_pf_issued":    pf_issued,
        f"{lv}_pf_useful":    pf_useful,
        f"{lv}_pf_useless":   pf_useless,
        f"{lv}_wp_access":    wp_access,
        f"{lv}_wp_useful":    wp_useful,
        f"{lv}_wp_fill":      wp_fill,
        f"{lv}_wp_useless":   wp_useless,
        f"{lv}_pollution":    pollution,
        f"{lv}_pol_wp_fill":  pol_wp_fill,
        f"{lv}_pol_wp_miss":  pol_wp_miss,
        f"{lv}_pol_cp_fill":  pol_cp_fill,
        f"{lv}_pol_cp_miss":  pol_cp_miss,
        f"{lv}_data_req":     data_req,
        f"{lv}_data_hit":     data_hit,
        f"{lv}_data_miss":    data_miss,
        f"{lv}_data_wp_req":  data_wp_req,
        f"{lv}_data_wp_hit":  data_wp_hit,
        f"{lv}_data_wp_miss": data_wp_miss,
        f"{lv}_miss_lat":     miss_lat,
        f"{lv}_wp_miss_lat":  wp_miss_lat,
        f"{lv}_cp_miss_lat":  cp_miss_lat,
    }


# ── Per-level TLB parser (returns 10-field dict) ──────────────────────────────

def parse_tlb_level(text, tlv, fmt_str, wp_on, inst):
    """
    Parse all 10 TLB-level fields for one TLB.
    tlv:    column prefix (e.g. 'dtlb')
    fmt_str: 'normal' or 'wp_capable'
    wp_on:  True if wp_mode == 'on'
    inst:   ROI instruction count (for MPKI)
    """
    pfx = _tlb_prefix(tlv, fmt_str)
    ep  = re.escape(pfx)

    def S(pat, flags=re.MULTILINE):
        return re.search(r"^" + ep + r" " + pat, text, flags)

    # Use LOAD line for access/hit/miss (TLBs have LOAD = TOTAL for access)
    m = S(r"LOAD\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)")
    access = _getint(m, 1)
    hit    = _getint(m, 2)
    miss   = _getint(m, 3)

    # Miss latency
    if fmt_str == "normal":
        m = S(r"AVERAGE MISS LATENCY:\s*([\S]+) cycles")
    else:
        m = S(r"AVERAGE DATA MISS LATENCY:\s*([\S]+) cycles")
    miss_lat = _getfloat(m, 1)

    # WP-capable-only
    if fmt_str == "wp_capable":
        m = S(r"WRONG-PATH ACCESS:\s*(\d+)\s+LOAD:\s*\d+\s+USEFULL:\s*(\d+)"
              r"\s+FILL:\s*\d+\s+USELESS:\s*(\d+)")
        wp_access  = _getint(m, 1)
        wp_useful  = _getint(m, 2)
        wp_useless = _getint(m, 3)

        m = S(r"AVERAGE WP DATA MISS LATENCY:\s*([\S]+) cycles")
        wp_miss_lat = _getfloat(m, 1)
        m = S(r"AVERAGE CP DATA MISS LATENCY:\s*([\S]+) cycles")
        cp_miss_lat = _getfloat(m, 1)

        if not wp_on:
            wp_access = wp_useful = wp_useless = None
    else:
        wp_access = wp_useful = wp_useless = None
        wp_miss_lat = cp_miss_lat = None

    return {
        f"{tlv}_access":      access,
        f"{tlv}_hit":         hit,
        f"{tlv}_miss":        miss,
        f"{tlv}_mpki":        mpki_val(miss, inst),
        f"{tlv}_wp_access":   wp_access,
        f"{tlv}_wp_useful":   wp_useful,
        f"{tlv}_wp_useless":  wp_useless,
        f"{tlv}_miss_lat":    miss_lat,
        f"{tlv}_wp_miss_lat": wp_miss_lat,
        f"{tlv}_cp_miss_lat": cp_miss_lat,
    }


# ── Core parser ───────────────────────────────────────────────────────────────

def parse_one_file(path):
    """
    Parse one ChampSim log file.

    Returns:
        (row_dict, None, None)          on success
        (None, error_code, detail_str)  on hard error (row skipped per spec §11.2)
    """
    try:
        text = open(path, "r", errors="ignore").read()
    except Exception as e:
        return None, "unreadable_file", str(e)

    # ── Format detection (spec §4.1) ──────────────────────────────────────────
    if _WP_SIG.search(text):
        log_format = "wp_capable"
    elif _NORM_SIG.search(text):
        log_format = "normal"
    else:
        return None, "unknown_format", f"No recognizable format signature in {path}"

    # ── WP mode detection (spec §4.2) ─────────────────────────────────────────
    wp_mode = "on" if _WP_MODE.search(text) else "off"
    wp_on   = (wp_mode == "on")

    # ── ROI – last occurrence (spec §5) ───────────────────────────────────────
    m_roi = last_roi(text)
    if not m_roi:
        return None, "missing_roi", f"No ROI line in {path}"

    ipc       = float(m_roi.group(1))
    inst      = int(m_roi.group(2))
    cycles    = int(m_roi.group(3))
    wp_cycles = int(m_roi.group(4)) if m_roi.group(4) is not None else None

    warnings = []
    if log_format == "wp_capable" and wp_cycles is None:
        warnings.append("missing_wp_cycles")

    # ── G2 WP insts ───────────────────────────────────────────────────────────
    m = WP_INSTS_RE.search(text)
    wp_insts_total    = _getint(m, 1)
    wp_insts_skipped  = _getint(m, 2)
    wp_insts_executed = _getint(m, 3)

    m = FOOTPRINT_RE.search(text)
    instr_footprint = _getint(m, 1)
    data_footprint  = _getint(m, 2)

    m = ISPF_RE.search(text)
    is_prefetch_insts   = _getint(m, 1)
    is_prefetch_skipped = _getint(m, 2)

    # ── G3 Branch ─────────────────────────────────────────────────────────────
    m = BR_RE.search(text)
    branch_acc_percent = _getfloat(m, 1)
    branch_mpki        = _getfloat(m, 2)

    br_direct_jump_mpki    = _getfloat(BR_DJ_RE.search(text), 1)
    br_indirect_mpki       = _getfloat(BR_I_RE.search(text),  1)
    br_conditional_mpki    = _getfloat(BR_C_RE.search(text),  1)
    br_direct_call_mpki    = _getfloat(BR_DC_RE.search(text), 1)
    br_indirect_call_mpki  = _getfloat(BR_IC_RE.search(text), 1)
    br_return_mpki         = _getfloat(BR_R_RE.search(text),  1)

    # ── G4 Pipeline / Execute stats (WP binary only) ──────────────────────────
    if log_format == "wp_capable":
        exec_only_wp_cycles     = _getint(EXEC_WP_CYC_RE.search(text),  1)
        exec_only_cp_cycles     = _getint(EXEC_CP_CYC_RE.search(text),  1)
        exec_cp_wp_cycles       = _getint(EXEC_CPWP_RE.search(text),    1)
        rob_full_cycles         = _getint(ROB_FULL_CYC_RE.search(text), 1)
        rob_empty_cycles        = _getint(ROB_EMPTY_CYC_RE.search(text),1)
        rob_full_events         = _getint(ROB_FULL_EVT_RE.search(text), 1)
        rob_empty_events        = _getint(ROB_EMPTY_EVT_RE.search(text),1)
        resteer_events          = _getint(RESTEER_EVT_RE.search(text),  1)
        resteer_penalty_pct     = _getfloat(RESTEER_PCT_RE.search(text),1)
        wp_not_avail_cycles_pct = _getfloat(WP_NA_PCT_RE.search(text), 1)
    else:
        exec_only_wp_cycles     = None
        exec_only_cp_cycles     = None
        exec_cp_wp_cycles       = None
        rob_full_cycles         = None
        rob_empty_cycles        = None
        rob_full_events         = None
        rob_empty_events        = None
        resteer_events          = None
        resteer_penalty_pct     = None
        wp_not_avail_cycles_pct = None

    # ── G5 Cache levels ───────────────────────────────────────────────────────
    cache_rows = {}
    for lv in ["l1d", "l1i", "l2c", "llc"]:
        cache_rows.update(parse_cache_level(text, lv, log_format, wp_on, inst))

    # ── G6 TLB levels ─────────────────────────────────────────────────────────
    tlb_rows = {}
    for tlv in ["dtlb", "itlb", "stlb"]:
        tlb_rows.update(parse_tlb_level(text, tlv, log_format, wp_on, inst))

    # ── G7 DRAM ───────────────────────────────────────────────────────────────
    m = DRAM_RE.search(text)
    dram_rq_row_hit  = _getint(m, 1)
    dram_rq_row_miss = _getint(m, 2)

    # ── Assemble row ──────────────────────────────────────────────────────────
    row = {
        "log_format":   log_format,
        "wp_mode":      wp_mode,
        "parse_warnings": "|".join(warnings),
        # G2
        "cycles":    cycles,
        "inst":      inst,
        "ipc":       ipc,
        "wp_cycles": wp_cycles,
        "wp_insts_total":     wp_insts_total,
        "wp_insts_skipped":   wp_insts_skipped,
        "wp_insts_executed":  wp_insts_executed,
        "instr_footprint":    instr_footprint,
        "data_footprint":     data_footprint,
        "is_prefetch_insts":  is_prefetch_insts,
        "is_prefetch_skipped":is_prefetch_skipped,
        # G3
        "branch_acc_percent":   branch_acc_percent,
        "branch_mpki":          branch_mpki,
        "br_direct_jump_mpki":  br_direct_jump_mpki,
        "br_indirect_mpki":     br_indirect_mpki,
        "br_conditional_mpki":  br_conditional_mpki,
        "br_direct_call_mpki":  br_direct_call_mpki,
        "br_indirect_call_mpki":br_indirect_call_mpki,
        "br_return_mpki":       br_return_mpki,
        # G4
        "exec_only_wp_cycles":     exec_only_wp_cycles,
        "exec_only_cp_cycles":     exec_only_cp_cycles,
        "exec_cp_wp_cycles":       exec_cp_wp_cycles,
        "rob_full_cycles":         rob_full_cycles,
        "rob_empty_cycles":        rob_empty_cycles,
        "rob_full_events":         rob_full_events,
        "rob_empty_events":        rob_empty_events,
        "resteer_events":          resteer_events,
        "resteer_penalty_pct":     resteer_penalty_pct,
        "wp_not_avail_cycles_pct": wp_not_avail_cycles_pct,
        # G7
        "dram_rq_row_hit":  dram_rq_row_hit,
        "dram_rq_row_miss": dram_rq_row_miss,
    }
    row.update(cache_rows)
    row.update(tlb_rows)

    return row, None, None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    label_map = build_label_map(args.label_map)
    os.makedirs(args.outdir, exist_ok=True)
    img_formats = [s.strip().lower() for s in args.img_formats.split(",") if s.strip()]

    rows = []
    error_rows = []

    for path in sorted(glob.glob(args.glob)):
        bench = bench_from_name(path)
        cfg   = label_from_name(path, label_map)
        rec, err_code, err_detail = parse_one_file(path)
        if err_code:
            error_rows.append({
                "file": os.path.basename(path),
                "bench": bench,
                "config": cfg,
                "error_code": err_code,
                "detail": err_detail or "",
            })
            continue
        row = {"bench": bench, "config": cfg, "file": os.path.basename(path)}
        row.update(rec)
        rows.append(row)

    # parse_errors.csv (spec §11.2)
    if error_rows:
        err_path = os.path.join(args.outdir, "parse_errors.csv")
        with open(err_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=ERROR_FIELDNAMES)
            w.writeheader()
            w.writerows(error_rows)
        print(err_path)

    if not rows:
        print("No rows parsed. Check --glob and log files.")
        for e in error_rows[:20]:
            print(f"  ERROR [{e['error_code']}] {e['file']}: {e['detail']}")
        return

    # full_metrics.csv (spec §6.1) – 183 columns
    full_path = os.path.join(args.outdir, "full_metrics.csv")
    with open(full_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FULL_FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: fmt(r.get(k), k) for k in FULL_FIELDNAMES})
    print(full_path)

    # summary.csv (spec §6.2)
    sum_path = os.path.join(args.outdir, "summary.csv")
    with open(sum_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: fmt(r.get(k), k) for k in SUMMARY_FIELDNAMES})
    print(sum_path)

    # normalized_ipc.csv (legacy feature, spec §6.3)
    baseline_label = args.baseline
    bybench = defaultdict(dict)
    for r in rows:
        bybench[r["bench"]][r["config"]] = r.get("ipc")

    norm_rows = []
    ratios_by_cfg = defaultdict(list)
    for b, d in sorted(bybench.items()):
        base = d.get(baseline_label)
        if not base or base <= 0:
            continue
        for cfg, val in d.items():
            norm = (val / base) if val else None
            norm_rows.append({"bench": b, "config": cfg, "ipc_norm_vs_" + baseline_label: norm})
            if norm is not None and cfg != baseline_label:
                ratios_by_cfg[cfg].append(norm)

    for cfg, lst in sorted(ratios_by_cfg.items()):
        norm_rows.append({
            "bench": "__geomean__",
            "config": cfg,
            "ipc_norm_vs_" + baseline_label: geomean(lst),
        })

    norm_path = os.path.join(args.outdir, "normalized_ipc.csv")
    with open(norm_path, "w", newline="") as f:
        keys = ["bench", "config", "ipc_norm_vs_" + baseline_label]
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(norm_rows)
    print(norm_path)

    # Charts (legacy, requires matplotlib)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print("matplotlib not available; skipping plots:", e)
        return

    # Plot 1: Normalized IPC bar
    norm_data = defaultdict(dict)
    with open(norm_path) as f:
        for row in csv.DictReader(f):
            if row["bench"] == "__geomean__":
                continue
            k = next((k for k in row.keys() if k.startswith("ipc_norm_vs_")), None)
            if not k:
                continue
            v = row.get(k)
            if not v or v == "None":
                continue
            norm_data[row["bench"]][row["config"]] = float(v)

    benches = sorted(norm_data.keys())
    if benches:
        configs = sorted({c for d in norm_data.values() for c in d.keys()})
        width = 0.8 / len(configs) if configs else 0.8
        xs = list(range(len(benches)))
        plt.figure()
        for i, cfg in enumerate(configs):
            ys = [norm_data[b].get(cfg, 0.0) for b in benches]
            plt.bar([x + i * width for x in xs], ys, width=width, label=cfg)
        plt.axhline(1.0, linestyle="--", linewidth=1)
        plt.xticks([x + 0.4 for x in xs], benches, rotation=45, ha="right")
        plt.ylabel("IPC normalized to " + baseline_label)
        plt.legend()
        plt.tight_layout()
        for ext in img_formats:
            outpath = os.path.join(args.outdir, f"ipc_normalized_bar.{ext}")
            plt.savefig(outpath, dpi=180)
            print(outpath)
        plt.close()

    # Plot 2: IPC vs LLC MPKI scatter
    plt.figure()
    have_any = False
    for cfg in sorted({r["config"] for r in rows}):
        xs2, ys2 = [], []
        for r in rows:
            if r["config"] != cfg:
                continue
            x = r.get("llc_load_mpki")
            y = r.get("ipc")
            if x is not None and y is not None:
                xs2.append(float(x) if isinstance(x, float) else x)
                ys2.append(float(y) if isinstance(y, float) else y)
        if xs2 and ys2:
            plt.scatter(xs2, ys2, label=cfg, alpha=0.7)
            have_any = True
    if have_any:
        plt.xlabel("LLC load MPKI")
        plt.ylabel("IPC")
        plt.legend()
        plt.tight_layout()
        for ext in img_formats:
            outpath = os.path.join(args.outdir, f"ipc_vs_llc_mpki.{ext}")
            plt.savefig(outpath, dpi=180)
            print(outpath)
        plt.close()
    else:
        plt.close()


if __name__ == "__main__":
    main()
