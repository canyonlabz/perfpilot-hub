import sys
import json
import os
import re
import urllib.parse
from typing import Callable, Optional, Dict, Any, List
from fastmcp import Context

from utils.config import load_config, load_jmeter_config

# === Global configuration ===
CONFIG = load_config()
ARTIFACTS_PATH = CONFIG["artifacts"]["artifacts_path"]
JMETER_CONFIG = load_jmeter_config()

# === Domain Exclusion (APM, analytics, etc.) ===
from services.correlations.utils import init_exclude_domains, is_excluded_url
init_exclude_domains(CONFIG)

# Import helper functions for correlation & extractor support
from services.helpers.extractor_helpers import (
    _load_correlation_naming,
    _load_correlation_spec,
    _normalize_url,
    _build_extractor_map,
    _find_extractors_for_url,
    _infer_field_to_check,
    _create_extractor_element,
    _should_add_oauth_extractor,
    _get_extractors_for_entry
)

# Import helper functions for variable substitution support
from services.helpers.substitution_helpers import (
    _build_variable_name_map,
    _build_substitution_map,
    _find_substitutions_for_url,
    _substitute_in_url,
    _substitute_in_body,
    _substitute_in_headers,
    _apply_substitutions_to_entry,
    _apply_pkce_substitutions_to_entry,
    _substitute_static_headers_in_entry
)

# Import helper functions for orphan variable handling (Phase D)
from services.helpers.orphan_helpers import (
    _extract_orphan_values,
    _extract_static_header_config,
    _get_orphan_udv_variables,
    _merge_udv_config,
    _build_orphan_substitution_map,
    _apply_orphan_substitutions_to_entry
)

# Import helper functions for hostname parameterization (obs-1)
from services.helpers.hostname_helpers import (
    _extract_unique_hostnames,
    _categorize_hostname,
    _build_hostname_variable_map,
    _substitute_hostname_in_entry
)

# Import necessary modules for JMeter JMX generation
import xml.etree.ElementTree as ET  # Needed for creating empty hashTree elements
from services.jmx.plan import (
    create_test_plan,
    create_thread_group
)
from services.jmx.controllers import (
    create_simple_controller,
    create_transaction_controller,
    # …add more factory functions as you build them…
)
from services.jmx.samplers import (
    create_http_sampler_get,
    create_http_sampler_with_body,
    append_sampler,
    create_flow_control_action
)
from services.jmx.config_elements import (
    create_cookie_manager,
    create_user_defined_variables,
    create_csv_data_set_config
)
from services.jmx.listeners import (
    create_view_results_tree,
    create_aggregate_report
)
from services.jmx.post_processor import (
    create_json_extractor,
    create_regex_extractor
)
from services.jmx.pre_processor import (
    create_pkce_preprocessor,
    create_entra_state_preprocessor,
    create_entra_wsfed_cookie_preprocessor,
    create_uuid_preprocessor,
    append_preprocessor
)
from services.correlations.extractors import detect_entra_flow, detect_pkce_flow
from utils.file_utils import save_jmx_file


# ============================================================
# Helper Functions - EntraID PreProcessor Insertion
# ============================================================

def _entra_is_authorize_url(url: str, entra_flow: Dict[str, Any]) -> bool:
    """Check if URL matches the EntraID authorize request for state/nonce attachment."""
    authorize_url = entra_flow.get("authorize_request_url", "")
    if not authorize_url or not url:
        return False
    if "/oauth2/v2.0/authorize" in url or "/oauth2/authorize" in url:
        ms_host = entra_flow.get("microsoft_host", "login.microsoftonline.com")
        if ms_host in url:
            return True
    return False


def _entra_is_wsfed_url(url: str, entra_flow: Dict[str, Any]) -> bool:
    """Check if URL matches the WS-Fed form submission step for cookie attachment."""
    wsfed_url = entra_flow.get("wsfed_request_url", "")
    if not wsfed_url:
        return False
    if "/wsfed" in url or "/kmsi" in url:
        ms_host = entra_flow.get("microsoft_host", "login.microsoftonline.com")
        if ms_host in url:
            return True
    return False


