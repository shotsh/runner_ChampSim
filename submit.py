#!/usr/bin/env python3
"""
最小の送信フロー + オプションで待機と集計
  [2] CLIで実行
  [3] runspec.yaml を読み込む
  [4] トレースのグロブ展開
  [5] ラン用フォルダを作成
  [6] 直積を matrix.tsv に書く
  [7] 総タスク数 N を数える
  [8] N を既定 chunk 件ずつに分割して sbatch --array 提出（ジョブIDを保存）
  [9] --wait で全ジョブ終了待ち
 [10] --summarize でこの run の results/*.txt を集計（CSV, SVG）

追加したデバッグ出力:
- runs/<run>/submit_debug.log
- runs/<run>/results/summary_out/diagnostics.txt
- runs/<run>/results/summary_out/e2e_stdout.txt
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
    """[3] runspec.yaml を辞書にロード"""
    try:
        import yaml
    except ImportError:
        print("PyYAML を入れてください: pip install --user pyyaml", file=sys.stderr)
        sys.exit(1)
    with open(path, "r") as f:
        return yaml.safe_load(f)

def expand_traces(patterns):
    """[4] グロブ展開（順序保持・重複除去）"""
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

def write_matrix(run_dir, bins, traces, argsets):
    """BIN×TRACE×ARGS の直積を matrix.tsv に書く。第4列は ARGS の番号(0始まり)。"""
    mpath = Path(run_dir) / "matrix.tsv"
    arg_index = {a: i for i, a in enumerate(argsets)}
    with mpath.open("w") as f:
        for t in traces:
            if not os.path.exists(t):
                sys.exit(f"トレースが見つかりません: {t}")
            for b in bins:
                if not os.path.isfile(b):
                    sys.exit(f"バイナリが見つかりません: {b}")
                for a in argsets:
                    idx = arg_index[a]
                    f.write(f"{b}\t{t}\t{a}\t{idx}\n")
    return str(mpath)

def submit_in_chunks(run_dir, name, total, res, jobfile, debug_log=None):
    """
    [8] 総タスク total を chunk ごとに分割して sbatch
        返り値: 提出した Slurm ジョブIDのリスト
    """
    chunk = int(res.get("chunk", 1000))  # 既定 1000
    part = res.get("partition")
    qos  = res.get("qos")
    account = res.get("account")
    nodelist = res.get("nodelist")
    tim  = res.get("time", "08:00:00")
    mem  = res.get("mem", "8G")
    cpus = int(res.get("cpus_per_task", 1))

    # sbatch に環境変数を渡す（CSIM_VENVがあれば確実にジョブ側に渡す）
    export_args = ["--export=ALL"]
    venv = os.environ.get("CSIM_VENV")
    if venv:
        export_args = [f"--export=ALL,CSIM_VENV={venv}"]

    sbatch_log = Path(run_dir) / "sbatch_cmd.txt"
    jobs_log   = Path(run_dir) / "sbatch_jobs.txt"
    job_ids    = []

    with sbatch_log.open("w") as wf, jobs_log.open("w") as jf:
        piece = 0
        for start in range(0, total, chunk):
            end = min(start + chunk, total) - 1  # inclusive
            jname = f"{name}_p{piece}" if total > chunk else name

            cmd = ["sbatch"]
            if part:
                cmd += [f"--partition={part}"]
            if qos:
                cmd += [f"--qos={qos}"]
            if account:
                cmd += [f"--account={account}"]
            if nodelist:
                cmd += [f"--nodelist={nodelist}"]

            cmd += export_args

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
                print("WARN: ジョブIDを取得できませんでした", file=sys.stderr)
                if debug_log:
                    append_log(debug_log, "WARN: could not parse job id from sbatch output")
            piece += 1

    return job_ids

def wait_for_jobs(job_ids, poll_sec=15, debug_log=None):
    """[9] すべての job_id が消えるまで待機"""
    if not job_ids:
        return
    msg = f"Waiting for jobs to finish: {','.join(job_ids)}"
    print(msg)
    if debug_log:
        append_log(debug_log, msg)

    while True:
        out, rc = run_capture(["squeue", "-j", ",".join(job_ids)], check=False)

        # Python3.6 f-stringの制約回避: 置換は先にやる
        if debug_log and rc != 0:
            safe_out = out.strip().replace("\n", "\\n")
            append_log(debug_log, f"squeue_rc={rc} out={safe_out}")

        lines = [ln for ln in out.splitlines() if ln.strip()]
        if len(lines) <= 1:  # headerのみ
            print("All jobs finished.")
            if debug_log:
                append_log(debug_log, "All jobs finished.")
            return
        time.sleep(poll_sec)

def summarize_this_run(repo_root, run_dir, baseline="latest",
                       label_map="resche2:schedcost_on,resche_:schedcost_off,ChampSim:latest",
                       img_formats="svg", debug_log=None):
    """[10] この run の results/*.txt を集計（CSV, SVG）+ デバッグログ出力"""
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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--wait", action="store_true", help="全ジョブ終了を待つ")
    parser.add_argument("--summarize", action="store_true", help="--wait 後にこの run を集計")
    parser.add_argument("--baseline", default="latest")
    parser.add_argument("--label-map", default="resche2:schedcost_on,resche_:schedcost_off,ChampSim:latest")
    parser.add_argument("--img-formats", default="svg")
    args = parser.parse_args()

    if args.summarize:
        args.wait = True

    runner_root = Path(__file__).resolve().parent

    spec = load_yaml(args.recipe)
    name = spec.get("name", "csim_run")
    bins = [os.path.abspath(b) for b in spec.get("bins", [])]
    traces = expand_traces(spec.get("traces", []))
    argsets = spec.get("args", [])
    res = spec.get("resources", {})

    if not bins:    sys.exit("bins が空です")
    if not traces:  sys.exit("traces が空です")
    if not argsets: sys.exit("args が空です")

    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = runner_root / "runs" / f"{ts}_{name}"
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (run_dir / "results").mkdir(parents=True, exist_ok=True)

    debug_log = run_dir / "submit_debug.log"
    append_log(str(debug_log), f"recipe={args.recipe}")
    append_log(str(debug_log), f"wait={args.wait} summarize={args.summarize}")
    append_log(str(debug_log), f"runner_root={runner_root}")
    append_log(str(debug_log), f"run_dir={run_dir}")
    append_log(str(debug_log), f"CSIM_VENV={os.environ.get('CSIM_VENV','')}")
    append_log(str(debug_log), f"sys.executable={sys.executable}")

    mpath = write_matrix(run_dir, bins, traces, argsets)
    total = sum(1 for _ in open(mpath, "r"))
    print(f"tasks={total} bins={len(bins)} traces={len(traces)} args={len(argsets)}")
    append_log(str(debug_log), f"tasks={total} bins={len(bins)} traces={len(traces)} args={len(argsets)}")

    jobfile = runner_root / "champsim_matrix.sbatch"
    if not jobfile.is_file():
        sys.exit(f"テンプレートがありません: {jobfile}")
    append_log(str(debug_log), f"jobfile={jobfile}")

    job_ids = submit_in_chunks(str(run_dir), name, total, res, jobfile, debug_log=str(debug_log))
    print(f"Run dir: {run_dir}")
    append_log(str(debug_log), f"job_ids={','.join(job_ids)}")

    if args.wait:
        wait_for_jobs(job_ids, debug_log=str(debug_log))

    if args.summarize:
        summarize_this_run(
            runner_root, run_dir,
            baseline=args.baseline,
            label_map=args.label_map,
            img_formats=args.img_formats,
            debug_log=str(debug_log),
        )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
