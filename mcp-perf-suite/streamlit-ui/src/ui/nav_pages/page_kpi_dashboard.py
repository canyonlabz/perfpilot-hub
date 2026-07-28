"""
KPI Dashboard Page - Interactive performance metrics viewer.

Displays test run results with interactive Altair charts, KPI cards,
and analysis data across 5 tabs: Summary, Performance, Infrastructure,
Bottlenecks, and Log Analysis.
"""

import json
import streamlit as st
import pandas as pd
from pathlib import Path

from src.ui.page_header import render_page_header
from src.ui.page_utils import render_page_title
from src.ui.page_styles import inject_kpi_dashboard_styles
from src.utils.config import load_config
from src.utils.path_utils import get_artifacts_path
from src.utils.state import KPI_SELECTED_RUN_ID
from src.services.artifact_loader import (
    load_json,
    load_csv,
    check_data_availability,
)
from src.services.chart_builder import (
    build_response_time_chart,
    build_throughput_chart,
    build_error_rate_chart,
    build_top_slowest_apis_chart,
    build_pass_fail_donut,
    build_infra_cpu_chart,
    build_infra_memory_chart,
    build_bottleneck_severity_chart,
    build_bottleneck_type_chart,
    build_log_severity_chart,
    build_log_category_chart,
)
from src.ui.components.export_helpers import render_csv_download, render_json_download


def _discover_test_runs(artifacts_path: Path) -> list[str]:
    """Scan the artifacts directory and return a sorted list of run IDs."""
    if not artifacts_path.exists():
        return []
    runs = [
        d.name for d in artifacts_path.iterdir()
        if d.is_dir() and d.name not in ("comparisons", "_ARCHIVE")
    ]
    runs.sort(reverse=True)
    return runs


def render_ui():
    render_page_header()
    render_page_title("KPI Dashboard", "Interactive performance metrics and analysis viewer")
    inject_kpi_dashboard_styles()

    config = load_config()
    artifacts_path = get_artifacts_path(config)
    available_runs = _discover_test_runs(artifacts_path)

    if not available_runs:
        st.warning(f"No test runs found in `{artifacts_path}`. Run a performance test first.")
        return

    # ── Run Selector ──
    col_sel, col_info = st.columns([0.35, 0.65])

    with col_sel:
        selected_run = st.selectbox(
            "Select Performance Test Run",
            options=available_runs, index=0, key="run_selector",
            help="Select a test run ID to view its KPI metrics and analysis",
        )
        st.session_state[KPI_SELECTED_RUN_ID] = selected_run

    if not selected_run:
        return

    availability = check_data_availability(selected_run, config)

    with col_info:
        avail_badges = []
        for source, info in availability.items():
            if info.get("available", False):
                avail_badges.append(source.title())
        if avail_badges:
            st.markdown(f"**Available data:** {' | '.join(avail_badges)}")

    st.markdown("---")

    # ── Dashboard Tabs ──
    tab_summary, tab_perf, tab_infra, tab_bottlenecks, tab_logs = st.tabs([
        "Summary", "Performance", "Infrastructure", "Bottlenecks", "Log Analysis",
    ])

    run_path = artifacts_path / selected_run

    with tab_summary:
        _render_summary_tab(selected_run, config, availability)
    with tab_perf:
        _render_performance_tab(selected_run, config, availability, run_path)
    with tab_infra:
        _render_infrastructure_tab(selected_run, config, availability, run_path)
    with tab_bottlenecks:
        _render_bottlenecks_tab(selected_run, config, availability)
    with tab_logs:
        _render_log_analysis_tab(selected_run, config, availability)


# ---------------------------------------------------------------------------
# Tab: Summary
# ---------------------------------------------------------------------------

