# services/jmeter_runner.py

from fastmcp import Context  # ✅ FastMCP 3.x import
import os
import subprocess
import uuid
import time
import csv
import math
import statistics
import signal
import sys
from dotenv import load_dotenv
from utils.config import load_config, load_jmeter_config

# Load environment variables (API keys, secrets, etc.)
load_dotenv()

# Load configuration
CONFIG = load_config()
JMETER_CONFIG = CONFIG.get('jmeter', {})
ARTIFACTS_PATH = CONFIG['artifacts']['artifacts_path']
JMX_CONFIG = load_jmeter_config()

# ----------------------------------------------------------
# Main JMeter Runner Functions
# ----------------------------------------------------------

def list_jmeter_scripts_for_run(test_run_id: str) -> dict:
    """
    Lists existing JMeter .jmx scripts for a given test_run_id under:
        <ARTIFACTS_PATH>/<test_run_id>/jmeter

    Does NOT create the directory if it doesn't exist.
    Returns:
        dict: {
            "test_run_id": str,
            "artifact_dir": str,
            "scripts": [
                {
                    "filename": str,
                    "full_path": str,
                    "size_bytes": int,
                    "modified_time_utc": str
                },
                ...
            ],
            "count": int,
            "status": "OK" | "NOT_FOUND" | "EMPTY",
            "message": str
        }
    """
    artifact_dir = os.path.join(ARTIFACTS_PATH, str(test_run_id), "jmeter")

    if not os.path.isdir(artifact_dir):
        return {
            "test_run_id": str(test_run_id),
            "artifact_dir": artifact_dir,
            "scripts": [],
            "count": 0,
            "status": "NOT_FOUND",
            "message": "No JMeter artifact directory found for this test_run_id."
        }

    scripts = []
    for name in os.listdir(artifact_dir):
        if not name.lower().endswith(".jmx"):
            continue

        full_path = os.path.join(artifact_dir, name)
        try:
            size_bytes = os.path.getsize(full_path)
            mtime = os.path.getmtime(full_path)
            modified_time_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime))
        except OSError:
            size_bytes = None
            modified_time_utc = None

        scripts.append(
            {
                "filename": name,
                "full_path": full_path,
                "size_bytes": size_bytes,
                "modified_time_utc": modified_time_utc,
            }
        )

    status = "OK" if scripts else "EMPTY"
    message = (
        "JMeter scripts found."
        if scripts
        else "Artifact directory exists but contains no .jmx files."
    )

    return {
        "test_run_id": str(test_run_id),
        "artifact_dir": artifact_dir,
        "scripts": scripts,
        "count": len(scripts),
        "status": status,
        "message": message,
    }

async def run_jmeter_test(test_run_id, jmx_path, ctx):
    """
    Starts a new JMeter test execution.
    Args:
        test_run_id (str): Unique test run identifier.
        jmx_path (str): Path to the JMeter JMX test plan.
        ctx (Context, optional): Workflow context.
    Returns:
        dict: Run status, artifact locations, and error (if any).
    """
    bin_dir = _get_jmeter_bin()
    start_exe = _get_start_exe()
    jtl_output = _make_jtl_path(test_run_id)
    log_output = _make_log_path(test_run_id)
    summary_output = _make_summary_path(test_run_id)

    cmd = [
        os.path.join(bin_dir, start_exe),
        f'-n',
        f'-t', jmx_path,
        f'-l', jtl_output,
        f'-j', log_output
    ]

    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        run_id = str(uuid.uuid4())

        # Give JMeter a moment to fail fast if something is wrong
        time.sleep(1.0)
        return_code = process.poll()

        if return_code is not None:
            out, err = process.communicate(timeout=5)
            status = "FAILED_TO_START"
            await ctx.set_state("run_status", status)
            await ctx.set_state("error", err.decode(errors="ignore"))
            return {
                "run_id": None,
                "status": status,
                "test_run_id": test_run_id,
                "cmd": " ".join(cmd),
                "jtl_path": jtl_output,
                "log_path": log_output,
                "summary_path": summary_output,
                "stdout": out.decode(errors="ignore"),
                "stderr": err.decode(errors="ignore"),
            }

        status = "RUNNING"
        await ctx.set_state("last_run_id", run_id)
        await ctx.set_state("run_status", status)
        await ctx.set_state("jmeter_pid", process.pid)

        return {
            "run_id": run_id,
            "status": status,
            "test_run_id": test_run_id,
            "cmd": " ".join(cmd),
            "jtl_path": jtl_output,
            "log_path": log_output,
            "summary_path": summary_output,
            "pid": process.pid,
            "start_time": time.time()
        }
    except Exception as e:
        await ctx.set_state("run_status", "ERROR")
        await ctx.set_state("error", str(e))
        return {
            "run_id": None,
            "status": "ERROR",
            "error": str(e)
        }

