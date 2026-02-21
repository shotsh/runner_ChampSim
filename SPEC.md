# submit.py Specification

## Overview

`submit.py` is a script that manages batch execution of ChampSim simulations.
It reads a recipe YAML, generates `matrix.tsv`, and submits jobs with `sbatch --array`.

Summarizer behavior and CSV schema are defined in `SUMMARY_SPEC.md`.

## Command-Line Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--recipe` | Yes | Path to recipe YAML file |
| `--wait` | No | Wait for all jobs to finish |
| `--summarize` | No | Summarize this run after wait (blocking) |
| `--no-auto-summarize` | No | Disable default afterok summarize job auto-submission |
| `--baseline` | No | Baseline for summarize (default: `latest`) |
| `--label-map` | No | Label map for summarize |
| `--img-formats` | No | Output image formats (default: `svg`) |

## Recipe YAML Format

### Legacy Format (traces + args)

Execute all combinations of traces × args (matrix expansion).

```yaml
name: my_run

bins:
  - /path/to/champsim

traces:
  - /path/to/trace1
  - /path/to/trace2
  - /path/to/*.trace  # Glob supported

args:
  - "--warmup-instructions 100000000 --simulation-instructions 100000000"
  - "--warmup-instructions 200000000 --simulation-instructions 200000000"

resources:
  partition: cpu-research
  qos: olympus-cpu-research
  time: 08:00:00
  mem: 8G
  cpus_per_task: 2
  chunk: 1000  # Array chunk size (default: 1000)
```

**Total tasks**: `len(bins) × len(traces) × len(args)`

### New Format (trace_configs)

Specify individual args per trace. Traces with same settings can be grouped.

**Notes:**
- `args` is **required** for each trace_config (error if omitted)
- Same trace can be listed in multiple configs (executed multiple times with different args)

```yaml
name: wp_20260122

bins:
  - /path/to/champsim

trace_configs:
  # Group traces with same settings
  - traces:
      - /path/to/B256.trace
      - /path/to/B384.trace
      - /path/to/B512.trace
    args: "--warmup-instructions 102000000 --simulation-instructions 102000000"  # Required

  # Individual settings
  - traces:
      - /path/to/B1024.trace
    args: "--warmup-instructions 103000000 --simulation-instructions 103000000"

  - traces:
      - /path/to/B2048.trace
    args: "--warmup-instructions 105000000 --simulation-instructions 105000000"

  # Globs are also supported
  - traces:
      - /path/to/*B8192*.trace
    args: "--warmup-instructions 118000000 --simulation-instructions 118000000"

resources:
  partition: cpu-research
  qos: olympus-cpu-research
  time: 08:00:00
  mem: 8G
  cpus_per_task: 2
```

**Total tasks**: `len(bins) × len(total traces across all trace_configs)`

## Resources Section

| Field | Description | Default |
|-------|-------------|---------|
| `partition` | SLURM partition | None |
| `qos` | QoS setting | None |
| `account` | Account | None |
| `nodelist` | Node specification | None |
| `time` | Time limit | `08:00:00` |
| `mem` | Memory | `8G` |
| `cpus_per_task` | CPU cores | `1` |
| `chunk` | Array job chunk size | `1000` |

## Output Directory Structure

```
runs/
└── 2026-01-22_123456_my_run/
    ├── matrix.tsv           # Execution matrix
    ├── sbatch_cmd.txt       # sbatch command record
    ├── sbatch_jobs.txt      # Submitted job IDs
    ├── submit_debug.log     # Debug log
    ├── summarize_afterok.sbatch  # Auto-generated summarize script
    ├── logs/
    │   ├── <name>.<jobid>.<arrayid>.out       # Without chunk splitting
    │   ├── <name>.<jobid>.<arrayid>.err
    │   ├── <name>_p0.<jobid>.<arrayid>.out    # With chunk splitting (_p0, _p1, ...)
    │   └── <name>_p0.<jobid>.<arrayid>.err
    └── results/
        ├── <arrayid>_<trace>_<repo>_<bin_name>_<args_idx>_j<jobid>.txt
        └── summary_out/     # Summarize results
            ├── diagnostics.txt
            ├── e2e_stdout.txt
            └── *.svg / *.csv
```

## matrix.tsv Format

Tab-separated 4 columns:

```
BIN<TAB>TRACE<TAB>ARGS<TAB>ARGS_IDX
```

| Column | Description |
|--------|-------------|
| BIN | Absolute path to ChampSim binary |
| TRACE | Absolute path to trace file |
| ARGS | Runtime arguments string |
| ARGS_IDX | Args index number (for filename generation) |

## Usage Examples