def _render_summary_tab(run_id: str, config: dict, availability: dict):
    """KPI cards, SLA compliance table, pass/fail donut."""
    perf_data = load_json(run_id, "analysis/performance_analysis.json", config)

    if not perf_data:
        st.info("Performance analysis data not available. Run `analyze_test_results` first.")
        return

    overall = perf_data.get("overall_stats", {})

    # ── KPI Card Row 1 ──
    cols = st.columns(4, border=True)
    cols[0].metric("Total Requests", f"{overall.get('total_samples', 0):,}")
    cols[1].metric("Avg Response Time", f"{overall.get('avg_response_time', 0):.1f} ms")
    cols[2].metric("P90 Response Time", f"{overall.get('p90_response_time', 0):.1f} ms")
    cols[3].metric("Error Rate", f"{overall.get('error_rate', 0) * 100:.2f}%")

    # ── KPI Card Row 2 ──
    cols2 = st.columns(4, border=True)
    cols2[0].metric("P95 Response Time", f"{overall.get('p95_response_time', 0):.1f} ms")
    cols2[1].metric("P99 Response Time", f"{overall.get('p99_response_time', 0):.1f} ms")
    cols2[2].metric("Avg Throughput", f"{overall.get('avg_throughput', 0):.1f} req/s")
    duration_min = overall.get("test_duration", 0) / 60
    cols2[3].metric("Test Duration", f"{duration_min:.1f} min")

    st.markdown("---")

    # ── SLA Compliance + Donut ──
    col_sla, col_donut = st.columns([0.65, 0.35])

    with col_sla:
        st.markdown("#### SLA Compliance by API")
        api_analysis = perf_data.get("api_analysis", {})
        if api_analysis:
            rows = []
            for api_name, stats in api_analysis.items():
                rows.append({
                    "API": api_name,
                    "Samples": stats.get("samples", 0),
                    "P90 (ms)": f"{stats.get('p90_response_time', 0):.0f}",
                    "P95 (ms)": f"{stats.get('p95_response_time', 0):.0f}",
                    "SLA (ms)": stats.get("sla_threshold_ms", "N/A"),
                    "Compliant": "PASS" if stats.get("sla_compliant", True) else "FAIL",
                })
            sla_df = pd.DataFrame(rows)

            # Color-code the Compliant column
            def _color_sla(val):
                if val == "PASS":
                    return "color: #2ecc40; font-weight: bold"
                elif val == "FAIL":
                    return "color: #ff4136; font-weight: bold"
                return ""

            styled = sla_df.style.map(_color_sla, subset=["Compliant"])
            st.dataframe(styled, width="stretch", hide_index=True,
                         height=min(40 * len(sla_df) + 40, 500))

            render_csv_download(sla_df, f"sla_compliance_{run_id}.csv", "Export SLA Table", f"dl_sla_{run_id}")
        else:
            st.info("No per-API analysis data available.")

    with col_donut:
        st.markdown("#### Pass / Fail")
        donut = build_pass_fail_donut(perf_data)
        if donut:
            st.altair_chart(donut, width="stretch")
        else:
            st.info("Not enough data for compliance chart.")


# ---------------------------------------------------------------------------
# Tab: Performance
# ---------------------------------------------------------------------------

def _render_performance_tab(run_id: str, config: dict, availability: dict, run_path: Path):
    """Interactive time-series charts from JTL data."""
    if not availability.get("blazemeter", {}).get("test_results"):
        st.info("BlazeMeter test results (JTL) not available. Process artifacts first.")
        return

    jtl_df = load_csv(run_id, "blazemeter/test-results.csv", config)
    if jtl_df is None or jtl_df.empty:
        st.warning("Could not load test-results.csv or file is empty.")
        return

    st.caption(f"Loaded {len(jtl_df):,} records | {jtl_df['label'].nunique()} unique endpoints")

    # ── Response Time vs VUsers ──
    chart_rt = build_response_time_chart(jtl_df)
    if chart_rt:
        st.altair_chart(chart_rt, width="stretch")

    # ── Throughput + Error Rate side by side ──
    col_tp, col_err = st.columns(2)

    with col_tp:
        chart_tp = build_throughput_chart(jtl_df)
        if chart_tp:
            st.altair_chart(chart_tp, width="stretch")

    with col_err:
        chart_err = build_error_rate_chart(jtl_df)
        if chart_err:
            st.altair_chart(chart_err, width="stretch")

    # ── Top Slowest APIs ──
    perf_data = load_json(run_id, "analysis/performance_analysis.json", config)
    if perf_data:
        st.markdown("---")
        chart_slow = build_top_slowest_apis_chart(perf_data)
        if chart_slow:
            st.altair_chart(chart_slow, width="stretch")

    # ── Export ──
    st.markdown("---")
    col_exp1, col_exp2, col_spacer = st.columns([0.2, 0.2, 0.6])
    with col_exp1:
        render_csv_download(jtl_df, f"test_results_{run_id}.csv", "Export JTL CSV", f"dl_jtl_{run_id}")
    with col_exp2:
        agg_df = load_csv(run_id, "blazemeter/aggregate_performance_report.csv", config)
        if agg_df is not None:
            render_csv_download(agg_df, f"aggregate_report_{run_id}.csv", "Export Aggregate", f"dl_agg_{run_id}")


