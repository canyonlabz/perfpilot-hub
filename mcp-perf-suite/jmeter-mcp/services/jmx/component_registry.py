# services/jmx/component_registry.py
"""
JMeter Component Registry

Central registry that maps component type identifiers to their builder functions,
required/optional configuration fields, defaults, and metadata. Used by the
jmx_editor service to validate and build components for add/edit operations.

To add a new component type:
  1. Create the builder function in the appropriate module (controllers.py, etc.)
  2. Export it via __init__.py
  3. Add a registry entry in COMPONENT_REGISTRY below
"""
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from services.jmx.controllers import (
    create_simple_controller,
    create_transaction_controller,
    create_loop_controller,
    create_if_controller,
    create_while_controller,
    create_once_only_controller,
    create_switch_controller,
    create_foreach_controller,
)
from services.jmx.samplers import (
    create_flow_control_action,
    create_jsr223_sampler,
)
from services.jmx.config_elements import (
    create_user_defined_variables,
    create_csv_data_set_config,
    create_header_manager,
    create_cookie_manager,
    create_http_request_defaults,
    create_auth_manager,
    create_keystore_config,
)
from services.jmx.listeners import (
    create_view_results_tree,
    create_aggregate_report,
)
from services.jmx.post_processor import (
    create_json_extractor,
    create_regex_extractor,
    create_boundary_extractor,
    create_jsr223_postprocessor,
    create_jsr223_debug_postprocessor,
)
from services.jmx.pre_processor import (
    create_jsr223_preprocessor,
    create_timestamp_preprocessor,
    create_uuid_preprocessor,
    create_pkce_preprocessor,
    create_cookie_preprocessor,
    create_entra_state_preprocessor,
    create_entra_wsfed_cookie_preprocessor,
)
from services.jmx.assertions import (
    create_response_assertion,
    create_duration_assertion,
)
from services.jmx.timers import (
    create_constant_timer,
    create_constant_throughput_timer,
    create_random_timer,
)
from services.jmx.plan import (
    create_thread_group,
)


# ---------------------------------------------------------------------------
# Builder adapter functions
# ---------------------------------------------------------------------------
# Some existing builders have signatures that don't accept a simple config dict.
# These thin adapters normalise the interface so every registry entry can be
# called as: builder(config_dict) -> (element, hashTree)

