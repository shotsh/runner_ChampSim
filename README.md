# runner README

A minimal runner for mass execution of ChampSim on Slurm environments like Grace.
Configure using a single YAML file, submit array jobs with `submit.py`, and results are organized per-run in dedicated folders.
Code comments use matching numbers to correlate processing flow.

---

## Directory Structure

```
~/champsim-work/runner/
  submit.py                 # Submission script
  champsim_matrix.sbatch    # Execution template (Slurm batch)
  recipes/
    runspec.yaml            # Experiment recipe (edit this primarily)
  runs/
    <timestamp>_<name>/     # Auto-generated per submission (contains results)
      matrix.tsv
      sbatch_cmd.txt
      logs/
      results/
```

---

## Prerequisites

* Python 3.x
* PyYAML

  ```
  pip install --user pyyaml
  ```
* Slurm environment with job submission capability
  Commands like `squeue -u $USER` should work

---

## Quick Start

1. Edit `recipes/runspec.yaml` `[1]`
2. Submit

   ```
   cd ~/champsim-work/runner
   python3 submit.py --recipe recipes/runspec.yaml   # [2]
   ```
   - By default, a summarize job is automatically submitted with afterok dependency after compute jobs complete.
   - Use `--no-auto-summarize` to disable automatic summarize submission.
3. Monitor status

   ```
   squeue -u $USER                                   # Manual monitoring
   ```
4. Check results

   ```
runs/<timestamp>_<name>/results/*.txt
runs/<timestamp>_<name>/logs/*.out, *.err
```

---

## Options (as needed)

```
--wait                 # Wait for job completion after submit (does not run summarize)
--summarize            # Run summarize locally after wait (blocking)
--no-auto-summarize    # Disable automatic afterok summarize job submission
--baseline <name>      # Baseline label for summarize (default: latest)
--label-map <map>      # Label map for summarize (default: resche2:..., resche_:..., ChampSim:latest)
--img-formats <fmt>    # Image formats for summarize (default: svg, comma-separated for multiple)
```

---

## Overall Pipeline (numbered)

1. You edit `recipes/runspec.yaml`
2. Run `python3 submit.py --recipe ...`
3. `submit.py` reads the YAML
4. `submit.py` expands trace patterns via glob
   - Legacy format: Matrix expansion of `traces` × `args`
   - New format: Generate (trace, args) pairs from `trace_configs`
5. Create run folder `runs/<timestamp>_<name>/`
6. Write BIN×(TRACE,ARGS) combinations to `matrix.tsv`
7. Count total tasks N
8. Split N into chunks of 1000 by default
9. Submit each chunk with `sbatch --array=<start>-<end>` and record in `sbatch_cmd.txt`
10. Slurm queues the array and assigns `SLURM_ARRAY_TASK_ID` to each task
11. `champsim_matrix.sbatch` starts on compute node
12. Read target row from `matrix.tsv` based on array index
13. Parse tab-separated fields into BIN, TRACE, ARGS
14. Execute `srun "$BIN" $ARGS "$TRACE"` and save results to `results/`
15. Slurm logs saved to `logs/`
16. (Default) After main jobs complete, submit summarize job with afterok dependency, output to `results/summary_out/`
17. Monitor progress with `squeue` as needed

---

## File-by-File Number Reference

### recipes/runspec.yaml

* `[1]` Edit target
* `[4]` `traces:` are glob-expanded
* `[8][9]` Change chunk size with `resources.chunk`
* `partition` / `qos` / `account` / `nodelist` specified only when needed (passed to sbatch)
* `time`, `mem`, `cpus_per_task` are passed directly to `sbatch`

### champsim_matrix.sbatch

* `[10]` `SLURM_ARRAY_TASK_ID` is the array index automatically provided by Slurm
* `[12]` Target row extracted with `sed`
* `[13]` `cut -f1,2,3-` extracts BIN, TRACE, ARGS
* `[14]` `srun` executes ChampSim and saves to `results/`
* `[15]` `%x.%A.%a` makes log filenames unique

### submit.py

* `[3]` Load runspec
* `[4]` Resolve to actual files via glob expansion
  - Legacy format: `expand_traces()` expands traces
  - New format: `expand_trace_configs()` expands trace_configs
* `[5]` Create run folder with `logs/` and `results/`
* `[6]` Write combinations to `matrix.tsv`
  - Legacy format: `write_matrix()` for BIN×TRACE×ARGS cartesian product
  - New format: `write_matrix_from_pairs()` for BIN×(TRACE,ARGS) pairs
