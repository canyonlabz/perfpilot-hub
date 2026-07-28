# services/jmx/__init__.py
"""
JMeter JMX Builder Package

This package provides modular functions to create JMeter JMX script elements:
- plan.py: Test Plan and Thread Group
- controllers.py: Simple, Transaction, Loop, If, While, OnceOnly, Switch, ForEach Controllers
- samplers.py: HTTP Request samplers, Flow Control Action, JSR223 Sampler
- config_elements.py: Cookie Manager, User Defined Variables, CSV Data Set, Header Manager,
                       HTTP Request Defaults, Auth Manager, Keystore Configuration
- listeners.py: View Results Tree, Aggregate Report
- post_processor.py: JSON Extractor, Regex Extractor, Boundary Extractor, JSR223 PostProcessor
- pre_processor.py: JSR223 PreProcessor, Timestamp, UUID, PKCE generators
- assertions.py: Response Assertion, Duration Assertion
- timers.py: Constant Timer, Constant Throughput Timer, Gaussian Random Timer
- oauth2.py: OAuth 2.0 specific elements
"""

# Controllers
from .controllers import (
    create_simple_controller,
    create_transaction_controller,
    create_loop_controller,
    create_if_controller,
    create_while_controller,
    create_once_only_controller,
    create_switch_controller,
    create_foreach_controller,
)

# Samplers
from .samplers import (
    create_http_sampler_get,
    create_http_sampler_with_body,
    append_sampler,
    create_flow_control_action,
    create_jsr223_sampler,
)

# Config Elements
from .config_elements import (
    create_user_defined_variables,
    create_csv_data_set_config,
    create_header_manager,
    create_cookie_manager,
    create_http_request_defaults,
    create_auth_manager,
    create_keystore_config,
)

# Listeners
from .listeners import (
    create_view_results_tree,
    create_aggregate_report,
)

# Post-Processors (Extractors)
from .post_processor import (
    create_json_extractor,
    create_regex_extractor,
    create_boundary_extractor,
    append_extractor,
    create_jsr223_postprocessor,
    create_jsr223_debug_postprocessor,
)

# Pre-Processors
from .pre_processor import (
    create_jsr223_preprocessor,
    create_timestamp_preprocessor,
    create_multiple_timestamps_preprocessor,
    create_uuid_preprocessor,
    create_pkce_preprocessor,
    create_cookie_preprocessor,
    append_preprocessor,
)

# Assertions
from .assertions import (
    create_response_assertion,
    create_duration_assertion,
)

# Timers
from .timers import (
    create_constant_timer,
    create_constant_throughput_timer,
    create_random_timer,
)

# Plan
from .plan import (
    create_test_plan,
    create_thread_group,
)

# Expose commonly used functions at package level
__all__ = [
    # Plan
    "create_test_plan",
    "create_thread_group",
    # Controllers
    "create_simple_controller",
    "create_transaction_controller",
    "create_loop_controller",
    "create_if_controller",
    "create_while_controller",
    "create_once_only_controller",
    "create_switch_controller",
    "create_foreach_controller",
    # Samplers
    "create_http_sampler_get",
    "create_http_sampler_with_body",
    "append_sampler",
    "create_flow_control_action",
    "create_jsr223_sampler",
    # Config Elements
    "create_user_defined_variables",
    "create_csv_data_set_config",
    "create_header_manager",
    "create_cookie_manager",
    "create_http_request_defaults",
    "create_auth_manager",
    "create_keystore_config",
    # Listeners
    "create_view_results_tree",
    "create_aggregate_report",
    # Post-Processors
    "create_json_extractor",
    "create_regex_extractor",
    "create_boundary_extractor",
    "append_extractor",
    "create_jsr223_postprocessor",
    "create_jsr223_debug_postprocessor",
    # Pre-Processors
    "create_jsr223_preprocessor",
    "create_timestamp_preprocessor",
    "create_multiple_timestamps_preprocessor",
    "create_uuid_preprocessor",
    "create_pkce_preprocessor",
    "create_cookie_preprocessor",
    "append_preprocessor",
    # Assertions
    "create_response_assertion",
    "create_duration_assertion",
    # Timers
    "create_constant_timer",
    "create_constant_throughput_timer",
    "create_random_timer",
]
