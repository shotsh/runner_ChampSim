#!/usr/bin/env python3
"""
submit.py
- Read runspec.yaml, create matrix.tsv, and execute with sbatch --array
- Default: Automatically submit afterok-dependent summarize job (non-blocking)
- --summarize: Run summarize locally after wait (blocking)
- --no-auto-summarize: Submit only, no summarize

Debug output:
- runs/<run>/submit_debug.log
- runs/<run>/results/summary_out/diagnostics.txt (auto/inline summarize)
- runs/<run>/results/summary_out/e2e_stdout.txt (auto/inline summarize)
"""

import argparse, os, sys, glob, datetime, subprocess, shlex, re, time
from pathlib import Path

# -----------------------
# Debug helpers
# -----------------------
def append_log(log_path, msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a") as f:
        f.write(f"[{ts}] {msg}\n")

def run_capture(cmd, check=False, cwd=None, env=None):
    """
    Python 3.6互換: text= は使わず universal_newlines=True
    return: (stdout, returncode)
    """
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        cwd=cwd,
        env=env,
    )
    if check and p.returncode != 0:
        raise subprocess.CalledProcessError(p.returncode, cmd, output=p.stdout)
    return p.stdout, p.returncode

def choose_python_exe():
    """
    summarize 実行時に使う python を選ぶ。
    優先: CSIM_VENV/bin/python3 があればそれ > sys.executable > "python3"
    """
    venv = os.environ.get("CSIM_VENV", "")
    if venv:
        cand = Path(venv) / "bin" / "python3"
        if cand.is_file():
            return str(cand)
    if sys.executable:
        return sys.executable
    return "python3"

# -----------------------
# Core logic
# -----------------------
def load_yaml(path):
    try:
        import yaml
    except ImportError:
        print("Please install PyYAML: pip install --user pyyaml", file=sys.stderr)
        sys.exit(1)
    with open(path, "r") as f:
        return yaml.safe_load(f)

def expand_traces(patterns):
    out = []
    for pat in patterns:
        matches = sorted(glob.glob(pat))
        if matches:
            out.extend(os.path.abspath(p) for p in matches if os.path.isfile(p))
        elif os.path.isfile(pat):
            out.append(os.path.abspath(pat))
        else:
            print(f"WARN: no matches for {pat}", file=sys.stderr)
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            uniq.append(p); seen.add(p)
    return uniq

def expand_trace_configs(trace_configs):
    """
    Expand trace_configs format and return a list of (trace, args) pairs.
    Same trace can be executed multiple times with different args (no deduplication).
    trace_configs:
      - traces: ["*B256*", "*B384*"]
        args: "--warmup-instructions 102000000 ..."  # Required
      - traces: ["*B1024*"]
        args: "--warmup-instructions 103000000 ..."
    """
    pairs = []
    for i, cfg in enumerate(trace_configs):
        patterns = cfg.get("traces", [])
        if "args" not in cfg:
            sys.exit(f"trace_configs[{i}]: args is required")
        args = cfg["args"]
        traces = expand_traces(patterns)
        for t in traces:
            pairs.append((t, args))
    return pairs

def write_matrix(run_dir, bins, traces, argsets):
    mpath = Path(run_dir) / "matrix.tsv"
    arg_index = {a: i for i, a in enumerate(argsets)}
    with mpath.open("w") as f:
        for t in traces:
            if not os.path.exists(t):
                sys.exit(f"Trace not found: {t}")
            for b in bins:
                if not os.path.isfile(b):
                    sys.exit(f"Binary not found: {b}")
                for a in argsets:
                    idx = arg_index[a]
                    f.write(f"{b}\t{t}\t{a}\t{idx}\n")
    return str(mpath)

