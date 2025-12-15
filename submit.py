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

# ---------------------------
# subprocess compatibility
# ---------------------------
def run_capture(cmd, check=True):
    """
    Python 3.6 (universal_newlines) と 3.7+ (text) 両対応で
    stdout を文字列として取得する。
    stderr は stdout に統合する。

    check=True の場合、失敗時に sbatch/squeue の出力を表示して終了する。
    """
    kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if sys.version_info >= (3, 7):
        kwargs["text"] = True
    else:
        kwargs["universal_newlines"] = True

    p = subprocess.run(cmd, **kwargs)
    out = p.stdout or ""

    if check and p.returncode != 0:
        if out.strip():
            print(out.strip(), file=sys.stderr)
        raise SystemExit(f"ERROR: command failed (rc={p.returncode}): {cmd}")

    return out

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
        pat = os.path.expanduser(pat)  # ★ 追加: ~ 展開
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
    """BIN×TRACE×ARGS の直積を matrix.tsv に書く。
       配列IDは trace 固定 → bin → args の順で付く。
       第4列は ARGS の番号(0始まり)。
    """
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
    chunk = int(res.get("chunk", 1000))  # 既定 1000
    part = res.get("partition")
    qos  = res.get("qos")

    # ★ 変更: account は env を優先（Grace では SLURM_ACCOUNT=132678812657 を設定）
    account = os.environ.get("SLURM_ACCOUNT") or res.get("account")

    nodelist = res.get("nodelist")
    tim  = res.get("time", "08:00:00")
    mem  = res.get("mem", "8G")
    cpus = int(res.get("cpus_per_task", 1))
    nodes = res.get("nodes")          # optional
    ntasks = res.get("ntasks")        # optional
    output = res.get("output")        # optional (例: logs/slurm_%x_%A_%a.out)

    sbatch_log = Path(run_dir) / "sbatch_cmd.txt"
    jobs_log   = Path(run_dir) / "sbatch_jobs.txt"
    job_ids    = []

    with sbatch_log.open("w") as wf, jobs_log.open("w") as jf:
        piece = 0
        for start in range(0, total, chunk):
            end = min(start + chunk, total) - 1  # inclusive
            jname = f"{name}_p{piece}" if total > chunk else name

            cmd = ["sbatch"]

            # リソース指定（必要なものだけ付ける）
            if part:
                cmd += [f"--partition={part}"]
            if qos:
                cmd += [f"--qos={qos}"]
            if account:
                cmd += [f"--account={account}"]
            if nodelist:
                cmd += [f"--nodelist={nodelist}"]
            if nodes:
                cmd += [f"--nodes={nodes}"]
            if ntasks:
                cmd += [f"--ntasks={ntasks}"]
            if output:
                cmd += [f"--output={output}"]

            # 共通オプション
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

            out = run_capture(cmd, check=True)
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
        out = run_capture(["squeue", "-j", ",".join(job_ids)], check=False)
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
    print(f"[summarize] wrote: {out_dir}/summary.csv, normalized_ipc.csv, *.{img_formats}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--wait", action="store_true", help="全ジョブ終了を待つ")
    parser.add_argument("--summarize", action="store_true", help="--wait 後にこの run を集計")
    parser.add_argument("--baseline", default="latest")
    parser.add_argument("--label-map", default="resche2:schedcost_on,resche_:schedcost_off,ChampSim:latest")
    parser.add_argument("--img-formats", default="svg")
    args = parser.parse_args()

    runner_root = Path(__file__).resolve().parent

    spec = load_yaml(args.recipe)
    name = spec.get("name", "csim_run")

    # ★ 変更: ~ 展開してから abspath
    bins = [os.path.abspath(os.path.expanduser(b)) for b in spec.get("bins", [])]

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

    mpath = write_matrix(run_dir, bins, traces, argsets)
    total = sum(1 for _ in open(mpath, "r"))
    print(f"tasks={total} bins={len(bins)} traces={len(traces)} args={len(argsets)}")

    jobfile = runner_root / "champsim_matrix.sbatch"
    if not jobfile.is_file():
        sys.exit(f"テンプレートがありません: {jobfile}")

    job_ids = submit_in_chunks(str(run_dir), name, total, res, jobfile)
    print(f"Run dir: {run_dir}")

    if args.wait or args.summarize:
        wait_for_jobs(job_ids)

    if args.summarize:
        summarize_this_run(runner_root, run_dir,
                           baseline=args.baseline,
                           label_map=args.label_map,
                           img_formats=args.img-formats)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