async def stop_running_test(test_run_id, ctx):
    """
    Stops a running JMeter test execution.
    Args:
        test_run_id (str): Unique test run identifier.
        ctx (Context): Workflow context.
    Returns:
        dict: Stop status and error info.
    """
    bin_dir = _get_jmeter_bin()
    stop_exe = _get_stop_exe()
    stop_cmd = [os.path.join(bin_dir, stop_exe)]

    try:
        proc = subprocess.Popen(stop_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = proc.communicate(timeout=10)
        status = "STOPPED" if proc.returncode == 0 else "ERROR"
        await ctx.set_state("run_status", status)
        return {
            "test_run_id": test_run_id,
            "cmd": " ".join(stop_cmd),
            "output": out.decode(),
            "error": err.decode() if proc.returncode != 0 else None,
            "status": status,
            "stop_time": time.time()
        }
    except Exception as e:
        await ctx.set_state("run_status", "ERROR")
        await ctx.set_state("error", str(e))
        return {
            "test_run_id": test_run_id,
            "status": "ERROR",
            "error": str(e)
        }

def get_jmeter_realtime_status(test_run_id: str, pid: int | None = None) -> dict:
    """
    Unified JMeter status checker.

    Combines:
      - PID check (is the JMeter process still running?)
      - JTL existence check
      - Live smoke-test metrics parsed from the JTL

    Returns a dict with:
      - test_run_id
      - pid, pid_running
      - jtl_exists
      - status: "RUNNING" | "STARTING" | "COMPLETE" | "FAILED_TO_START" | "NO_SAMPLES" | "NO_JTL" | "UNKNOWN"
      - metrics: parsed JTL metrics (may be empty if JTL missing/empty)
      - jtl_path
      - last_updated_utc (filesystem mtime of JTL, if available)
    """
    jtl_path = _make_jtl_path(test_run_id)

    # 1) PID check
    pid_running = is_pid_running(pid) if pid else False

    # 2) JTL existence
    jtl_exists = os.path.exists(jtl_path)

    metrics: dict = {}
    last_updated = None

    if jtl_exists:
        try:
            metrics = parse_jtl_live(jtl_path)
            # Last updated based on JTL file modification time
            last_updated = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(os.path.getmtime(jtl_path)),
            )
        except Exception as e:
            metrics = {
                "error": f"Failed to parse JTL: {str(e)}",
                "total_samples": metrics.get("total_samples", 0) if isinstance(metrics, dict) else 0,
            }

    total_samples = metrics.get("total_samples", 0) if isinstance(metrics, dict) else 0

    # 3) Infer high-level status
    if not jtl_exists:
        # If there's no JTL at all yet
        if pid_running:
            status = "STARTING"
        else:
            status = "NO_JTL"
    else:
        # JTL exists
        if pid_running and total_samples > 0:
            status = "RUNNING"
        elif pid_running and total_samples == 0:
            status = "STARTING"
        elif not pid_running and total_samples > 0:
            status = "COMPLETE"
        elif not pid_running and total_samples == 0:
            status = "NO_SAMPLES"
        else:
            status = "UNKNOWN"

    return {
        "test_run_id": test_run_id,
        "pid": pid,
        "pid_running": pid_running,
        "jtl_exists": jtl_exists,
        "status": status,
        "metrics": metrics,
        "jtl_path": jtl_path,
        "last_updated_utc": last_updated,
    }