# ---------------------------------------------------------------------------
# Tab: Infrastructure
# ---------------------------------------------------------------------------

def _render_infrastructure_tab(run_id: str, config: dict, availability: dict, run_path: Path):
    """CPU/Memory utilization charts from Datadog CSVs."""
    if not availability.get("datadog", {}).get("available"):
        st.info("Datadog infrastructure metrics not available. Run metrics collection first.")
        return

    datadog_dir = run_path / "datadog"

    # ── Infrastructure Analysis Summary ──
    infra_data = load_json(run_id, "analysis/infrastructure_analysis.json", config)
    if infra_data:
        insights = infra_data.get("resource_insights", {})
        high_util = insights.get("high_utilization", [])
        low_util = insights.get("low_utilization", [])

        col1, col2, col3 = st.columns(3, border=True)
        col1.metric("High Utilization Warnings", len(high_util))
        col2.metric("Low Utilization Warnings", len(low_util))
        col3.metric("Right-Sized Resources", len(insights.get("right_sized", [])))

        if high_util:
            st.markdown("##### High Utilization Resources")
            for item in high_util:
                st.warning(f"**{item.get('resource', 'Unknown')}** - {item.get('type', '')} "
                           f"peaked at {item.get('peak_utilization', 0):.1f}% "
                           f"(threshold: {item.get('threshold', 0)}%) - {item.get('recommendation', '')}")

    st.markdown("---")

    # ── Unit Toggle Switches ──
    # Build CPU chart first with defaults to detect environment type
    cpu_result = build_infra_cpu_chart(datadog_dir, cpu_unit="millicores")
    mem_result = build_infra_memory_chart(datadog_dir, mem_unit="mb")

    is_k8s = (cpu_result and cpu_result["is_k8s"]) or (mem_result and mem_result["is_k8s"])

    toggle_col1, toggle_col2, toggle_spacer = st.columns([0.25, 0.25, 0.5])

    with toggle_col1:
        if is_k8s:
            cpu_unit = st.radio(
                "CPU Unit", ["Millicores", "Cores"],
                horizontal=True, key="infra_cpu_unit",
            )
        else:
            cpu_unit = st.radio(
                "CPU Unit", ["—"],
                horizontal=True, key="infra_cpu_unit", disabled=True,
                label_visibility="collapsed",
            )
            cpu_unit = None

    with toggle_col2:
        if is_k8s:
            mem_unit = st.radio(
                "Memory Unit", ["MB", "GB"],
                horizontal=True, key="infra_mem_unit",
            )
        else:
            mem_unit = st.radio(
                "Memory Unit", ["—"],
                horizontal=True, key="infra_mem_unit", disabled=True,
                label_visibility="collapsed",
            )
            mem_unit = None

    # Rebuild charts with selected units if they changed from defaults
    if is_k8s:
        selected_cpu = cpu_unit.lower() if cpu_unit else "millicores"
        selected_mem = mem_unit.lower() if mem_unit else "mb"

        if selected_cpu != "millicores":
            cpu_result = build_infra_cpu_chart(datadog_dir, cpu_unit=selected_cpu)
        if selected_mem != "mb":
            mem_result = build_infra_memory_chart(datadog_dir, mem_unit=selected_mem)

    # ── CPU Chart ──
    if cpu_result:
        st.altair_chart(cpu_result["chart"], use_container_width=True)
    else:
        st.info("No CPU metric data found in Datadog CSVs.")

    # ── Memory Chart ──
    if mem_result:
        st.altair_chart(mem_result["chart"], use_container_width=True)
    else:
        st.info("No Memory metric data found in Datadog CSVs.")

    # ── Raw Metric Files ──
    metric_files = list(datadog_dir.glob("*.csv"))
    if metric_files:
        with st.expander(f"Raw metric files ({len(metric_files)})"):
            for f in metric_files:
                st.markdown(f"- `{f.name}`")
                df = pd.read_csv(f)
                st.dataframe(df.head(50), use_container_width=True, height=200)
                render_csv_download(df, f.name, f"Export {f.name}", f"dl_infra_{f.stem}")


