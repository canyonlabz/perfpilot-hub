# services/bottleneck_analyzer.py
"""
Bottleneck Analysis Engine for PerfAnalysis MCP Server.

Identifies when and why performance degradation begins during load testing.
Answers the core question: "At what concurrency level does system performance
begin to degrade, and what is the limiting factor?"

Inputs:
    - JTL CSV (test-results.csv) from JMeter/BlazeMeter
    - Datadog infrastructure metrics (optional but recommended)

Outputs (all under artifacts/<run_id>/analysis/):
    - bottleneck_analysis.json
    - bottleneck_analysis.csv
    - bottleneck_analysis.md
"""

import json
import math
import datetime
import traceback
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from fastmcp import Context
from dotenv import load_dotenv

from utils.config import load_config
from utils.sla_config import get_sla_for_api
from utils.file_processor import (
    write_json_output,
    write_csv_output,
    write_markdown_output,
)
from utils.kpi_utils import discover_kpi_files, load_kpi_pivoted
from services.kpi_analyzer import detect_kpi_bottlenecks

# ---------------------------------------------------------------------------
# Module-level configuration
# ---------------------------------------------------------------------------
load_dotenv()
CONFIG = load_config()
ARTIFACTS_CONFIG = CONFIG.get("artifacts", {})
PA_CONFIG = CONFIG.get("perf_analysis", {})
ARTIFACTS_PATH = Path(ARTIFACTS_CONFIG.get("artifacts_path", "./artifacts"))

# Bottleneck-specific defaults (overridden by config.yaml > perf_analysis.bottleneck_analysis)
BN_DEFAULTS = {
    "bucket_seconds": 60,
    "warmup_buckets": 2,
    "sustained_buckets": 2,
    "persistence_ratio": 0.6,     # min fraction of remaining test that must stay degraded
    "rolling_window_buckets": 3,  # window size for rolling median smoothing
    "latency_degrade_pct": 25.0,
    "error_rate_degrade_abs": 5.0,
    "throughput_plateau_pct": 5.0,
    "sla_p90_ms": None,           # resolved dynamically from slas.yaml via _get_sla_threshold()
    "cpu_high_pct": None,         # falls back to resource_thresholds.cpu.high
    "memory_high_pct": None,      # falls back to resource_thresholds.memory.high
    "raw_metric_degrade_pct": 50.0,  # relative increase from baseline to flag when utilization % unavailable (no K8s limits)
    "max_jtl_rows": None,         # None = load all rows; set to cap memory on very large JTL files
}


def _get_bn_config() -> Dict[str, Any]:
    """Merge bottleneck defaults with values from config.yaml."""
    overrides = PA_CONFIG.get("bottleneck_analysis", {})
    cfg = {**BN_DEFAULTS, **{k: v for k, v in overrides.items() if v is not None}}

    # SLA thresholds are now resolved from slas.yaml via _get_sla_threshold(),
    # NOT from config.yaml. The sla_p90_ms key in BN_DEFAULTS / config.yaml is
    # no longer used. Kept as None for backward compat of the config dict shape.
    cfg["sla_p90_ms"] = None  # resolved dynamically by _get_sla_threshold()

    # Resource thresholds remain in config.yaml (infrastructure, not SLA)
    if cfg.get("cpu_high_pct") is None:
        cfg["cpu_high_pct"] = PA_CONFIG.get("resource_thresholds", {}).get("cpu", {}).get("high", 80)
    if cfg.get("memory_high_pct") is None:
        cfg["memory_high_pct"] = PA_CONFIG.get("resource_thresholds", {}).get("memory", {}).get("high", 85)

    return cfg


# ============================================================================
# SLA THRESHOLD HELPER
# ============================================================================

def _get_sla_threshold(cfg: Dict, label: Optional[str] = None, sla_id: Optional[str] = None) -> float:
    """
    Return the SLA threshold (in ms) for a given endpoint label.

    Resolves the SLA from slas.yaml using the three-level pattern matching
    hierarchy (full label > TC#_TS# > TC#) with inheritance
    (api_override > profile default > file default).

    This is the single place where SLA resolution lives for the bottleneck
    analyzer. All downstream detection functions (latency SLA breach,
    infrastructure capacity risk, per-endpoint analysis) call this function.

    Args:
        cfg:    Bottleneck analysis config dict (kept for signature compat).
        label:  JMeter sampler label / API endpoint name for per-API resolution.
                If None or empty, the profile or file-level default is returned.
        sla_id: SLA profile ID from slas.yaml. If None, the file-level
                default_sla is used.

    Returns:
        SLA threshold in milliseconds (float).
    """
    resolved = get_sla_for_api(sla_id, label or "")
    return float(resolved["response_time_sla_ms"])


# ============================================================================
# PUBLIC API  (called from perfanalysis.py)
# ============================================================================

