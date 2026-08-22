"""
JMeter Config Section - Form fields for jmeter-mcp configuration.
"""

import streamlit as st

from src.ui.sections.config_common import (
    render_server_section,
    render_general_section,
    render_logging_section,
    render_artifacts_section,
    _show_path_status,
)


def render_jmeter_config_form(data: dict, key_prefix: str = "jm") -> dict:
    """Render the full JMeter config.yaml form."""
    result = {}

    result["server"] = render_server_section(data, key_prefix)
    result["general"] = render_general_section(data, key_prefix)
    result["logging"] = render_logging_section(data, key_prefix)

    # JMeter log analysis settings
    st.markdown("##### JMeter Log Analysis")
    jm_log = data.get("jmeter_log", {})
    col1, col2 = st.columns(2)
    with col1:
        max_desc = st.number_input("Max Description Length", value=jm_log.get("max_description_length", 200), min_value=50, max_value=1000, step=50, key=f"{key_prefix}_max_desc")
        max_req = st.number_input("Max Request Length", value=jm_log.get("max_request_length", 500), min_value=100, max_value=2000, step=100, key=f"{key_prefix}_max_req")
    with col2:
        max_resp = st.number_input("Max Response Length", value=jm_log.get("max_response_length", 500), min_value=100, max_value=2000, step=100, key=f"{key_prefix}_max_resp")
        max_stack = st.number_input("Max Stack Trace Lines", value=jm_log.get("max_stack_trace_lines", 50), min_value=10, max_value=200, step=10, key=f"{key_prefix}_max_stack")

    error_levels_text = st.text_input("Error Levels (comma-separated)", value=", ".join(jm_log.get("error_levels", ["ERROR", "FATAL"])), key=f"{key_prefix}_error_levels")

    result["jmeter_log"] = {
        "max_description_length": max_desc,
        "max_request_length": max_req,
        "max_response_length": max_resp,
        "max_stack_trace_lines": max_stack,
        "error_levels": [l.strip() for l in error_levels_text.split(",") if l.strip()],
    }

    result["artifacts"] = render_artifacts_section(data, key_prefix)

    # JMeter paths
    st.markdown("##### JMeter Installation")
    jm = data.get("jmeter", {})
    col1, col2 = st.columns(2)
    with col1:
        jmeter_home = st.text_input("JMeter Home", value=jm.get("jmeter_home", ""), key=f"{key_prefix}_home", help="Root directory of Apache JMeter installation")
        _show_path_status(jmeter_home)
        jmeter_bin = st.text_input("JMeter Bin Path", value=jm.get("jmeter_bin_path", ""), key=f"{key_prefix}_bin", help="Path to JMeter bin/ directory")
        _show_path_status(jmeter_bin)
    with col2:
        start_exe = st.text_input("Start Command", value=jm.get("jmeter_start_exe", "jmeter.bat"), key=f"{key_prefix}_start_exe", help="jmeter.bat (Windows) or jmeter (Linux/Mac)")
        stop_exe = st.text_input("Stop Command", value=jm.get("jmeter_stop_exe", "stoptest.cmd"), key=f"{key_prefix}_stop_exe", help="stoptest.cmd (Windows) or stoptest.sh (Linux/Mac)")

    result["jmeter"] = {
        "jmeter_home": jmeter_home,
        "jmeter_bin_path": jmeter_bin,
        "jmeter_start_exe": start_exe,
        "jmeter_stop_exe": stop_exe,
    }

    # Test specs
    st.markdown("##### Test Specs Paths")
    specs = data.get("test_specs", {})
    col1, col2, col3 = st.columns(3)
    with col1:
        web_flows = st.text_input("Web Flows Path", value=specs.get("web_flows_path", "test-specs\\web-flows"), key=f"{key_prefix}_web_flows")
    with col2:
        api_flows = st.text_input("API Flows Path", value=specs.get("api_flows_path", "test-specs\\api-flows"), key=f"{key_prefix}_api_flows")
    with col3:
        examples = st.text_input("Examples Path", value=specs.get("examples_path", "test-specs\\examples"), key=f"{key_prefix}_examples")

    result["test_specs"] = {
        "web_flows_path": web_flows,
        "api_flows_path": api_flows,
        "examples_path": examples,
    }

    # Browser settings
    st.markdown("##### Browser Settings")
    browser = data.get("browser", {})
    col1, col2, col3 = st.columns(3)
    with col1:
        browser_type = st.selectbox("Browser Type", options=["chrome", "firefox", "edge"], index=["chrome", "firefox", "edge"].index(browser.get("browser_type", "chrome")), key=f"{key_prefix}_browser_type")
        headless = st.toggle("Headless Mode", value=browser.get("headless_mode", True), key=f"{key_prefix}_headless")
    with col2:
        window_size = st.text_input("Window Size", value=browser.get("window_size", "1920,1080"), key=f"{key_prefix}_window_size")
        implicit_wait = st.number_input("Implicit Wait (sec)", value=browser.get("implicit_wait", 10), min_value=1, max_value=60, key=f"{key_prefix}_implicit_wait")
    with col3:
        page_load_timeout = st.number_input("Page Load Timeout (sec)", value=browser.get("page_load_timeout", 60), min_value=10, max_value=300, key=f"{key_prefix}_page_timeout")
        think_time = st.number_input("Think Time (ms)", value=browser.get("think_time", 5000), min_value=500, max_value=30000, step=500, key=f"{key_prefix}_think_time")

    result["browser"] = {
        "browser_type": browser_type,
        "headless_mode": headless,
        "window_size": window_size,
        "implicit_wait": implicit_wait,
        "page_load_timeout": page_load_timeout,
        "think_time": think_time,
    }

    # Network capture
    st.markdown("##### Network Capture")
    nc = data.get("network_capture", {})
    col1, col2 = st.columns(2)
    with col1:
        capture_api = st.toggle("Capture API Requests", value=nc.get("capture_api_requests", True), key=f"{key_prefix}_cap_api")
        capture_static = st.toggle("Capture Static Assets", value=nc.get("capture_static_assets", False), key=f"{key_prefix}_cap_static")
        capture_fonts = st.toggle("Capture Fonts", value=nc.get("capture_fonts", False), key=f"{key_prefix}_cap_fonts")
        capture_video = st.toggle("Capture Video Streams", value=nc.get("capture_video_streams", False), key=f"{key_prefix}_cap_video")
    with col2:
        capture_3p = st.toggle("Capture Third Party", value=nc.get("capture_third_party", True), key=f"{key_prefix}_cap_3p")
        capture_cookies = st.toggle("Capture Cookies", value=nc.get("capture_cookies", True), key=f"{key_prefix}_cap_cookies")
        capture_domain = st.text_input("Capture Domain", value=nc.get("capture_domain", ""), key=f"{key_prefix}_cap_domain", help="Leave empty to capture all domains")

    # Exclude domains as text area
    exclude_domains = nc.get("exclude_domains", [])
    exclude_text = st.text_area(
        "Exclude Domains (one per line)",
        value="\n".join(exclude_domains) if isinstance(exclude_domains, list) else "",
        height=150,
        key=f"{key_prefix}_exclude_domains",
        help="Domains to exclude from network capture (APM, analytics, ads, etc.)",
    )

    result["network_capture"] = {
        "capture_api_requests": capture_api,
        "capture_static_assets": capture_static,
        "capture_fonts": capture_fonts,
        "capture_video_streams": capture_video,
        "capture_third_party": capture_3p,
        "capture_cookies": capture_cookies,
        "capture_domain": capture_domain,
        "exclude_domains": [d.strip() for d in exclude_text.split("\n") if d.strip()],
    }

    return result
