"""
KPI Summary Section - Helpers for the Summary tab.

Provides data preparation functions for the Summary tab's
KPI cards, SLA table, and pass/fail visualization.
"""

import pandas as pd
from typing import Optional


def prepare_sla_dataframe(perf_analysis: dict) -> Optional[pd.DataFrame]:
    """
    Build a SLA compliance DataFrame from performance analysis data.

    Returns:
        DataFrame with columns: API, Samples, P90, P95, SLA, Compliant
        or None if no data.
    """
    api_analysis = perf_analysis.get("api_analysis", {})
    if not api_analysis:
        return None

    rows = []
    for api_name, stats in api_analysis.items():
        rows.append({
            "API": api_name,
            "Samples": stats.get("samples", 0),
            "Avg (ms)": round(stats.get("avg_response_time", 0), 1),
            "P90 (ms)": round(stats.get("p90_response_time", 0), 1),
            "P95 (ms)": round(stats.get("p95_response_time", 0), 1),
            "P99 (ms)": round(stats.get("p99_response_time", 0), 1),
            "Error Rate %": round(stats.get("error_rate", 0) * 100, 2),
            "SLA (ms)": stats.get("sla_threshold_ms", "N/A"),
            "Compliant": stats.get("sla_compliant", True),
        })

    return pd.DataFrame(rows)


def calculate_summary_metrics(perf_analysis: dict) -> dict:
    """
    Extract summary metrics from performance analysis for KPI cards.

    Returns:
        dict with keys: total_requests, avg_rt, p90, p95, p99,
                        error_rate, throughput, duration_min, success_rate
    """
    overall = perf_analysis.get("overall_stats", {})
    return {
        "total_requests": overall.get("total_samples", 0),
        "avg_rt": overall.get("avg_response_time", 0),
        "p90": overall.get("p90_response_time", 0),
        "p95": overall.get("p95_response_time", 0),
        "p99": overall.get("p99_response_time", 0),
        "error_rate": overall.get("error_rate", 0),
        "success_rate": overall.get("success_rate", 0),
        "throughput": overall.get("avg_throughput", 0),
        "duration_min": overall.get("test_duration", 0) / 60,
    }
