#!/usr/bin/env python3
# End-to-end ChampSim log summarizer and plotter

import re
import glob
import os
import math
import csv
import argparse
from collections import defaultdict

def parse_args():
    p = argparse.ArgumentParser(description="Summarize ChampSim logs and make charts")
    p.add_argument("--glob", default="*.txt", help="Glob pattern for log files")
    p.add_argument("--outdir", default="summary_out", help="Output directory for CSVs and charts")
    p.add_argument("--baseline", default="latest", help="Label name used as normalization baseline")
    p.add_argument(
        "--label-map",
        default="resche2:schedcost_on,resche_:schedcost_off,ChampSim:latest",
        help="Comma-separated mapping of filename-substring:label (first match wins)"
    )
    p.add_argument(
        "--img-formats",
        default="svg",
        help="Comma-separated image formats to save (example: 'svg' or 'png,svg'). Default: svg"
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

# Regex patterns
ROI_RE   = re.compile(r"CPU\s+\d+\s+cumulative IPC:\s*([\d.]+)\s*instructions:\s*(\d+)\s*cycles:\s*(\d+)")
BR_RE    = re.compile(r"Branch Prediction Accuracy:\s*([\d.]+)%\s*MPKI:\s*([\d.]+)")
L1D_RE   = re.compile(r"cpu0->cpu0_L1D\s+LOAD\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)")
L2_RE    = re.compile(r"cpu0->cpu0_L2C\s+LOAD\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)")
LLC_RE   = re.compile(r"cpu0->LLC\s+LOAD\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)")
DTLB_RE  = re.compile(r"cpu0->cpu0_DTLB\s+TOTAL\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+)")
LAT_L1D  = re.compile(r"cpu0->cpu0_L1D AVERAGE MISS LATENCY:\s*([\d.]+) cycles")
LAT_L2   = re.compile(r"cpu0->cpu0_L2C AVERAGE MISS LATENCY:\s*([\d.]+) cycles")
LAT_LLC  = re.compile(r"cpu0->LLC AVERAGE MISS LATENCY:\s*([\d.]+) cycles")

def label_from_name(fname: str, label_map):
    base = os.path.basename(fname)
    for key, lab in label_map:
        if key and (key in fname or key in base):
            return lab
    return "unknown"

def bench_from_name(fname: str) -> str:
    # 例: 0_wp_..._ChampSim_0_j219721.txt -> 0_wp_...
    base = os.path.splitext(os.path.basename(fname))[0]
    base = re.sub(r"_ChampSim.*$", "", base)
    return base

def mpki(miss, inst):
    try:
        miss = int(miss); inst = int(inst)
        return miss * 1000.0 / inst if inst > 0 else None
    except (TypeError, ValueError):
        return None

def parse_one_file(path):
    try:
        text = open(path, "r", errors="ignore").read()
    except Exception as e:
        return None, f"Failed to read {path}: {e}"

    m_roi = ROI_RE.search(text)
    if not m_roi:
        return None, f"No ROI line in {path}"
    ipc = float(m_roi.group(1))
    inst = int(m_roi.group(2))
    cycles = int(m_roi.group(3))

    brA = brM = None
    m = BR_RE.search(text)
    if m:
        brA = float(m.group(1))
        brM = float(m.group(2))

    def get3(pat):
        m = pat.search(text)
        return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (None, None, None)

    l1dA, l1dH, l1dM = get3(L1D_RE)
    l2A, l2H, l2M = get3(L2_RE)
    llcA, llcH, llcM = get3(LLC_RE)
    dtlbA, dtlbH, dtlbM = get3(DTLB_RE)

    def getlat(p):
        m = p.search(text)
        return float(m.group(1)) if m else None

    lat1 = getlat(LAT_L1D)
    lat2 = getlat(LAT_L2)
    lat3 = getlat(LAT_LLC)

    return {
        "ipc": ipc,
        "inst": inst,
        "cycles": cycles,
        "branch_acc_percent": brA,
        "branch_mpki": brM,
        "l1d_load_access": l1dA, "l1d_load_hit": l1dH, "l1d_load_miss": l1dM, "l1d_miss_lat": lat1,
        "l2_load_access": l2A,   "l2_load_hit": l2H,   "l2_load_miss": l2M,   "l2_miss_lat": lat2,
        "llc_load_access": llcA, "llc_load_hit": llcH, "llc_load_miss": llcM, "llc_miss_lat": lat3,
        "dtlb_access": dtlbA,    "dtlb_hit": dtlbH,    "dtlb_miss": dtlbM,
        "l1d_load_mpki": mpki(l1dM, inst),
        "l2_load_mpki": mpki(l2M, inst),
        "llc_load_mpki": mpki(llcM, inst),
        "dtlb_mpki": mpki(dtlbM, inst),
    }, None

def geomean(lst):
    vals = [v for v in lst if v and v > 0]
    if not vals:
        return None
    return math.exp(sum(math.log(x) for x in vals) / len(vals))

def main():
    args = parse_args()
    label_map = build_label_map(args.label_map)
    os.makedirs(args.outdir, exist_ok=True)
    img_formats = [fmt.strip().lower() for fmt in args.img_formats.split(",") if fmt.strip()]

    rows = []
    errors = []
    files = sorted(glob.glob(args.glob))
    for path in files:
        rec, err = parse_one_file(path)
        if err:
            errors.append(err)
            continue
        bench = bench_from_name(path)
        cfg   = label_from_name(path, label_map)
        rec_out = {"bench": bench, "config": cfg, "file": os.path.basename(path)}
        rec_out.update(rec)
        rows.append(rec_out)

    if errors:
        with open(os.path.join(args.outdir, "parse_errors.txt"), "w") as f:
            for e in errors:
                f.write(e + "\n")

    if not rows:
        print("No rows parsed. Check --glob and input files.")
        for e in errors[:50]:
            print("WARN:", e)
        return

    fieldnames = [
        "bench", "config", "file",
        "ipc", "cycles", "inst",
        "branch_acc_percent", "branch_mpki",
        "l1d_load_access","l1d_load_hit","l1d_load_miss","l1d_miss_lat","l1d_load_mpki",
        "l2_load_access","l2_load_hit","l2_load_miss","l2_miss_lat","l2_load_mpki",
        "llc_load_access","llc_load_hit","llc_load_miss","llc_miss_lat","llc_load_mpki",
        "dtlb_access","dtlb_hit","dtlb_miss","dtlb_mpki",
    ]
    summary_path = os.path.join(args.outdir, "summary.csv")
    with open(summary_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fieldnames})
    print(summary_path)

    # Per-bench normalization vs baseline
    baseline_label = args.baseline
    bybench = defaultdict(dict)
    for r in rows:
        bybench[r["bench"]][r["config"]] = r["ipc"]

    norm_rows = []
    ratios_by_cfg = defaultdict(list)
    for b, d in sorted(bybench.items()):
        base = d.get(baseline_label)
        if not base or base <= 0:
            continue
        for cfg, val in d.items():
            norm = (val / base) if val else None
            norm_rows.append({"bench": b, "config": cfg, "ipc_norm_vs_"+baseline_label: norm})
            if norm is not None and cfg != baseline_label:
                ratios_by_cfg[cfg].append(norm)

    for cfg, lst in sorted(ratios_by_cfg.items()):
        norm_rows.append({"bench": "__geomean__", "config": cfg, "ipc_norm_vs_"+baseline_label: geomean(lst)})

    norm_path = os.path.join(args.outdir, "normalized_ipc.csv")
    with open(norm_path, "w", newline="") as f:
        keys = ["bench", "config", "ipc_norm_vs_"+baseline_label]
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(norm_rows)
    print(norm_path)

    # Plots
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
        r = csv.DictReader(f)
        for row in r:
            if row["bench"] == "__geomean__":
                continue
            k = next(k for k in row.keys() if k.startswith("ipc_norm_vs_"))
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
    for cfg in sorted({row["config"] for row in rows}):
        xs, ys = [], []
        for row in rows:
            if row["config"] != cfg:
                continue
            try:
                x = float(row["llc_load_mpki"]) if row["llc_load_mpki"] not in (None, "", "None") else None
                y = float(row["ipc"]) if row["ipc"] not in (None, "", "None") else None
            except ValueError:
                x = y = None
            if x is not None and y is not None:
                xs.append(x)
                ys.append(y)
        if xs and ys:
            plt.scatter(xs, ys, label=cfg, alpha=0.7)
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

if __name__ == "__main__":
    main()