def write_matrix_from_pairs(run_dir, bins, trace_args_pairs):
    """
    For trace_configs format: Generate matrix from (trace, args) pairs.
    Each trace has its own associated args.
    """
    mpath = Path(run_dir) / "matrix.tsv"
    # Create unique list of args and index them
    unique_args = []
    seen_args = set()
    for _, args in trace_args_pairs:
        if args not in seen_args:
            unique_args.append(args)
            seen_args.add(args)
    arg_index = {a: i for i, a in enumerate(unique_args)}

    with mpath.open("w") as f:
        for t, args in trace_args_pairs:
            if not os.path.exists(t):
                sys.exit(f"Trace not found: {t}")
            for b in bins:
                if not os.path.isfile(b):
                    sys.exit(f"Binary not found: {b}")
                idx = arg_index[args]
                f.write(f"{b}\t{t}\t{args}\t{idx}\n")
    return str(mpath)

def sbatch_common_prefix(res):
    part = res.get("partition")
    qos  = res.get("qos")
    account = res.get("account")
    nodelist = res.get("nodelist")

    cmd = ["sbatch"]
    if part:
        cmd += [f"--partition={part}"]
    if qos:
        cmd += [f"--qos={qos}"]
    if account:
        cmd += [f"--account={account}"]
    if nodelist:
        cmd += [f"--nodelist={nodelist}"]

    # Pass environment variables to job (ensure CSIM_VENV is passed if set)
    venv = os.environ.get("CSIM_VENV")
    if venv:
        cmd += [f"--export=ALL,CSIM_VENV={venv}"]
    else:
        cmd += ["--export=ALL"]

    return cmd

def submit_in_chunks(run_dir, name, total, res, jobfile, debug_log=None):
    chunk = int(res.get("chunk", 1000))
    tim  = res.get("time", "08:00:00")
    mem  = res.get("mem", "8G")
    cpus = int(res.get("cpus_per_task", 1))

    sbatch_log = Path(run_dir) / "sbatch_cmd.txt"
    jobs_log   = Path(run_dir) / "sbatch_jobs.txt"
    job_ids    = []

    with sbatch_log.open("w") as wf, jobs_log.open("w") as jf:
        piece = 0
        for start in range(0, total, chunk):
            end = min(start + chunk, total) - 1
            jname = f"{name}_p{piece}" if total > chunk else name

            cmd = sbatch_common_prefix(res)
            cmd += [
                f"--array={start}-{end}",
                f"--time={tim}",
                f"--mem={mem}",
                f"--cpus-per-task={cpus}",
                f"--job-name={jname}",
                f"--chdir={run_dir}",
                str(jobfile),
            ]

            line = " ".join(shlex.quote(x) for x in cmd)
            print("submit:", line)
            wf.write(line + "\n")
            if debug_log:
                append_log(debug_log, f"sbatch_cmd: {line}")

            try:
                out, rc = run_capture(cmd, check=True)
            except subprocess.CalledProcessError as e:
                out = getattr(e, "output", "") or ""
                if debug_log:
                    append_log(debug_log, f"sbatch_failed_rc={e.returncode}")
                    append_log(debug_log, "sbatch_failed_out: " + out.strip().replace("\n", "\\n"))
                print(out.strip())
                raise

            print(out.strip())
            if debug_log:
                append_log(debug_log, f"sbatch_rc={rc}")
                append_log(debug_log, "sbatch_out: " + out.strip().replace("\n", "\\n"))

            m = re.search(r"Submitted batch job (\d+)", out)
            if m:
                jid = m.group(1)
                job_ids.append(jid)
                jf.write(jid + "\n")
            else:
                print("WARN: Could not retrieve job ID", file=sys.stderr)
                if debug_log:
                    append_log(debug_log, "WARN: could not parse job id from sbatch output")
            piece += 1

    return job_ids

