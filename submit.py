#!/usr/bin/env python3
"""
最小の送信フロー。番号は全体パイプラインと対応:
  [2] CLIで実行
  [3] runspec.yaml を読み込む
  [4] トレースのグロブ展開
  [5] ラン用フォルダを作成
  [6] 直積を matrix.tsv に書く
  [7] 総タスク数 N を数える
  [8] N を既定 1000 件ずつに分割（resources.chunk で変更可）
  [9] 各チャンクを sbatch --array=<start>-<end> で投入し、sbatch_cmd.txt に記録
"""
import argparse, os, sys, glob, datetime, subprocess, shlex
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
    """
    [4] グロブ展開の最小実装:
      - 各パターンを glob 展開
      - 1件もヒットしない場合は実在ファイルなら採用
      - どちらでも無ければ警告
      - 重複は順序保持で除去
    """
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
    """[6] BIN×TRACE×ARGS の直積を matrix.tsv にタブ区切りで書く"""
    mpath = Path(run_dir) / "matrix.tsv"
    with mpath.open("w") as f:
        for b in bins:
            if not os.path.isfile(b):
                sys.exit(f"バイナリが見つかりません: {b}")
            for t in traces:
                for a in argsets:
                    f.write(f"{b}\t{t}\t{a}\n")
    return str(mpath)

def submit_in_chunks(run_dir, name, total, res, jobfile):
    """
    [8][9] 総タスク total を chunk ごとに分割して複数回 sbatch する
      例: N=237 なら --array=0-236 を1回
          N=2300 なら --array=0-999, 1000-1999, 2000-2299 を3回
    """
    chunk = int(res.get("chunk", 1000))  # 既定 1000
    part = res.get("partition")
    tim  = res.get("time", "08:00:00")
    mem  = res.get("mem", "8G")
    cpus = int(res.get("cpus_per_task", 1))

    sbatch_log = Path(run_dir) / "sbatch_cmd.txt"
    with sbatch_log.open("w") as wf:
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
            print("submit:", line)         # [9] 投入コマンドを表示
            wf.write(line + "\n")          # [9] 再現用に記録
            subprocess.run(cmd, check=True) # [9] 実際に sbatch 実行
            piece += 1

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", required=True)  # [2]
    args = parser.parse_args()

    runner_root = Path(__file__).resolve().parent

    spec = load_yaml(args.recipe)                  # [3]
    name = spec.get("name", "csim_run")
    bins = [os.path.abspath(b) for b in spec.get("bins", [])]
    traces = expand_traces(spec.get("traces", [])) # [4]
    argsets = spec.get("args", [])
    res = spec.get("resources", {})

    if not bins:    sys.exit("bins が空です")
    if not traces:  sys.exit("traces が空です")
    if not argsets: sys.exit("args が空です")

    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = runner_root / "runs" / f"{ts}_{name}"
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)    # [5]
    (run_dir / "results").mkdir(parents=True, exist_ok=True) # [5]

    mpath = write_matrix(run_dir, bins, traces, argsets)     # [6]
    total = sum(1 for _ in open(mpath, "r"))                 # [7]
    print(f"tasks={total} bins={len(bins)} traces={len(traces)} args={len(argsets)}")

    jobfile = runner_root / "champsim_matrix.sbatch"
    if not jobfile.is_file():
        sys.exit(f"テンプレートがありません: {jobfile}")

    submit_in_chunks(str(run_dir), name, total, res, jobfile) # [8][9]
    print(f"Run dir: {run_dir}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