def generate_aggregate_report_csv(test_run_id: str) -> dict:
    """
    Generate a BlazeMeter-style Aggregate Performance Report CSV
    for the given test_run_id, based on its JTL file.

    Output:
      <ARTIFACTS_PATH>/<test_run_id>/jmeter/<test_run_id>_aggregate_report.csv
    """
    jtl_path = _make_jtl_path(test_run_id)
    if not os.path.exists(jtl_path):
        return {
            "test_run_id": test_run_id,
            "status": "NO_JTL",
            "message": f"No JTL file found for test_run_id={test_run_id}",
        }

    rows = build_aggregate_rows_from_jtl(jtl_path)
    out_path = _make_aggregate_report_path(test_run_id)

    fieldnames = [
        "labelName",
        "samples",
        "avgResponseTime",
        "minResponseTime",
        "maxResponseTime",
        "medianResponseTime",
        "90line",
        "95line",
        "99line",
        "stDev",
        "avgLatency",
        "errorsCount",
        "errorsRate",
        "avgThroughput",
        "avgBytes",
        "duration",
        "concurrency",
        "hasLabelPassedThresholds",
    ]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return {
        "test_run_id": test_run_id,
        "status": "OK",
        "aggregate_report_path": out_path,
        "label_count": len(rows),
    }

# ----------------------------------------------------------
# Helper Functions
# ----------------------------------------------------------

def parse_jtl_live(jtl_path: str) -> dict:
    """
    Parse a JMeter JTL (CSV) file and compute live smoke-test metrics.

    Returns:
        dict with keys:
          - total_samples
          - error_count, error_rate, success_rate
          - avg_response_time_ms, p90_response_time_ms
          - per_label: { label: { count, errors, error_rate, avg_ms, p90_ms } }
          - start_time_utc, end_time_utc, duration_ms, duration_seconds
    """
    total = 0
    error_count = 0
    elapsed_all: list[int] = []
    per_label: dict[str, dict] = {}

    first_ts = None  # earliest timeStamp (ms since epoch)
    last_ts = None   # latest timeStamp (ms since epoch)

    with open(jtl_path, newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Parse elapsed
            try:
                elapsed = int(row.get("elapsed") or 0)
            except ValueError:
                continue

            # Parse timestamp (JMeter uses epoch ms)
            ts_raw = row.get("timeStamp")
            try:
                ts_val = int(ts_raw) if ts_raw is not None else None
            except ValueError:
                ts_val = None

            label = row.get("label", "UNKNOWN")
            rc = (row.get("responseCode") or "").strip()

            total += 1
            elapsed_all.append(elapsed)

            if ts_val is not None:
                if first_ts is None or ts_val < first_ts:
                    first_ts = ts_val
                if last_ts is None or ts_val > last_ts:
                    last_ts = ts_val

            if label not in per_label:
                per_label[label] = {"count": 0, "errors": 0, "elapsed": []}

            per_label[label]["count"] += 1
            per_label[label]["elapsed"].append(elapsed)

            # simple rule: non-2xx/3xx = error
            if not (rc.startswith("2") or rc.startswith("3")):
                error_count += 1
                per_label[label]["errors"] += 1

    # If no samples, return an empty-ish metrics struct
    if total == 0:
        return {
            "total_samples": 0,
            "error_count": 0,
            "error_rate": 0.0,
            "success_rate": 1.0,
            "avg_response_time_ms": None,
            "p90_response_time_ms": None,
            "per_label": {},
            "start_time_utc": None,
            "end_time_utc": None,
            "duration_ms": None,
            "duration_seconds": None,
        }

    avg = sum(elapsed_all) / total
    p90 = _percentile(elapsed_all, 90)
    error_rate = error_count / total if total else 0.0

    label_summaries = {}
    for label, stats in per_label.items():
        c = stats["count"]
        if c == 0:
            continue
        avg_l = sum(stats["elapsed"]) / c
        p90_l = _percentile(stats["elapsed"], 90)
        label_summaries[label] = {
            "count": c,
            "errors": stats.get("errors", 0),
            "error_rate": (stats.get("errors", 0) / c) if c else 0.0,
            "avg_ms": avg_l,
            "p90_ms": p90_l,
        }

    # Derive start/end/duration from timestamps (epoch ms)
    if first_ts is not None and last_ts is not None and last_ts >= first_ts:
        duration_ms = last_ts - first_ts
        start_time_utc = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(first_ts / 1000.0)
        )
        end_time_utc = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(last_ts / 1000.0)
        )
    else:
        duration_ms = None
        start_time_utc = None
        end_time_utc = None

    return {
        "total_samples": total,
        "error_count": error_count,
        "error_rate": error_rate,
        "success_rate": 1.0 - error_rate,
        "avg_response_time_ms": avg,
        "p90_response_time_ms": p90,
        "per_label": label_summaries,
        "start_time_utc": start_time_utc,
        "end_time_utc": end_time_utc,
        "duration_ms": duration_ms,
        "duration_seconds": (duration_ms / 1000.0) if duration_ms is not None else None,
    }