```bash
# Basic usage
python submit.py --recipe recipes/runspec_wp_20260122.yaml

# Wait for job completion
python submit.py --recipe recipes/runspec.yaml --wait

# Run summarize locally after completion
python submit.py --recipe recipes/runspec.yaml --summarize

# Disable auto-summarize
python submit.py --recipe recipes/runspec.yaml --no-auto-summarize
```

## Format Auto-Detection

- `trace_configs` key exists → New format
- `traces` + `args` keys exist → Legacy format

If both are specified, `trace_configs` takes priority.

---

# Analysis Scripts Specification

## Log Format Variants

ChampSim produces two distinct log formats depending on the binary used.

### Normal ChampSim format
```
cpu0->cpu0_L1D LOAD  ACCESS: X  HIT: Y  MISS: Z  MISS_MERGE: W
cpu0->LLC      LOAD  ACCESS: X  HIT: Y  MISS: Z  MISS_MERGE: W
```

### WP ChampSim format (applies to both WP ON and WP OFF runs)
```
cpu0_L1D LOAD        ACCESS: X  HIT: Y  MISS: Z          ← no MISS_MERGE
cpu0_LLC WRONG-PATH  ACCESS: X  LOAD: X  USEFULL: X  FILL: X  USELESS: X
cpu0_LLC POLLUTION:  X  WP_FILL: X  WP_MISS: X  CP_FILL: X  CP_MISS: X
cpu0_LLC DATA REQ:   X  HIT: X  MISS: X  WP_REQ: X  WP_HIT: X  WP_MISS: X
```

**Detection rules**:

| Condition | Format |
|-----------|--------|
| `WRONG-PATH` line present + `Wrong path enabled` in header | WP ChampSim, **WP ON** |
| `WRONG-PATH` line present, no `Wrong path enabled` | WP ChampSim, **WP OFF** |
| `WRONG-PATH` line absent (`cpu0->cpu0_` prefix) | Normal ChampSim |

`WRONG-PATH` / `POLLUTION` lines exist in all WP ChampSim logs (including WP OFF), but with all-zero values when WP is disabled. These lines are completely absent in Normal ChampSim logs.

---

## champsim_e2e.py — Generic Summarizer

Parses ChampSim result logs and outputs CSVs and charts.

**Target**: Normal ChampSim logs (`cpu0->cpu0_` prefix format)

**Usage**:
```bash
cd results/
python3 /path/to/champsim_e2e.py --glob "*.txt" --outdir summary_out
```

**Output** (`summary_out/`):

| File | Description |
|------|-------------|
| `summary.csv` | Key metrics per run: cycles, inst, ipc, branch MPKI, LLC miss MPKI/latency |
| `full_metrics.csv` | All parsed metrics (superset of summary.csv) |
| `normalized_ipc.csv` | IPC normalized to baseline config |
| `ipc_normalized_bar.svg` | Bar chart of normalized IPC |
| `diagnostics.txt` | Parse errors and warnings |

**Key options**:

| Option | Default | Description |
|--------|---------|-------------|
| `--glob` | `*.txt` | Glob pattern for log files |
| `--outdir` | `summary_out` | Output directory |
| `--baseline` | `latest` | Config label used as normalization baseline |
| `--label-map` | `ChampSim:latest` | Substring→label mapping for config detection |

---

## wp_summary.py — WP ChampSim Summarizer

Parses WP ChampSim result logs and outputs WP-specific analysis.

**Target**: WP ChampSim logs (`cpu0_` prefix format, both WP ON and WP OFF)

**Pairing rule**: Even-indexed files (`_0`) = WP OFF baseline, odd-indexed files (`_1`) = WP ON

**Usage**:
```bash
python3 /path/to/wp_summary.py ./runs/<run_dir>/results/
```

**Output** (`summary_out/`):

| File | Description |
|------|-------------|
| `wp_summary.csv` | Per-benchmark: cycles (OFF/ON), speedup, WP ACCESS, WP USEFUL, USEFUL rate, CP_MISS |
| `full_metrics.csv` | All parsed metrics for all files |

**Metrics extracted**:

| Metric | Source line |
|--------|-------------|
| cycles, ipc | `CPU 0 cumulative IPC:` |
| wp_cycles | `CPU 0 cumulative IPC: ... wp_cycles:` |
| branch_acc, branch_mpki | `Branch Prediction Accuracy:` |
| llc_load_miss, llc_miss_lat | `cpu0_LLC LOAD` / `cpu0_LLC AVERAGE MISS LATENCY` |
| wp_access, wp_useful | `cpu0_LLC WRONG-PATH` |
| cp_miss, wp_miss | `cpu0_LLC POLLUTION` |
| speedup | Computed: cycles_off / cycles_on |
| geomean_speedup | Geometric mean of speedup across benchmarks |