def wait_for_jobs(job_ids, poll_sec=15, debug_log=None):
    if not job_ids:
        return
    msg = f"Waiting for jobs to finish: {','.join(job_ids)}"
    print(msg)
    if debug_log:
        append_log(debug_log, msg)

    while True:
        out, rc = run_capture(["squeue", "-j", ",".join(job_ids)], check=False)
        if debug_log and rc != 0:
            safe_out = out.strip().replace("\n", "\\n")
            append_log(debug_log, f"squeue_rc={rc} out={safe_out}")

        lines = [ln for ln in out.splitlines() if ln.strip()]
        if len(lines) <= 1:
            print("All jobs finished.")
            if debug_log:
                append_log(debug_log, "All jobs finished.")
            return
        time.sleep(poll_sec)

def summarize_this_run(repo_root, run_dir, baseline="latest",
                       label_map="resche2:schedcost_on,resche_:schedcost_off,ChampSim:latest",
                       img_formats="svg", debug_log=None):
    e2e = Path(repo_root) / "champsim_e2e.py"
    res_dir = Path(run_dir) / "results"
    out_dir = res_dir / "summary_out"
    out_dir.mkdir(parents=True, exist_ok=True)

    e2e_log = out_dir / "e2e_stdout.txt"
    diag_log = out_dir / "diagnostics.txt"

    def d(msg):
        if debug_log:
            append_log(debug_log, msg)
        with diag_log.open("a") as f:
            f.write(msg + "\n")

    d(f"[summarize] run_dir={run_dir}")
    d(f"[summarize] res_dir={res_dir}")
    d(f"[summarize] out_dir={out_dir}")
    d(f"[summarize] e2e={e2e}")
    d(f"[summarize] baseline={baseline}")
    d(f"[summarize] label_map={label_map}")
    d(f"[summarize] img_formats={img_formats}")
    d(f"[summarize] CSIM_VENV={os.environ.get('CSIM_VENV','')}")
    d(f"[summarize] python_exe={choose_python_exe()}")

    txts = sorted(res_dir.glob("*.txt"))
    d(f"[summarize] txt_count={len(txts)}")
    for p in txts[:30]:
        try:
            d(f"[summarize] txt: {p.name} size={p.stat().st_size}")
        except Exception as e:
            d(f"[summarize] WARN: stat fail {p.name}: {e}")

    if not txts:
        d("[summarize] SKIP: no .txt in results/")
        print(f"[summarize] skip: {res_dir} (no .txt)")
        return

    roi_hits = 0
    for p in txts[:30]:
        try:
            s = p.read_text(errors="ignore")
            if "cumulative IPC" in s:
                roi_hits += 1
        except Exception as e:
            d(f"[summarize] WARN: read fail {p.name}: {e}")
    d(f"[summarize] roi_hits_in_first30={roi_hits}")

    pyexe = choose_python_exe()
    cmd = [
        pyexe, str(e2e),
        "--glob", str(res_dir / "*.txt"),
        "--outdir", str(out_dir),
        "--baseline", baseline,
        "--label-map", label_map,
        "--img-formats", img_formats,
    ]
    d("[summarize] cmd=" + " ".join(shlex.quote(x) for x in cmd))
    print("[summarize]", " ".join(shlex.quote(x) for x in cmd))

    out, rc = run_capture(cmd, check=False)
    e2e_log.write_text(out)

    d(f"[summarize] e2e_rc={rc}")
    d(f"[summarize] e2e_stdout_path={e2e_log}")

    if rc != 0:
        d("[summarize] ERROR: champsim_e2e.py failed (see e2e_stdout.txt)")
        print("[summarize] ERROR: champsim_e2e.py failed. See:", e2e_log)
        raise SystemExit(rc)

    d("[summarize] DONE")
    print(f"[summarize] wrote under: {out_dir}")