# ---------------------------------------------------------------------------
# Tab: Bottlenecks
# ---------------------------------------------------------------------------

def _render_bottlenecks_tab(run_id: str, config: dict, availability: dict):
    """Bottleneck analysis findings and visualizations."""
    bottleneck_data = load_json(run_id, "analysis/bottleneck_analysis.json", config)

    if not bottleneck_data:
        st.info("Bottleneck analysis not available. Run `identify_bottlenecks` first.")
        return

    summary = bottleneck_data.get("summary", {})

    # ── Headline ──
    headline = summary.get("headline", "")
    if headline:
        st.markdown(f"##### {headline}")

    # ── KPI Cards ──
    cols = st.columns(4, border=True)
    cols[0].metric("Threshold Concurrency",
                   summary.get("threshold_concurrency") or "None detected")
    cols[1].metric("Max Concurrency Tested",
                   f"{summary.get('max_concurrency_tested', 'N/A')}")
    cols[2].metric("Total Bottlenecks",
                   summary.get("total_bottlenecks", 0))
    cols[3].metric("Max Throughput",
                   f"{summary.get('max_throughput_rps', 0):.1f} req/s")

    st.markdown("---")

    # ── Charts side by side ──
    col_sev, col_type = st.columns(2)

    with col_sev:
        sev_chart = build_bottleneck_severity_chart(bottleneck_data)
        if sev_chart:
            st.altair_chart(sev_chart, width="stretch")
        else:
            st.info("No severity data to chart.")

    with col_type:
        type_chart = build_bottleneck_type_chart(bottleneck_data)
        if type_chart:
            st.altair_chart(type_chart, width="stretch")
        else:
            st.info("No type data to chart.")

    # ── Findings Table ──
    findings = bottleneck_data.get("findings", [])
    if findings:
        st.markdown("#### Detailed Findings")

        rows = []
        for f in findings:
            rows.append({
                "Severity": f.get("severity", "unknown"),
                "Type": f.get("bottleneck_type", "").replace("_", " ").title(),
                "Scope": f.get("scope_name", f.get("scope", "")),
                "Concurrency": f.get("concurrency", ""),
                "Metric": f.get("metric_name", ""),
                "Value": f"{f.get('metric_value', 0):.1f}",
                "Baseline": f"{f.get('baseline_value', 0):.1f}",
                "Delta %": f"{f.get('delta_pct', 0):.1f}%",
                "Evidence": f.get("evidence", ""),
            })

        findings_df = pd.DataFrame(rows)

        # Color severity
        def _color_severity(val):
            colors = {
                "critical": "color: #ff4136; font-weight: bold",
                "high": "color: #ff851b; font-weight: bold",
                "medium": "color: #ffdc00; font-weight: bold",
                "low": "color: #7fdbff",
                "info": "color: #aaaaaa",
            }
            return colors.get(val.lower(), "")

        styled = findings_df.style.map(_color_severity, subset=["Severity"])
        st.dataframe(styled, width="stretch", hide_index=True,
                     height=min(40 * len(findings_df) + 40, 500))

        render_csv_download(findings_df, f"bottleneck_findings_{run_id}.csv",
                            "Export Findings", f"dl_bn_{run_id}")

    # ── Baseline Metrics ──
    baseline = bottleneck_data.get("baseline_metrics", {})
    if baseline:
        with st.expander("Baseline Metrics"):
            bl_cols = st.columns(4, border=True)
            bl_cols[0].metric("Baseline Concurrency", f"{baseline.get('concurrency', 'N/A')}")
            bl_cols[1].metric("Baseline P90", f"{baseline.get('p90', 0):.1f} ms")
            bl_cols[2].metric("Baseline Throughput", f"{baseline.get('throughput_rps', 0):.1f} req/s")
            bl_cols[3].metric("Baseline Error Rate", f"{baseline.get('error_rate', 0):.2f}%")


# ---------------------------------------------------------------------------
# Tab: Log Analysis
# ---------------------------------------------------------------------------