def _insert_entra_preprocessors(
    entra_flow: Dict[str, Any],
    original_url: str,
    sampler_hash_tree,
    state_inserted: bool,
    nonce_inserted: bool,
    client_request_id_inserted: bool,
    cookies_inserted: bool,
) -> None:
    """
    Insert EntraID pre-processors on the appropriate samplers.

    Attaches to the authorize request:
    - MSAL oauth_state generator
    - oauth_nonce UUID generator
    - client_request_id UUID generator

    Attaches to the WS-Fed form submission:
    - ESTSWCTXFLOWTOKEN + AADSSO cookie injector
    """
    if not state_inserted and _entra_is_authorize_url(original_url, entra_flow):
        state_pp = create_entra_state_preprocessor()
        append_preprocessor(sampler_hash_tree, state_pp)

        nonce_pp = create_uuid_preprocessor(
            variable_name="oauth_nonce",
            testname="Generate oauth_nonce"
        )
        append_preprocessor(sampler_hash_tree, nonce_pp)

        crid_pp = create_uuid_preprocessor(
            variable_name="client_request_id",
            testname="Generate client_request_id"
        )
        append_preprocessor(sampler_hash_tree, crid_pp)

    if not cookies_inserted and _entra_is_wsfed_url(original_url, entra_flow):
        ms_host = entra_flow.get("microsoft_host", "login.microsoftonline.com")
        cookie_pp = create_entra_wsfed_cookie_preprocessor(domain=ms_host)
        append_preprocessor(sampler_hash_tree, cookie_pp)


# ============================================================
# Helper Functions - Step/Test Case Naming
# ============================================================

def _transform_step_to_testcase(step_name: str) -> str:
    """
    Transform "Step X: Description" to "TC0X_Description" format.
    
    This naming convention is important for alphanumeric sorting in
    JMeter and BlazeMeter reports (TC01, TC02, ... TC09, TC10).
    
    Args:
        step_name: Original step name like "Step 1: Navigate to..."
        
    Returns:
        Transformed name like "TC01_Navigate to..."
        
    Examples:
        "Step 1: Navigate to homepage" -> "TC01_Navigate to homepage"
        "Step 10: Validate results" -> "TC10_Validate results"
        "Custom Name" -> "Custom Name" (unchanged if not matching pattern)
    """
    # Match "Step N: Description" pattern
    match = re.match(r'^Step\s+(\d+):\s*(.*)$', step_name, re.IGNORECASE)
    if match:
        step_num = int(match.group(1))
        description = match.group(2)
        # Format as TC##_ with leading zero for single digits
        return f"TC{step_num:02d}_{description}"
    
    # Return unchanged if doesn't match expected pattern
    return step_name


# ============================================================
# Builder functions for CSV Data Set Config and Environment CSV creation
# ============================================================

def _create_environment_csv(
    test_run_id: str,
    hostname_var_map: Dict[str, str],
    csv_subfolder: str,
    csv_filename: str
) -> str:
    """
    Create the environment.csv file with hostname variables.
    
    Creates the CSV file with:
    - Header row: variable names
    - Data row: actual hostname values
    
    Args:
        test_run_id: The test run identifier
        hostname_var_map: Mapping from hostname to variable name
        csv_subfolder: Subfolder name (e.g., "testdata_csv")
        csv_filename: CSV filename (e.g., "environment.csv")
        
    Returns:
        The relative path to the CSV file (for JMX reference)
    """
    # Build output directory path
    output_dir = os.path.join(ARTIFACTS_PATH, test_run_id, "jmeter", csv_subfolder)
    os.makedirs(output_dir, exist_ok=True)
    
    csv_path = os.path.join(output_dir, csv_filename)
    
    # Sort by variable name for consistent ordering
    sorted_items = sorted(hostname_var_map.items(), key=lambda x: x[1])
    
    # Build header and data rows
    headers = [var_name for _, var_name in sorted_items]
    values = [hostname for hostname, _ in sorted_items]
    
    # Write CSV file
    with open(csv_path, "w", encoding="utf-8", newline='') as f:
        f.write(",".join(headers) + "\n")
        f.write(",".join(values) + "\n")
    
    # Return relative path for JMX reference (use forward slashes for cross-platform JMeter compatibility)
    return f"{csv_subfolder}/{csv_filename}"