def is_pid_running(pid: int) -> bool:
    """Check whether a process with given PID is still running on any OS."""
    if pid is None:
        return False

    try:
        if sys.platform.startswith("win"):
            # Windows: os.kill with signal 0 works but behaves differently
            # So we use this method:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if handle == 0:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True

        else:
            # Mac/Linux: signal 0 doesn't kill the process
            os.kill(pid, 0)
            return True

    except OSError:
        return False

def build_aggregate_rows_from_jtl(jtl_path: str):
    """
    Parse a JMeter JTL CSV and compute BlazeMeter-style aggregate metrics per label.

    Output row schema (per label):
      labelName,samples,avgResponseTime,minResponseTime,maxResponseTime,
      medianResponseTime,90line,95line,99line,stDev,avgLatency,
      errorsCount,errorsRate,avgThroughput,avgBytes,duration,concurrency,
      hasLabelPassedThresholds
    """
    if not os.path.exists(jtl_path):
        raise FileNotFoundError(f"JTL file not found: {jtl_path}")

    label_data: dict[str, dict] = {}

    with open(jtl_path, newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            label = row.get("label") or row.get("Label") or "UNKNOWN"

            def to_int(key: str, default=0):
                val = row.get(key)
                if val in (None, ""):
                    return default
                try:
                    return int(float(val))
                except ValueError:
                    return default

            elapsed = to_int("elapsed", 0)
            latency = to_int("Latency", 0)
            bytes_val = to_int("bytes", 0)
            ts = to_int("timeStamp", None)
            all_threads = to_int("allThreads", 0)

            # Determine success / error
            success_raw = row.get("success")
            if success_raw is not None:
                success = str(success_raw).lower() == "true"
            else:
                rc = (row.get("responseCode") or "").strip()
                success = rc.startswith("2") or rc.startswith("3")

            stats = label_data.setdefault(label, {
                "samples": 0,
                "elapsed_values": [],
                "latency_values": [],
                "bytes_values": [],
                "errors": 0,
                "first_ts": None,
                "last_ts": None,
                "max_all_threads": 0,
            })

            stats["samples"] += 1
            stats["elapsed_values"].append(elapsed)
            stats["latency_values"].append(latency)
            stats["bytes_values"].append(bytes_val)
            if not success:
                stats["errors"] += 1

            if ts is not None:
                if stats["first_ts"] is None or ts < stats["first_ts"]:
                    stats["first_ts"] = ts
                if stats["last_ts"] is None or ts > stats["last_ts"]:
                    stats["last_ts"] = ts

            if all_threads > stats["max_all_threads"]:
                stats["max_all_threads"] = all_threads

    rows = []
    for label, stats in label_data.items():
        samples = stats["samples"]
        elapsed_vals = stats["elapsed_values"]
        latency_vals = stats["latency_values"]
        bytes_vals = stats["bytes_values"]
        errors = stats["errors"]

        if not samples:
            continue

        avg_rt = sum(elapsed_vals) / samples
        min_rt = min(elapsed_vals)
        max_rt = max(elapsed_vals)

        # Uses the _percentile helper you already added for run-status
        median_rt = _percentile(elapsed_vals, 50)
        p90 = _percentile(elapsed_vals, 90)
        p95 = _percentile(elapsed_vals, 95)
        p99 = _percentile(elapsed_vals, 99)

        stdev = _stddev(elapsed_vals)
        avg_latency = (sum(latency_vals) / len(latency_vals)) if latency_vals else 0.0

        error_rate = errors / samples if samples else 0.0

        first_ts = stats["first_ts"]
        last_ts = stats["last_ts"]
        if first_ts is not None and last_ts is not None and last_ts >= first_ts:
            duration_ms = last_ts - first_ts
            duration_s = duration_ms / 1000.0 if duration_ms > 0 else 0.0
        else:
            duration_ms = None
            duration_s = 0.0

        if duration_s > 0:
            throughput = samples / duration_s
        else:
            throughput = 0.0

        avg_bytes = (sum(bytes_vals) / len(bytes_vals)) if bytes_vals else 0.0
        concurrency = stats["max_all_threads"] or None

        row = {
            "labelName": label,
            "samples": samples,
            "avgResponseTime": avg_rt,
            "minResponseTime": min_rt,
            "maxResponseTime": max_rt,
            "medianResponseTime": median_rt,
            "90line": p90,
            "95line": p95,
            "99line": p99,
            "stDev": stdev,
            "avgLatency": avg_latency,
            "errorsCount": errors,
            "errorsRate": error_rate,
            "avgThroughput": throughput,
            "avgBytes": avg_bytes,
            "duration": duration_s,  # seconds, like the BlazeMeter CSV
            "concurrency": concurrency if concurrency is not None else "",
            "hasLabelPassedThresholds": "",
        }
        rows.append(row)

    # We intentionally DO NOT sort rows; they stay in first-seen order like your BlazeMeter sample
    return rows

# ----------------------------------------------------------
# Utility/Internal Functions
# ----------------------------------------------------------

def _get_jmeter_bin():
    return JMETER_CONFIG.get('jmeter_bin_path', '')

def _get_start_exe():
    return JMETER_CONFIG.get('jmeter_start_exe', 'jmeter.bat')

def _get_stop_exe():
    return JMETER_CONFIG.get('jmeter_stop_exe', 'stoptest.cmd')

def _get_jmeter_home():
    return JMETER_CONFIG.get('jmeter_home', '')

def _get_artifact_dir(test_run_id):
    path = os.path.join(ARTIFACTS_PATH, str(test_run_id), 'jmeter')
    os.makedirs(path, exist_ok=True)
    return path

def _make_jtl_path(test_run_id):
    return os.path.join(_get_artifact_dir(test_run_id), 'test-results.csv')

def _make_log_path(test_run_id):
    return os.path.join(_get_artifact_dir(test_run_id), f'{test_run_id}.log')

def _make_summary_path(test_run_id):
    return os.path.join(_get_artifact_dir(test_run_id), f'{test_run_id}_summary.json')

def _percentile(values, pct):
    """Simple percentile helper used for p90, etc."""
    if not values:
        return None
    vals = sorted(values)
    idx = max(0, min(len(vals) - 1, int(math.ceil(pct / 100.0 * len(vals)) - 1)))
    return vals[idx]

def _get_artifact_paths(test_run_id):
    """Returns all artifact paths for this run."""
    return {
        "jtl_path": _make_jtl_path(test_run_id),
        "log_path": _make_log_path(test_run_id),
        "summary_path": _make_summary_path(test_run_id)
    }

def _make_aggregate_report_path(test_run_id: str) -> str:
    """
    Build the output path for the aggregate performance report CSV:
        <ARTIFACTS_PATH>/<test_run_id>/jmeter/aggregate_performance_report.csv
    """
    return os.path.join(_get_artifact_dir(test_run_id), "aggregate_performance_report.csv")

def _stddev(values):
    """Population standard deviation for response times."""
    if not values or len(values) < 2:
        return 0.0
    return statistics.pstdev(values)
