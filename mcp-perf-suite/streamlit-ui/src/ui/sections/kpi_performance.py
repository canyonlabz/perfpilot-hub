"""
KPI Performance Section - Helpers for the Performance tab.

Provides data preparation functions for JTL time-series aggregation.
"""

import pandas as pd
import numpy as np
from typing import Optional


def aggregate_jtl_by_time(
    jtl_df: pd.DataFrame,
    bucket_seconds: int = 30,
) -> Optional[pd.DataFrame]:
    """
    Aggregate JTL data into time buckets for charting.

    Returns:
        DataFrame with columns: bucket, avg_rt, p90_rt, p95_rt,
        throughput_rps, error_rate_pct, max_vusers
    """
    if jtl_df is None or jtl_df.empty:
        return None

    required = {"timeStamp", "elapsed", "success", "allThreads"}
    if not required.issubset(jtl_df.columns):
        return None

    df = jtl_df.copy()
    df["timestamp"] = pd.to_datetime(df["timeStamp"], unit="ms")
    df["bucket"] = df["timestamp"].dt.floor(f"{bucket_seconds}s")

    if df["success"].dtype == object:
        df["is_error"] = df["success"].str.lower() != "true"
    else:
        df["is_error"] = ~df["success"].astype(bool)

    agg = df.groupby("bucket").agg(
        avg_rt=("elapsed", "mean"),
        p90_rt=("elapsed", lambda x: np.percentile(x, 90)),
        p95_rt=("elapsed", lambda x: np.percentile(x, 95)),
        total=("timeStamp", "count"),
        errors=("is_error", "sum"),
        max_vusers=("allThreads", "max"),
    ).reset_index()

    agg["throughput_rps"] = agg["total"] / bucket_seconds
    agg["error_rate_pct"] = (agg["errors"] / agg["total"]) * 100

    return agg


def aggregate_jtl_by_endpoint(jtl_df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    Aggregate JTL data per endpoint for bar charts.

    Returns:
        DataFrame with columns: label, samples, avg_rt, p90_rt, p95_rt, error_rate_pct
    """
    if jtl_df is None or jtl_df.empty or "label" not in jtl_df.columns:
        return None

    df = jtl_df.copy()
    if df["success"].dtype == object:
        df["is_error"] = df["success"].str.lower() != "true"
    else:
        df["is_error"] = ~df["success"].astype(bool)

    agg = df.groupby("label").agg(
        samples=("elapsed", "count"),
        avg_rt=("elapsed", "mean"),
        p90_rt=("elapsed", lambda x: np.percentile(x, 90)),
        p95_rt=("elapsed", lambda x: np.percentile(x, 95)),
        errors=("is_error", "sum"),
    ).reset_index()

    agg["error_rate_pct"] = (agg["errors"] / agg["samples"]) * 100
    return agg