def _render_log_analysis_tab(run_id: str, config: dict, availability: dict):
    """Log analysis findings from BlazeMeter and Datadog logs."""
    # Try blazemeter_log_analysis.json first, then log_analysis.json
    log_data = load_json(run_id, "analysis/blazemeter_log_analysis.json", config)
    if not log_data:
        log_data = load_json(run_id, "analysis/log_analysis.json", config)

    if not log_data:
        st.info("Log analysis not available. Run `analyze_logs` or `analyze_jmeter_log` first.")
        return

    summary = log_data.get("summary", {})

    # ── KPI Cards ──
    cols = st.columns(4, border=True)
    cols[0].metric("Total Unique Issues", summary.get("total_unique_issues", 0))
    cols[1].metric("Total Occurrences", summary.get("total_occurrences", 0))

    by_severity = summary.get("issues_by_severity", {})
    cols[2].metric("Critical Issues", by_severity.get("Critical", 0))
    cols[3].metric("High Severity", by_severity.get("High", 0))

    # ── Log Files Analyzed ──
    log_files = log_data.get("log_files_analyzed", [])
    if log_files:
        with st.expander(f"Log Files Analyzed ({len(log_files)})"):
            for lf in log_files:
                error_lines = lf.get("error_lines", 0)
                total_lines = lf.get("total_lines", 0)
                st.markdown(
                    f"- **{lf.get('filename', 'Unknown')}**: "
                    f"{total_lines:,} lines, {error_lines:,} error lines"
                )

    # ── Charts ──
    if summary.get("total_unique_issues", 0) > 0:
        st.markdown("---")
        col_sev, col_cat = st.columns(2)

        with col_sev:
            sev_chart = build_log_severity_chart(log_data)
            if sev_chart:
                st.altair_chart(sev_chart, width="stretch")

        with col_cat:
            cat_chart = build_log_category_chart(log_data)
            if cat_chart:
                st.altair_chart(cat_chart, width="stretch")

        # ── Issues Table ──
        issues = log_data.get("issues", [])
        if issues:
            st.markdown("#### Issue Details")
            rows = []
            for issue in issues:
                rows.append({
                    "ID": issue.get("error_id", ""),
                    "Severity": issue.get("severity", "Unknown"),
                    "Category": issue.get("error_category", ""),
                    "API Endpoint": issue.get("api_endpoint", ""),
                    "Response Code": issue.get("response_code", ""),
                    "Count": issue.get("error_count", 0),
                })

            issues_df = pd.DataFrame(rows)

            def _color_severity(val):
                colors = {
                    "Critical": "color: #ff4136; font-weight: bold",
                    "High": "color: #ff851b; font-weight: bold",
                    "Medium": "color: #ffdc00; font-weight: bold",
                    "Low": "color: #7fdbff",
                }
                return colors.get(val, "")

            styled = issues_df.style.map(_color_severity, subset=["Severity"])
            st.dataframe(styled, width="stretch", hide_index=True)

            render_csv_download(issues_df, f"log_issues_{run_id}.csv",
                                "Export Issues", f"dl_log_{run_id}")

        # ── Top Affected APIs ──
        top_apis = summary.get("top_affected_apis", [])
        if top_apis:
            st.markdown("#### Top Affected APIs")
            api_rows = []
            for api in top_apis:
                api_rows.append({
                    "API Endpoint": api.get("api_endpoint", ""),
                    "Total Errors": api.get("total_errors", 0),
                    "Error Categories": ", ".join(api.get("error_categories", [])),
                })
            st.dataframe(pd.DataFrame(api_rows), width="stretch", hide_index=True)

        # ── Error Timeline ──
        timeline = summary.get("error_timeline", {})
        if timeline.get("first_error"):
            st.markdown("#### Error Timeline")
            tl_cols = st.columns(2, border=True)
            tl_cols[0].metric("First Error", timeline.get("first_error", "N/A"))
            tl_cols[1].metric("Last Error", timeline.get("last_error", "N/A"))

    else:
        st.success("No issues found in the analyzed log files.")

    # ── JTL Correlation ──
    jtl_corr = summary.get("jtl_correlation", {})
    if jtl_corr:
        with st.expander("JTL Correlation"):
            corr_cols = st.columns(3, border=True)
            corr_cols[0].metric("Log Errors Matched to JTL",
                                jtl_corr.get("log_errors_matched_to_jtl", 0))
            corr_cols[1].metric("JTL-Only Failures",
                                jtl_corr.get("jtl_only_failures", 0))
            corr_cols[2].metric("Unmatched Log Errors",
                                jtl_corr.get("unmatched_log_errors", 0))


render_ui()
