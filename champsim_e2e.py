#!/usr/bin/env python3
"""Unified ChampSim log summarizer.

Handles both normal ChampSim and WP ChampSim log formats automatically.
Implements SUMMARY_SPEC.md.

Outputs:
  full_metrics.csv   – all parsed metrics
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


# ── Shared regex patterns ──────────────────────────────────────────────────────

# ROI line – both formats; WP appends wp_cycles
# Spec §5: use LAST occurrence
ROI_RE = re.compile(
    r"CPU\s+\d+\s+cumulative IPC:\s*([\d.]+)\s+"
    r"instructions:\s*(\d+)\s+cycles:\s*(\d+)"
    r"(?:\s+wp_cycles:\s*(\d+))?"
)
BR_RE = re.compile(r"Branch Prediction Accuracy:\s*([\d.]+)%\s*MPKI:\s*([\d.]+)")

# ── Format / mode detection (spec §4) ─────────────────────────────────────────
# wp_capable: "LLC WRONG-PATH ACCESS:" present (always in WP binary, even WP OFF)
# normal:     "cpu0->cpu0_" prefix present
_WP_SIG   = re.compile(r"^LLC WRONG-PATH\s+ACCESS:", re.MULTILINE)
_NORM_SIG = re.compile(r"cpu0->cpu0_")
_WP_MODE  = re.compile(r"Wrong path enabled")

# ── Normal ChampSim patterns (cpu0->cpu0_ / cpu0->LLC prefix) ─────────────────
N_L1D   = re.compile(r"cpu0->cpu0_L1D\s+LOAD\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)")
N_L2    = re.compile(r"cpu0->cpu0_L2C\s+LOAD\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)")
N_LLC   = re.compile(r"cpu0->LLC\s+LOAD\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)")
N_L1D_L = re.compile(r"cpu0->cpu0_L1D AVERAGE MISS LATENCY:\s*([\d.]+) cycles")
N_L2_L  = re.compile(r"cpu0->cpu0_L2C AVERAGE MISS LATENCY:\s*([\d.]+) cycles")
N_LLC_L = re.compile(r"cpu0->LLC AVERAGE MISS LATENCY:\s*([\d.]+) cycles")
N_DTLB  = re.compile(r"cpu0->cpu0_DTLB\s+TOTAL\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)")
N_ITLB  = re.compile(r"cpu0->cpu0_ITLB\s+TOTAL\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)")
N_STLB  = re.compile(r"cpu0->cpu0_STLB\s+TOTAL\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)")

# ── WP ChampSim patterns (cpu0_ prefix; LLC has no cpu prefix) ────────────────
W_L1D   = re.compile(r"^cpu0_L1D\s+LOAD\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)", re.MULTILINE)
W_L2    = re.compile(r"^cpu0_L2C\s+LOAD\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)", re.MULTILINE)
W_LLC   = re.compile(r"^LLC\s+LOAD\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)", re.MULTILINE)
W_L1D_L = re.compile(r"cpu0_L1D AVERAGE MISS LATENCY:\s*([\d.]+) cycles")
W_L2_L  = re.compile(r"cpu0_L2C AVERAGE MISS LATENCY:\s*([\d.]+) cycles")
W_LLC_L = re.compile(r"^LLC AVERAGE MISS LATENCY:\s*([\d.]+) cycles", re.MULTILINE)
W_DTLB  = re.compile(r"^cpu0_DTLB\s+TOTAL\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)", re.MULTILINE)
W_ITLB  = re.compile(r"^cpu0_ITLB\s+TOTAL\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)", re.MULTILINE)
W_STLB  = re.compile(r"^cpu0_STLB\s+TOTAL\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)", re.MULTILINE)
# WRONG-PATH: ACCESS=total WP accesses, USEFULL=useful fills (typo preserved from log)
W_WP    = re.compile(
    r"^LLC WRONG-PATH\s+ACCESS:\s*(\d+)\s+LOAD:\s*\d+\s+USEFULL:\s*(\d+)\s+FILL:\s*\d+\s+USELESS:\s*\d+",
    re.MULTILINE,
)
# POLLUTION: WP_MISS=WP misses to DRAM, CP_MISS=correct-path LLC misses
W_POL   = re.compile(
    r"^LLC POLLUTION:\s*[\d.]+\s+WP_FILL:\s*\d+\s+WP_MISS:\s*(\d+)\s+CP_FILL:\s*\d+\s+CP_MISS:\s*(\d+)",
    re.MULTILINE,
)

# ── Column definitions (SUMMARY_SPEC.md §9.2 and §12) ────────────────────────

FULL_FIELDNAMES = [
    # 識別
    "bench", "config", "file",
    # 判定
    "log_format", "wp_mode", "parse_warnings",
    # ROI core
    "cycles", "wp_cycles", "inst", "ipc",
    # Branch
    "branch_acc_percent", "branch_mpki",
    # L1D
    "l1d_load_access", "l1d_load_hit", "l1d_load_miss", "l1d_miss_lat", "l1d_load_mpki",
    # L2
    "l2_load_access", "l2_load_hit", "l2_load_miss", "l2_miss_lat", "l2_load_mpki",
    # LLC
    "llc_load_access", "llc_load_hit", "llc_load_miss", "llc_miss_lat", "llc_load_mpki",
    # WP-specific (empty for normal format)
    "wp_access", "wp_useful", "wp_useful_percent", "wp_miss", "cp_miss",
    # TLB
    "dtlb_access", "dtlb_hit", "dtlb_miss", "dtlb_mpki",
    "itlb_access", "itlb_hit", "itlb_miss", "itlb_mpki",
    "stlb_access", "stlb_hit", "stlb_miss", "stlb_mpki",
]

SUMMARY_FIELDNAMES = [
    "bench", "config", "log_format", "wp_mode", "parse_warnings",
    "cycles", "wp_cycles", "inst", "ipc",
    "branch_mpki",
    "llc_load_miss", "llc_load_mpki", "llc_miss_lat",
    # WP-specific (empty for normal format)
    "wp_access", "wp_useful", "wp_useful_percent", "cp_miss",
]

ERROR_FIELDNAMES = ["file", "bench", "config", "error_code", "detail"]


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
    """Parse float; return None for NaN or unparseable values."""
    try:
        v = float(s)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def last_roi(text):
    """Return the last ROI match (spec §5: use last occurrence)."""
    m = None
    for m in ROI_RE.finditer(text):
        pass
    return m


def get3(pat, text):
    """Extract (access, hit, miss) integers from a cache stat line."""
    m = pat.search(text)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None, None, None


def getlat(pat, text):
    """Extract latency float; returns None for missing or -nan."""
    m = pat.search(text)
    return safe_float(m.group(1)) if m else None


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
      *_mpki, *_lat  → 4 decimal places
      *_percent      → 2 decimal places
      ipc            → 4 decimal places
      other floats   → 4 decimal places
    """
    if v is None:
        return ""
    if isinstance(v, float):
        if "_lat" in field:
            return f"{v:.4g}"   # 4 sig figs, no trailing zeros (1158.0 → 1158)
        if "_mpki" in field:
            return f"{v:.4f}"   # 4 decimal places
        if "_percent" in field:
            return f"{v:.2f}"   # 2 decimal places
        return f"{v:.4f}"
    return str(v)


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

    # ── Branch ────────────────────────────────────────────────────────────────
    brA = brM = None
    m = BR_RE.search(text)
    if m:
        brA = float(m.group(1))
        brM = float(m.group(2))

    # ── Cache metrics ─────────────────────────────────────────────────────────
    if log_format == "normal":
        l1dA, l1dH, l1dM = get3(N_L1D, text)
        l2A,  l2H,  l2M  = get3(N_L2,  text)
        llcA, llcH, llcM = get3(N_LLC, text)
        lat1 = getlat(N_L1D_L, text)
        lat2 = getlat(N_L2_L,  text)
        lat3 = getlat(N_LLC_L, text)
        dtlbA, dtlbH, dtlbM = get3(N_DTLB, text)
        itlbA, itlbH, itlbM = get3(N_ITLB, text)
        stlbA, stlbH, stlbM = get3(N_STLB, text)
        wp_access = wp_useful = wp_miss = cp_miss = None

    else:  # wp_capable
        l1dA, l1dH, l1dM = get3(W_L1D, text)
        l2A,  l2H,  l2M  = get3(W_L2,  text)
        llcA, llcH, llcM = get3(W_LLC, text)
        lat1 = getlat(W_L1D_L, text)
        lat2 = getlat(W_L2_L,  text)
        lat3 = getlat(W_LLC_L, text)
        dtlbA, dtlbH, dtlbM = get3(W_DTLB, text)
        itlbA, itlbH, itlbM = get3(W_ITLB, text)
        stlbA, stlbH, stlbM = get3(W_STLB, text)

        m_wp  = W_WP.search(text)
        m_pol = W_POL.search(text)

        if m_wp:
            wp_access = int(m_wp.group(1))
            wp_useful = int(m_wp.group(2))
        else:
            wp_access = wp_useful = None
            warnings.append("missing_wp_stats")

        wp_miss = int(m_pol.group(1)) if m_pol else None
        cp_miss = int(m_pol.group(2)) if m_pol else None

        # WP OFF: activity metrics are 0 by definition → blank out for clarity
        if wp_mode == "off":
            wp_access = wp_useful = wp_miss = None

    wp_useful_percent = (
        wp_useful * 100.0 / wp_access
        if wp_access and wp_access > 0 and wp_useful is not None
        else None
    )

    return {
        "log_format": log_format,
        "wp_mode": wp_mode,
        "parse_warnings": "|".join(warnings),
        # ROI core
        "ipc": ipc, "inst": inst, "cycles": cycles, "wp_cycles": wp_cycles,
        # Branch
        "branch_acc_percent": brA, "branch_mpki": brM,
        # L1D
        "l1d_load_access": l1dA, "l1d_load_hit": l1dH, "l1d_load_miss": l1dM,
        "l1d_miss_lat": lat1,    "l1d_load_mpki": mpki_val(l1dM, inst),
        # L2
        "l2_load_access": l2A, "l2_load_hit": l2H, "l2_load_miss": l2M,
        "l2_miss_lat": lat2,   "l2_load_mpki": mpki_val(l2M, inst),
        # LLC
        "llc_load_access": llcA, "llc_load_hit": llcH, "llc_load_miss": llcM,
        "llc_miss_lat": lat3,    "llc_load_mpki": mpki_val(llcM, inst),
        # WP
        "wp_access": wp_access, "wp_useful": wp_useful,
        "wp_useful_percent": wp_useful_percent,
        "wp_miss": wp_miss, "cp_miss": cp_miss,
        # TLB
        "dtlb_access": dtlbA, "dtlb_hit": dtlbH, "dtlb_miss": dtlbM,
        "dtlb_mpki": mpki_val(dtlbM, inst),
        "itlb_access": itlbA, "itlb_hit": itlbH, "itlb_miss": itlbM,
        "itlb_mpki": mpki_val(itlbM, inst),
        "stlb_access": stlbA, "stlb_hit": stlbH, "stlb_miss": stlbM,
        "stlb_mpki": mpki_val(stlbM, inst),
    }, None, None


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

    # full_metrics.csv (spec §6.1)
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
                xs2.append(float(x))
                ys2.append(float(y))
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