def _create_environment_csv_data_set(
    csv_relative_path: str,
    variable_names: List[str]
) -> ET.Element:
    """
    Create a CSV Data Set Config element for environment variables.
    
    Args:
        csv_relative_path: Relative path to the CSV file
        variable_names: List of variable names (in order)
        
    Returns:
        The CSVDataSet XML element
    """
    csv_data = ET.Element("CSVDataSet", attrib={
        "guiclass": "TestBeanGUI",
        "testclass": "CSVDataSet",
        "testname": "CSV Data Set Config (Environment)",
        "enabled": "true"
    })
    ET.SubElement(csv_data, "stringProp", attrib={"name": "delimiter"}).text = ","
    ET.SubElement(csv_data, "stringProp", attrib={"name": "fileEncoding"}).text = ""
    ET.SubElement(csv_data, "stringProp", attrib={"name": "filename"}).text = csv_relative_path
    ET.SubElement(csv_data, "boolProp", attrib={"name": "ignoreFirstLine"}).text = "true"
    ET.SubElement(csv_data, "boolProp", attrib={"name": "quotedData"}).text = "false"
    ET.SubElement(csv_data, "boolProp", attrib={"name": "recycle"}).text = "true"
    ET.SubElement(csv_data, "stringProp", attrib={"name": "shareMode"}).text = "shareMode.all"
    ET.SubElement(csv_data, "boolProp", attrib={"name": "stopThread"}).text = "false"
    ET.SubElement(csv_data, "stringProp", attrib={"name": "variableNames"}).text = ",".join(variable_names)
    
    return csv_data


# ============================================================
# Main JMeter JMX Generator function
# ============================================================

