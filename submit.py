#!/usr/bin/env python3
"""
最小の送信フロー + オプションで待機と集計
  [2] CLIで実行
  [3] runspec.yaml を読み込む
  [4] トレースのグロブ展開
  [5] ラン用フォルダを作成
  [6] 直積を matrix.tsv に書く
  [7] 総タスク数 N を数える
  [8] N を既定 1000 件ずつに分割して sbatch --array 提出（ジョブIDを保存）
  [9] --wait で全ジョブ終了待ち
 [10] --summarize でこの run の results/*.txt を集計（CSV, SVG）
"""
import argparse, os, sys, glob, datetime, subprocess, shlex, re, time
from pathlib import Path

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
    """[6] BIN×TRACE×ARGS の直積を matrix.tsv に書く"""
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

def submit_in_chunks(run_dir, name, total, res, jobfile):
    """
    [8] 総タスク total を chunk ごとに分割して sbatch
        返り値: 提出した Slurm ジョブIDのリスト
    """
    chunk = int(res.get("chunk", 1000))
    part  = res.get("partition")
    tim   = res.get("time", "08:00:00")
    mem   = res.get("mem", "8G")
    cpus  = int(res.get("cpus_per_task", 1))

    sbatch_log = Path(run_dir) / "sbatch_cmd.txt"
    jobs_log   = Path(run_dir) / "sbatch_jobs.txt"
    job_ids    = []

    with sbatch_log.open("w") as wf, jobs_log.open("w") as jf:
        piece = 0
        for start in range(0, total, chunk):
            end = min(start + chunk, total) - 1  # inclusive
            jname = f"{name}_p{piece}" if total > chunk else name
            cmd = [
                "sbatch",
                f"--array={start}-{end}",
                f"--time={tim}",
                f"--mem={mem}",
                f"--cpus-per-task={cpus}",
                f"--job-name={jname}",
                f"--chdir={run_dir}",
                str(jobfile),
            ]
            if part:
                cmd.insert(1, f"--partition={part}")
            line = " ".join(shlex.quote(x) for x in cmd)
            print("submit:", line)
            wf.write(line + "\n")

            out = subprocess.run(cmd, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout
            print(out.strip())
            m = re.search(r"Submitted batch job (\d+)", out)
            if m:
                jid = m.group(1)
                job_ids.append(jid)
                jf.write(jid + "\n")
            else:
                print("WARN: ジョブIDを取得できませんでした", file=sys.stderr)
            piece += 1

    return job_ids

def wait_for_jobs(job_ids, poll_sec=15):
    """[9] すべての job_id が消えるまで待機"""
    if not job_ids:
        return
    print(f"Waiting for jobs to finish: {','.join(job_ids)}")
    while True:
        out = subprocess.run(["squeue", "-j", ",".join(job_ids)], text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout
        lines = [ln for ln in out.splitlines() if ln.strip()]
        if len(lines) <= 1:  # headerのみ
            print("All jobs finished.")
            return
        time.sleep(poll_sec)

def summarize_this_run(repo_root, run_dir, baseline="latest",
                       label_map="resche2:schedcost_on,resche_:schedcost_off,ChampSim:latest",
                       img_formats="svg"):
    """[10] この run の results/*.txt を集計"""
    e2e = Path(repo_root) / "champsim_e2e.py"
    res_dir = Path(run_dir) / "results"
    out_dir = res_dir / "summary_out"
    out_dir.mkdir(parents=True, exist_ok=True)

    # .txt が1つも無いならスキップ
    txts = list(res_dir.glob("*.txt"))
    if not txts:
        print(f"[summarize] skip: {res_dir} (no .txt)")
        return

    cmd = [
        "python3", str(e2e),
        "--glob", str(res_dir / "*.txt"),
        "--outdir", str(out_dir),
        "--baseline", baseline,
        "--label-map", label_map,
        "--img-formats", img_formats,
    ]
    print("[summarize] ", " ".join(shlex.quote(x) for x in cmd))
    subprocess.run(cmd, check=True)
    print(f"[summarize] wrote: {out_dir}/summary.csv, normalized_ipc.csv, *.svg")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", required=True)             # [2]
    parser.add_argument("--wait", action="store_true", help="全ジョブ終了を待つ")
    parser.add_argument("--summarize", action="store_true", help="--wait 後にこの run を集計")
    # まとめ時の軽い調整
    parser.add_argument("--baseline", default="latest")
    parser.add_argument("--label-map", default="resche2:schedcost_on,resche_:schedcost_off,ChampSim:latest")
    parser.add_argument("--img-formats", default="svg")
    args = parser.parse_args()

    runner_root = Path(__file__).resolve().parent

    spec = load_yaml(args.recipe)                              # [3]
    name = spec.get("name", "csim_run")
    bins = [os.path.abspath(b) for b in spec.get("bins", [])]
    traces = expand_traces(spec.get("traces", []))             # [4]
    argsets = spec.get("args", [])
    res = spec.get("resources", {})

    if not bins:    sys.exit("bins が空です")
    if not traces:  sys.exit("traces が空です")
    if not argsets: sys.exit("args が空です")

    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = runner_root / "runs" / f"{ts}_{name}"
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)      # [5]
    (run_dir / "results").mkdir(parents=True, exist_ok=True)   # [5]

    mpath = write_matrix(run_dir, bins, traces, argsets)       # [6]
    total = sum(1 for _ in open(mpath, "r"))                   # [7]
    print(f"tasks={total} bins={len(bins)} traces={len(traces)} args={len(argsets)}")

    jobfile = runner_root / "champsim_matrix.sbatch"
    if not jobfile.is_file():
        sys.exit(f"テンプレートがありません: {jobfile}")

    job_ids = submit_in_chunks(str(run_dir), name, total, res, jobfile)  # [8]
    print(f"Run dir: {run_dir}")

    if args.wait or args.summarize:
        wait_for_jobs(job_ids)                                  # [9]

    if args.summarize:
        summarize_this_run(runner_root, run_dir,                # [10]
                           baseline=args.baseline,
                           label_map=args.label_map,
                           img_formats=args.img-formats)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