def write_summarize_sbatch(runner_root, run_dir, baseline, label_map, img_formats):
    """
    Write sbatch script for summarize job with afterok dependency to run_dir
    """
    path = Path(run_dir) / "summarize_afterok.sbatch"
    # Enable venv in bash with same priority as champsim_matrix.sbatch
    script = f"""#!/bin/bash
#SBATCH --job-name=csim_summarize
#SBATCH --output=logs/summarize.%j.out
#SBATCH --error=logs/summarize.%j.err
#SBATCH --time=00:30:00
#SBATCH --mem=2G
#SBATCH --cpus-per-task=1

set -euo pipefail

RUN_DIR="{run_dir}"
RUNNER_ROOT="{runner_root}"

mkdir -p "$RUN_DIR/results/summary_out"

# module は無い環境もあるので落とさない
if command -v module >/dev/null 2>&1; then
  module -q load GCCcore/13.2.0 2>/dev/null || true
  module -q load Python/3.11.5  2>/dev/null || true
fi

VENV_PATH="${{CSIM_VENV:-${{VIRTUAL_ENV:-${{SCRATCH:-$HOME}}/venvs/csim}}}}"
if [[ -f "${{VENV_PATH}}/bin/activate" ]]; then
  # shellcheck disable=SC1090
  source "${{VENV_PATH}}/bin/activate"
else
  echo "[INFO] venv not found, skipping: ${{VENV_PATH}}/bin/activate" >&2
fi
export MPLBACKEND=Agg

PYEXE="python3"
if [[ -x "${{VENV_PATH}}/bin/python3" ]]; then
  PYEXE="${{VENV_PATH}}/bin/python3"
fi

echo "[auto] hostname=$(hostname)" > "$RUN_DIR/results/summary_out/diagnostics.txt"
echo "[auto] VENV_PATH=$VENV_PATH" >> "$RUN_DIR/results/summary_out/diagnostics.txt"
echo "[auto] PYEXE=$PYEXE" >> "$RUN_DIR/results/summary_out/diagnostics.txt"
ls -l "$RUN_DIR/results" >> "$RUN_DIR/results/summary_out/diagnostics.txt" || true

cd "$RUN_DIR"

"$PYEXE" "$RUNNER_ROOT/champsim_e2e.py" \\
  --glob "$RUN_DIR/results/*.txt" \\
  --outdir "$RUN_DIR/results/summary_out" \\
  --baseline "{baseline}" \\
  --label-map "{label_map}" \\
  --img-formats "{img_formats}" \\
  2>&1 | tee "$RUN_DIR/results/summary_out/e2e_stdout.txt"
"""
    path.write_text(script)
    # Set executable permission just in case
    try:
        path.chmod(0o755)
    except Exception:
        pass
    return str(path)