async def generate_jmeter_jmx(test_run_id: str, json_path: str, ctx: Context) -> Dict[str, Any]:
    """
    Generate a JMeter JMX script from a network capture JSON file.

    This version is MCP-friendly and matches the JMeter MCP tool signature:

      - test_run_id: used to resolve output path: artifacts/<test_run_id>/jmeter/
      - json_path: full path to the network capture JSON
      - ctx: FastMCP Context object for logging (ctx.info / ctx.error, etc.)

    Returns:
      {
        "status": "success" | "error",
        "jmx_path": "<full path to .jmx file (if success)>",
        "message": "<human readable status>",
      }
    """
    # === Network Capture File ===
    # Check if the provided JSON file exists and is valid.
    if not json_path or not os.path.isfile(json_path):
        raise ValueError(f"No JSON file provided or file does not exist for given: '{json_path}'")
    if not json_path.endswith('.json'):
        raise ValueError(f"File '{json_path}' is not a valid JSON file.")
    
    with open(json_path, "r", encoding="utf-8") as f:
        network_data = json.load(f)
    
    # === PKCE Flow Detection (Sprint C) ===
    pkce_flow = None
    pkce_subs_applied = 0
    pkce_preprocessor_inserted = False
    flat_entries = []
    global_idx = 0
    for step_num, (step_label, value) in enumerate(network_data.items()):
        entries_list = value if isinstance(value, list) else [value]
        for entry in entries_list:
            flat_entries.append((global_idx, step_num, step_label, entry))
            global_idx += 1
    pkce_result = detect_pkce_flow(flat_entries)
    if pkce_result and pkce_result.get("detected"):
        pkce_flow = pkce_result
        method = pkce_result.get("code_challenge_method", "S256")
        await ctx.info(f"🔐 PKCE flow detected (method: {method})")

    # === EntraID Flow Detection ===
    entra_flow = None
    entra_state_inserted = False
    entra_nonce_inserted = False
    entra_client_request_id_inserted = False
    entra_cookies_inserted = False
    entra_result = detect_entra_flow(flat_entries)
    if entra_result and entra_result.get("detected"):
        entra_flow = entra_result
        ms_host = entra_result.get("microsoft_host", "N/A")
        await ctx.info(f"🔐 EntraID flow detected (host: {ms_host})")

    # === Load Correlation Data (if available) ===
    # correlation_naming.json: JMeter variable names and extractor configurations
    # correlation_spec.json: Actual values and usage locations for substitution
    correlation_naming = _load_correlation_naming(test_run_id)
    correlation_spec = _load_correlation_spec(test_run_id)
    
    extractor_map = {}
    substitution_map = {}
    orphan_udv_vars = {}  # Orphan variables for User Defined Variables (Phase D)
    orphan_substitution_map = []  # Orphan value substitutions for HTTP requests (obs-3)
    static_header_udv_vars = {}  # Static header variables for UDV
    static_header_sub_map = {}  # Static header name -> variable name for substitution
    extracted_variables = set()  # Track variables that already have extractors (obs-2)
    
    # Load extractor placement config
    extractor_config = JMETER_CONFIG.get("extractor_placement", {})
    extractor_mode = extractor_config.get("mode", "all_occurrences")
    
    if correlation_naming:
        extractor_map = _build_extractor_map(correlation_naming)
        var_count = len(correlation_naming.get("variables", []))
        orphan_count = len(correlation_naming.get("orphan_variables", []))
        await ctx.info(f"✅ Loaded correlation naming: {var_count} variables, {orphan_count} orphans")
        
        # Build substitution map and extract orphan values if we have correlation_spec
        if correlation_spec:
            variable_name_map = _build_variable_name_map(correlation_naming)
            substitution_map = _build_substitution_map(correlation_spec, variable_name_map)
            total_subs = sum(len(subs) for subs in substitution_map.values())
            await ctx.info(f"✅ Built substitution map: {total_subs} substitutions across {len(substitution_map)} URLs")
            
            # Extract orphan values and build UDV variables (Phase D)
            orphan_values = _extract_orphan_values(correlation_spec)
            orphan_udv_vars = _get_orphan_udv_variables(correlation_naming, orphan_values)
            if orphan_udv_vars:
                await ctx.info(f"✅ Extracted {len(orphan_udv_vars)} orphan variable(s) for User Defined Variables")
            
            # Build orphan substitution map for replacing values in requests (obs-3)
            orphan_substitution_map = _build_orphan_substitution_map(correlation_naming, orphan_values)
            if orphan_substitution_map:
                await ctx.info(f"✅ Built orphan substitution map: {len(orphan_substitution_map)} value(s) to replace")
            
            # Extract static header config for UDV and header substitution
            static_header_udv_vars, static_header_sub_map = _extract_static_header_config(
                correlation_spec, correlation_naming
            )
            if static_header_udv_vars:
                await ctx.info(f"✅ Detected {len(static_header_udv_vars)} static header(s) for UDV parameterization")
        else:
            await ctx.info("ℹ️ No correlation_spec.json found - skipping variable substitution")
    else:
        await ctx.info("ℹ️ No correlation_naming.json found - generating JMX without extractors or substitutions")
    
    # === HTTP/2 Pseudo-Header Exclusion ===
    # JMeter uses HTTP/1.1 by default; HTTP/2 pseudo-headers cause errors on non-HTTP/2 backends
    http2_config = JMETER_CONFIG.get("http2_headers", {})
    exclude_http2_pseudo_headers = http2_config.get("exclude_pseudo_headers", True)
    
    # === Hostname Parameterization (obs-1) ===
    # Extract unique hostnames and create environment CSV for parameterization
    hostname_param_config = JMETER_CONFIG.get("hostname_parameterization", {})
    hostname_var_map = {}
    env_csv_relative_path = ""
    # Get patterns config early - needed for both hostname parameterization and OAuth extractor filtering
    patterns_config = hostname_param_config.get("default_patterns", {})
    
    if hostname_param_config.get("enabled", False):
        # Extract unique hostnames from network data
        unique_hostnames = _extract_unique_hostnames(network_data)
        
        if unique_hostnames:
            # Build hostname -> variable name mapping
            hostname_var_map = _build_hostname_variable_map(unique_hostnames, patterns_config)
            
            # Create environment CSV file
            csv_subfolder = hostname_param_config.get("csv_subfolder", "testdata_csv")
            csv_filename = hostname_param_config.get("csv_filename", "environment.csv")
            
            env_csv_relative_path = _create_environment_csv(
                test_run_id, 
                hostname_var_map, 
                csv_subfolder, 
                csv_filename
            )
            
            await ctx.info(f"✅ Hostname parameterization: {len(unique_hostnames)} unique hostname(s) found")
            await ctx.info(f"✅ Created environment CSV: {env_csv_relative_path}")
    
    # ============================================================
    # === JMeter JMX File Configurations ===
    # ============================================================

    # === Create JMeter Test Plan ===
    # Create the root Test Plan and its hashTree.
    test_plan, test_plan_hash_tree = create_test_plan()

    # === Add Optional Test Plan Elements ===
    # Add Cookie Manager if enabled.
    cookie_mgr_cfg = JMETER_CONFIG.get("cookie_manager", {"enabled": False})
    if cookie_mgr_cfg.get("enabled", False):
        cookie_manager_elem = create_cookie_manager()
        test_plan_hash_tree.append(cookie_manager_elem)
        test_plan_hash_tree.append(ET.Element("hashTree"))
    
    # Add User Defined Variables (merge with orphan variables from Phase D)
    udv_cfg = JMETER_CONFIG.get("user_defined_variables", {"enabled": False})
    
    # Merge orphan variables into UDV config if any were extracted
    if orphan_udv_vars:
        udv_cfg = _merge_udv_config(udv_cfg, orphan_udv_vars)
    
    # Merge static header variables into UDV config
    if static_header_udv_vars:
        udv_cfg = _merge_udv_config(udv_cfg, static_header_udv_vars)
    
    udv_elem = create_user_defined_variables(udv_cfg)
    if udv_elem is not None:
        test_plan_hash_tree.append(udv_elem)
        test_plan_hash_tree.append(ET.Element("hashTree"))
    
    # Add CSV Data Set Config if enabled.
    csv_cfg = JMETER_CONFIG.get("csv_dataset_config", {"enabled": False})
    csv_elem = create_csv_data_set_config(csv_cfg)
    if csv_elem is not None:
        test_plan_hash_tree.append(csv_elem)
        test_plan_hash_tree.append(ET.Element("hashTree"))
    
    # Add Environment CSV Data Set Config (from hostname parameterization)
    if hostname_var_map and env_csv_relative_path:
        # Get sorted variable names for the CSV Data Set
        sorted_var_names = [var for _, var in sorted(hostname_var_map.items(), key=lambda x: x[1])]
        env_csv_elem = _create_environment_csv_data_set(env_csv_relative_path, sorted_var_names)
        test_plan_hash_tree.append(env_csv_elem)
        test_plan_hash_tree.append(ET.Element("hashTree"))

    # === Create Thread Group ===
    # Create a single Thread Group using defaults from jmeter_config.
    tg_config = JMETER_CONFIG.get("thread_group", {})
    num_threads = str(tg_config.get("num_threads", 1))
    ramp_time = str(tg_config.get("ramp_time", 1))
    loops = str(tg_config.get("loops", 1))
    thread_group, thread_group_hash_tree = create_thread_group(
        num_threads=num_threads, ramp_time=ramp_time, loops=loops
    )
    # Append the thread group and its hashTree to the Test Plan.
    test_plan_hash_tree.append(thread_group)
    test_plan_hash_tree.append(thread_group_hash_tree)
    
    # === Create HTTP Request Samplers (possibly grouped in Controllers) ===
    ctrl_cfg = JMETER_CONFIG.get("controller_config", {})
    use_controllers = ctrl_cfg.get("enabled", False)

    # Track correlation additions for logging
    extractors_added = 0
    substitutions_applied = 0
    orphan_subs_applied = 0  # Track orphan UDV substitutions (obs-3)
    static_header_subs_applied = 0  # Track static header substitutions
    excluded_entries = 0
    hostname_subs_applied = 0  # Track hostname substitutions
    think_time_added = 0  # Track Think Time additions
    
    if use_controllers:
        ctrl_type = ctrl_cfg.get("controller_type", "simple").lower()
        # pick factory based on type
        factory = {
            "simple": create_simple_controller,
            "transaction": create_transaction_controller,
            # Add more controller types as needed
        }.get(ctrl_type, create_simple_controller)
        
        # === Think Time (Test Action) Configuration ===
        # Add Flow Control Action at the end of each step to simulate realistic user behavior
        test_action_cfg = JMETER_CONFIG.get("test_action_config", {})
        test_action_enabled = test_action_cfg.get("enabled", False)
        test_action_name = test_action_cfg.get("test_action_name", "Think Time")
        test_action_type = test_action_cfg.get("action", "pause")
        # Duration uses UDV variable reference by default for parameterization
        test_action_duration = "${thinkTime}"
        
        # === Sampler Naming Configuration ===
        # Apply naming convention: TC##_S##_METHOD /path for report-friendly sorting
        sampler_naming_cfg = JMETER_CONFIG.get("sampler_naming", {})
        sampler_naming_enabled = sampler_naming_cfg.get("enabled", True)
        
        # Get total number of steps to identify the last step
        step_items = list(network_data.items())
        total_steps = len(step_items)

        for step_index, (step_name, entries) in enumerate(step_items):
            # Transform "Step X: Description" to "TC0X_Description" for report sorting
            testcase_name = _transform_step_to_testcase(step_name)
            # create the Controller node + its hashTree
            ctrl_elem, ctrl_hash = factory(testname=testcase_name)
            thread_group_hash_tree.append(ctrl_elem)
            thread_group_hash_tree.append(ctrl_hash)
            
            # Track sampler counter for naming convention (resets per step)
            step_num = step_index + 1  # 1-based step number for TC##
            sampler_counter = 0  # Reset for each step

            # Now append each sampler under this controller
            for entry in entries:
                # Filter out excluded domains (APM, analytics, advertising, etc.)
                entry_url = entry.get("url", "")
                if is_excluded_url(entry_url):
                    excluded_entries += 1
                    continue
                
                # Increment sampler counter for naming (only for non-excluded entries)
                sampler_counter += 1
                
                # Generate sampler name prefix if naming is enabled
                sampler_prefix = f"TC{step_num:02d}_S{sampler_counter:02d}" if sampler_naming_enabled else None
                
                # Apply variable substitutions before creating sampler (Phase C)
                original_url = entry.get("url", "")
                if substitution_map:
                    _apply_substitutions_to_entry(entry, substitution_map)
                    if entry.get("url", "") != original_url:
                        substitutions_applied += 1
                
                # Apply orphan UDV substitutions (obs-3)
                if orphan_substitution_map:
                    if _apply_orphan_substitutions_to_entry(entry, orphan_substitution_map):
                        orphan_subs_applied += 1
                
                # Apply static header substitutions
                if static_header_sub_map:
                    if _substitute_static_headers_in_entry(entry, static_header_sub_map):
                        static_header_subs_applied += 1
                
                # Apply PKCE variable substitutions (Sprint C)
                if pkce_flow:
                    if _apply_pkce_substitutions_to_entry(entry, pkce_flow):
                        pkce_subs_applied += 1
                
                # Apply hostname parameterization (obs-1)
                if hostname_var_map:
                    if _substitute_hostname_in_entry(entry, hostname_var_map):
                        hostname_subs_applied += 1
                
                method = entry.get("method", "GET").upper()
                if method == "GET":
                    sampler, header_manager = create_http_sampler_get(
                        entry, hostname_var_map, exclude_http2_pseudo_headers,
                        testname_prefix=sampler_prefix
                    )
                else:
                    sampler, header_manager = create_http_sampler_with_body(
                        entry, hostname_var_map, exclude_http2_pseudo_headers,
                        testname_prefix=sampler_prefix
                    )
                
                # Get extractors for this URL (correlation support - Phase B)
                entry_for_extractor = {"url": original_url} if original_url else entry
                extractors = _get_extractors_for_entry(
                    entry_for_extractor, 
                    extractor_map,
                    extracted_variables=extracted_variables,
                    extractor_config=extractor_config,
                    hostname_patterns_config=patterns_config
                )
                extractors_added += len(extractors)
                
                sampler_hash_tree = append_sampler(ctrl_hash, sampler, header_manager, extractors=extractors)
                
                # Insert PKCE PreProcessor on the authorize request (Sprint C)
                if pkce_flow and not pkce_preprocessor_inserted:
                    cc_val = pkce_flow.get("code_challenge_value", "")
                    if cc_val and cc_val in original_url:
                        pkce_element = create_pkce_preprocessor()
                        append_preprocessor(sampler_hash_tree, pkce_element)
                        pkce_preprocessor_inserted = True

                # Insert EntraID PreProcessors
                if entra_flow:
                    _insert_entra_preprocessors(
                        entra_flow, original_url, sampler_hash_tree,
                        entra_state_inserted, entra_nonce_inserted,
                        entra_client_request_id_inserted, entra_cookies_inserted
                    )
                    # Update flags from mutable container
                    if not entra_state_inserted and _entra_is_authorize_url(original_url, entra_flow):
                        entra_state_inserted = True
                        entra_nonce_inserted = True
                        entra_client_request_id_inserted = True
                    if not entra_cookies_inserted and _entra_is_wsfed_url(original_url, entra_flow):
                        entra_cookies_inserted = True

            # === Add Think Time (Test Action) at end of each step (except last) ===
            # This simulates realistic user think time between steps, matching browser automation behavior
            is_last_step = (step_index == total_steps - 1)
            if test_action_enabled and not is_last_step:
                think_time_element = create_flow_control_action(
                    action_type=test_action_type,
                    testname=test_action_name,
                    duration=test_action_duration
                )
                ctrl_hash.append(think_time_element)
                ctrl_hash.append(ET.Element("hashTree"))
                think_time_added += 1
    else:
        # === Create HTTP Request Samplers (Flat Mode - No Controllers) ===
        # Iterate through the network capture entries.
        # Assume network_data is a dictionary where keys are URLs and values are entry dicts.
        
        # === Sampler Naming Configuration for Flat Mode ===
        # Apply naming convention: TC01_S##_METHOD /path (all samplers under single TC01)
        sampler_naming_cfg = JMETER_CONFIG.get("sampler_naming", {})
        sampler_naming_enabled = sampler_naming_cfg.get("enabled", True)
        sampler_counter = 0  # Single counter for flat mode
        
        for url, entry in network_data.items():
            # Ensure each entry has its "url" field.
            if "url" not in entry:
                entry["url"] = url
            
            # Filter out excluded domains (APM, analytics, advertising, etc.)
            entry_url = entry.get("url", "")
            if is_excluded_url(entry_url):
                excluded_entries += 1
                continue
            
            # Increment sampler counter for naming (only for non-excluded entries)
            sampler_counter += 1
            
            # Generate sampler name prefix if naming is enabled (TC01 for flat mode)
            sampler_prefix = f"TC01_S{sampler_counter:02d}" if sampler_naming_enabled else None
            
            # Apply variable substitutions before creating sampler (Phase C)
            original_url = entry.get("url", "")
            if substitution_map:
                _apply_substitutions_to_entry(entry, substitution_map)
                if entry.get("url", "") != original_url:
                    substitutions_applied += 1
            
            # Apply orphan UDV substitutions (obs-3)
            if orphan_substitution_map:
                if _apply_orphan_substitutions_to_entry(entry, orphan_substitution_map):
                    orphan_subs_applied += 1
            
            # Apply static header substitutions
            if static_header_sub_map:
                if _substitute_static_headers_in_entry(entry, static_header_sub_map):
                    static_header_subs_applied += 1
            
            # Apply PKCE variable substitutions (Sprint C)
            if pkce_flow:
                if _apply_pkce_substitutions_to_entry(entry, pkce_flow):
                    pkce_subs_applied += 1
            
            # Apply hostname parameterization (obs-1)
            if hostname_var_map:
                if _substitute_hostname_in_entry(entry, hostname_var_map):
                    hostname_subs_applied += 1
            
            method = entry.get("method", "GET").upper()
            if method == "GET":
                sampler, header_manager = create_http_sampler_get(
                    entry, hostname_var_map, exclude_http2_pseudo_headers,
                    testname_prefix=sampler_prefix
                )
            else:
                sampler, header_manager = create_http_sampler_with_body(
                    entry, hostname_var_map, exclude_http2_pseudo_headers,
                    testname_prefix=sampler_prefix
                )
            
            # Get extractors for this URL (correlation support - Phase B)
            entry_for_extractor = {"url": original_url} if original_url else entry
            extractors = _get_extractors_for_entry(
                entry_for_extractor, 
                extractor_map,
                extracted_variables=extracted_variables,
                extractor_config=extractor_config,
                hostname_patterns_config=patterns_config
            )
            extractors_added += len(extractors)
            
            sampler_hash_tree = append_sampler(thread_group_hash_tree, sampler, header_manager, extractors=extractors)
            
            # Insert PKCE PreProcessor on the authorize request (Sprint C)
            if pkce_flow and not pkce_preprocessor_inserted:
                cc_val = pkce_flow.get("code_challenge_value", "")
                if cc_val and cc_val in original_url:
                    pkce_element = create_pkce_preprocessor()
                    append_preprocessor(sampler_hash_tree, pkce_element)
                    pkce_preprocessor_inserted = True

            # Insert EntraID PreProcessors
            if entra_flow:
                _insert_entra_preprocessors(
                    entra_flow, original_url, sampler_hash_tree,
                    entra_state_inserted, entra_nonce_inserted,
                    entra_client_request_id_inserted, entra_cookies_inserted
                )
                if not entra_state_inserted and _entra_is_authorize_url(original_url, entra_flow):
                    entra_state_inserted = True
                    entra_nonce_inserted = True
                    entra_client_request_id_inserted = True
                if not entra_cookies_inserted and _entra_is_wsfed_url(original_url, entra_flow):
                    entra_cookies_inserted = True

    # Build generation summary for return value
    generation_summary = {
        "excluded_entries": excluded_entries,
        "extractors_added": extractors_added,
        "extractor_mode": extractor_mode,
        "unique_variables_extracted": len(extracted_variables),
        "substitutions_applied": substitutions_applied,
        "orphan_subs_applied": orphan_subs_applied,
        "static_header_subs_applied": static_header_subs_applied,
        "hostname_subs_applied": hostname_subs_applied,
        "think_time_added": think_time_added,
        "pkce_flow_detected": pkce_preprocessor_inserted,
        "pkce_subs_applied": pkce_subs_applied,
        "entra_id_detected": entra_state_inserted,
        "entra_cookies_inserted": entra_cookies_inserted,
    }

    # === Add Listeners (outside the Thread Group) ===
    results_cfg = JMETER_CONFIG.get("results_collector_config", {})
    listener_artifact_dir = os.path.join(ARTIFACTS_PATH, test_run_id, "jmeter")

    # Add View Results Tree if enabled.
    if results_cfg.get("view_results_tree", True):
        view_results_tree_settings = results_cfg.get("view_results_tree_settings", {})
        if "filename" not in view_results_tree_settings:
            view_results_tree_settings["filename"] = os.path.join(
                listener_artifact_dir, "results_tree.csv"
            )
        vrt_elem, vrt_hash_tree = create_view_results_tree(view_results_tree_settings)
        test_plan_hash_tree.append(vrt_elem)
        test_plan_hash_tree.append(vrt_hash_tree)

    # Add Aggregate Report if enabled.
    if results_cfg.get("aggregate_report", True):
        aggregate_report_settings = results_cfg.get("aggregate_report_settings", {})
        if "filename" not in aggregate_report_settings:
            aggregate_report_settings["filename"] = os.path.join(
                listener_artifact_dir, "aggregate_report.csv"
            )
        ar_elem, ar_hash_tree = create_aggregate_report(aggregate_report_settings)
        test_plan_hash_tree.append(ar_elem)
        test_plan_hash_tree.append(ar_hash_tree)

    # (You can add additional listeners here, e.g., Aggregate Report, Response Time Graph, etc.)

    try:
        jmx_path = save_jmx_file(test_plan, test_run_id)

        if not jmx_path:
            return {
                "status": "error",
                "jmx_path": "",
                "message": "Failed to generate JMX file."
            }

        return {
            "status": "success",
            "jmx_path": jmx_path,
            "message": f"JMX script generated successfully: {jmx_path}",
            "generation_summary": generation_summary,
        }

    except Exception as e:
        return {
            "status": "error",
            "jmx_path": "",
            "message": f"Failed to save JMX script: {e}",
        }