async def analyze_bottlenecks(
    test_run_id: str,
    ctx: Context,
    baseline_run_id: Optional[str] = None,
    sla_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Main entry point for bottleneck analysis.

    Args:
        test_run_id:      Current test run to analyse.
        ctx:              FastMCP workflow context.
        baseline_run_id:  (Optional) Previous run for comparison mode.

    Returns:
        dict suitable for returning directly from an MCP tool.
    """
    try:
        await ctx.info(f"Bottleneck Analysis: Starting analysis for run {test_run_id}")
        cfg = _get_bn_config()

        # ------------------------------------------------------------------
        # 1. Load raw JTL data
        # ------------------------------------------------------------------
        jtl_path = ARTIFACTS_PATH / test_run_id / "blazemeter" / "test-results.csv"
        if not jtl_path.exists():
            jtl_path = ARTIFACTS_PATH / test_run_id / "jmeter" / "test-results.csv"
        if not jtl_path.exists():
            msg = f"JTL file not found (checked blazemeter/ and jmeter/). Run a load test workflow first."
            await ctx.error(f"Missing JTL: {msg}")
            return {"error": msg, "status": "prerequisite_missing"}

        jtl_df = _load_jtl(jtl_path, cfg)
        if jtl_df is None or jtl_df.empty:
            return {"error": "JTL file could not be loaded or is empty", "status": "failed"}

        engine_count = _detect_engine_count(jtl_df)
        if engine_count > 1:
            await ctx.info(
                f"Multi-Engine Detected: {engine_count} load-generator engines detected — "
                f"concurrency will be computed as sum of per-engine threads"
            )

        # ------------------------------------------------------------------
        # 2. Build time buckets
        # ------------------------------------------------------------------
        buckets_df = _build_time_buckets(jtl_df, cfg)

        # ------------------------------------------------------------------
        # 2b. Outlier filtering (rolling median smoothing)
        # ------------------------------------------------------------------
        buckets_df = _apply_outlier_filtering(buckets_df, cfg)

        # ------------------------------------------------------------------
        # 3. Optionally load infrastructure metrics
        # ------------------------------------------------------------------
        infra_df, infra_meta = _load_infrastructure_metrics(test_run_id, cfg)
        has_infra = infra_df is not None and not infra_df.empty
        if has_infra:
            buckets_df = _align_infra_to_buckets(buckets_df, infra_df, cfg)
            if infra_meta["metric_mode"] == "raw":
                await ctx.info(
                    f"Infrastructure: Datadog metrics aligned to time buckets (RAW mode: K8s limits not defined, "
                    f"using {infra_meta['cpu_unit']}/{infra_meta['memory_unit']} with relative-from-baseline thresholds)"
                )
        else:
            await ctx.warning("Infrastructure: No Datadog metrics found - infra analysis will be skipped")

        # ------------------------------------------------------------------
        # 4. Establish baseline window
        # ------------------------------------------------------------------
        baseline = _compute_baseline(buckets_df, cfg)

        # ------------------------------------------------------------------
        # 5. Phase 1 -- Performance Degradation Detection (JTL only)
        # ------------------------------------------------------------------
        # Test start time = first bucket's timestamp (used for elapsed-seconds calc)
        test_start_time = buckets_df["bucket_start"].iloc[0] if len(buckets_df) > 0 else None

        findings: List[Dict[str, Any]] = []

        # Phase 1 detectors use only JTL data to find *when* degradation occurs
        findings.extend(_detect_latency_degradation(buckets_df, baseline, cfg, test_run_id, test_start_time))
        findings.extend(_detect_error_rate_increase(buckets_df, baseline, cfg, test_run_id, test_start_time))
        findings.extend(_detect_throughput_plateau(buckets_df, baseline, cfg, test_run_id, test_start_time))
        findings.extend(_detect_multi_tier_bottlenecks(jtl_df, cfg, test_run_id, test_start_time, sla_id=sla_id))

        phase1_count = len(findings)
        await ctx.info(f"Phase 1: Performance detection found {phase1_count} finding(s)")

        # ------------------------------------------------------------------
        # 5b. Phase 2a -- Infrastructure Cross-Reference
        # ------------------------------------------------------------------
        # Scope infra analysis to degradation windows from Phase 1
        degradation_windows = _extract_degradation_windows(findings, buckets_df, cfg)

        if has_infra and degradation_windows:
            findings = _cross_reference_infrastructure(
                findings, buckets_df, baseline, cfg, degradation_windows, infra_meta,
            )
            correlated_count = sum(
                1 for f in findings
                if (f.get("infrastructure_context") or {}).get("infra_correlated") is True
            )
            independent_count = sum(
                1 for f in findings
                if (f.get("infrastructure_context") or {}).get("infra_correlated") is False
            )
            await ctx.info(
                f"Phase 2a: Infrastructure cross-reference: {len(degradation_windows)} window(s) analyzed, "
                f"{correlated_count} infra-correlated, {independent_count} infra-independent"
            )

        # ------------------------------------------------------------------
        # 5c. Phase 2b -- Capacity Risk Detection
        # ------------------------------------------------------------------
        if has_infra:
            capacity_risks = _detect_capacity_risks(
                buckets_df, baseline, cfg, test_run_id,
                degradation_windows, test_start_time, infra_meta,
                sla_id=sla_id,
            )
            if capacity_risks:
                findings.extend(capacity_risks)
                await ctx.info(
                    f"Phase 2b: Capacity risk detection: {len(capacity_risks)} risk(s) identified"
                )

        # ------------------------------------------------------------------
        # 5c2. Phase 3 -- KPI-driven bottleneck detection (optional)
        # ------------------------------------------------------------------
        apm_tool = PA_CONFIG.get("apm_tool", "datadog").lower()
        kpi_dir = ARTIFACTS_PATH / test_run_id / apm_tool
        kpi_files = discover_kpi_files(kpi_dir)
        if kpi_files:
            kpi_df = load_kpi_pivoted(kpi_files, convert_units=True)
            if kpi_df is not None and not kpi_df.empty:
                kpi_findings = detect_kpi_bottlenecks(
                    kpi_df, buckets_df, baseline, cfg, test_run_id,
                    test_start_time, _make_finding, _onset_fields, _classify_severity_v2,
                )
                if kpi_findings:
                    findings.extend(kpi_findings)
                    await ctx.info(
                        f"Phase 3: KPI bottleneck detection: {len(kpi_findings)} finding(s) from "
                        f"{len(kpi_files)} KPI file(s)"
                    )
            else:
                await ctx.info("Phase 3: KPI files found but contained no usable data")

        await ctx.info(f"Detection: Identified {len(findings)} finding(s) total")

        # ------------------------------------------------------------------
        # 5d. Comparison mode (if baseline_run_id supplied)
        # ------------------------------------------------------------------
        comparison = None
        if baseline_run_id:
            comparison = await _run_comparison(
                test_run_id, baseline_run_id, buckets_df, findings, cfg, ctx
            )

        # ------------------------------------------------------------------
        # 6. Compute summary & threshold concurrency
        # ------------------------------------------------------------------
        summary = _compute_summary(buckets_df, findings, cfg, has_infra)

        # ------------------------------------------------------------------
        # 7. Assemble full result payload
        # ------------------------------------------------------------------
        result = {
            "test_run_id": test_run_id,
            "sla_id": sla_id,
            "analysis_mode": "comparison" if baseline_run_id else "single_run",
            "analysis_timestamp": datetime.datetime.now().isoformat(),
            "configuration": cfg,
            "engine_count": engine_count,
            "time_buckets_total": len(buckets_df),
            "warmup_buckets_skipped": cfg["warmup_buckets"],
            "baseline_metrics": baseline,
            "summary": summary,
            "findings": findings,
            "comparison": comparison,
            "infrastructure_available": has_infra,
            "infrastructure_meta": infra_meta if has_infra else None,
        }

        # ------------------------------------------------------------------
        # 8. Write output artifacts
        # ------------------------------------------------------------------
        analysis_path = ARTIFACTS_PATH / test_run_id / "analysis"
        analysis_path.mkdir(parents=True, exist_ok=True)

        output_files = await _write_outputs(result, analysis_path, test_run_id, ctx)
        result["output_files"] = output_files

        await ctx.info(
            f"Bottleneck Analysis Complete: {len(findings)} bottleneck(s) detected. "
            f"Threshold concurrency: {summary.get('threshold_concurrency', 'N/A')}. "
            f"Files saved to {analysis_path}"
        )

        return {
            "status": "success",
            "test_run_id": test_run_id,
            "summary": summary,
            "findings_count": len(findings),
            "output_files": output_files,
        }

    except Exception as e:
        tb = traceback.format_exc()
        msg = f"Bottleneck analysis failed: {e}"
        await ctx.error(f"Bottleneck Error: {msg}")
        return {"error": msg, "status": "failed", "traceback": tb}


# ============================================================================
# DATA LOADING
# ============================================================================

def _detect_engine_count(df: pd.DataFrame) -> int:
    """Return the number of distinct load-generator engines in the JTL data."""
    if "Hostname" in df.columns:
        return int(df["Hostname"].nunique())
    return 1


def _load_jtl(path: Path, cfg: Dict) -> Optional[pd.DataFrame]:
    """Load raw JTL CSV with memory-optimised I/O.

    Applies three techniques to handle large JTL files (hundreds of MB):
      1. ``usecols`` — only loads the columns the analyser actually uses.
      2. Explicit ``dtype`` map — avoids pandas object-column overhead and
         uses ``category`` for low-cardinality string columns (label, Hostname).
      3. Configurable ``max_jtl_rows`` — safety valve for extremely large files.
    """
    try:
        required = {"timeStamp", "elapsed", "label", "responseCode", "success", "allThreads"}

        header = pd.read_csv(path, nrows=0)
        available = set(header.columns)
        if not required.issubset(available):
            missing = required - available
            print(f"[bottleneck_analyzer] Missing columns: {missing}")
            return None

        use_cols = list(required)
        if "Hostname" in available:
            use_cols.append("Hostname")

        dtype_map = {
            "timeStamp": "int64",
            "elapsed": "int64",
            "label": "category",
            "responseCode": "category",
            "success": "str",
            "allThreads": "int32",
        }
        if "Hostname" in available:
            dtype_map["Hostname"] = "category"

        max_rows = cfg.get("max_jtl_rows")

        df = pd.read_csv(
            path,
            usecols=use_cols,
            dtype=dtype_map,
            low_memory=True,
            nrows=max_rows,
        )

        if max_rows and len(df) >= max_rows:
            print(f"[bottleneck_analyzer] Row limit applied: loaded {max_rows:,} of available rows")

        engine_count = _detect_engine_count(df)
        if engine_count > 1:
            print(f"[bottleneck_analyzer] Multi-engine JTL detected: {engine_count} engines")

        df["timestamp"] = pd.to_datetime(df["timeStamp"], unit="ms", utc=True)
        df["success"] = df["success"].astype(str).str.lower().map({"true": True, "false": False})
        return df
    except Exception as e:
        print(f"[bottleneck_analyzer] Error loading JTL: {e}")
        return None


def _load_infrastructure_metrics(
    test_run_id: str, cfg: Dict,
) -> Tuple[Optional[pd.DataFrame], Dict[str, Any]]:
    """Load Datadog host/k8s metrics CSVs and return unified DataFrame + metadata.

    Returns a tuple ``(infra_df, infra_meta)`` where *infra_meta* describes
    the metric mode:

    * **percentage mode** (``limits_available=True``): ``avg_cpu`` / ``avg_memory``
      values are utilization percentages (0-100).  Absolute thresholds
      (``cpu_high_pct``, ``memory_high_pct``) apply.
    * **raw mode** (``limits_available=False``): K8s resource limits are not
      defined so utilization % cannot be computed.  Values are in raw units
      (CPU cores, Memory GB).  Only relative-from-baseline thresholds apply.

    Both modes produce identical column names (``cpu_util_pct``,
    ``mem_util_pct``) so that ``_align_infra_to_buckets`` and downstream code
    work unchanged -- the *infra_meta* dict tells callers how to interpret
    the numbers.
    """
    _DEFAULT_META: Dict[str, Any] = {
        "metric_mode": "percentage",
        "limits_available": True,
        "cpu_unit": "%",
        "memory_unit": "%",
    }

    apm_tool = PA_CONFIG.get("apm_tool", "datadog").lower()
    metrics_dir = ARTIFACTS_PATH / test_run_id / apm_tool

    if not metrics_dir.exists():
        return None, _DEFAULT_META

    csv_files = sorted(
        list(metrics_dir.glob("host_metrics_*.csv"))
        + list(metrics_dir.glob("k8s_metrics_*.csv"))
    )
    if not csv_files:
        return None, _DEFAULT_META

    pct_frames: List[pd.DataFrame] = []
    raw_cpu_frames: List[pd.DataFrame] = []
    raw_mem_frames: List[pd.DataFrame] = []

    for csv_file in csv_files:
        try:
            df = pd.read_csv(csv_file)
            scope = df["scope"].iloc[0] if not df.empty else "unknown"

            # --- Percentage metrics (always try first) ---
            cpu_pct = df[df["metric"] == "cpu_util_pct"].copy()
            mem_pct = df[df["metric"] == "mem_util_pct"].copy()

            for part, col in [(cpu_pct, "cpu_util_pct"), (mem_pct, "mem_util_pct")]:
                if part.empty:
                    continue
                processed = part[["timestamp_utc", "value"]].copy()
                processed.columns = ["timestamp", col]
                processed["timestamp"] = pd.to_datetime(processed["timestamp"], utc=True)
                if scope == "k8s":
                    processed["resource"] = part["filter"].values
                else:
                    processed["resource"] = part["hostname"].values
                pct_frames.append(processed)

            # --- Raw K8s metrics (for fallback when limits missing) ---
            if scope == "k8s":
                cpu_raw = df[df["metric"] == "kubernetes.cpu.usage.total"].copy()
                if not cpu_raw.empty:
                    proc = cpu_raw[["timestamp_utc", "value"]].copy()
                    proc.columns = ["timestamp", "cpu_util_pct"]
                    proc["timestamp"] = pd.to_datetime(proc["timestamp"], utc=True)
                    proc["cpu_util_pct"] = proc["cpu_util_pct"] / 1e9  # nanocores -> cores
                    proc["resource"] = cpu_raw["filter"].values
                    raw_cpu_frames.append(proc)

                mem_raw = df[df["metric"] == "kubernetes.memory.usage"].copy()
                if not mem_raw.empty:
                    proc = mem_raw[["timestamp_utc", "value"]].copy()
                    proc.columns = ["timestamp", "mem_util_pct"]
                    proc["timestamp"] = pd.to_datetime(proc["timestamp"], utc=True)
                    proc["mem_util_pct"] = proc["mem_util_pct"] / (1024 ** 3)  # bytes -> GB
                    proc["resource"] = mem_raw["filter"].values
                    raw_mem_frames.append(proc)

        except Exception as e:
            print(f"[bottleneck_analyzer] Skipping {csv_file}: {e}")
            continue

    # ------------------------------------------------------------------
    # Decide: are percentage metrics meaningful?
    # If all values are zero/near-zero (limits=0 produces 0% utilization)
    # then fall back to raw metrics.
    # ------------------------------------------------------------------
    def _merge_cpu_mem(frames: List[pd.DataFrame]) -> Optional[pd.DataFrame]:
        """Concat frames and merge CPU + Memory on timestamp + resource."""
        if not frames:
            return None
        combined = pd.concat(frames, ignore_index=True)

        cpu_col = "cpu_util_pct" if "cpu_util_pct" in combined.columns else None
        mem_col = "mem_util_pct" if "mem_util_pct" in combined.columns else None

        cpu_data = (
            combined[combined[cpu_col].notna()][["timestamp", "resource", cpu_col]]
            if cpu_col and cpu_col in combined.columns
            else pd.DataFrame()
        )
        mem_data = (
            combined[combined[mem_col].notna()][["timestamp", "resource", mem_col]]
            if mem_col and mem_col in combined.columns
            else pd.DataFrame()
        )

        if cpu_data.empty and mem_data.empty:
            return None

        if not cpu_data.empty and not mem_data.empty:
            merged = pd.merge(cpu_data, mem_data, on=["timestamp", "resource"], how="outer")
        elif not cpu_data.empty:
            merged = cpu_data.copy()
        else:
            merged = mem_data.copy()

        for col in ["cpu_util_pct", "mem_util_pct"]:
            if col in merged.columns:
                merged.loc[merged[col] < 0, col] = np.nan

        return merged.sort_values("timestamp").reset_index(drop=True)

    # -- Check percentage path --
    pct_usable = False
    if pct_frames:
        pct_merged = _merge_cpu_mem(pct_frames)
        if pct_merged is not None:
            has_real_cpu = (
                "cpu_util_pct" in pct_merged.columns
                and (pct_merged["cpu_util_pct"].abs() > 0.01).any()
            )
            has_real_mem = (
                "mem_util_pct" in pct_merged.columns
                and (pct_merged["mem_util_pct"].abs() > 0.01).any()
            )
            pct_usable = has_real_cpu or has_real_mem

    if pct_usable:
        return pct_merged, _DEFAULT_META

    # -- Fallback: raw metrics --
    raw_frames = raw_cpu_frames + raw_mem_frames
    raw_merged = _merge_cpu_mem(raw_frames)
    if raw_merged is not None:
        raw_meta: Dict[str, Any] = {
            "metric_mode": "raw",
            "limits_available": False,
            "cpu_unit": "cores",
            "memory_unit": "GB",
        }
        return raw_merged, raw_meta

    return None, _DEFAULT_META


# ============================================================================
# TIME BUCKETING
# ============================================================================

def _compute_bucket_concurrency(
    df: pd.DataFrame, bucket_seconds: int,
) -> pd.Series:
    """Compute per-bucket concurrency, correcting for multi-engine JTL files.

    In distributed BlazeMeter/JMeter tests the merged JTL contains rows from every
    engine, but ``allThreads`` only reports the **per-engine** thread count.
    True concurrency = sum of each engine's max ``allThreads`` per bucket.

    Single-engine tests (one unique ``Hostname``, or column absent) fall back
    to the simpler ``max(allThreads)`` per bucket.
    """
    has_hostname = "Hostname" in df.columns
    is_multi_engine = has_hostname and df["Hostname"].nunique() > 1

    if is_multi_engine:
        return (
            df.groupby([pd.Grouper(freq=f"{bucket_seconds}s"), "Hostname"])["allThreads"]
            .max()
            .groupby(level=0)
            .sum()
            .rename("concurrency")
        )

    return (
        df.resample(f"{bucket_seconds}s")["allThreads"]
        .max()
        .rename("concurrency")
    )


def _build_time_buckets(jtl_df: pd.DataFrame, cfg: Dict) -> pd.DataFrame:
    """
    Bucket JTL data into fixed-width time windows.

    Returns DataFrame with one row per bucket:
        bucket_start, concurrency, p50, p90, p95, avg_rt, max_rt,
        error_rate, throughput_rps, total_requests, error_count
    """
    bucket_seconds = cfg["bucket_seconds"]

    df = jtl_df.set_index("timestamp").sort_index()

    concurrency_series = _compute_bucket_concurrency(df, bucket_seconds)

    resampled = df.resample(f"{bucket_seconds}s").agg(
        p50=("elapsed", lambda x: x.quantile(0.50) if len(x) else np.nan),
        p90=("elapsed", lambda x: x.quantile(0.90) if len(x) else np.nan),
        p95=("elapsed", lambda x: x.quantile(0.95) if len(x) else np.nan),
        avg_rt=("elapsed", "mean"),
        max_rt=("elapsed", "max"),
        total_requests=("elapsed", "count"),
        error_count=("success", lambda x: (~x).sum()),
    )

    resampled = resampled.join(concurrency_series)

    resampled["throughput_rps"] = resampled["total_requests"] / bucket_seconds
    resampled["error_rate"] = (
        resampled["error_count"] / resampled["total_requests"] * 100
    ).fillna(0.0)

    # Drop empty buckets (can happen at test boundaries)
    resampled = resampled[resampled["total_requests"] > 0].copy()
    resampled = resampled.reset_index().rename(columns={"timestamp": "bucket_start"})

    return resampled


# ============================================================================
# OUTLIER FILTERING (Improvement 2)
# ============================================================================

def _apply_outlier_filtering(buckets_df: pd.DataFrame, cfg: Dict) -> pd.DataFrame:
    """
    Apply rolling median smoothing to key performance metrics and flag outlier
    buckets.

    For each metric (p50, p90, p95, avg_rt, error_rate, throughput_rps):
      - Compute a rolling median with window = cfg['rolling_window_buckets'].
      - The smoothed value replaces individual spikes while preserving sustained
        trends.

    A bucket is flagged as ``is_outlier = True`` when its raw p90 deviates from
    the rolling median by more than 2x the rolling MAD (median absolute
    deviation).  This identifies isolated spikes that should NOT drive
    bottleneck findings.

    The original raw values are preserved in ``<metric>_raw`` columns so that
    reports can reference them for transparency.

    Args:
        buckets_df: Output of ``_build_time_buckets()``.
        cfg:        Bottleneck config dict (needs ``rolling_window_buckets``).

    Returns:
        DataFrame with added columns:
            p50_raw, p90_raw, p95_raw, avg_rt_raw, error_rate_raw,
            throughput_rps_raw, is_outlier
        and the main metric columns replaced with their smoothed versions.
    """
    window = int(cfg.get("rolling_window_buckets", 3))
    df = buckets_df.copy()

    # Metrics to smooth -- (column_name, higher_is_worse)
    metrics_to_smooth = [
        "p50", "p90", "p95", "avg_rt", "error_rate", "throughput_rps",
    ]

    # Preserve raw values
    for col in metrics_to_smooth:
        df[f"{col}_raw"] = df[col].copy()

    # Rolling median smoothing
    for col in metrics_to_smooth:
        df[col] = df[col].rolling(window=window, center=True, min_periods=1).median()

    # Outlier detection based on rolling MAD of raw p90
    raw_p90 = df["p90_raw"]
    smoothed_p90 = df["p90"]

    # MAD = median absolute deviation of the raw p90
    rolling_mad = (
        (raw_p90 - smoothed_p90)
        .abs()
        .rolling(window=window, center=True, min_periods=1)
        .median()
    )
    # Avoid zero MAD (constant series) -- use a small floor so we don't flag everything
    mad_floor = smoothed_p90.median() * 0.05 if smoothed_p90.median() > 0 else 1.0
    rolling_mad = rolling_mad.clip(lower=mad_floor)

    # A bucket is an outlier if its raw p90 deviates from the smoothed value
    # by more than 2x the rolling MAD
    deviation = (raw_p90 - smoothed_p90).abs()
    df["is_outlier"] = deviation > (2.0 * rolling_mad)

    return df


def _align_infra_to_buckets(
    buckets_df: pd.DataFrame, infra_df: pd.DataFrame, cfg: Dict
) -> pd.DataFrame:
    """Aggregate infra metrics into the same time buckets and merge."""
    bucket_seconds = cfg["bucket_seconds"]

    infra = infra_df.set_index("timestamp").sort_index()

    # Build agg dict dynamically based on which columns are available
    agg_dict = {}
    if "cpu_util_pct" in infra.columns:
        agg_dict["avg_cpu"] = ("cpu_util_pct", "mean")
        agg_dict["max_cpu"] = ("cpu_util_pct", "max")
    if "mem_util_pct" in infra.columns:
        agg_dict["avg_memory"] = ("mem_util_pct", "mean")
        agg_dict["max_memory"] = ("mem_util_pct", "max")

    if not agg_dict:
        return buckets_df

    infra_resampled = infra.resample(f"{bucket_seconds}s").agg(**agg_dict)

    # Keep rows where at least one metric has data
    notna_mask = pd.Series(False, index=infra_resampled.index)
    if "avg_cpu" in infra_resampled.columns:
        notna_mask = notna_mask | infra_resampled["avg_cpu"].notna()
    if "avg_memory" in infra_resampled.columns:
        notna_mask = notna_mask | infra_resampled["avg_memory"].notna()
    infra_resampled = infra_resampled[notna_mask]

    infra_resampled = infra_resampled.reset_index().rename(columns={"timestamp": "bucket_start"})

    merged = pd.merge(buckets_df, infra_resampled, on="bucket_start", how="left")
    return merged


# ============================================================================
# BASELINE COMPUTATION
# ============================================================================

def _compute_baseline(buckets_df: pd.DataFrame, cfg: Dict) -> Dict[str, Any]:
    """
    Compute baseline metrics from the first stable buckets after warmup.

    The baseline is the average of buckets [warmup .. warmup + sustained].
    Outlier buckets (if the ``is_outlier`` column exists) are excluded from
    the baseline calculation to prevent a single spike in the early test
    from skewing the reference values.
    """
    warmup = cfg["warmup_buckets"]
    sustained = cfg["sustained_buckets"]

    start_idx = warmup
    end_idx = warmup + sustained

    if end_idx > len(buckets_df):
        # Not enough buckets; use whatever is available after warmup
        end_idx = len(buckets_df)
        start_idx = min(warmup, len(buckets_df) - 1)

    baseline_slice = buckets_df.iloc[start_idx:end_idx]

    # Exclude outlier buckets from baseline if the column exists
    if "is_outlier" in baseline_slice.columns:
        non_outlier = baseline_slice[~baseline_slice["is_outlier"]]
        if not non_outlier.empty:
            baseline_slice = non_outlier

    if baseline_slice.empty:
        baseline_slice = buckets_df.head(1)

    def _safe_mean(series):
        val = series.mean()
        return float(val) if pd.notna(val) else 0.0

    baseline = {
        "concurrency": _safe_mean(baseline_slice["concurrency"]),
        "p90": _safe_mean(baseline_slice["p90"]),
        "p95": _safe_mean(baseline_slice["p95"]),
        "avg_rt": _safe_mean(baseline_slice["avg_rt"]),
        "error_rate": _safe_mean(baseline_slice["error_rate"]),
        "throughput_rps": _safe_mean(baseline_slice["throughput_rps"]),
        "buckets_used": f"{start_idx}-{end_idx - 1}",
    }

    # Infrastructure baseline if available
    if "avg_cpu" in baseline_slice.columns:
        baseline["avg_cpu"] = _safe_mean(baseline_slice["avg_cpu"])
        baseline["avg_memory"] = _safe_mean(baseline_slice["avg_memory"])

    return baseline


# ============================================================================
# DETECTION ALGORITHMS
# ============================================================================

def _make_finding(
    *,
    bottleneck_type: str,
    scope: str,
    scope_name: str,
    concurrency: float,
    metric_name: str,
    metric_value: float,
    baseline_value: float,
    severity: str,
    confidence: str,
    evidence: str,
    test_run_id: str,
    classification: str = "bottleneck",
    persistence_ratio: Optional[float] = None,
    outlier_filtered: bool = False,
    onset_timestamp: Optional[str] = None,
    onset_bucket_index: Optional[int] = None,
    test_elapsed_seconds: Optional[float] = None,
    infrastructure_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Construct a standardised finding dict.

    All numeric values are explicitly cast to native Python types to prevent
    NumPy scalars (int64, float64) from leaking into JSON output.
    """
    delta_abs = float(metric_value - baseline_value)
    delta_pct = float((delta_abs / baseline_value * 100) if baseline_value else 0.0)
    return {
        "test_run_id": test_run_id,
        "analysis_mode": "single_run",
        "bottleneck_type": bottleneck_type,
        "scope": scope,
        "scope_name": scope_name,
        "concurrency": float(round(concurrency, 1)),
        "metric_name": metric_name,
        "metric_value": float(round(metric_value, 2)),
        "baseline_value": float(round(baseline_value, 2)),
        "delta_abs": float(round(delta_abs, 2)),
        "delta_pct": float(round(delta_pct, 2)),
        "severity": severity,
        "confidence": confidence,
        "classification": classification,
        "persistence_ratio": persistence_ratio,
        "outlier_filtered": outlier_filtered,
        "onset_timestamp": onset_timestamp,
        "onset_bucket_index": int(onset_bucket_index) if onset_bucket_index is not None else None,
        "test_elapsed_seconds": float(round(test_elapsed_seconds, 1)) if test_elapsed_seconds is not None else None,
        "infrastructure_context": infrastructure_context,
        "evidence": evidence,
    }


def _onset_fields(
    bucket_start_value, bucket_index: int, test_start_time
) -> Dict[str, Any]:
    """
    Compute the three timestamp fields for a finding from raw bucket data.

    Args:
        bucket_start_value: The ``bucket_start`` value from the bucket row
                            (pandas Timestamp or string).
        bucket_index:       Zero-based bucket index within the **full** buckets
                            DataFrame (i.e. warmup + position in active slice).
        test_start_time:    The ``bucket_start`` of the very first bucket in the
                            test (pandas Timestamp or string).

    Returns:
        dict with ``onset_timestamp``, ``onset_bucket_index``,
        ``test_elapsed_seconds`` ready to be unpacked into ``_make_finding(**)``.
    """
    try:
        onset_ts = pd.Timestamp(bucket_start_value)
        start_ts = pd.Timestamp(test_start_time)
        elapsed = (onset_ts - start_ts).total_seconds()
    except Exception:
        onset_ts = bucket_start_value
        elapsed = None

    return {
        "onset_timestamp": str(onset_ts) if onset_ts is not None else None,
        "onset_bucket_index": bucket_index,
        "test_elapsed_seconds": float(elapsed) if elapsed is not None else None,
    }


def _classify_severity(delta_pct: float, thresholds: Tuple[float, float, float] = (25.0, 50.0, 100.0)) -> str:
    """Classify severity based on percentage deviation from baseline (legacy v0.1).

    Retained only for backward compatibility.  New code should use
    ``_classify_severity_v2()`` which factors in persistence, scope,
    and classification in addition to delta magnitude.
    """
    low, medium, high = thresholds
    abs_pct = abs(delta_pct)
    if abs_pct >= high:
        return "critical"
    elif abs_pct >= medium:
        return "high"
    elif abs_pct >= low:
        return "medium"
    return "low"


def _classify_severity_v2(
    *,
    delta_pct: float,
    persistence_ratio: Optional[float] = None,
    classification: str = "bottleneck",
    scope: str = "overall",
    bottleneck_type: str = "latency_degradation",
    delta_thresholds: Tuple[float, float, float] = (25.0, 50.0, 100.0),
) -> str:
    """Multi-factor severity classification (v0.2).

    Computes severity by combining four dimensions:

    1. **Delta magnitude** -- how far the metric deviates from baseline.
    2. **Persistence** -- what fraction of the remaining test stayed degraded.
       Higher persistence = more severe.
    3. **Classification** -- ``known_slow_endpoint`` is always ``info``;
       ``transient_spike`` is capped at ``low``.
    4. **Scope** -- ``overall`` findings carry more weight than single-label
       findings.

    The scoring intentionally keeps infrastructure-saturation and
    resource-performance-coupling callers out of this function -- those
    have domain-specific thresholds and remain separate.

    Returns one of: ``critical``, ``high``, ``medium``, ``low``, ``info``.
    """
    # --- Short-circuit for non-bottleneck classifications ---
    if classification == "known_slow_endpoint":
        return "info"
    if classification == "transient_spike":
        return "low"

    # --- Delta score (0-3) ---
    low_t, med_t, high_t = delta_thresholds
    abs_delta = abs(delta_pct)
    if abs_delta >= high_t:
        delta_score = 3
    elif abs_delta >= med_t:
        delta_score = 2
    elif abs_delta >= low_t:
        delta_score = 1
    else:
        delta_score = 0

    # --- Persistence score (0-3) ---
    pr = persistence_ratio if persistence_ratio is not None else 0.0
    if pr >= 0.9:
        persist_score = 3
    elif pr >= 0.7:
        persist_score = 2
    elif pr >= 0.5:
        persist_score = 1
    else:
        persist_score = 0

    # --- Scope score (0-1) ---
    scope_score = 1 if scope == "overall" else 0

    # --- Composite ---
    # Total possible = 3 + 3 + 1 = 7
    # critical requires near-maximum score across all dimensions:
    #   severe delta (3) + high persistence (3) + wide scope (1) = 7
    # high requires strong showing in at least two dimensions.
    # medium is moderate combination.
    total = delta_score + persist_score + scope_score

    if total >= 7:
        return "critical"
    elif total >= 5:
        return "high"
    elif total >= 3:
        return "medium"
    return "low"


def _check_persistence(
    metric_series: pd.Series,
    onset_pos: int,
    threshold_value: float,
    persistence_ratio: float,
    comparator: str = "gte",
) -> Tuple[bool, float]:
    """
    Check whether a degradation that started at ``onset_pos`` persists for
    a significant fraction of the remaining test.

    Args:
        metric_series:    The full (post-warmup) metric column (e.g. p90).
        onset_pos:        Integer position within metric_series where onset was detected.
        threshold_value:  The value above (or below for 'lte') which counts as degraded.
        persistence_ratio: Required fraction of remaining buckets that must stay degraded.
        comparator:       'gte' = metric >= threshold is degraded (latency, error rate).
                          'lte' = metric <= threshold is degraded (throughput plateau).

    Returns:
        (is_persistent, actual_ratio)
    """
    remaining = metric_series.iloc[onset_pos:]
    if remaining.empty:
        return False, 0.0

    valid = remaining.dropna()
    if valid.empty:
        return False, 0.0

    if comparator == "gte":
        degraded_count = (valid >= threshold_value).sum()
    else:  # lte
        degraded_count = (valid <= threshold_value).sum()

    actual_ratio = float(degraded_count) / float(len(valid))
    return actual_ratio >= persistence_ratio, round(actual_ratio, 4)


# ---------------------------------------------------------------------------
# 1. Latency Degradation
# ---------------------------------------------------------------------------

def _detect_latency_degradation(
    buckets_df: pd.DataFrame, baseline: Dict, cfg: Dict, test_run_id: str,
    test_start_time=None, sla_id: Optional[str] = None,
) -> List[Dict]:
    """Detect when P90 latency degrades beyond threshold relative to baseline.

    v0.2: After finding sustained consecutive buckets above threshold, validates
    that degradation **persists** for the remainder of the test using persistence_ratio.
    Transient spikes that recover are classified as 'transient_spike' instead of 'bottleneck'.
    """
    findings = []
    degrade_pct = cfg["latency_degrade_pct"]
    warmup = cfg["warmup_buckets"]
    sustained = cfg["sustained_buckets"]
    required_persistence = cfg["persistence_ratio"]
    baseline_p90 = baseline["p90"]

    if baseline_p90 <= 0:
        return findings

    threshold_ms = baseline_p90 * (1 + degrade_pct / 100)

    # Work with the active (post-warmup) slice
    active = buckets_df.iloc[warmup:].reset_index(drop=True)
    p90_series = active["p90"]  # already smoothed by _apply_outlier_filtering
    has_outlier_col = "is_outlier" in active.columns

    sustained_count = 0
    onset_pos = None  # position within 'active' where consecutive degradation started
    outliers_skipped = 0

    for pos in range(len(active)):
        p90 = p90_series.iloc[pos]
        if pd.isna(p90):
            sustained_count = 0
            onset_pos = None
            continue

        # Skip outlier buckets -- do not let them anchor an onset or count as sustained
        if has_outlier_col and active["is_outlier"].iloc[pos]:
            outliers_skipped += 1
            continue

        if p90 >= threshold_ms:
            if sustained_count == 0:
                onset_pos = pos  # mark the start of this consecutive run
            sustained_count += 1
        else:
            sustained_count = 0
            onset_pos = None

        if sustained_count >= sustained and onset_pos is not None:
            # --- Persistence check ---
            is_persistent, actual_persistence = _check_persistence(
                p90_series, onset_pos, threshold_ms, required_persistence, comparator="gte"
            )

            row = active.iloc[onset_pos]
            p90_at_onset = p90_series.iloc[onset_pos]
            delta_pct_val = (p90_at_onset - baseline_p90) / baseline_p90 * 100

            if is_persistent:
                classification = "bottleneck"
                confidence = "high"
            else:
                classification = "transient_spike"
                confidence = "low"

            findings.append(_make_finding(
                bottleneck_type="latency_degradation",
                scope="overall",
                scope_name="ALL",
                concurrency=row["concurrency"],
                metric_name="p90_response_time_ms",
                metric_value=p90_at_onset,
                baseline_value=baseline_p90,
                severity=_classify_severity_v2(
                    delta_pct=delta_pct_val,
                    persistence_ratio=actual_persistence,
                    classification=classification,
                    scope="overall",
                    bottleneck_type="latency_degradation",
                ),
                confidence=confidence,
                classification=classification,
                persistence_ratio=actual_persistence,
                outlier_filtered=outliers_skipped > 0,
                **_onset_fields(row["bucket_start"], warmup + onset_pos, test_start_time),
                evidence=(
                    f"P90 latency reached {p90_at_onset:.0f}ms (baseline {baseline_p90:.0f}ms, "
                    f"+{delta_pct_val:.1f}%) at {row['concurrency']:.0f} concurrent users. "
                    f"Persistence: {actual_persistence:.0%} of remaining buckets stayed degraded"
                    f"{' (confirmed bottleneck)' if is_persistent else ' (transient spike, recovered)'}"
                    + (f". {outliers_skipped} outlier bucket(s) filtered." if outliers_skipped else "")
                ),
                test_run_id=test_run_id,
            ))
            break  # only report the first onset

    # --- SLA breach check (also with persistence) ---
    sla = _get_sla_threshold(cfg, sla_id=sla_id)  # profile/file-level default SLA
    sustained_count = 0
    onset_pos = None
    sla_outliers_skipped = 0

    for pos in range(len(active)):
        p90 = p90_series.iloc[pos]
        if pd.isna(p90):
            sustained_count = 0
            onset_pos = None
            continue

        if has_outlier_col and active["is_outlier"].iloc[pos]:
            sla_outliers_skipped += 1
            continue

        if p90 >= sla:
            if sustained_count == 0:
                onset_pos = pos
            sustained_count += 1
        else:
            sustained_count = 0
            onset_pos = None

        if sustained_count >= sustained and onset_pos is not None:
            is_persistent, actual_persistence = _check_persistence(
                p90_series, onset_pos, sla, required_persistence, comparator="gte"
            )

            row = active.iloc[onset_pos]
            p90_at_onset = p90_series.iloc[onset_pos]

            if is_persistent:
                classification = "bottleneck"
                confidence = "high"
            else:
                classification = "transient_spike"
                confidence = "low"

            sla_delta_pct = (p90_at_onset - sla) / sla * 100 if sla > 0 else 0.0
            findings.append(_make_finding(
                bottleneck_type="latency_degradation",
                scope="overall",
                scope_name="ALL",
                concurrency=row["concurrency"],
                metric_name="p90_sla_breach",
                metric_value=p90_at_onset,
                baseline_value=sla,
                severity=_classify_severity_v2(
                    delta_pct=sla_delta_pct,
                    persistence_ratio=actual_persistence,
                    classification=classification,
                    scope="overall",
                    bottleneck_type="latency_degradation",
                ),
                confidence=confidence,
                classification=classification,
                persistence_ratio=actual_persistence,
                outlier_filtered=sla_outliers_skipped > 0,
                **_onset_fields(row["bucket_start"], warmup + onset_pos, test_start_time),
                evidence=(
                    f"P90 latency {p90_at_onset:.0f}ms exceeded SLA threshold {sla}ms "
                    f"at {row['concurrency']:.0f} concurrent users. "
                    f"Persistence: {actual_persistence:.0%} of remaining buckets stayed above SLA"
                    f"{' (confirmed)' if is_persistent else ' (transient, recovered)'}"
                    + (f". {sla_outliers_skipped} outlier bucket(s) filtered." if sla_outliers_skipped else "")
                ),
                test_run_id=test_run_id,
            ))
            break

    return findings


# ---------------------------------------------------------------------------
# 2. Error Rate Increase
# ---------------------------------------------------------------------------

def _detect_error_rate_increase(
    buckets_df: pd.DataFrame, baseline: Dict, cfg: Dict, test_run_id: str,
    test_start_time=None,
) -> List[Dict]:
    """Detect when error rate exceeds absolute or relative thresholds.

    v0.2: Validates that error rate increase persists for the remainder of the test.
    """
    findings = []
    warmup = cfg["warmup_buckets"]
    sustained = cfg["sustained_buckets"]
    required_persistence = cfg["persistence_ratio"]
    abs_threshold = cfg["error_rate_degrade_abs"]
    baseline_error = baseline["error_rate"]

    # The effective threshold: whichever is lower of absolute threshold or 2x baseline
    effective_threshold = abs_threshold
    if baseline_error > 0:
        effective_threshold = min(abs_threshold, baseline_error * 2)

    active = buckets_df.iloc[warmup:].reset_index(drop=True)
    err_series = active["error_rate"]  # already smoothed
    has_outlier_col = "is_outlier" in active.columns

    sustained_count = 0
    onset_pos = None
    outliers_skipped = 0

    for pos in range(len(active)):
        err = err_series.iloc[pos]
        if pd.isna(err):
            sustained_count = 0
            onset_pos = None
            continue

        if has_outlier_col and active["is_outlier"].iloc[pos]:
            outliers_skipped += 1
            continue

        if err >= effective_threshold:
            if sustained_count == 0:
                onset_pos = pos
            sustained_count += 1
        else:
            sustained_count = 0
            onset_pos = None

        if sustained_count >= sustained and onset_pos is not None:
            is_persistent, actual_persistence = _check_persistence(
                err_series, onset_pos, effective_threshold, required_persistence, comparator="gte"
            )

            row = active.iloc[onset_pos]
            err_at_onset = err_series.iloc[onset_pos]

            if is_persistent:
                classification = "bottleneck"
                confidence = "high"
            else:
                classification = "transient_spike"
                confidence = "low"

            err_delta_pct = (err_at_onset - baseline_error) / baseline_error * 100 if baseline_error > 0 else err_at_onset * 100
            findings.append(_make_finding(
                bottleneck_type="error_rate_increase",
                scope="overall",
                scope_name="ALL",
                concurrency=row["concurrency"],
                metric_name="error_rate_pct",
                metric_value=err_at_onset,
                baseline_value=baseline_error,
                severity=_classify_severity_v2(
                    delta_pct=err_delta_pct,
                    persistence_ratio=actual_persistence,
                    classification=classification,
                    scope="overall",
                    bottleneck_type="error_rate_increase",
                    delta_thresholds=(50.0, 200.0, 500.0),  # error rate uses wider thresholds
                ),
                confidence=confidence,
                classification=classification,
                persistence_ratio=actual_persistence,
                outlier_filtered=outliers_skipped > 0,
                **_onset_fields(row["bucket_start"], warmup + onset_pos, test_start_time),
                evidence=(
                    f"Error rate reached {err_at_onset:.2f}% (baseline {baseline_error:.2f}%) "
                    f"at {row['concurrency']:.0f} concurrent users. "
                    f"Persistence: {actual_persistence:.0%} of remaining buckets stayed elevated"
                    f"{' (confirmed bottleneck)' if is_persistent else ' (transient spike, recovered)'}"
                    + (f". {outliers_skipped} outlier bucket(s) filtered." if outliers_skipped else "")
                ),
                test_run_id=test_run_id,
            ))
            break

    return findings


# ---------------------------------------------------------------------------
# 3. Throughput Plateau
# ---------------------------------------------------------------------------

def _detect_throughput_plateau(
    buckets_df: pd.DataFrame, baseline: Dict, cfg: Dict, test_run_id: str,
    test_start_time=None,
) -> List[Dict]:
    """
    Detect when throughput stops increasing even as concurrency rises.

    v0.2: After detecting plateau onset, validates that throughput stays flat
    or declining for the remainder of the test using persistence_ratio.
    """
    findings = []
    warmup = cfg["warmup_buckets"]
    sustained = cfg["sustained_buckets"]
    required_persistence = cfg["persistence_ratio"]
    plateau_pct = cfg["throughput_plateau_pct"]

    active = buckets_df.iloc[warmup:].copy().reset_index(drop=True)
    has_outlier_col = "is_outlier" in active.columns
    if len(active) < 4:
        return findings

    # Compute rolling throughput change (3-bucket window) -- uses smoothed throughput_rps
    active["tps_pct_change"] = active["throughput_rps"].pct_change(periods=3) * 100
    active["conc_pct_change"] = active["concurrency"].pct_change(periods=3) * 100

    plateau_count = 0
    onset_pos = None
    outliers_skipped = 0

    for pos in range(len(active)):
        tps_change = active["tps_pct_change"].iloc[pos]
        conc_change = active["conc_pct_change"].iloc[pos]

        if pd.isna(tps_change) or pd.isna(conc_change):
            plateau_count = 0
            onset_pos = None
            continue

        if has_outlier_col and active["is_outlier"].iloc[pos]:
            outliers_skipped += 1
            continue

        # Concurrency is rising but throughput is flat or declining
        if conc_change > 5.0 and tps_change < plateau_pct:
            if plateau_count == 0:
                onset_pos = pos
            plateau_count += 1
        else:
            plateau_count = 0
            onset_pos = None

        if plateau_count >= sustained and onset_pos is not None:
            # For throughput plateau, persistence means throughput stays at or below
            # the level it was at onset. Use the onset throughput as the ceiling.
            onset_tps = active["throughput_rps"].iloc[onset_pos]
            is_persistent, actual_persistence = _check_persistence(
                active["throughput_rps"], onset_pos, onset_tps * 1.05, required_persistence,
                comparator="lte"  # throughput staying at or below onset level = still plateaued
            )

            row = active.iloc[onset_pos]

            if is_persistent:
                classification = "bottleneck"
                confidence = "high"
            else:
                classification = "transient_spike"
                confidence = "low"

            tps_delta_pct = (
                (onset_tps - baseline["throughput_rps"]) / baseline["throughput_rps"] * 100
                if baseline["throughput_rps"] > 0 else 0.0
            )
            findings.append(_make_finding(
                bottleneck_type="throughput_plateau",
                scope="overall",
                scope_name="ALL",
                concurrency=row["concurrency"],
                metric_name="throughput_rps",
                metric_value=onset_tps,
                baseline_value=baseline["throughput_rps"],
                severity=_classify_severity_v2(
                    delta_pct=tps_delta_pct,
                    persistence_ratio=actual_persistence,
                    classification=classification,
                    scope="overall",
                    bottleneck_type="throughput_plateau",
                ),
                confidence=confidence,
                classification=classification,
                persistence_ratio=actual_persistence,
                outlier_filtered=outliers_skipped > 0,
                **_onset_fields(row["bucket_start"], warmup + onset_pos, test_start_time),
                evidence=(
                    f"Throughput plateaued at {onset_tps:.1f} RPS "
                    f"while concurrency rose to {row['concurrency']:.0f} users. "
                    f"Persistence: {actual_persistence:.0%} of remaining buckets stayed flat"
                    f"{' (confirmed bottleneck)' if is_persistent else ' (transient, recovered)'}"
                    + (f". {outliers_skipped} outlier bucket(s) filtered." if outliers_skipped else "")
                ),
                test_run_id=test_run_id,
            ))
            break

    return findings


# ---------------------------------------------------------------------------
# 4. Infrastructure Saturation
# ---------------------------------------------------------------------------

def _detect_infra_saturation(
    buckets_df: pd.DataFrame, baseline: Dict, cfg: Dict, test_run_id: str,
    test_start_time=None,
) -> List[Dict]:
    """Detect when CPU or memory exceeds configured thresholds."""
    findings = []
    warmup = cfg["warmup_buckets"]
    sustained = cfg["sustained_buckets"]
    cpu_threshold = cfg["cpu_high_pct"]
    mem_threshold = cfg["memory_high_pct"]

    has_cpu = "avg_cpu" in buckets_df.columns
    has_mem = "avg_memory" in buckets_df.columns
    if not has_cpu and not has_mem:
        return findings

    cpu_sustained = 0
    mem_sustained = 0
    cpu_flagged = False
    mem_flagged = False

    active_slice = buckets_df.iloc[warmup:]
    for pos, (idx, row) in enumerate(active_slice.iterrows()):
        bucket_idx = warmup + pos  # absolute bucket index

        # CPU saturation
        cpu = row.get("avg_cpu", np.nan)
        if pd.notna(cpu) and cpu >= cpu_threshold:
            cpu_sustained += 1
        else:
            cpu_sustained = 0

        if cpu_sustained >= sustained and not cpu_flagged:
            cpu_flagged = True
            findings.append(_make_finding(
                bottleneck_type="infrastructure_saturation",
                scope="infrastructure",
                scope_name="CPU",
                concurrency=row["concurrency"],
                metric_name="avg_cpu_util_pct",
                metric_value=cpu,
                baseline_value=baseline.get("avg_cpu", 0),
                severity="critical" if cpu >= 90 else "high",
                confidence="high",
                **_onset_fields(row["bucket_start"], bucket_idx, test_start_time),
                evidence=(
                    f"CPU utilization reached {cpu:.1f}% (threshold {cpu_threshold}%) "
                    f"at {row['concurrency']:.0f} concurrent users"
                ),
                test_run_id=test_run_id,
            ))

        # Memory saturation
        mem = row.get("avg_memory", np.nan)
        if pd.notna(mem) and mem >= mem_threshold:
            mem_sustained += 1
        else:
            mem_sustained = 0

        if mem_sustained >= sustained and not mem_flagged:
            mem_flagged = True
            findings.append(_make_finding(
                bottleneck_type="infrastructure_saturation",
                scope="infrastructure",
                scope_name="Memory",
                concurrency=row["concurrency"],
                metric_name="avg_memory_util_pct",
                metric_value=mem,
                baseline_value=baseline.get("avg_memory", 0),
                severity="critical" if mem >= 95 else "high",
                confidence="high",
                **_onset_fields(row["bucket_start"], bucket_idx, test_start_time),
                evidence=(
                    f"Memory utilization reached {mem:.1f}% (threshold {mem_threshold}%) "
                    f"at {row['concurrency']:.0f} concurrent users"
                ),
                test_run_id=test_run_id,
            ))

    return findings


# ---------------------------------------------------------------------------
# 5. Resource <-> Performance Coupling
# ---------------------------------------------------------------------------

def _detect_resource_performance_coupling(
    buckets_df: pd.DataFrame, cfg: Dict, test_run_id: str,
    test_start_time=None,
) -> List[Dict]:
    """Detect temporal coincidence of latency degradation and infra pressure."""
    findings = []

    if "avg_cpu" not in buckets_df.columns:
        return findings

    warmup = cfg["warmup_buckets"]
    active = buckets_df.iloc[warmup:].copy()

    if len(active) < 4:
        return findings

    # Compute correlations across the active window
    for resource, col in [("CPU", "avg_cpu"), ("Memory", "avg_memory")]:
        if col not in active.columns or active[col].isna().all():
            continue

        valid = active[["p90", col]].dropna()
        if len(valid) < 4:
            continue

        correlation = valid["p90"].corr(valid[col])
        if pd.isna(correlation):
            continue

        abs_corr = abs(correlation)
        if abs_corr >= 0.5:
            strength = "strong" if abs_corr >= 0.7 else "moderate"
            direction = "positive" if correlation > 0 else "negative"

            # Find peak co-occurrence bucket
            peak_idx = active["p90"].idxmax()
            peak_row = active.loc[peak_idx]
            # Determine absolute bucket index for the peak
            peak_bucket_idx = warmup + active.index.get_loc(peak_idx)

            findings.append(_make_finding(
                bottleneck_type="resource_performance_coupling",
                scope="infrastructure",
                scope_name=resource,
                concurrency=peak_row["concurrency"],
                metric_name=f"{resource.lower()}_p90_correlation",
                metric_value=round(correlation, 3),
                baseline_value=0.0,
                severity="high" if abs_corr >= 0.7 else "medium",
                confidence="high" if abs_corr >= 0.7 else "medium",
                **_onset_fields(peak_row["bucket_start"], peak_bucket_idx, test_start_time),
                evidence=(
                    f"{strength.title()} {direction} correlation ({correlation:.3f}) between "
                    f"{resource} utilization and P90 latency. Peak latency at "
                    f"{peak_row['concurrency']:.0f} concurrent users"
                ),
                test_run_id=test_run_id,
            ))

    return findings


# ---------------------------------------------------------------------------
# Phase 2a: Infrastructure Cross-Reference (scoped to degradation windows)
# ---------------------------------------------------------------------------

def _extract_degradation_windows(
    findings: List[Dict], buckets_df: pd.DataFrame, cfg: Dict
) -> List[Dict[str, Any]]:
    """Extract degradation windows from Phase 1 findings.

    Each degradation window describes **when** performance degraded.
    Only findings classified as actual bottlenecks (not transient spikes,
    not known-slow endpoints, not info-level) produce windows.

    Returns a list of dicts, each containing:
        - finding_index: index into the findings list
        - onset_bucket_index: absolute bucket index
        - onset_timestamp: timestamp string
        - end_bucket_index: last bucket index (end of test if persistent)
        - end_timestamp: timestamp string
        - persistence_ratio: from the finding
        - bottleneck_type: from the finding
    """
    windows = []
    warmup = cfg["warmup_buckets"]
    total_buckets = len(buckets_df)

    for i, f in enumerate(findings):
        # Only real bottlenecks produce degradation windows
        if f.get("classification") in ("transient_spike", "known_slow_endpoint"):
            continue
        if f.get("severity") == "info":
            continue

        onset_idx = f.get("onset_bucket_index")
        if onset_idx is None:
            continue

        # Determine end of degradation window
        persistence = f.get("persistence_ratio")
        if persistence is not None and persistence >= cfg["persistence_ratio"]:
            # Persistent degradation -- assume it extends to end of test
            end_idx = total_buckets - 1
        else:
            # Non-persistent -- approximate window as onset + sustained_buckets
            end_idx = min(onset_idx + cfg["sustained_buckets"], total_buckets - 1)

        onset_ts = f.get("onset_timestamp", "")
        if end_idx < total_buckets:
            end_ts = str(buckets_df["bucket_start"].iloc[end_idx]) if end_idx < len(buckets_df) else ""
        else:
            end_ts = str(buckets_df["bucket_start"].iloc[-1]) if len(buckets_df) > 0 else ""

        windows.append({
            "finding_index": i,
            "onset_bucket_index": onset_idx,
            "onset_timestamp": onset_ts,
            "end_bucket_index": end_idx,
            "end_timestamp": end_ts,
            "persistence_ratio": persistence,
            "bottleneck_type": f.get("bottleneck_type"),
        })

    return windows


def _cross_reference_infrastructure(
    findings: List[Dict],
    buckets_df: pd.DataFrame,
    baseline: Dict,
    cfg: Dict,
    degradation_windows: List[Dict],
    infra_meta: Optional[Dict[str, Any]] = None,
) -> List[Dict]:
    """Phase 2a: cross-reference infrastructure metrics with degradation windows.

    For each degradation window:
    1. Extract avg CPU and Memory during the degradation window.
    2. Extract avg CPU and Memory during the baseline (healthy) window.
    3. Compute deltas and a window-scoped correlation.
    4. Classify as infrastructure-correlated, infrastructure-independent,
       or infrastructure-inconclusive.
    5. Attach the ``infrastructure_context`` dict to the corresponding finding.

    When ``infra_meta["metric_mode"] == "raw"`` (K8s limits missing), absolute
    threshold comparisons are skipped and only relative delta from baseline is
    used for correlation classification.

    Returns the modified findings list with infrastructure_context populated.
    """
    if infra_meta is None:
        infra_meta = {"metric_mode": "percentage", "limits_available": True, "cpu_unit": "%", "memory_unit": "%"}
    is_raw = infra_meta["metric_mode"] == "raw"
    cpu_unit = infra_meta["cpu_unit"]
    mem_unit = infra_meta["memory_unit"]
    has_infra = "avg_cpu" in buckets_df.columns
    if not has_infra:
        # Mark all findings as inconclusive
        for w in degradation_windows:
            idx = w["finding_index"]
            if idx < len(findings):
                findings[idx]["infrastructure_context"] = {
                    "status": "no_infra_data",
                    "infra_correlated": None,
                    "baseline_window": None,
                    "degradation_window": None,
                }
        return findings

    warmup = cfg["warmup_buckets"]
    # Infra baseline: use the healthy window before the earliest degradation onset.
    # If no degradation windows exist, this function won't be called, but handle gracefully.
    earliest_onset = min(
        (w["onset_bucket_index"] for w in degradation_windows),
        default=warmup
    )
    # Baseline window: from end of warmup to the earliest onset (exclusive)
    baseline_start = warmup
    baseline_end = max(earliest_onset, baseline_start + 1)  # at least 1 bucket
    baseline_slice = buckets_df.iloc[baseline_start:baseline_end]

    # Exclude outliers from baseline if available
    if "is_outlier" in baseline_slice.columns:
        non_outlier = baseline_slice[~baseline_slice["is_outlier"]]
        if not non_outlier.empty:
            baseline_slice = non_outlier

    def _safe_mean(series):
        v = series.mean()
        return round(float(v), 2) if pd.notna(v) else None

    infra_baseline = {
        "avg_cpu": _safe_mean(baseline_slice["avg_cpu"]) if "avg_cpu" in baseline_slice.columns else None,
        "avg_memory": _safe_mean(baseline_slice["avg_memory"]) if "avg_memory" in baseline_slice.columns else None,
        "bucket_range": f"{baseline_start}-{baseline_end - 1}",
    }

    for w in degradation_windows:
        idx = w["finding_index"]
        if idx >= len(findings):
            continue

        onset = w["onset_bucket_index"]
        end = w["end_bucket_index"] + 1  # exclusive
        degrade_slice = buckets_df.iloc[onset:end]

        if degrade_slice.empty:
            findings[idx]["infrastructure_context"] = {
                "status": "no_data_in_window",
                "infra_correlated": None,
                "baseline_window": infra_baseline,
                "degradation_window": None,
            }
            continue

        infra_during = {
            "avg_cpu": _safe_mean(degrade_slice["avg_cpu"]) if "avg_cpu" in degrade_slice.columns else None,
            "avg_memory": _safe_mean(degrade_slice["avg_memory"]) if "avg_memory" in degrade_slice.columns else None,
            "max_cpu": round(float(degrade_slice["max_cpu"].max()), 2) if "max_cpu" in degrade_slice.columns and degrade_slice["max_cpu"].notna().any() else None,
            "max_memory": round(float(degrade_slice["max_memory"].max()), 2) if "max_memory" in degrade_slice.columns and degrade_slice["max_memory"].notna().any() else None,
            "bucket_range": f"{onset}-{end - 1}",
        }

        # Compute deltas
        cpu_delta_pct = None
        mem_delta_pct = None
        cpu_correlated = False
        mem_correlated = False

        b_cpu = infra_baseline["avg_cpu"]
        d_cpu = infra_during["avg_cpu"]
        if b_cpu is not None and d_cpu is not None and b_cpu > 0:
            cpu_delta_pct = round((d_cpu - b_cpu) / b_cpu * 100, 1)
            if is_raw:
                # Raw mode: only relative delta matters (no absolute threshold)
                cpu_correlated = cpu_delta_pct >= 25.0
            else:
                # Percentage mode: correlated if delta >= 25% OR exceeds threshold
                cpu_correlated = (
                    cpu_delta_pct >= 25.0
                    or (d_cpu >= cfg["cpu_high_pct"])
                )

        b_mem = infra_baseline["avg_memory"]
        d_mem = infra_during["avg_memory"]
        if b_mem is not None and d_mem is not None and b_mem > 0:
            mem_delta_pct = round((d_mem - b_mem) / b_mem * 100, 1)
            if is_raw:
                mem_correlated = mem_delta_pct >= 25.0
            else:
                mem_correlated = (
                    mem_delta_pct >= 25.0
                    or (d_mem >= cfg["memory_high_pct"])
                )

        # Window-scoped correlation (P90 vs CPU/Memory within degradation window)
        window_correlations = {}
        for resource, col in [("cpu", "avg_cpu"), ("memory", "avg_memory")]:
            if col in degrade_slice.columns:
                valid = degrade_slice[["p90", col]].dropna()
                if len(valid) >= 3:
                    corr = valid["p90"].corr(valid[col])
                    if pd.notna(corr):
                        window_correlations[f"{resource}_p90_correlation"] = round(float(corr), 3)

        # Determine overall classification
        any_correlated = cpu_correlated or mem_correlated
        has_any_data = (b_cpu is not None or b_mem is not None)

        if not has_any_data:
            status = "inconclusive"
            infra_correlated_flag = None
            root_cause = "No infrastructure data available for this time window."
        elif any_correlated:
            status = "infrastructure_correlated"
            infra_correlated_flag = True
            factors = []
            if cpu_correlated:
                if is_raw:
                    factors.append(
                        f"CPU (baseline {b_cpu:.3f} {cpu_unit} -> {d_cpu:.3f} {cpu_unit}, +{cpu_delta_pct}%)"
                    )
                else:
                    factors.append(f"CPU (baseline {b_cpu}% -> {d_cpu}%, +{cpu_delta_pct}%)")
            if mem_correlated:
                if is_raw:
                    factors.append(
                        f"Memory (baseline {b_mem:.2f} {mem_unit} -> {d_mem:.2f} {mem_unit}, +{mem_delta_pct}%)"
                    )
                else:
                    factors.append(f"Memory (baseline {b_mem}% -> {d_mem}%, +{mem_delta_pct}%)")
            root_cause = f"Infrastructure stress likely contributing: {', '.join(factors)}."
            if is_raw:
                root_cause += (
                    " Note: K8s resource limits not defined; reporting raw usage. "
                    "Unable to determine utilization % or remaining capacity."
                )
        else:
            status = "infrastructure_independent"
            infra_correlated_flag = False
            root_cause = (
                "Infrastructure metrics remained stable during degradation. "
                "Bottleneck is likely application-level (code, database, or external dependency)."
            )

        findings[idx]["infrastructure_context"] = {
            "status": status,
            "infra_correlated": infra_correlated_flag,
            "limits_available": infra_meta["limits_available"],
            "metric_mode": infra_meta["metric_mode"],
            "cpu_unit": cpu_unit,
            "memory_unit": mem_unit,
            "baseline_window": infra_baseline,
            "degradation_window": infra_during,
            "cpu_delta_pct": cpu_delta_pct,
            "memory_delta_pct": mem_delta_pct,
            "cpu_correlated": cpu_correlated,
            "memory_correlated": mem_correlated,
            "window_correlations": window_correlations if window_correlations else None,
            "root_cause_indicator": root_cause,
        }

        # Append root cause to finding evidence
        existing_evidence = findings[idx].get("evidence", "")
        findings[idx]["evidence"] = f"{existing_evidence} Infra: {root_cause}"

    return findings


# ---------------------------------------------------------------------------
# Phase 2b: Capacity Risk Detection (infra stress without latency degradation)
# ---------------------------------------------------------------------------

def _detect_capacity_risks(
    buckets_df: pd.DataFrame,
    baseline: Dict,
    cfg: Dict,
    test_run_id: str,
    degradation_windows: List[Dict],
    test_start_time=None,
    infra_meta: Optional[Dict[str, Any]] = None,
    sla_id: Optional[str] = None,
) -> List[Dict]:
    """Phase 2b: detect sustained infrastructure stress when performance is healthy.

    Scans CPU and Memory across the full test for sustained periods above
    threshold.  If the high-infra window does **not** overlap any Phase 1
    degradation window, the finding is classified as ``capacity_risk`` --
    the system is coping but has limited headroom.

    **Raw mode** (``infra_meta["metric_mode"] == "raw"``):
    When K8s limits are not defined, absolute percentage thresholds cannot
    be applied.  Instead, sustained periods where raw usage exceeds
    ``baseline * (1 + raw_metric_degrade_pct / 100)`` are flagged.
    ``headroom_pct`` is not reported (ceiling unknown).

    Also detects:
    - **Climbing trends**: Memory or CPU steadily increasing over the test
      (potential leak or unbounded growth) even if the threshold was never
      breached.
    """
    if infra_meta is None:
        infra_meta = {"metric_mode": "percentage", "limits_available": True, "cpu_unit": "%", "memory_unit": "%"}
    is_raw = infra_meta["metric_mode"] == "raw"
    cpu_unit = infra_meta["cpu_unit"]
    mem_unit = infra_meta["memory_unit"]

    findings: List[Dict] = []

    if "avg_cpu" not in buckets_df.columns and "avg_memory" not in buckets_df.columns:
        return findings

    warmup = cfg["warmup_buckets"]
    sustained = cfg["sustained_buckets"]
    required_persistence = cfg["persistence_ratio"]
    cpu_threshold = cfg["cpu_high_pct"]
    mem_threshold = cfg["memory_high_pct"]
    raw_degrade_pct = cfg.get("raw_metric_degrade_pct", 50.0)
    sla = _get_sla_threshold(cfg, sla_id=sla_id)  # profile/file-level default SLA

    active = buckets_df.iloc[warmup:].copy().reset_index(drop=True)
    if active.empty:
        return findings

    # In raw mode, compute dynamic thresholds from baseline
    if is_raw:
        baseline_cpu = baseline.get("avg_cpu", 0)
        baseline_mem = baseline.get("avg_memory", 0)
        # Dynamic threshold = baseline * (1 + raw_degrade_pct / 100)
        cpu_dynamic_threshold = baseline_cpu * (1 + raw_degrade_pct / 100) if baseline_cpu > 0 else None
        mem_dynamic_threshold = baseline_mem * (1 + raw_degrade_pct / 100) if baseline_mem > 0 else None
    else:
        cpu_dynamic_threshold = None
        mem_dynamic_threshold = None

    # Build set of bucket indices covered by degradation windows (absolute index)
    degraded_buckets: set = set()
    for w in degradation_windows:
        for b in range(w["onset_bucket_index"], w["end_bucket_index"] + 1):
            degraded_buckets.add(b)

    # --- Sustained High Utilization Scan ---
    resource_configs = []
    if is_raw:
        # Raw mode: use dynamic thresholds from baseline
        if cpu_dynamic_threshold is not None:
            resource_configs.append(("CPU", "avg_cpu", cpu_dynamic_threshold))
        if mem_dynamic_threshold is not None:
            resource_configs.append(("Memory", "avg_memory", mem_dynamic_threshold))
    else:
        # Percentage mode: use absolute thresholds
        resource_configs.append(("CPU", "avg_cpu", cpu_threshold))
        resource_configs.append(("Memory", "avg_memory", mem_threshold))

    for resource, col, threshold in resource_configs:
        if col not in active.columns or active[col].isna().all():
            continue

        # Find contiguous runs above threshold (post-warmup)
        above = active[col] >= threshold
        onset_pos = None
        run_length = 0

        for pos in range(len(active)):
            if pd.isna(active[col].iloc[pos]):
                run_length = 0
                onset_pos = None
                continue

            if above.iloc[pos]:
                if run_length == 0:
                    onset_pos = pos
                run_length += 1
            else:
                run_length = 0
                onset_pos = None

            if run_length >= sustained and onset_pos is not None:
                # Found sustained high utilization -- check persistence
                is_persistent, actual_persistence = _check_persistence(
                    active[col], onset_pos, threshold, required_persistence, comparator="gte"
                )

                if not is_persistent:
                    # Not sustained enough -- skip
                    break

                # Check overlap with degradation windows
                abs_onset = warmup + onset_pos
                abs_end = warmup + len(active) - 1  # persistent = through end
                overlaps_degradation = any(
                    b in degraded_buckets for b in range(abs_onset, abs_end + 1)
                )

                if overlaps_degradation:
                    # Already captured by Phase 2a
                    break

                # This is a capacity risk -- infra stressed but performance held
                window_slice = active.iloc[onset_pos:]
                avg_util = float(window_slice[col].mean())
                # Use max_cpu / max_memory columns if available
                max_col = "max_cpu" if resource == "CPU" else "max_memory"
                if max_col in active.columns and active[max_col].iloc[onset_pos:].notna().any():
                    max_util = float(active[max_col].iloc[onset_pos:].max())
                else:
                    max_util = float(window_slice[col].max())

                # Performance during this window
                p90_during = float(window_slice["p90"].mean()) if "p90" in window_slice.columns else None
                within_sla = p90_during < sla if p90_during is not None and sla else None

                # Duration in minutes
                duration_buckets = len(window_slice)
                duration_minutes = duration_buckets * cfg["bucket_seconds"] / 60

                r_unit = cpu_unit if resource == "CPU" else mem_unit
                baseline_val = baseline.get(f"avg_{resource.lower()}", 0)

                if is_raw:
                    # Raw mode: report in raw units, no headroom
                    delta_from_baseline = (
                        round((avg_util - baseline_val) / baseline_val * 100, 1)
                        if baseline_val > 0 else None
                    )
                    evidence = (
                        f"{resource} usage averaged {avg_util:.3f} {r_unit} "
                        f"(peak {max_util:.3f} {r_unit}) for "
                        f"{duration_minutes:.0f} minutes while response times remained "
                        f"{'within SLA' if within_sla else 'healthy'} "
                        f"(avg P90={p90_during:.0f}ms). "
                        f"Baseline {resource} was {baseline_val:.3f} {r_unit}"
                    )
                    if delta_from_baseline is not None:
                        evidence += f" (+{delta_from_baseline}% from baseline)."
                    else:
                        evidence += "."
                    evidence += (
                        f" Resource limits are not defined for this K8s service. "
                        f"Unable to determine utilization % or remaining capacity. "
                        f"Consider setting K8s resource limits for more accurate analysis."
                    )
                    infra_ctx = {
                        "status": "capacity_risk",
                        "resource": resource,
                        "limits_available": False,
                        "metric_mode": "raw",
                        "avg_value": round(avg_util, 4),
                        "max_value": round(max_util, 4),
                        "unit": r_unit,
                        "baseline_value": round(baseline_val, 4),
                        "delta_from_baseline_pct": delta_from_baseline,
                        "threshold_applied": f"relative (raw_metric_degrade_pct: {raw_degrade_pct}%)",
                        "headroom_pct": None,
                        "performance_during_window": {
                            "avg_p90": round(p90_during, 1) if p90_during is not None else None,
                            "sla_threshold": sla,
                            "within_sla": within_sla,
                        },
                        "duration_minutes": round(duration_minutes, 1),
                        "persistence_ratio": actual_persistence,
                    }
                    metric_name = f"avg_{resource.lower()}_{r_unit}"
                else:
                    # Percentage mode: report with headroom
                    headroom = round(100.0 - avg_util, 1)
                    evidence = (
                        f"{resource} averaged {avg_util:.1f}% (peak {max_util:.1f}%) for "
                        f"{duration_minutes:.0f} minutes while response times remained "
                        f"{'within SLA' if within_sla else 'healthy'} "
                        f"(avg P90={p90_during:.0f}ms). "
                        f"System has approximately {headroom}% {resource} headroom "
                        f"before saturation. "
                        f"Consider scaling {resource} allocation before increasing load further."
                    )
                    infra_ctx = {
                        "status": "capacity_risk",
                        "resource": resource,
                        "limits_available": True,
                        "metric_mode": "percentage",
                        "avg_utilization": round(avg_util, 1),
                        "max_utilization": round(max_util, 1),
                        "threshold": threshold,
                        "headroom_pct": headroom,
                        "performance_during_window": {
                            "avg_p90": round(p90_during, 1) if p90_during is not None else None,
                            "sla_threshold": sla,
                            "within_sla": within_sla,
                        },
                        "duration_minutes": round(duration_minutes, 1),
                        "persistence_ratio": actual_persistence,
                    }
                    metric_name = f"avg_{resource.lower()}_util_pct"

                findings.append(_make_finding(
                    bottleneck_type="capacity_risk",
                    scope="infrastructure",
                    scope_name=resource,
                    concurrency=float(active["concurrency"].iloc[onset_pos]),
                    metric_name=metric_name,
                    metric_value=round(avg_util, 4 if is_raw else 1),
                    baseline_value=round(baseline_val, 4 if is_raw else 1) if baseline_val else 0,
                    severity="medium",
                    confidence="high" if not is_raw else "medium",
                    classification="capacity_risk",
                    persistence_ratio=actual_persistence,
                    **_onset_fields(
                        buckets_df["bucket_start"].iloc[warmup + onset_pos],
                        warmup + onset_pos,
                        test_start_time,
                    ),
                    evidence=evidence,
                    infrastructure_context=infra_ctx,
                    test_run_id=test_run_id,
                ))
                break  # Only report first sustained window per resource

    # --- Climbing Trend Detection ---
    # Detect if CPU or Memory is trending upward over the test (potential leak)
    if len(active) >= 6:
        for resource, col in [("CPU", "avg_cpu"), ("Memory", "avg_memory")]:
            if col not in active.columns or active[col].isna().all():
                continue

            series = active[col].dropna()
            if len(series) < 6:
                continue

            # Split into first and second half
            mid = len(series) // 2
            first_half_mean = float(series.iloc[:mid].mean())
            second_half_mean = float(series.iloc[mid:].mean())

            if first_half_mean <= 0:
                continue

            climb_pct = (second_half_mean - first_half_mean) / first_half_mean * 100

            # Only flag significant, sustained climbs (>= 30% increase from first to second half)
            if is_raw:
                # Raw mode: only check relative climb (no absolute min_level check)
                min_level_met = True
            else:
                # Percentage mode: second half should be non-trivially high (>= 50% of threshold)
                min_level = cpu_threshold * 0.5 if resource == "CPU" else mem_threshold * 0.5
                min_level_met = second_half_mean >= min_level

            if climb_pct >= 30.0 and min_level_met:
                # Check this wasn't already captured by the sustained scan above
                already_flagged = any(
                    f["scope_name"] == resource and f["bottleneck_type"] == "capacity_risk"
                    for f in findings
                )
                if already_flagged:
                    continue

                r_unit = cpu_unit if resource == "CPU" else mem_unit
                if is_raw:
                    climb_evidence = (
                        f"{resource} usage climbed from avg {first_half_mean:.3f} {r_unit} (first half) to "
                        f"{second_half_mean:.3f} {r_unit} (second half), a {climb_pct:.0f}% increase. "
                        f"This rising trend may indicate resource pressure building over time. "
                        f"K8s resource limits not defined; unable to determine utilization %. "
                        f"Monitor for potential {resource.lower()} saturation at higher concurrency or longer test duration."
                    )
                else:
                    climb_evidence = (
                        f"{resource} usage climbed from avg {first_half_mean:.1f}% (first half) to "
                        f"{second_half_mean:.1f}% (second half), a {climb_pct:.0f}% increase. "
                        f"This rising trend may indicate resource pressure building over time. "
                        f"Monitor for potential {resource.lower()} saturation at higher concurrency or longer test duration."
                    )

                findings.append(_make_finding(
                    bottleneck_type="capacity_risk",
                    scope="infrastructure",
                    scope_name=f"{resource} (Climbing Trend)",
                    concurrency=float(active["concurrency"].iloc[-1]),
                    metric_name=f"avg_{resource.lower()}_trend",
                    metric_value=round(second_half_mean, 4 if is_raw else 1),
                    baseline_value=round(first_half_mean, 4 if is_raw else 1),
                    severity="low",
                    confidence="medium",
                    classification="capacity_risk",
                    persistence_ratio=None,
                    **_onset_fields(
                        buckets_df["bucket_start"].iloc[warmup + mid],
                        warmup + mid,
                        test_start_time,
                    ),
                    evidence=climb_evidence,
                    infrastructure_context={
                        "status": "climbing_trend",
                        "resource": resource,
                        "limits_available": infra_meta["limits_available"],
                        "metric_mode": infra_meta["metric_mode"],
                        "unit": r_unit,
                        "first_half_avg": round(first_half_mean, 4 if is_raw else 1),
                        "second_half_avg": round(second_half_mean, 4 if is_raw else 1),
                        "climb_pct": round(climb_pct, 1),
                    },
                    test_run_id=test_run_id,
                ))

    return findings


# ---------------------------------------------------------------------------
# 7. Multi-Tier Bottlenecks (per-label analysis)
# ---------------------------------------------------------------------------

def _detect_multi_tier_bottlenecks(
    jtl_df: pd.DataFrame, cfg: Dict, test_run_id: str,
    test_start_time=None, sla_id: Optional[str] = None,
) -> List[Dict]:
    """
    Detect which endpoints degrade under load, distinguishing true
    load-induced bottlenecks from inherently slow endpoints.

    v0.2 (Improvement 4): Per-endpoint analysis with:

    1. Per-label bucketing with rolling median smoothing.
    2. **Per-label baseline** computed from early buckets.
    3. An endpoint is a **load-induced bottleneck** only if its P90
       *degrades significantly from its own baseline* AND breaches the SLA.
       An endpoint that is *always* above SLA (i.e. already above SLA at
       baseline) is classified as ``known_slow_endpoint`` -- an informational
       observation, not a bottleneck.
    4. Sustained consecutive degraded buckets required.
    5. Persistence check classifies findings.
    """
    findings = []
    bucket_seconds = cfg["bucket_seconds"]
    warmup = cfg["warmup_buckets"]
    sustained_required = cfg["sustained_buckets"]
    required_persistence = cfg["persistence_ratio"]
    rolling_window = int(cfg.get("rolling_window_buckets", 3))
    degrade_pct = cfg["latency_degrade_pct"]

    labels = jtl_df["label"].unique()
    if len(labels) <= 1:
        return findings  # Nothing to compare

    # Per-label analysis results
    label_results: Dict[str, Dict[str, Any]] = {}

    for label in labels:
        label_sla = _get_sla_threshold(cfg, label=label, sla_id=sla_id)

        label_df = jtl_df[jtl_df["label"] == label].copy()
        if len(label_df) < 10:
            continue

        # --- Per-label bucketing ---
        label_indexed = label_df.set_index("timestamp").sort_index()
        label_concurrency = _compute_bucket_concurrency(label_indexed, bucket_seconds)
        resampled = label_indexed.resample(f"{bucket_seconds}s").agg(
            p90=("elapsed", lambda x: x.quantile(0.90) if len(x) else np.nan),
            total_requests=("elapsed", "count"),
        )
        resampled = resampled.join(label_concurrency)
        resampled = resampled[resampled["total_requests"] > 0]

        if len(resampled) <= warmup:
            continue

        # --- Per-label rolling median smoothing ---
        p90_raw = resampled["p90"].copy()
        resampled["p90"] = p90_raw.rolling(
            window=rolling_window, center=True, min_periods=1
        ).median()

        # --- Per-label outlier detection (MAD-based) ---
        smoothed_p90 = resampled["p90"]
        rolling_mad = (
            (p90_raw - smoothed_p90)
            .abs()
            .rolling(window=rolling_window, center=True, min_periods=1)
            .median()
        )
        mad_floor = smoothed_p90.median() * 0.05 if smoothed_p90.median() > 0 else 1.0
        rolling_mad = rolling_mad.clip(lower=mad_floor)
        is_outlier = (p90_raw - smoothed_p90).abs() > (2.0 * rolling_mad)

        # --- Per-label baseline (from early post-warmup buckets) ---
        baseline_end = min(warmup + sustained_required, len(resampled))
        baseline_slice = resampled.iloc[warmup:baseline_end]
        if baseline_slice.empty:
            baseline_slice = resampled.head(1)
        label_baseline_p90 = float(baseline_slice["p90"].mean())

        # --- Classification: inherently slow vs load-induced ---
        # If the endpoint is already above SLA at baseline, it's inherently
        # slow -- not a load-induced bottleneck.
        already_above_sla = label_baseline_p90 >= label_sla

        if already_above_sla:
            # Record as known slow endpoint (informational only)
            label_results[label] = {
                "concurrency": float(baseline_slice["concurrency"].iloc[0]) if not baseline_slice.empty else 0.0,
                "p90": label_baseline_p90,
                "baseline_p90": label_baseline_p90,
                "sla": label_sla,
                "onset_ts": resampled.index[warmup] if warmup < len(resampled) else resampled.index[0],
                "onset_idx": warmup,
                "is_persistent": False,
                "persistence_ratio": None,
                "classification": "known_slow_endpoint",
            }
            continue

        # --- Degradation threshold: endpoint must degrade from ITS OWN baseline ---
        # Use the same degrade_pct as overall latency detection
        label_threshold = label_baseline_p90 * (1 + degrade_pct / 100)
        # Also require the degraded value to exceed the SLA
        # (we don't flag sub-SLA degradation as a multi-tier bottleneck)
        effective_threshold = max(label_threshold, label_sla)

        # --- Sustained degradation scan (post-warmup, skip outliers) ---
        active_start = warmup
        sustained_count = 0
        onset_idx = None

        for idx in range(active_start, len(resampled)):
            p90_val = resampled["p90"].iloc[idx]
            if pd.isna(p90_val):
                sustained_count = 0
                onset_idx = None
                continue

            if is_outlier.iloc[idx]:
                continue

            if p90_val >= effective_threshold:
                if sustained_count == 0:
                    onset_idx = idx
                sustained_count += 1
            else:
                sustained_count = 0
                onset_idx = None

            if sustained_count >= sustained_required and onset_idx is not None:
                break
        else:
            onset_idx = None

        if onset_idx is None:
            continue

        # --- Persistence check ---
        is_persistent, actual_persistence = _check_persistence(
            resampled["p90"], onset_idx, effective_threshold, required_persistence, comparator="gte"
        )

        onset_row = resampled.iloc[onset_idx]
        onset_ts = resampled.index[onset_idx]
        p90_at_onset = resampled["p90"].iloc[onset_idx]

        label_results[label] = {
            "concurrency": float(onset_row["concurrency"]),
            "p90": float(p90_at_onset),
            "baseline_p90": label_baseline_p90,
            "sla": label_sla,
            "onset_ts": onset_ts,
            "onset_idx": onset_idx,
            "is_persistent": is_persistent,
            "persistence_ratio": actual_persistence,
            "classification": "bottleneck" if is_persistent else "transient_spike",
        }

    if not label_results:
        return findings

    # --- Separate true bottlenecks from known-slow endpoints ---
    degraded_labels = {
        k: v for k, v in label_results.items()
        if v["classification"] in ("bottleneck", "transient_spike")
    }
    known_slow_labels = {
        k: v for k, v in label_results.items()
        if v["classification"] == "known_slow_endpoint"
    }

    total_labels = len(labels)

    # --- Report load-induced bottlenecks ---
    if degraded_labels:
        sorted_degraded = sorted(
            degraded_labels.items(),
            key=lambda x: (
                0 if x[1]["is_persistent"] else 1,
                x[1]["concurrency"],
            ),
        )
        bottleneck_count = sum(1 for _, r in sorted_degraded if r["is_persistent"])

        for rank, (label, r) in enumerate(sorted_degraded[:5]):
            degrade_from_baseline = (
                (r["p90"] - r["baseline_p90"]) / r["baseline_p90"] * 100
                if r["baseline_p90"] > 0 else 0
            )

            findings.append(_make_finding(
                bottleneck_type="multi_tier_bottleneck",
                scope="label",
                scope_name=label,
                concurrency=r["concurrency"],
                metric_name="p90_response_time_ms",
                metric_value=r["p90"],
                baseline_value=r["baseline_p90"],
                severity=_classify_severity_v2(
                    delta_pct=degrade_from_baseline,
                    persistence_ratio=r["persistence_ratio"],
                    classification=r["classification"],
                    scope="label",
                    bottleneck_type="multi_tier_bottleneck",
                ),
                confidence="high" if r["is_persistent"] else "low",
                classification=r["classification"],
                persistence_ratio=r["persistence_ratio"],
                **_onset_fields(r["onset_ts"], r["onset_idx"], test_start_time),
                evidence=(
                    f"Endpoint '{label}' P90 degraded from baseline {r['baseline_p90']:.0f}ms to "
                    f"{r['p90']:.0f}ms (+{degrade_from_baseline:.1f}%) at "
                    f"{r['concurrency']:.0f} concurrent users, breaching SLA ({r['sla']}ms). "
                    f"Persistence: {r['persistence_ratio']:.0%}"
                    f"{' (confirmed bottleneck)' if r['is_persistent'] else ' (transient, recovered)'}. "
                    f"{len(degraded_labels)}/{total_labels} endpoints degraded under load"
                    + (f", {len(known_slow_labels)} known slow." if known_slow_labels else ".")
                ),
                test_run_id=test_run_id,
            ))

    # --- Report known-slow endpoints as informational observations ---
    if known_slow_labels:
        for label, r in list(known_slow_labels.items())[:3]:
            findings.append(_make_finding(
                bottleneck_type="multi_tier_bottleneck",
                scope="label",
                scope_name=label,
                concurrency=r["concurrency"],
                metric_name="p90_response_time_ms",
                metric_value=r["p90"],
                baseline_value=r["sla"],
                severity="info",
                confidence="high",
                classification="known_slow_endpoint",
                persistence_ratio=None,
                **_onset_fields(r["onset_ts"], r["onset_idx"], test_start_time),
                evidence=(
                    f"Endpoint '{label}' has P90={r['p90']:.0f}ms at baseline, "
                    f"already above SLA ({r['sla']}ms). This is an inherently slow endpoint, "
                    f"not a load-induced bottleneck. Consider a per-API SLA override."
                ),
                test_run_id=test_run_id,
            ))

    return findings


# ============================================================================
# COMPARISON MODE
# ============================================================================

async def _run_comparison(
    current_run_id: str,
    baseline_run_id: str,
    current_buckets: pd.DataFrame,
    current_findings: List[Dict],
    cfg: Dict,
    ctx: Context,
) -> Optional[Dict[str, Any]]:
    """Compare current run findings against a baseline run."""
    try:
        baseline_json = ARTIFACTS_PATH / baseline_run_id / "analysis" / "bottleneck_analysis.json"
        if not baseline_json.exists():
            await ctx.warning(f"Comparison: Baseline analysis not found for {baseline_run_id}")
            return {"error": f"No bottleneck analysis found for baseline run {baseline_run_id}"}

        with open(baseline_json, "r") as f:
            baseline_data = json.load(f)

        baseline_summary = baseline_data.get("summary", {})
        baseline_findings = baseline_data.get("findings", [])

        current_threshold = _get_threshold_concurrency(current_findings)
        baseline_threshold = baseline_summary.get("threshold_concurrency")

        comparison = {
            "baseline_run_id": baseline_run_id,
            "current_threshold_concurrency": current_threshold,
            "baseline_threshold_concurrency": baseline_threshold,
            "threshold_shift": None,
            "severity_changes": [],
            "new_bottlenecks": [],
            "resolved_bottlenecks": [],
        }

        # Threshold shift analysis
        if current_threshold is not None and baseline_threshold is not None:
            shift = current_threshold - baseline_threshold
            comparison["threshold_shift"] = {
                "absolute": round(shift, 1),
                "direction": "improved" if shift > 0 else "degraded" if shift < 0 else "unchanged",
                "interpretation": (
                    f"Degradation threshold moved from {baseline_threshold} to "
                    f"{current_threshold} concurrent users "
                    f"({'improvement' if shift > 0 else 'regression' if shift < 0 else 'no change'})"
                ),
            }

        # Compare bottleneck types
        current_types = {f["bottleneck_type"] for f in current_findings}
        baseline_types = {f["bottleneck_type"] for f in baseline_findings}

        comparison["new_bottlenecks"] = list(current_types - baseline_types)
        comparison["resolved_bottlenecks"] = list(baseline_types - current_types)

        return comparison

    except Exception as e:
        return {"error": f"Comparison failed: {e}"}


# ============================================================================
# SUMMARY COMPUTATION
# ============================================================================

def _get_threshold_concurrency(findings: List[Dict]) -> Optional[float]:
    """Extract the lowest concurrency at which any real bottleneck was first detected.

    Excludes transient spikes, known-slow endpoints, and info-level findings
    since these do not represent genuine load-induced degradation.
    """
    concurrency_values = [
        f["concurrency"]
        for f in findings
        if f.get("concurrency") is not None
        and f["bottleneck_type"] in (
            "latency_degradation", "error_rate_increase",
            "throughput_plateau", "infrastructure_saturation",
            "multi_tier_bottleneck",
        )
        and f.get("classification") not in ("transient_spike", "known_slow_endpoint")
        and f.get("severity") != "info"
    ]
    return min(concurrency_values) if concurrency_values else None


def _compute_summary(
    buckets_df: pd.DataFrame,
    findings: List[Dict],
    cfg: Dict,
    has_infra: bool,
) -> Dict[str, Any]:
    """Produce a high-level summary for the tool response and report."""
    threshold_conc = _get_threshold_concurrency(findings)
    max_conc = buckets_df["concurrency"].max()
    max_rps = buckets_df["throughput_rps"].max()

    # Categorise findings
    by_type: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    for f in findings:
        by_type[f["bottleneck_type"]] = by_type.get(f["bottleneck_type"], 0) + 1
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1

    # Best-performing bucket (lowest P90 outside warmup)
    warmup = cfg["warmup_buckets"]
    active = buckets_df.iloc[warmup:]
    if not active.empty:
        best_idx = active["p90"].idxmin()
        best_bucket = active.loc[best_idx]
        optimal_concurrency = float(best_bucket["concurrency"])
        optimal_p90 = float(best_bucket["p90"])
    else:
        optimal_concurrency = None
        optimal_p90 = None

    summary = {
        "total_bottlenecks": len(findings),
        "bottlenecks_by_type": by_type,
        "bottlenecks_by_severity": by_severity,
        "threshold_concurrency": float(round(threshold_conc, 0)) if threshold_conc else None,
        "max_concurrency_tested": round(float(max_conc), 0) if pd.notna(max_conc) else None,
        "max_throughput_rps": round(float(max_rps), 1) if pd.notna(max_rps) else None,
        "optimal_concurrency": round(optimal_concurrency, 0) if optimal_concurrency else None,
        "optimal_p90_ms": round(optimal_p90, 1) if optimal_p90 else None,
        "infrastructure_analyzed": has_infra,
        "headline": _generate_headline(threshold_conc, max_conc, findings, has_infra),
    }

    return summary


def _generate_headline(
    threshold_conc: Optional[float],
    max_conc: float,
    findings: List[Dict],
    has_infra: bool,
) -> str:
    """Generate a one-sentence headline answer to the stakeholder question."""
    if not findings:
        return (
            f"The system handled up to {max_conc:.0f} concurrent users with no "
            f"detected bottlenecks during this test."
        )

    # Check if we only have informational / non-bottleneck findings
    real_bottlenecks = [
        f for f in findings
        if f.get("classification") not in ("transient_spike", "known_slow_endpoint", "capacity_risk")
        and f.get("severity") != "info"
    ]

    if not real_bottlenecks and threshold_conc is None:
        # Only transient spikes, known-slow endpoints, or capacity risks
        info_count = sum(1 for f in findings if f.get("classification") == "known_slow_endpoint")
        transient_count = sum(1 for f in findings if f.get("classification") == "transient_spike")
        capacity_risk_count = sum(1 for f in findings if f.get("classification") == "capacity_risk")
        notes = []
        if transient_count:
            notes.append(f"{transient_count} transient spike(s)")
        if info_count:
            notes.append(f"{info_count} known slow endpoint(s)")
        if capacity_risk_count:
            notes.append(f"{capacity_risk_count} capacity risk(s)")
        note_str = " (" + ", ".join(notes) + " noted)" if notes else ""
        return (
            f"The system handled up to {max_conc:.0f} concurrent users with no "
            f"sustained performance degradation{note_str}."
        )

    if threshold_conc is not None:
        first_finding = min(
            [f for f in findings if f["concurrency"] == threshold_conc],
            key=lambda f: f["concurrency"],
            default=findings[0],
        )
        factor = _bottleneck_type_label(first_finding["bottleneck_type"])
        return (
            f"Performance began degrading at {threshold_conc:.0f} concurrent users "
            f"(max tested: {max_conc:.0f}). Primary limiting factor: {factor}."
        )

    return f"Bottlenecks detected during testing up to {max_conc:.0f} concurrent users."


def _bottleneck_type_label(bt: str) -> str:
    """Human-readable label for a bottleneck type."""
    labels = {
        "latency_degradation": "response time degradation",
        "error_rate_increase": "error rate spike",
        "throughput_plateau": "throughput plateau",
        "infrastructure_saturation": "infrastructure saturation (CPU/Memory)",
        "resource_performance_coupling": "resource-performance coupling",
        "multi_tier_bottleneck": "endpoint-specific degradation",
        "capacity_risk": "capacity risk",
    }
    return labels.get(bt, bt)


# ============================================================================
# OUTPUT GENERATION
# ============================================================================

def _flatten_findings_for_csv(findings: List[Dict]) -> List[Dict]:
    """Flatten the nested ``infrastructure_context`` dict into top-level CSV columns.

    The original ``infrastructure_context`` dict is removed and replaced with
    flat columns: ``infra_correlated``, ``infra_status``, ``infra_metric_mode``,
    ``infra_limits_available``, ``infra_cpu_baseline``, ``infra_cpu_during``,
    ``infra_cpu_delta_pct``, ``infra_memory_baseline``, ``infra_memory_during``,
    ``infra_memory_delta_pct``.
    """
    flattened = []
    for f in findings:
        row = {k: v for k, v in f.items() if k != "infrastructure_context"}
        ic = f.get("infrastructure_context") or {}
        row["infra_correlated"] = ic.get("infra_correlated")
        row["infra_status"] = ic.get("status")
        row["infra_metric_mode"] = ic.get("metric_mode")
        row["infra_limits_available"] = ic.get("limits_available")
        bl = ic.get("baseline_window") or {}
        dw = ic.get("degradation_window") or {}
        row["infra_cpu_baseline"] = bl.get("avg_cpu")
        row["infra_cpu_during"] = dw.get("avg_cpu")
        row["infra_cpu_delta_pct"] = ic.get("cpu_delta_pct")
        row["infra_memory_baseline"] = bl.get("avg_memory")
        row["infra_memory_during"] = dw.get("avg_memory")
        row["infra_memory_delta_pct"] = ic.get("memory_delta_pct")
        flattened.append(row)
    return flattened


async def _write_outputs(
    result: Dict, analysis_path: Path, test_run_id: str, ctx: Context
) -> Dict[str, str]:
    """Write JSON, CSV and Markdown outputs."""
    output_files: Dict[str, str] = {}

    # --- JSON ---
    json_file = analysis_path / "bottleneck_analysis.json"
    await write_json_output(result, json_file)
    output_files["json"] = str(json_file)

    # --- CSV ---
    csv_file = analysis_path / "bottleneck_analysis.csv"
    csv_rows = _flatten_findings_for_csv(result.get("findings", []))
    if csv_rows:
        await write_csv_output(csv_rows, csv_file)
    else:
        # Write header-only CSV
        pd.DataFrame(columns=[
            "test_run_id", "analysis_mode", "bottleneck_type", "scope", "scope_name",
            "concurrency", "metric_name", "metric_value", "baseline_value",
            "delta_abs", "delta_pct", "severity", "confidence",
            "classification", "persistence_ratio", "outlier_filtered",
            "onset_timestamp", "onset_bucket_index", "test_elapsed_seconds",
            "infra_correlated", "infra_status", "infra_metric_mode", "infra_limits_available",
            "infra_cpu_baseline", "infra_cpu_during", "infra_cpu_delta_pct",
            "infra_memory_baseline", "infra_memory_during", "infra_memory_delta_pct",
            "evidence",
        ]).to_csv(csv_file, index=False)
    output_files["csv"] = str(csv_file)

    # --- Markdown ---
    md_file = analysis_path / "bottleneck_analysis.md"
    md_content = format_bottleneck_markdown(result)
    await write_markdown_output(md_content, md_file)
    output_files["markdown"] = str(md_file)

    return output_files


# ============================================================================
# MARKDOWN REPORT FORMATTING
# ============================================================================

def format_bottleneck_markdown(result: Dict) -> str:
    """Generate a comprehensive human-readable bottleneck analysis report."""
    test_run_id = result.get("test_run_id", "Unknown")
    summary = result.get("summary", {})
    findings = result.get("findings", [])
    baseline = result.get("baseline_metrics", {})
    cfg = result.get("configuration", {})
    comparison = result.get("comparison")
    has_infra = result.get("infrastructure_available", False)
    infra_meta = result.get("infrastructure_meta") or {
        "metric_mode": "percentage", "limits_available": True, "cpu_unit": "%", "memory_unit": "%",
    }
    is_raw = infra_meta.get("metric_mode") == "raw"
    cpu_unit = infra_meta.get("cpu_unit", "%")
    mem_unit = infra_meta.get("memory_unit", "%")

    md = []
    md.append(f"# Bottleneck Analysis Report - Run {test_run_id}\n")

    # --- Headline ---
    md.append("## Executive Summary\n")
    headline = summary.get("headline", "Analysis complete.")
    md.append(f"**{headline}**\n")

    md.append("| Metric | Value |")
    md.append("|--------|-------|")
    md.append(f"| Threshold Concurrency | **{summary.get('threshold_concurrency', 'N/A')}** users |")
    md.append(f"| Max Concurrency Tested | {summary.get('max_concurrency_tested', 'N/A')} users |")
    md.append(f"| Optimal Concurrency | {summary.get('optimal_concurrency', 'N/A')} users |")
    md.append(f"| Optimal P90 Latency | {summary.get('optimal_p90_ms', 'N/A')} ms |")
    md.append(f"| Max Throughput | {summary.get('max_throughput_rps', 'N/A')} RPS |")
    md.append(f"| Total Bottlenecks Found | {summary.get('total_bottlenecks', 0)} |")
    md.append(f"| Infrastructure Data | {'Yes' if has_infra else 'No'} |")
    md.append("")

    # --- Bottleneck Breakdown ---
    by_type = summary.get("bottlenecks_by_type", {})
    by_severity = summary.get("bottlenecks_by_severity", {})

    if by_type:
        md.append("### Bottlenecks by Type\n")
        md.append("| Type | Count |")
        md.append("|------|-------|")
        for bt, count in by_type.items():
            md.append(f"| {_bottleneck_type_label(bt)} | {count} |")
        md.append("")

    if by_severity:
        md.append("### Bottlenecks by Severity\n")
        md.append("| Severity | Count |")
        md.append("|----------|-------|")
        for sev in ["critical", "high", "medium", "low", "info"]:
            if sev in by_severity:
                md.append(f"| {sev.title()} | {by_severity[sev]} |")
        md.append("")

    # --- Baseline ---
    md.append("## Baseline Metrics\n")
    md.append(f"Baseline computed from buckets {baseline.get('buckets_used', 'N/A')} "
              f"(after {cfg.get('warmup_buckets', 'N/A')} warmup buckets).\n")
    md.append("| Metric | Baseline Value |")
    md.append("|--------|----------------|")
    md.append(f"| Concurrency | {baseline.get('concurrency', 'N/A'):.0f} users |")
    md.append(f"| P90 Latency | {baseline.get('p90', 'N/A'):.0f} ms |")
    md.append(f"| P95 Latency | {baseline.get('p95', 'N/A'):.0f} ms |")
    md.append(f"| Error Rate | {baseline.get('error_rate', 'N/A'):.2f}% |")
    md.append(f"| Throughput | {baseline.get('throughput_rps', 'N/A'):.1f} RPS |")
    if "avg_cpu" in baseline:
        if is_raw:
            md.append(f"| Avg CPU | {baseline.get('avg_cpu', 'N/A'):.3f} {cpu_unit} |")
            md.append(f"| Avg Memory | {baseline.get('avg_memory', 'N/A'):.2f} {mem_unit} |")
            md.append(f"| Infra Metric Mode | **Raw** (K8s limits not defined) |")
        else:
            md.append(f"| Avg CPU | {baseline.get('avg_cpu', 'N/A'):.1f}% |")
            md.append(f"| Avg Memory | {baseline.get('avg_memory', 'N/A'):.1f}% |")
    md.append("")

    # --- Split findings into bottleneck findings and capacity observations ---
    bottleneck_findings = [f for f in findings if f.get("classification") != "capacity_risk"]
    capacity_findings = [f for f in findings if f.get("classification") == "capacity_risk"]

    # --- Detailed Findings ---
    if bottleneck_findings:
        md.append("## Detailed Findings\n")
        for i, f in enumerate(bottleneck_findings, 1):
            severity_icon = {
                "critical": "🔴", "high": "🟠", "medium": "🟡",
                "low": "🟢", "info": "ℹ️",
            }.get(f["severity"], "⚪")
            md.append(
                f"### {i}. {severity_icon} {_bottleneck_type_label(f['bottleneck_type']).title()} "
                f"({f['severity'].title()})\n"
            )
            classification = f.get('classification', 'bottleneck')
            classification_label = classification.replace('_', ' ').title()
            md.append(f"- **Classification**: {classification_label}")
            md.append(f"- **Scope**: {f['scope']} > {f['scope_name']}")
            md.append(f"- **Concurrency**: {f['concurrency']:.0f} users")
            # Temporal context
            onset_ts = f.get('onset_timestamp')
            bucket_idx = f.get('onset_bucket_index')
            elapsed_s = f.get('test_elapsed_seconds')
            if onset_ts:
                elapsed_str = ""
                if elapsed_s is not None:
                    mins = int(elapsed_s // 60)
                    secs = int(elapsed_s % 60)
                    elapsed_str = f", {mins}m {secs}s into test" if mins else f", {secs}s into test"
                bucket_str = f", bucket #{bucket_idx}" if bucket_idx is not None else ""
                md.append(f"- **Onset**: {onset_ts}{bucket_str}{elapsed_str}")
            md.append(f"- **Metric**: {f['metric_name']} = {f['metric_value']}")
            md.append(f"- **Baseline**: {f['baseline_value']}")
            md.append(f"- **Delta**: {f['delta_abs']:+.2f} ({f['delta_pct']:+.1f}%)")
            md.append(f"- **Confidence**: {f['confidence']}")
            persistence = f.get('persistence_ratio')
            if persistence is not None:
                md.append(f"- **Persistence**: {persistence:.0%} of remaining test")
            if f.get('outlier_filtered'):
                md.append(f"- **Outlier Filtering**: Yes (outlier buckets were skipped during detection)")
            md.append(f"- **Evidence**: {f['evidence']}")

            # Infrastructure context (Phase 2a)
            ic = f.get("infrastructure_context")
            if ic and ic.get("status") not in (None, "no_infra_data"):
                md.append("")
                md.append("  **Infrastructure Context (Phase 2a):**\n")
                status_label = ic["status"].replace("_", " ").title()
                md.append(f"  - Status: {status_label}")

                bl = ic.get("baseline_window") or {}
                dw = ic.get("degradation_window") or {}
                ic_raw = ic.get("metric_mode") == "raw"
                ic_cpu_unit = ic.get("cpu_unit", cpu_unit)
                ic_mem_unit = ic.get("memory_unit", mem_unit)
                if bl.get("avg_cpu") is not None or dw.get("avg_cpu") is not None:
                    md.append("")
                    md.append("  | Metric | Baseline | During Degradation | Delta |")
                    md.append("  |--------|----------|--------------------|-------|")
                    if bl.get("avg_cpu") is not None:
                        cpu_d = ic.get("cpu_delta_pct")
                        cpu_d_str = f"{cpu_d:+.1f}%" if cpu_d is not None else "N/A"
                        corr_tag = " *" if ic.get("cpu_correlated") else ""
                        if ic_raw:
                            md.append(
                                f"  | Avg CPU | {bl.get('avg_cpu', 'N/A'):.3f} {ic_cpu_unit} | "
                                f"{dw.get('avg_cpu', 'N/A'):.3f} {ic_cpu_unit} | {cpu_d_str}{corr_tag} |"
                            )
                        else:
                            md.append(
                                f"  | Avg CPU | {bl.get('avg_cpu', 'N/A')}% | "
                                f"{dw.get('avg_cpu', 'N/A')}% | {cpu_d_str}{corr_tag} |"
                            )
                    if bl.get("avg_memory") is not None:
                        mem_d = ic.get("memory_delta_pct")
                        mem_d_str = f"{mem_d:+.1f}%" if mem_d is not None else "N/A"
                        corr_tag = " *" if ic.get("memory_correlated") else ""
                        if ic_raw:
                            md.append(
                                f"  | Avg Memory | {bl.get('avg_memory', 'N/A'):.2f} {ic_mem_unit} | "
                                f"{dw.get('avg_memory', 'N/A'):.2f} {ic_mem_unit} | {mem_d_str}{corr_tag} |"
                            )
                        else:
                            md.append(
                                f"  | Avg Memory | {bl.get('avg_memory', 'N/A')}% | "
                                f"{dw.get('avg_memory', 'N/A')}% | {mem_d_str}{corr_tag} |"
                            )
                    md.append("")
                    md.append("  \\* = correlated with degradation")
                    if ic_raw:
                        md.append("")
                        md.append(
                            "  > **Note:** K8s resource limits not defined. Values shown in raw units. "
                            "Utilization % and headroom cannot be calculated."
                        )

                root_cause = ic.get("root_cause_indicator")
                if root_cause:
                    md.append(f"\n  > {root_cause}")
            md.append("")
    else:
        md.append("## Findings\n")
        md.append("No bottlenecks were detected during this test run.\n")

    # --- Capacity Observations (Phase 2b) ---
    if capacity_findings:
        md.append("## Capacity Observations\n")
        if is_raw:
            md.append(
                "> The following observations are **not bottlenecks**. Performance remained healthy. "
                "Infrastructure metrics are reported in **raw units** because K8s resource limits "
                "are not defined. Utilization percentage and headroom cannot be calculated.\n"
            )
        else:
            md.append(
                "> The following observations are **not bottlenecks**. Performance remained healthy. "
                "However, infrastructure resources are under stress, which limits headroom for "
                "additional load.\n"
            )
        for j, cf in enumerate(capacity_findings, 1):
            md.append(f"### Capacity Risk {j}: {cf['scope_name']}\n")
            md.append(f"- **Concurrency**: {cf['concurrency']:.0f} users")

            onset_ts = cf.get('onset_timestamp')
            bucket_idx = cf.get('onset_bucket_index')
            elapsed_s = cf.get('test_elapsed_seconds')
            if onset_ts:
                elapsed_str = ""
                if elapsed_s is not None:
                    mins = int(elapsed_s // 60)
                    secs = int(elapsed_s % 60)
                    elapsed_str = f", {mins}m {secs}s into test" if mins else f", {secs}s into test"
                bucket_str = f", bucket #{bucket_idx}" if bucket_idx is not None else ""
                md.append(f"- **Onset**: {onset_ts}{bucket_str}{elapsed_str}")

            md.append(f"- **Metric**: {cf['metric_name']} = {cf['metric_value']}")
            md.append(f"- **Baseline**: {cf['baseline_value']}")
            delta = cf.get('delta_pct', 0)
            md.append(f"- **Delta**: {cf.get('delta_abs', 0):+.2f} ({delta:+.1f}%)")

            ic = cf.get("infrastructure_context") or {}
            if ic.get("headroom_pct") is not None:
                md.append(f"- **Headroom**: {ic['headroom_pct']}%")
            elif ic.get("limits_available") is False:
                md.append("- **Headroom**: N/A (K8s limits not defined)")
            if ic.get("duration_minutes") is not None:
                md.append(f"- **Duration**: {ic['duration_minutes']:.0f} minutes")
            if ic.get("threshold_applied"):
                md.append(f"- **Threshold**: {ic['threshold_applied']}")
            perf = ic.get("performance_during_window", {})
            if perf.get("avg_p90") is not None:
                sla_note = " (within SLA)" if perf.get("within_sla") else ""
                md.append(f"- **Avg P90 during window**: {perf['avg_p90']:.0f}ms{sla_note}")

            md.append(f"- **Evidence**: {cf['evidence']}")
            md.append("")

    # --- Comparison ---
    if comparison and "error" not in comparison:
        md.append("## Comparison with Baseline Run\n")
        md.append(f"**Baseline Run**: {comparison.get('baseline_run_id', 'N/A')}\n")

        shift = comparison.get("threshold_shift")
        if shift:
            direction = shift.get("direction", "unchanged")
            icon = "✅" if direction == "improved" else "❌" if direction == "degraded" else "➡️"
            md.append(f"**Threshold Shift**: {icon} {shift.get('interpretation', 'N/A')}\n")

        new_bns = comparison.get("new_bottlenecks", [])
        resolved = comparison.get("resolved_bottlenecks", [])
        if new_bns:
            md.append("**New Bottleneck Types**: " + ", ".join(new_bns) + "\n")
        if resolved:
            md.append("**Resolved Bottleneck Types**: " + ", ".join(resolved) + "\n")
        md.append("")

    # --- Configuration ---
    md.append("## Analysis Configuration\n")
    md.append("| Parameter | Value |")
    md.append("|-----------|-------|")
    md.append(f"| Bucket Size | {cfg.get('bucket_seconds', 'N/A')}s |")
    md.append(f"| Warmup Buckets | {cfg.get('warmup_buckets', 'N/A')} |")
    md.append(f"| Sustained Buckets | {cfg.get('sustained_buckets', 'N/A')} |")
    md.append(f"| Persistence Ratio | {cfg.get('persistence_ratio', 'N/A')} |")
    md.append(f"| Rolling Window (Outlier) | {cfg.get('rolling_window_buckets', 'N/A')} buckets |")
    md.append(f"| Latency Degradation Threshold | {cfg.get('latency_degrade_pct', 'N/A')}% |")
    md.append(f"| Error Rate Threshold | {cfg.get('error_rate_degrade_abs', 'N/A')}% |")
    sla_id_display = result.get("sla_id") or "file-level default"
    md.append(f"| SLA Profile | {sla_id_display} (per-API thresholds from slas.yaml) |")
    if is_raw:
        md.append(f"| Infra Metric Mode | **Raw** (K8s limits not defined) |")
        md.append(f"| CPU Unit | {cpu_unit} |")
        md.append(f"| Memory Unit | {mem_unit} |")
        md.append(f"| Raw Metric Degrade Threshold | {cfg.get('raw_metric_degrade_pct', 'N/A')}% from baseline |")
    else:
        md.append(f"| CPU High Threshold | {cfg.get('cpu_high_pct', 'N/A')}% |")
        md.append(f"| Memory High Threshold | {cfg.get('memory_high_pct', 'N/A')}% |")
    md.append("")

    md.append(f"---\n*Generated: {result.get('analysis_timestamp', 'N/A')}*")

    return "\n".join(md)
