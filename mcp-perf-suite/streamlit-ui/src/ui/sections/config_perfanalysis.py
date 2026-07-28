"""
PerfAnalysis Config Section - Form fields for perfanalysis-mcp configuration.
"""

import streamlit as st

from src.ui.sections.config_common import (
    render_server_section,
    render_general_section,
    render_logging_section,
    render_artifacts_section,
)


def render_perfanalysis_config_form(data: dict, key_prefix: str = "pa") -> dict:
    """Render the full PerfAnalysis config.yaml form."""
    result = {}

    result["server"] = render_server_section(data, key_prefix)
    result["general"] = render_general_section(data, key_prefix)
    result["logging"] = render_logging_section(data, key_prefix)
    result["artifacts"] = render_artifacts_section(data, key_prefix)

    # Performance Analysis settings
    st.markdown("##### Performance Analysis Settings")
    pa = data.get("perf_analysis", {})

    col1, col2, col3 = st.columns(3)
    with col1:
        load_tool = st.selectbox("Load Tool", options=["blazemeter", "jmeter", "gatling"], index=["blazemeter", "jmeter", "gatling"].index(pa.get("load_tool", "blazemeter")), key=f"{key_prefix}_load_tool")
        statistical_confidence = st.number_input("Statistical Confidence", value=pa.get("statistical_confidence", 0.95), min_value=0.80, max_value=0.99, step=0.01, format="%.2f", key=f"{key_prefix}_stat_conf")
        correlation_threshold = st.number_input("Correlation Threshold", value=pa.get("correlation_threshold", 0.3), min_value=0.1, max_value=0.9, step=0.05, format="%.2f", key=f"{key_prefix}_corr_thresh")

    with col2:
        apm_tool = st.selectbox("APM Tool", options=["datadog", "newrelic", "appdynamics", "dynatrace"], index=["datadog", "newrelic", "appdynamics", "dynatrace"].index(pa.get("apm_tool", "datadog")), key=f"{key_prefix}_apm_tool")
        min_samples = st.number_input("Min Samples Required", value=pa.get("min_samples_required", 100), min_value=10, max_value=1000, step=10, key=f"{key_prefix}_min_samples")
        corr_window = st.number_input("Correlation Granularity (sec)", value=pa.get("correlation_granularity_window", 60), min_value=10, max_value=300, step=10, key=f"{key_prefix}_corr_window")

    with col3:
        st.markdown("**Anomaly Sensitivity**")
        sens_low = st.number_input("Low (std devs)", value=pa.get("anomaly_sensitivity", {}).get("low", 3.0), min_value=1.0, max_value=5.0, step=0.5, format="%.1f", key=f"{key_prefix}_sens_low")
        sens_med = st.number_input("Medium (std devs)", value=pa.get("anomaly_sensitivity", {}).get("medium", 2.5), min_value=1.0, max_value=5.0, step=0.5, format="%.1f", key=f"{key_prefix}_sens_med")
        sens_high = st.number_input("High (std devs)", value=pa.get("anomaly_sensitivity", {}).get("high", 2.0), min_value=1.0, max_value=5.0, step=0.5, format="%.1f", key=f"{key_prefix}_sens_high")

    # Resource thresholds
    st.markdown("##### Resource Thresholds")
    rt = pa.get("resource_thresholds", {})
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**CPU Thresholds (%)**")
        cpu_high = st.number_input("CPU High", value=rt.get("cpu", {}).get("high", 80), min_value=50, max_value=100, key=f"{key_prefix}_cpu_high")
        cpu_low = st.number_input("CPU Low", value=rt.get("cpu", {}).get("low", 20), min_value=0, max_value=50, key=f"{key_prefix}_cpu_low")
    with col2:
        st.markdown("**Memory Thresholds (%)**")
        mem_high = st.number_input("Memory High", value=rt.get("memory", {}).get("high", 85), min_value=50, max_value=100, key=f"{key_prefix}_mem_high")
        mem_low = st.number_input("Memory Low", value=rt.get("memory", {}).get("low", 15), min_value=0, max_value=50, key=f"{key_prefix}_mem_low")

    # Bottleneck analysis
    st.markdown("##### Bottleneck Analysis Settings")
    ba = pa.get("bottleneck_analysis", {})
    col1, col2, col3 = st.columns(3)
    with col1:
        bucket_seconds = st.number_input("Bucket Size (sec)", value=ba.get("bucket_seconds", 60), min_value=10, max_value=300, step=10, key=f"{key_prefix}_bucket_sec")
        warmup_buckets = st.number_input("Warmup Buckets", value=ba.get("warmup_buckets", 2), min_value=0, max_value=10, key=f"{key_prefix}_warmup")
        sustained_buckets = st.number_input("Sustained Buckets", value=ba.get("sustained_buckets", 2), min_value=1, max_value=10, key=f"{key_prefix}_sustained")
    with col2:
        persistence_ratio = st.number_input("Persistence Ratio", value=ba.get("persistence_ratio", 0.6), min_value=0.1, max_value=1.0, step=0.1, format="%.1f", key=f"{key_prefix}_persistence")
        rolling_window = st.number_input("Rolling Window Buckets", value=ba.get("rolling_window_buckets", 3), min_value=1, max_value=10, key=f"{key_prefix}_rolling")
        latency_degrade = st.number_input("Latency Degrade (%)", value=ba.get("latency_degrade_pct", 25.0), min_value=5.0, max_value=100.0, step=5.0, format="%.1f", key=f"{key_prefix}_lat_degrade")
    with col3:
        error_degrade = st.number_input("Error Rate Degrade (%)", value=ba.get("error_rate_degrade_abs", 5.0), min_value=1.0, max_value=50.0, step=1.0, format="%.1f", key=f"{key_prefix}_err_degrade")
        throughput_plateau = st.number_input("Throughput Plateau (%)", value=ba.get("throughput_plateau_pct", 5.0), min_value=1.0, max_value=50.0, step=1.0, format="%.1f", key=f"{key_prefix}_tp_plateau")
        raw_degrade = st.number_input("Raw Metric Degrade (%)", value=ba.get("raw_metric_degrade_pct", 50.0), min_value=10.0, max_value=200.0, step=10.0, format="%.1f", key=f"{key_prefix}_raw_degrade")

    # OpenAI settings
    st.markdown("##### OpenAI Integration")
    oai = data.get("openai", {})
    col1, col2, col3 = st.columns(3)
    with col1:
        model = st.text_input("Model", value=oai.get("model", "gpt-4o-mini"), key=f"{key_prefix}_oai_model")
    with col2:
        max_tokens = st.number_input("Max Tokens", value=oai.get("max_tokens", 2000), min_value=100, max_value=8000, step=100, key=f"{key_prefix}_oai_tokens")
    with col3:
        temperature = st.number_input("Temperature", value=oai.get("temperature", 0.3), min_value=0.0, max_value=2.0, step=0.1, format="%.1f", key=f"{key_prefix}_oai_temp")

    # Output settings
    st.markdown("##### Output Settings")
    out = data.get("output", {})
    col1, col2 = st.columns(2)
    with col1:
        default_format = st.selectbox("Default Format", options=["json", "csv", "markdown"], index=["json", "csv", "markdown"].index(out.get("default_format", "json")), key=f"{key_prefix}_out_format")
        precision = st.number_input("Precision (decimals)", value=out.get("precision", 4), min_value=0, max_value=8, key=f"{key_prefix}_precision")
    with col2:
        create_md = st.toggle("Create Markdown", value=out.get("create_markdown", True), key=f"{key_prefix}_create_md")
        create_csv = st.toggle("Create CSV", value=out.get("create_csv", True), key=f"{key_prefix}_create_csv")

    result["perf_analysis"] = {
        "load_tool": load_tool,
        "apm_tool": apm_tool,
        "statistical_confidence": statistical_confidence,
        "anomaly_sensitivity": {"low": sens_low, "medium": sens_med, "high": sens_high},
        "correlation_threshold": correlation_threshold,
        "min_samples_required": min_samples,
        "correlation_granularity_window": corr_window,
        "resource_thresholds": {"cpu": {"high": cpu_high, "low": cpu_low}, "memory": {"high": mem_high, "low": mem_low}},
        "bottleneck_analysis": {
            "bucket_seconds": bucket_seconds, "warmup_buckets": warmup_buckets, "sustained_buckets": sustained_buckets,
            "persistence_ratio": persistence_ratio, "rolling_window_buckets": rolling_window, "latency_degrade_pct": latency_degrade,
            "error_rate_degrade_abs": error_degrade, "throughput_plateau_pct": throughput_plateau, "raw_metric_degrade_pct": raw_degrade,
        },
    }
    result["openai"] = {"model": model, "max_tokens": max_tokens, "temperature": temperature}
    result["output"] = {"default_format": default_format, "precision": precision, "create_markdown": create_md, "create_csv": create_csv}

    return result