def submit_summarize_job(run_dir, res, dep_job_ids, summarize_sbatch, debug_log=None):
    dep = "afterok:" + ":".join(dep_job_ids)
    cmd = sbatch_common_prefix(res)
    cmd += [
        f"--dependency={dep}",
        f"--chdir={run_dir}",
        summarize_sbatch,
    ]
    line = " ".join(shlex.quote(x) for x in cmd)
    if debug_log:
        append_log(debug_log, f"summarize_sbatch_cmd: {line}")
    out, rc = run_capture(cmd, check=True)
    if debug_log:
        append_log(debug_log, f"summarize_sbatch_rc={rc}")
        append_log(debug_log, "summarize_sbatch_out: " + out.strip().replace("\n", "\\n"))
    m = re.search(r"Submitted batch job (\d+)", out)
    return m.group(1) if m else None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--wait", action="store_true", help="Wait for all jobs to finish (effective for submit only)")
    parser.add_argument("--summarize", action="store_true", help="Summarize this run after wait (blocking)")
    parser.add_argument("--no-auto-summarize", action="store_true", help="Disable automatic afterok summarize job submission")
    parser.add_argument("--baseline", default="latest")
    parser.add_argument("--label-map", default="resche2:schedcost_on,resche_:schedcost_off,ChampSim:latest")
    parser.add_argument("--img-formats", default="svg")
    args = parser.parse_args()

    runner_root = Path(__file__).resolve().parent

    spec = load_yaml(args.recipe)
    name = spec.get("name", "csim_run")
    bins = [os.path.abspath(b) for b in spec.get("bins", [])]
    res = spec.get("resources", {})

    # Determine trace_configs (new format) vs traces+args (legacy format)
    trace_configs = spec.get("trace_configs")
    use_trace_configs = trace_configs is not None

    if use_trace_configs:
        # New format: trace_configs
        trace_args_pairs = expand_trace_configs(trace_configs)
        if not trace_args_pairs:
            sys.exit("No traces found from trace_configs")
        num_traces = len(trace_args_pairs)
    else:
        # Legacy format: traces + args
        traces = expand_traces(spec.get("traces", []))
        argsets = spec.get("args", [])
        if not traces:  sys.exit("traces is empty")
        if not argsets: sys.exit("args is empty")
        num_traces = len(traces)

    if not bins:    sys.exit("bins is empty")

    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = runner_root / "runs" / f"{ts}_{name}"
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (run_dir / "results").mkdir(parents=True, exist_ok=True)

    debug_log = run_dir / "submit_debug.log"
    append_log(str(debug_log), f"recipe={args.recipe}")
    append_log(str(debug_log), f"wait={args.wait} summarize={args.summarize} no_auto={args.no_auto_summarize}")
    append_log(str(debug_log), f"runner_root={runner_root}")
    append_log(str(debug_log), f"run_dir={run_dir}")
    append_log(str(debug_log), f"CSIM_VENV={os.environ.get('CSIM_VENV','')}")
    append_log(str(debug_log), f"sys.executable={sys.executable}")
    append_log(str(debug_log), f"use_trace_configs={use_trace_configs}")

    if use_trace_configs:
        mpath = write_matrix_from_pairs(run_dir, bins, trace_args_pairs)
        total = sum(1 for _ in open(mpath, "r"))
        print(f"tasks={total} bins={len(bins)} traces={num_traces} (trace_configs mode)")
        append_log(str(debug_log), f"tasks={total} bins={len(bins)} traces={num_traces} (trace_configs mode)")
    else:
        mpath = write_matrix(run_dir, bins, traces, argsets)
        total = sum(1 for _ in open(mpath, "r"))
        print(f"tasks={total} bins={len(bins)} traces={len(traces)} args={len(argsets)}")
        append_log(str(debug_log), f"tasks={total} bins={len(bins)} traces={len(traces)} args={len(argsets)}")

    jobfile = runner_root / "champsim_matrix.sbatch"
    if not jobfile.is_file():
        sys.exit(f"Template not found: {jobfile}")
    append_log(str(debug_log), f"jobfile={jobfile}")

    job_ids = submit_in_chunks(str(run_dir), name, total, res, jobfile, debug_log=str(debug_log))
    print(f"Run dir: {run_dir}")
    append_log(str(debug_log), f"job_ids={','.join(job_ids)}")

    # If inline summarize is requested, block and execute
    if args.summarize:
        args.wait = True
        wait_for_jobs(job_ids, debug_log=str(debug_log))
        summarize_this_run(
            runner_root, run_dir,
            baseline=args.baseline,
            label_map=args.label_map,
            img_formats=args.img_formats,
            debug_log=str(debug_log),
        )
        return 0

    # If only wait is specified, just wait (no summary)
    if args.wait:
        wait_for_jobs(job_ids, debug_log=str(debug_log))
        return 0

    # Default behavior: Automatically submit afterok summarize job (non-blocking)
    if not args.no_auto_summarize:
        summarize_sbatch = write_summarize_sbatch(
            str(runner_root), str(run_dir),
            args.baseline, args.label_map, args.img_formats
        )
        summ_jid = submit_summarize_job(str(run_dir), res, job_ids, summarize_sbatch, debug_log=str(debug_log))
        if summ_jid:
            print(f"Submitted summarize job: {summ_jid} (afterok on {','.join(job_ids)})")
            append_log(str(debug_log), f"summarize_job_id={summ_jid}")
        else:
            print("WARN: summarize job id could not be parsed")
            append_log(str(debug_log), "WARN: summarize job id could not be parsed")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