* `[7]` Calculate total task count
* `[8][9]` Auto-split into 1000 per chunk and submit as array, record in `sbatch_cmd.txt`
* By default also submits afterok summarize job (`--no-auto-summarize` to disable, `--summarize` to run locally)

---

## Understanding Output Files

```
runs/<timestamp>_<name>/
  matrix.tsv          # One line per task (BIN<TAB>TRACE<TAB>ARGS<TAB>ARGS_IDX)
  sbatch_cmd.txt      # Record of sbatch commands used for submission
  sbatch_jobs.txt     # List of submitted job IDs
  submit_debug.log    # Debug log from submit.py
  summarize_afterok.sbatch  # Auto-generated summarize script
  logs/               # Slurm standard logs
    <name>.<jobid>.<arrayid>.out      # Without chunk splitting
    <name>.<jobid>.<arrayid>.err
    <name>_p0.<jobid>.<arrayid>.out   # With chunk splitting (_p0, _p1, ...)
    <name>_p0.<jobid>.<arrayid>.err
  results/            # ChampSim output
    <arrayid>_<tracename>_<repo>_<bin_name>_<args_idx>_j<jobid>.txt
    summary_out/      # Summarize output (auto/inline shared)
      diagnostics.txt
      e2e_stdout.txt
      *.svg / *.png / *.csv (depending on img-formats)
```

* Filenames in `results/` follow: `<array_id(zero-padded)>_<trace_name>_<REPO_name>_<binary_name>_<ARGS_number>_j<JobID>.txt`
* Corresponding row can be found with `sed -n '<index+1>p' matrix.tsv`

---

## FAQ

* **What is TSV?**
  Tab Separated Values. A tab-delimited text table similar to CSV
* **What is glob?**
  Filename pattern expansion using `*`, `?`, `[]`
  Example: `/path/gap/bc-*.trace.gz`
* **Do I define SLURM_ARRAY_TASK_ID myself?**
  No. Slurm automatically provides it to each task when using array jobs
* **Is mass submission safe?**
  By default, jobs are split into chunks of 1000 and submitted as arrays to comply with site array limits
  For sites with smaller limits, reduce `resources.chunk`

---

## Monitoring and Operations Tips

* Monitoring

  ```
  squeue -u $USER
  squeue -u $USER -o "%.18i %.9P %.8j %.8T %.10M %.9l %.6D %R" | grep <name>
  ```
* Queue status

  ```
  sinfo
  ```
* Site array limit

  ```
  scontrol show config | grep -i MaxArraySize
  ```

---

## Troubleshooting

* `bins is empty / traces is empty / args is empty`
  Check the corresponding section in runspec.yaml. Absolute paths recommended
* `No traces found from trace_configs`
  Check traces paths in trace_configs. Verify glob patterns match
* `Template not found`
  Verify `champsim_matrix.sbatch` is in the same directory as `submit.py`
* Jobs submitted but no results
  Check `logs/*.err`. Review trace paths and permissions
* Hit array limit
  Lower `resources.chunk` to below the limit

---

## Recipe Format

There are two recipe YAML formats.

### Legacy Format (traces + args)

Execute all combinations of traces × args (matrix expansion).

```yaml
name: spec_sample
bins:
  - /home/sshintani/champsim-work/ChampSim/bin/champsim
traces:
  - /scratch/user/sshintani/traces/speccpu/403.gcc-*.trace.gz
  - /scratch/user/sshintani/traces/gap/bfs-3.trace.gz
args:
  - "--warmup_instructions 100000000 --simulation_instructions 100000000"
resources:
  partition: cpu-research      # Optional
  qos: olympus-cpu-research    # Optional
  account: myaccount           # Optional
  nodelist: node01             # Optional
  time: 08:00:00
  mem: 8G
  cpus_per_task: 1
  chunk: 1000
```

**Total tasks**: `bins count × traces count × args count`

### New Format (trace_configs)

Specify individual args per trace. Useful when different warmup/simulation settings are needed.

**Notes:**
- `args` is **required** for each trace_config (error if omitted)
- Same trace can be listed in multiple configs (executed multiple times with different args)

```yaml
name: wp_microbench
bins:
  - /home/sshintani/champsim-work/ChampSim/bin/champsim

trace_configs:
  # Traces with same settings can be grouped
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

**Total tasks**: `bins count × total traces across all trace_configs`

### Format Auto-Detection

- `trace_configs` key exists → New format
- `traces` + `args` keys exist → Legacy format

### Resources Notes

- `time` default 08:00:00, `mem` default 8G, `cpus_per_task` default 1, `chunk` default 1000
- `partition` / `qos` / `account` / `nodelist` specified only when needed (passed directly to sbatch)