def _build_simple_controller(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    return create_simple_controller(testname=cfg.get("name", "Simple Controller"))


def _build_transaction_controller(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    return create_transaction_controller(
        testname=cfg.get("name", "Transaction Controller"),
        include_timers=cfg.get("include_timers", True),
    )


def _build_loop_controller(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    return create_loop_controller(
        testname=cfg.get("name", "Loop Controller"),
        loops=str(cfg.get("loops", "1")),
        continue_forever=cfg.get("continue_forever", False),
    )


def _build_if_controller(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    return create_if_controller(
        testname=cfg.get("name", "If Controller"),
        condition=cfg.get("condition", ""),
        evaluate_all=cfg.get("evaluate_all", False),
        use_expression=cfg.get("use_expression", True),
    )


def _build_while_controller(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    return create_while_controller(
        testname=cfg.get("name", "While Controller"),
        condition=cfg.get("condition", ""),
    )


def _build_once_only_controller(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    return create_once_only_controller(testname=cfg.get("name", "Once Only Controller"))


def _build_switch_controller(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    return create_switch_controller(
        testname=cfg.get("name", "Switch Controller"),
        selection=str(cfg.get("selection", "0")),
    )


def _build_foreach_controller(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    return create_foreach_controller(
        testname=cfg.get("name", "ForEach Controller"),
        input_variable=cfg.get("input_variable", ""),
        output_variable=cfg.get("output_variable", ""),
        start_index=str(cfg.get("start_index", "0")),
        end_index=str(cfg.get("end_index", "")),
        use_separator=cfg.get("use_separator", True),
    )


def _build_thread_group(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    return create_thread_group(
        thread_group_name=cfg.get("name", "Thread Group"),
        num_threads=str(cfg.get("num_threads", "1")),
        ramp_time=str(cfg.get("ramp_time_seconds", "1")),
        loops=str(cfg.get("loops", "1")),
    )


def _build_flow_control_action(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_flow_control_action(
        action_type=cfg.get("action", "pause"),
        testname=cfg.get("name", "Think Time"),
        duration=str(cfg.get("duration_ms", "${thinkTime}")),
        target=cfg.get("target", 0),
    )
    return elem, ET.Element("hashTree")


def _build_jsr223_sampler(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_jsr223_sampler(
        testname=cfg.get("name", "JSR223 Sampler"),
        script=cfg.get("script", ""),
        language=cfg.get("language", "groovy"),
        cache_key=str(cfg.get("cache_compiled_script", True)).lower(),
        parameters=cfg.get("parameters", ""),
    )
    return elem, ET.Element("hashTree")


def _build_header_manager(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    headers_list = cfg.get("headers", [])
    headers_dict = {h["name"]: h["value"] for h in headers_list} if headers_list else {}
    elem = create_header_manager(headers_dict)
    if cfg.get("name"):
        elem.set("testname", cfg["name"])
    return elem, ET.Element("hashTree")


def _build_cookie_manager(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_cookie_manager()
    if cfg.get("name"):
        elem.set("testname", cfg["name"])
    clear_prop = elem.find("boolProp[@name='CookieManager.clearEachIteration']")
    if clear_prop is not None:
        clear_prop.text = str(cfg.get("clear_each_iteration", True)).lower()
    return elem, ET.Element("hashTree")


def _build_csv_dataset(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    csv_cfg = {
        "enabled": True,
        "filename": cfg.get("filename", "test_data.csv"),
        "ignore_first_line": cfg.get("ignore_first_line", True),
        "variable_names": cfg.get("variable_names", ""),
        "delimiter": cfg.get("delimiter", ","),
        "recycle_on_end": cfg.get("recycle_on_eof", True),
        "stop_thread_on_error": cfg.get("stop_thread_on_eof", False),
        "sharing_mode": cfg.get("share_mode", "shareMode.all"),
    }
    elem = create_csv_data_set_config(csv_cfg)
    if elem is not None and cfg.get("name"):
        elem.set("testname", cfg["name"])
    return elem, ET.Element("hashTree")


def _build_udv(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    udv_cfg = {
        "enabled": True,
        "variables": cfg.get("variables", {}),
    }
    elem = create_user_defined_variables(udv_cfg)
    if elem is not None and cfg.get("name"):
        elem.set("testname", cfg["name"])
    return elem, ET.Element("hashTree")


def _build_http_request_defaults(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_http_request_defaults(
        testname=cfg.get("name", "HTTP Request Defaults"),
        protocol=cfg.get("protocol", "https"),
        domain=cfg.get("domain", ""),
        port=str(cfg.get("port", "")),
        connect_timeout=str(cfg.get("connect_timeout_ms", "30000")),
        response_timeout=str(cfg.get("response_timeout_ms", "30000")),
    )
    return elem, ET.Element("hashTree")


def _build_auth_manager(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_auth_manager(
        testname=cfg.get("name", "HTTP Authorization Manager"),
        entries=cfg.get("entries", []),
    )
    return elem, ET.Element("hashTree")


def _build_keystore_config(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_keystore_config(
        testname=cfg.get("name", "Keystore Configuration"),
        keystore_path=cfg.get("keystore_path", ""),
        keystore_password=cfg.get("keystore_password", ""),
        keystore_type=cfg.get("keystore_type", "PKCS12"),
        alias=cfg.get("alias", ""),
        key_password=cfg.get("key_password", ""),
    )
    return elem, ET.Element("hashTree")


def _build_json_extractor(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_json_extractor(
        variable_name=cfg.get("variable_names", [""])[0] if isinstance(cfg.get("variable_names"), list) else cfg.get("variable_name", ""),
        json_path=cfg.get("json_path_expressions", [""])[0] if isinstance(cfg.get("json_path_expressions"), list) else cfg.get("json_path", ""),
        match_no=str(cfg.get("match_numbers", ["1"])[0] if isinstance(cfg.get("match_numbers"), list) else cfg.get("match_number", "1")),
        default_value=cfg.get("default_values", ["NOT_FOUND"])[0] if isinstance(cfg.get("default_values"), list) else cfg.get("default_value", "NOT_FOUND"),
        testname=cfg.get("name"),
    )
    return elem, ET.Element("hashTree")


def _build_regex_extractor(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_regex_extractor(
        variable_name=cfg.get("refname", cfg.get("variable_name", "")),
        regex=cfg.get("regex", ""),
        template=cfg.get("template", "$1$"),
        match_no=str(cfg.get("match_number", "1")),
        default_value=cfg.get("default_value", "NOT_FOUND"),
        field_to_check=cfg.get("field_to_check", "body"),
        testname=cfg.get("name"),
    )
    return elem, ET.Element("hashTree")


def _build_boundary_extractor(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_boundary_extractor(
        variable_name=cfg.get("refname", cfg.get("variable_name", "")),
        left_boundary=cfg.get("left_boundary", ""),
        right_boundary=cfg.get("right_boundary", ""),
        match_no=str(cfg.get("match_number", "1")),
        default_value=cfg.get("default_value", "NOT_FOUND"),
        field_to_check=cfg.get("field_to_check", "body"),
        testname=cfg.get("name"),
    )
    return elem, ET.Element("hashTree")


def _build_jsr223_preprocessor(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_jsr223_preprocessor(
        script=cfg.get("script", ""),
        language=cfg.get("language", "groovy"),
        testname=cfg.get("name", "JSR223 PreProcessor"),
        cache_key=str(cfg.get("cache_compiled_script", True)).lower(),
        parameters=cfg.get("parameters", ""),
    )
    return elem, ET.Element("hashTree")


def _build_timestamp_preprocessor(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_timestamp_preprocessor(
        variable_name=cfg.get("variable_name", "timestamp"),
        testname=cfg.get("name"),
    )
    return elem, ET.Element("hashTree")


def _build_uuid_preprocessor(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_uuid_preprocessor(
        variable_name=cfg.get("variable_name", "uuid"),
        testname=cfg.get("name"),
    )
    return elem, ET.Element("hashTree")


def _build_pkce_preprocessor(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_pkce_preprocessor(
        code_verifier_var=cfg.get("code_verifier_var", "code_verifier"),
        code_challenge_var=cfg.get("code_challenge_var", "code_challenge"),
        testname=cfg.get("name", "Generate PKCE Values"),
    )
    return elem, ET.Element("hashTree")


def _build_cookie_preprocessor(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_cookie_preprocessor(
        cookie_name=cfg.get("cookie_name", ""),
        cookie_value_var=cfg.get("cookie_value_var", ""),
        domain=cfg.get("domain", ""),
        testname=cfg.get("name"),
    )
    return elem, ET.Element("hashTree")


def _build_entra_state_preprocessor(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_entra_state_preprocessor(
        state_var=cfg.get("state_var", "oauth_state"),
        testname=cfg.get("name", "Generate MSAL oauth_state"),
    )
    return elem, ET.Element("hashTree")


def _build_entra_wsfed_cookie_preprocessor(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_entra_wsfed_cookie_preprocessor(
        flow_token_var=cfg.get("flow_token_var", "flowToken_1"),
        domain=cfg.get("domain", "login.microsoftonline.com"),
        testname=cfg.get("name", "Add EntraID WS-Fed Cookies"),
    )
    return elem, ET.Element("hashTree")


def _build_jsr223_postprocessor(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_jsr223_postprocessor(
        script=cfg.get("script", ""),
        language=cfg.get("language", "groovy"),
        testname=cfg.get("name", "JSR223 PostProcessor"),
        cache_key=str(cfg.get("cache_compiled_script", True)).lower(),
        parameters=cfg.get("parameters", ""),
    )
    return elem, ET.Element("hashTree")


def _build_jsr223_debug_postprocessor(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_jsr223_debug_postprocessor(
        testname=cfg.get("name", "AI Debug PostProcessor"),
    )
    return elem, ET.Element("hashTree")


def _build_response_assertion(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_response_assertion(
        testname=cfg.get("name", "Response Assertion"),
        field_to_test=cfg.get("field_to_test", "response_code"),
        match_type=cfg.get("match_type", "equals"),
        patterns=cfg.get("patterns", ["200"]),
        assume_success=cfg.get("assume_success", False),
    )
    return elem, ET.Element("hashTree")


def _build_duration_assertion(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_duration_assertion(
        testname=cfg.get("name", "Duration Assertion"),
        max_duration_ms=cfg.get("max_duration_ms", 2000),
    )
    return elem, ET.Element("hashTree")


def _build_constant_timer(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_constant_timer(
        testname=cfg.get("name", "Constant Timer"),
        delay_ms=str(cfg.get("delay_ms", "300")),
    )
    return elem, ET.Element("hashTree")


def _build_constant_throughput_timer(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_constant_throughput_timer(
        testname=cfg.get("name", "Constant Throughput Timer"),
        target_throughput_per_min=str(cfg.get("target_throughput_per_min", "60.0")),
        calc_mode=cfg.get("calc_mode", 0),
    )
    return elem, ET.Element("hashTree")


def _build_random_timer(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    elem = create_random_timer(
        testname=cfg.get("name", "Gaussian Random Timer"),
        delay_ms=str(cfg.get("delay_ms", "300")),
        range_ms=str(cfg.get("range_ms", "100")),
    )
    return elem, ET.Element("hashTree")


def _build_view_results_tree(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    listener_cfg = {
        "save_response_data": cfg.get("save_response_data", False),
        "save_request_headers": cfg.get("save_request_headers", False),
        "save_response_headers": cfg.get("save_response_headers", False),
        "filename": cfg.get("filename", "results_tree.csv"),
    }
    return create_view_results_tree(listener_cfg)


def _build_aggregate_report(cfg: dict) -> Tuple[ET.Element, ET.Element]:
    listener_cfg = {
        "save_response_data": cfg.get("save_response_data", False),
        "save_request_headers": cfg.get("save_request_headers", False),
        "save_response_headers": cfg.get("save_response_headers", False),
        "filename": cfg.get("filename", "aggregate_report.csv"),
    }
    return create_aggregate_report(listener_cfg)


# ---------------------------------------------------------------------------
# Component Registry
# ---------------------------------------------------------------------------
# Each entry maps a component_type identifier to its metadata.
# The "builder" value is a callable: (config_dict) -> (element, hashTree)
#
# Categories mirror JMeter's component types:
#   controller, sampler, config_element, pre_processor, post_processor,
#   assertion, timer, listener

COMPONENT_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ---- Controllers ----
    "simple_controller": {
        "testclass": "GenericController",
        "category": "controller",
        "builder": _build_simple_controller,
        "required_fields": [],
        "optional_fields": ["name"],
        "defaults": {"name": "Simple Controller"},
        "description": "Logical grouping container for organizing test steps",
    },
    "transaction_controller": {
        "testclass": "TransactionController",
        "category": "controller",
        "builder": _build_transaction_controller,
        "required_fields": [],
        "optional_fields": ["name", "include_timers"],
        "defaults": {"name": "Transaction Controller", "include_timers": True},
        "description": "Groups samplers for aggregated timing in reports",
    },
    "loop_controller": {
        "testclass": "LoopController",
        "category": "controller",
        "builder": _build_loop_controller,
        "required_fields": ["loops"],
        "optional_fields": ["name", "continue_forever"],
        "defaults": {"name": "Loop Controller", "continue_forever": False},
        "description": "Repeat child elements a specified number of times",
    },
    "if_controller": {
        "testclass": "IfController",
        "category": "controller",
        "builder": _build_if_controller,
        "required_fields": ["condition"],
        "optional_fields": ["name", "evaluate_all", "use_expression"],
        "defaults": {"name": "If Controller", "evaluate_all": False, "use_expression": True},
        "description": "Execute children only when condition is true",
    },
    "while_controller": {
        "testclass": "WhileController",
        "category": "controller",
        "builder": _build_while_controller,
        "required_fields": ["condition"],
        "optional_fields": ["name"],
        "defaults": {"name": "While Controller"},
        "description": "Repeat children while condition evaluates to true",
    },
    "once_only_controller": {
        "testclass": "OnceOnlyController",
        "category": "controller",
        "builder": _build_once_only_controller,
        "required_fields": [],
        "optional_fields": ["name"],
        "defaults": {"name": "Once Only Controller"},
        "description": "Execute children only on the first iteration of the parent loop",
    },
    "switch_controller": {
        "testclass": "SwitchController",
        "category": "controller",
        "builder": _build_switch_controller,
        "required_fields": [],
        "optional_fields": ["name", "selection"],
        "defaults": {"name": "Switch Controller", "selection": "0"},
        "description": "Run one child based on selection index or name (business flow routing)",
    },
    "foreach_controller": {
        "testclass": "ForeachController",
        "category": "controller",
        "builder": _build_foreach_controller,
        "required_fields": ["input_variable", "output_variable"],
        "optional_fields": ["name", "start_index", "end_index", "use_separator"],
        "defaults": {"name": "ForEach Controller", "start_index": "0", "end_index": "", "use_separator": True},
        "description": "Iterate over extracted variable list (e.g., product_id_1, product_id_2, ...)",
    },
    "thread_group": {
        "testclass": "ThreadGroup",
        "category": "controller",
        "builder": _build_thread_group,
        "required_fields": [],
        "optional_fields": ["name", "num_threads", "ramp_time_seconds", "loops"],
        "defaults": {"name": "Thread Group", "num_threads": "1", "ramp_time_seconds": "1", "loops": "1"},
        "description": "Top-level container defining virtual users, ramp-up, and loop count",
    },

    # ---- Samplers ----
    "jsr223_sampler": {
        "testclass": "JSR223Sampler",
        "category": "sampler",
        "builder": _build_jsr223_sampler,
        "required_fields": ["script"],
        "optional_fields": ["name", "language", "cache_compiled_script", "parameters"],
        "defaults": {"name": "JSR223 Sampler", "language": "groovy", "cache_compiled_script": True},
        "description": "Custom scripting sampler for data creation or utility logic",
    },
    "flow_control_action": {
        "testclass": "TestAction",
        "category": "sampler",
        "builder": _build_flow_control_action,
        "required_fields": [],
        "optional_fields": ["name", "action", "duration_ms", "target"],
        "defaults": {"name": "Think Time", "action": "pause", "duration_ms": "${thinkTime}", "target": 0},
        "description": "Flow control action (pause/think time, stop thread, etc.)",
    },

    # ---- Config Elements ----
    "header_manager": {
        "testclass": "HeaderManager",
        "category": "config_element",
        "builder": _build_header_manager,
        "required_fields": ["headers"],
        "optional_fields": ["name"],
        "defaults": {"name": "HTTP Header Manager"},
        "description": "Manage HTTP request headers (list of {name, value} pairs)",
    },
    "cookie_manager": {
        "testclass": "CookieManager",
        "category": "config_element",
        "builder": _build_cookie_manager,
        "required_fields": [],
        "optional_fields": ["name", "clear_each_iteration"],
        "defaults": {"name": "HTTP Cookie Manager", "clear_each_iteration": True},
        "description": "Automatic cookie handling across requests",
    },
    "csv_dataset": {
        "testclass": "CSVDataSet",
        "category": "config_element",
        "builder": _build_csv_dataset,
        "required_fields": ["filename", "variable_names"],
        "optional_fields": ["name", "delimiter", "ignore_first_line", "recycle_on_eof", "stop_thread_on_eof", "share_mode"],
        "defaults": {"name": "CSV Data Set Config", "delimiter": ",", "ignore_first_line": True, "recycle_on_eof": True, "stop_thread_on_eof": False, "share_mode": "shareMode.all"},
        "description": "Load test data from CSV file (credentials, IDs, etc.)",
    },
    "user_defined_variables": {
        "testclass": "Arguments",
        "category": "config_element",
        "builder": _build_udv,
        "required_fields": ["variables"],
        "optional_fields": ["name"],
        "defaults": {"name": "User Defined Variables"},
        "description": "Define global variables (thinkTime, pacing, env toggles, etc.)",
    },
    "http_request_defaults": {
        "testclass": "ConfigTestElement",
        "category": "config_element",
        "builder": _build_http_request_defaults,
        "required_fields": [],
        "optional_fields": ["name", "protocol", "domain", "port", "connect_timeout_ms", "response_timeout_ms"],
        "defaults": {"name": "HTTP Request Defaults", "protocol": "https", "connect_timeout_ms": "30000", "response_timeout_ms": "30000"},
        "description": "Default protocol, domain, port, and timeouts for all HTTP samplers",
    },
    "auth_manager": {
        "testclass": "AuthManager",
        "category": "config_element",
        "builder": _build_auth_manager,
        "required_fields": [],
        "optional_fields": ["name", "entries"],
        "defaults": {"name": "HTTP Authorization Manager"},
        "description": "Manage Basic/Digest/Kerberos authentication credentials",
    },
    "keystore_config": {
        "testclass": "KeystoreConfig",
        "category": "config_element",
        "builder": _build_keystore_config,
        "required_fields": ["keystore_path"],
        "optional_fields": ["name", "keystore_password", "keystore_type", "alias", "key_password"],
        "defaults": {"name": "Keystore Configuration", "keystore_type": "PKCS12"},
        "description": "Client certificate / mutual TLS keystore configuration",
    },

    # ---- Pre-Processors ----
    "jsr223_preprocessor": {
        "testclass": "JSR223PreProcessor",
        "category": "pre_processor",
        "builder": _build_jsr223_preprocessor,
        "required_fields": ["script"],
        "optional_fields": ["name", "language", "cache_compiled_script", "parameters"],
        "defaults": {"name": "JSR223 PreProcessor", "language": "groovy", "cache_compiled_script": True},
        "description": "Execute custom Groovy script before a sampler runs",
    },
    "timestamp_preprocessor": {
        "testclass": "JSR223PreProcessor",
        "category": "pre_processor",
        "builder": _build_timestamp_preprocessor,
        "required_fields": [],
        "optional_fields": ["name", "variable_name"],
        "defaults": {"variable_name": "timestamp"},
        "description": "Generate a millisecond timestamp variable (cache-busting, SignalR, etc.)",
    },
    "uuid_preprocessor": {
        "testclass": "JSR223PreProcessor",
        "category": "pre_processor",
        "builder": _build_uuid_preprocessor,
        "required_fields": [],
        "optional_fields": ["name", "variable_name"],
        "defaults": {"variable_name": "uuid"},
        "description": "Generate a random UUID variable",
    },
    "pkce_preprocessor": {
        "testclass": "JSR223PreProcessor",
        "category": "pre_processor",
        "builder": _build_pkce_preprocessor,
        "required_fields": [],
        "optional_fields": ["name", "code_verifier_var", "code_challenge_var"],
        "defaults": {"code_verifier_var": "code_verifier", "code_challenge_var": "code_challenge"},
        "description": "Generate PKCE code_verifier and code_challenge for OAuth 2.0 flows",
    },
    "cookie_preprocessor": {
        "testclass": "JSR223PreProcessor",
        "category": "pre_processor",
        "builder": _build_cookie_preprocessor,
        "required_fields": ["cookie_name", "cookie_value_var", "domain"],
        "optional_fields": ["name"],
        "defaults": {},
        "description": "Add a cookie to the Cookie Manager via Groovy script (cross-domain SSO)",
    },
    "entra_state_preprocessor": {
        "testclass": "JSR223PreProcessor",
        "category": "pre_processor",
        "builder": _build_entra_state_preprocessor,
        "required_fields": [],
        "optional_fields": ["name", "state_var"],
        "defaults": {"state_var": "oauth_state"},
        "description": "Generate MSAL-format base64url-encoded oauth_state for EntraID flows",
    },
    "entra_wsfed_cookie_preprocessor": {
        "testclass": "BeanShellPreProcessor",
        "category": "pre_processor",
        "builder": _build_entra_wsfed_cookie_preprocessor,
        "required_fields": [],
        "optional_fields": ["name", "flow_token_var", "domain"],
        "defaults": {"flow_token_var": "flowToken_1", "domain": "login.microsoftonline.com"},
        "description": "Inject ESTSWCTXFLOWTOKEN and AADSSO cookies for EntraID WS-Fed form submission",
    },

    # ---- Post-Processors / Extractors ----
    "json_extractor": {
        "testclass": "JSONPostProcessor",
        "category": "post_processor",
        "builder": _build_json_extractor,
        "required_fields": ["variable_name", "json_path"],
        "optional_fields": ["name", "match_number", "default_value"],
        "defaults": {"match_number": "1", "default_value": "NOT_FOUND"},
        "description": "Extract values from JSON responses using JSONPath expressions",
    },
    "regex_extractor": {
        "testclass": "RegexExtractor",
        "category": "post_processor",
        "builder": _build_regex_extractor,
        "required_fields": ["variable_name", "regex"],
        "optional_fields": ["name", "template", "match_number", "default_value", "field_to_check"],
        "defaults": {"template": "$1$", "match_number": "1", "default_value": "NOT_FOUND", "field_to_check": "body"},
        "description": "Extract values from responses using regular expressions",
    },
    "boundary_extractor": {
        "testclass": "BoundaryExtractor",
        "category": "post_processor",
        "builder": _build_boundary_extractor,
        "required_fields": ["variable_name", "left_boundary", "right_boundary"],
        "optional_fields": ["name", "match_number", "default_value", "field_to_check"],
        "defaults": {"match_number": "1", "default_value": "NOT_FOUND", "field_to_check": "body"},
        "description": "Extract text between two boundary strings",
    },
    "jsr223_postprocessor": {
        "testclass": "JSR223PostProcessor",
        "category": "post_processor",
        "builder": _build_jsr223_postprocessor,
        "required_fields": ["script"],
        "optional_fields": ["name", "language", "cache_compiled_script", "parameters"],
        "defaults": {"name": "JSR223 PostProcessor", "language": "groovy", "cache_compiled_script": True},
        "description": "Execute custom Groovy script after a sampler runs (parse, compute, extract)",
    },
    "jsr223_debug_postprocessor": {
        "testclass": "JSR223PostProcessor",
        "category": "post_processor",
        "builder": _build_jsr223_debug_postprocessor,
        "required_fields": [],
        "optional_fields": ["name"],
        "defaults": {"name": "AI Debug PostProcessor"},
        "description": "AI verbose debug logger - logs request/response details to JMeter log when VERBOSE_LOGGING=true and sampler fails",
    },

    # ---- Assertions ----
    "response_assertion": {
        "testclass": "ResponseAssertion",
        "category": "assertion",
        "builder": _build_response_assertion,
        "required_fields": ["patterns"],
        "optional_fields": ["name", "field_to_test", "match_type", "assume_success"],
        "defaults": {"name": "Response Assertion", "field_to_test": "response_code", "match_type": "equals"},
        "description": "Validate response code, body, or headers against patterns",
    },
    "duration_assertion": {
        "testclass": "DurationAssertion",
        "category": "assertion",
        "builder": _build_duration_assertion,
        "required_fields": ["max_duration_ms"],
        "optional_fields": ["name"],
        "defaults": {"name": "Duration Assertion", "max_duration_ms": 2000},
        "description": "Fail sample if response time exceeds threshold",
    },

    # ---- Timers ----
    "constant_timer": {
        "testclass": "ConstantTimer",
        "category": "timer",
        "builder": _build_constant_timer,
        "required_fields": [],
        "optional_fields": ["name", "delay_ms"],
        "defaults": {"name": "Constant Timer", "delay_ms": "300"},
        "description": "Fixed delay before next sampler (supports ${thinkTime} variables)",
    },
    "constant_throughput_timer": {
        "testclass": "ConstantThroughputTimer",
        "category": "timer",
        "builder": _build_constant_throughput_timer,
        "required_fields": ["target_throughput_per_min"],
        "optional_fields": ["name", "calc_mode"],
        "defaults": {"name": "Constant Throughput Timer", "calc_mode": 0},
        "description": "Throttle execution to achieve target requests per minute",
    },
    "random_timer": {
        "testclass": "GaussianRandomTimer",
        "category": "timer",
        "builder": _build_random_timer,
        "required_fields": [],
        "optional_fields": ["name", "delay_ms", "range_ms"],
        "defaults": {"name": "Gaussian Random Timer", "delay_ms": "300", "range_ms": "100"},
        "description": "Gaussian-distributed random delay for realistic user think time",
    },

    # ---- Listeners ----
    "view_results_tree": {
        "testclass": "ResultCollector",
        "category": "listener",
        "builder": _build_view_results_tree,
        "required_fields": [],
        "optional_fields": ["save_response_data", "save_request_headers", "save_response_headers", "filename"],
        "defaults": {"save_response_data": False, "save_request_headers": False, "save_response_headers": False},
        "description": "View individual request/response details (debugging listener)",
    },
    "aggregate_report": {
        "testclass": "ResultCollector",
        "category": "listener",
        "builder": _build_aggregate_report,
        "required_fields": [],
        "optional_fields": ["save_response_data", "save_request_headers", "save_response_headers", "filename"],
        "defaults": {"save_response_data": False, "save_request_headers": False, "save_response_headers": False},
        "description": "Aggregate performance statistics by sampler label",
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_component_config(
    component_type: str,
    component_config: Dict[str, Any]
) -> Tuple[bool, List[str]]:
    """
    Validate a component configuration dict against its registry entry.

    Args:
        component_type: Registry key (e.g., "loop_controller").
        component_config: Configuration dict provided by the user/agent.

    Returns:
        Tuple of (is_valid, list_of_error_messages).
    """
    errors: List[str] = []

    if component_type not in COMPONENT_REGISTRY:
        supported = ", ".join(sorted(COMPONENT_REGISTRY.keys()))
        errors.append(
            f"Unknown component_type '{component_type}'. "
            f"Supported types: {supported}"
        )
        return False, errors

    entry = COMPONENT_REGISTRY[component_type]

    for field in entry["required_fields"]:
        if field not in component_config:
            errors.append(f"Missing required field '{field}' for component type '{component_type}'.")

    known_fields = set(entry["required_fields"]) | set(entry["optional_fields"])
    for field in component_config:
        if field not in known_fields:
            errors.append(f"Unknown field '{field}' for component type '{component_type}'. Known fields: {sorted(known_fields)}")

    return (len(errors) == 0), errors


def build_component(
    component_type: str,
    component_config: Dict[str, Any]
) -> Tuple[ET.Element, ET.Element]:
    """
    Validate config and build a JMeter component XML element pair.

    Args:
        component_type: Registry key (e.g., "loop_controller").
        component_config: Configuration dict.

    Returns:
        Tuple of (element, hashTree).

    Raises:
        ValueError: If component_type is unknown or config is invalid.
    """
    is_valid, errors = validate_component_config(component_type, component_config)
    if not is_valid:
        raise ValueError(f"Invalid component config: {'; '.join(errors)}")

    entry = COMPONENT_REGISTRY[component_type]

    merged_config = dict(entry.get("defaults", {}))
    merged_config.update(component_config)

    return entry["builder"](merged_config)


def list_supported_components(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List all supported component types in the registry.

    Args:
        category: Optional filter by category (e.g., "controller", "timer").
            If None, returns all components.

    Returns:
        List of dicts with component_type, testclass, category, description,
        required_fields, and optional_fields.
    """
    results = []
    for comp_type, entry in sorted(COMPONENT_REGISTRY.items()):
        if category and entry["category"] != category:
            continue
        results.append({
            "component_type": comp_type,
            "testclass": entry["testclass"],
            "category": entry["category"],
            "description": entry["description"],
            "required_fields": entry["required_fields"],
            "optional_fields": entry["optional_fields"],
            "defaults": entry.get("defaults", {}),
        })
    return results
