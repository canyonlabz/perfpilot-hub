"""
swagger_adapter.py

Adapter module that reads Swagger 2.x / OpenAPI 3.x specification files
and converts them into the canonical, step-aware network capture JSON
used for JMeter script generation.

High-level responsibilities:
- Parse OpenAPI 3.x and Swagger 2.x specs (JSON and YAML)
- Normalize Swagger 2.x to OpenAPI 3.x internally
- Resolve $ref references recursively (with circular ref detection)
- Generate synthetic request/response bodies from JSON schemas
- Group endpoints into logical steps (by tag, path, or single step)
- Emit network_capture_<timestamp>.json under:
    artifacts/<run_id>/jmeter/network-capture/
"""

import json
import logging
import os
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlencode, urlparse

import yaml

from utils.config import load_config

logger = logging.getLogger(__name__)

# Optional: faker for more realistic sample data
try:
    from faker import Faker as _FakerClass
    _fake = _FakerClass()
    _FAKER_AVAILABLE = True
except ImportError:
    _fake = None
    _FAKER_AVAILABLE = False

# === Global configuration ===
CONFIG = load_config()
ARTIFACTS_PATH = CONFIG["artifacts"]["artifacts_path"]

_VALID_STRATEGIES = {"tag", "path", "single_step"}
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_METHOD_ORDER = {
    "get": 0, "post": 1, "put": 2, "patch": 3,
    "delete": 4, "head": 5, "options": 6,
}

_MAX_SPEC_FILE_SIZE_BYTES = 50 * 1024 * 1024   # 50 MB — reject
_WARN_SPEC_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB — warn
_MAX_REF_DEPTH = 10

_REQUIRED_ENTRY_FIELDS = {
    "request_id": str,
    "method": str,
    "url": str,
    "headers": dict,
    "post_data": str,
    "step": dict,
    "response": str,
    "log_timestamp": str,
    "status": int,
    "response_headers": dict,
}

_DEFAULT_SAMPLE_VALUES: Dict[str, Any] = {
    "string": "sample_string",
    "integer": 1,
    "number": 1.5,
    "boolean": True,
}

_FORMAT_SAMPLE_VALUES: Dict[str, Any] = {
    "uuid": "550e8400-e29b-41d4-a716-446655440000",
    "date-time": "2026-01-15T10:30:00Z",
    "date": "2026-01-15",
    "time": "10:30:00",
    "email": "user@example.com",
    "uri": "https://example.com/resource",
    "url": "https://example.com/resource",
    "hostname": "example.com",
    "ipv4": "192.168.1.1",
    "ipv6": "::1",
    "int32": 1,
    "int64": 100,
    "float": 1.5,
    "double": 1.5,
    "byte": "dGVzdA==",
    "binary": "",
    "password": "P@ssw0rd123",
}


# ============================================================
# Public API
# ============================================================

def convert_swagger_to_capture(
    spec_path: str,
    test_run_id: str,
    base_url: str = "",
    step_strategy: str = "tag",
    include_deprecated: bool = False,
    step_prefix: str = "Step",
) -> str:
    """
    Convert a Swagger/OpenAPI spec to network capture JSON format.

    Args:
        spec_path: Full path to the spec file (.json or .yaml/.yml).
        test_run_id: Unique identifier for the test run.
        base_url: Base URL for the API. Required when the spec has a
            relative server URL.
        step_strategy: Grouping strategy (tag/path/single_step).
        include_deprecated: Whether to include deprecated endpoints.
        step_prefix: Prefix for step labels (default: "Step").

    Returns:
        Path to the generated network capture JSON file.

    Raises:
        FileNotFoundError: If spec file doesn't exist.
        ValueError: If spec is invalid or contains no usable operations.
    """
    spec = _load_spec_file(spec_path)
    effective_base_url = _resolve_base_url(spec, base_url)

    all_operations = _extract_operations(spec)
    operations_total = len(all_operations)

    if not include_deprecated:
        operations = [
            op for op in all_operations if not op.get("deprecated", False)
        ]
        deprecated_skipped = operations_total - len(operations)
    else:
        operations = all_operations
        deprecated_skipped = 0

    if deprecated_skipped:
        logger.info("Skipped %d deprecated operations", deprecated_skipped)

    if not operations:
        raise ValueError(
            "No usable operations found in spec"
            + (" (deprecated endpoints excluded)"
               if not include_deprecated else "")
            + ". Check the spec file."
        )

    logger.info("Extracted %d operations from spec", len(operations))

    strategy = _validate_strategy(step_strategy)
    if strategy == "tag":
        grouped = _group_by_tag(operations, step_prefix)
    elif strategy == "path":
        grouped = _group_by_path(operations, step_prefix)
    else:
        grouped = _group_single_step(operations, step_prefix)

    per_step: Dict[str, List[Dict[str, Any]]] = {}
    for step_idx, (step_label, ops_in_step) in enumerate(
        grouped.items(), start=1,
    ):
        converted = []
        for op in ops_in_step:
            entry = _convert_operation_to_capture_entry(
                op, spec, effective_base_url, step_idx, step_label,
            )
            converted.append(entry)
        per_step[step_label] = converted

    _validate_capture_output(per_step)

    output_path = _write_step_network_capture(per_step, test_run_id)
    logger.info("Network capture written: %s", output_path)

    _write_capture_manifest(
        run_id=test_run_id,
        source_file=os.path.basename(spec_path),
        spec_version=spec.get("openapi", spec.get("swagger", "unknown")),
        spec_title=spec.get("info", {}).get("title", "unknown"),
        step_strategy=strategy,
        operations_total=operations_total,
        operations_deprecated_skipped=deprecated_skipped,
        operations_captured=len(operations),
        base_url=effective_base_url,
    )

    return output_path


def validate_spec_file(spec_path: str) -> Dict[str, Any]:
    """
    Validate a spec file and return summary statistics without converting.

    Args:
        spec_path: Full path to the spec file.

    Returns:
        Dict with keys: valid, version, title, operation_count,
        tag_count, has_deprecated, errors.
    """
    errors: List[str] = []

    try:
        spec = _load_spec_file(spec_path)
    except (FileNotFoundError, ValueError) as exc:
        return {
            "valid": False,
            "version": "",
            "title": "",
            "operation_count": 0,
            "tag_count": 0,
            "has_deprecated": False,
            "errors": [str(exc)],
        }

    version = spec.get("openapi", spec.get("swagger", "unknown"))
    title = spec.get("info", {}).get("title", "unknown")
    all_ops = _extract_operations(spec)
    deprecated_ops = [op for op in all_ops if op.get("deprecated", False)]

    tags: Set[str] = set()
    for op in all_ops:
        for tag in op.get("tags", []):
            tags.add(tag)

    return {
        "valid": len(errors) == 0,
        "version": version,
        "title": title,
        "operation_count": len(all_ops),
        "tag_count": len(tags),
        "has_deprecated": len(deprecated_ops) > 0,
        "errors": errors,
    }


# ============================================================
# Internal — Spec Loading
# ============================================================

def _load_spec_file(spec_path: str) -> Dict[str, Any]:
    """
    Load, parse, and validate a Swagger/OpenAPI spec file.

    Supports JSON (.json) and YAML (.yaml, .yml).
    Normalizes Swagger 2.x to OpenAPI 3.x internally.

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If file exceeds size limit or is not a valid spec.
    """
    if not os.path.isfile(spec_path):
        raise FileNotFoundError(f"Spec file not found: {spec_path}")

    file_size = os.path.getsize(spec_path)

    if file_size > _MAX_SPEC_FILE_SIZE_BYTES:
        raise ValueError(
            f"Spec file too large ({file_size / (1024*1024):.1f} MB). "
            f"Maximum supported size is "
            f"{_MAX_SPEC_FILE_SIZE_BYTES / (1024*1024):.0f} MB."
        )

    if file_size > _WARN_SPEC_FILE_SIZE_BYTES:
        logger.warning(
            "Large spec file: %.1f MB — parsing may take a moment",
            file_size / (1024 * 1024),
        )

    ext = os.path.splitext(spec_path)[1].lower()

    try:
        with open(spec_path, "r", encoding="utf-8", errors="replace") as f:
            if ext in (".yaml", ".yml"):
                spec = yaml.safe_load(f)
            else:
                spec = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in spec file: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in spec file: {exc}") from exc

    if not isinstance(spec, dict):
        raise ValueError(
            "Spec file must contain a JSON/YAML object at the top level"
        )

    is_swagger_2 = "swagger" in spec and str(spec["swagger"]).startswith("2")
    is_openapi_3 = "openapi" in spec and str(spec["openapi"]).startswith("3")

    if not is_swagger_2 and not is_openapi_3:
        raise ValueError(
            "Unrecognized spec format. Expected 'openapi: 3.x' or "
            "'swagger: 2.x' at the top level."
        )

    if "paths" not in spec or not spec["paths"]:
        raise ValueError("Spec contains no 'paths' — nothing to convert.")

    if is_swagger_2:
        logger.info("Detected Swagger 2.x — normalizing to OpenAPI 3.x")
        spec = _normalize_swagger_2x(deepcopy(spec))

    return spec


# ============================================================
# Internal — Swagger 2.x Normalization
# ============================================================

def _normalize_swagger_2x(spec: Dict) -> Dict:
    """
    Convert a Swagger 2.0 spec to OpenAPI 3.x structure in-place.

    Handles:
    - host/basePath/schemes → servers[].url
    - definitions → components.schemas
    - body parameters → requestBody
    - formData parameters → requestBody (form-encoded)
    - response schemas → response content
    - $ref path fix: #/definitions/ → #/components/schemas/
    """
    host = spec.pop("host", "")
    base_path = spec.pop("basePath", "")
    schemes = spec.pop("schemes", ["https"])

    if host:
        scheme = schemes[0] if schemes else "https"
        spec["servers"] = [{"url": f"{scheme}://{host}{base_path}"}]
    elif base_path:
        spec["servers"] = [{"url": base_path}]

    definitions = spec.pop("definitions", {})
    if definitions:
        spec.setdefault("components", {})["schemas"] = definitions

    global_consumes = spec.pop("consumes", ["application/json"])
    global_produces = spec.pop("produces", ["application/json"])

    paths = spec.get("paths", {})
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for method in _HTTP_METHODS:
            if method not in path_item:
                continue
            operation = path_item[method]
            if not isinstance(operation, dict):
                continue

            params = operation.get("parameters", [])
            body_params = [p for p in params if p.get("in") == "body"]
            form_params = [p for p in params if p.get("in") == "formData"]
            other_params = [
                p for p in params
                if p.get("in") not in ("body", "formData")
            ]

            if body_params:
                body_param = body_params[0]
                schema = body_param.get("schema", {})
                ct = (global_consumes[0]
                      if global_consumes else "application/json")
                operation["requestBody"] = {
                    "content": {ct: {"schema": schema}},
                }

            elif form_params:
                properties = {}
                required = []
                for fp in form_params:
                    properties[fp["name"]] = {
                        k: v for k, v in fp.items()
                        if k not in ("name", "in", "required", "description")
                    }
                    if fp.get("required"):
                        required.append(fp["name"])
                schema: Dict[str, Any] = {
                    "type": "object", "properties": properties,
                }
                if required:
                    schema["required"] = required
                operation["requestBody"] = {
                    "content": {
                        "application/x-www-form-urlencoded": {"schema": schema}
                    },
                }

            operation["parameters"] = other_params

            for resp in operation.get("responses", {}).values():
                if not isinstance(resp, dict):
                    continue
                if "schema" in resp and "content" not in resp:
                    resp_schema = resp.pop("schema")
                    ct = (global_produces[0]
                          if global_produces else "application/json")
                    resp["content"] = {ct: {"schema": resp_schema}}

    _fix_swagger2_ref_paths(spec)

    spec.pop("swagger", None)
    spec["openapi"] = "3.0.0"
    return spec


def _fix_swagger2_ref_paths(obj: Any) -> None:
    """Recursively rewrite #/definitions/ → #/components/schemas/."""
    if isinstance(obj, dict):
        if "$ref" in obj and isinstance(obj["$ref"], str):
            obj["$ref"] = obj["$ref"].replace(
                "#/definitions/", "#/components/schemas/",
            )
        for value in obj.values():
            _fix_swagger2_ref_paths(value)
    elif isinstance(obj, list):
        for item in obj:
            _fix_swagger2_ref_paths(item)


# ============================================================
# Internal — Base URL Resolution
# ============================================================

def _resolve_base_url(spec: Dict, user_base_url: str) -> str:
    """
    Determine the effective base URL.

    Priority:
    1. user_base_url if provided
    2. First servers[].url if it's absolute
    3. ValueError if no usable base URL
    """
    if user_base_url:
        return user_base_url.rstrip("/")

    servers = spec.get("servers", [])
    if servers:
        server_url = servers[0].get("url", "")
        parsed = urlparse(server_url)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return server_url.rstrip("/")

        raise ValueError(
            f"Spec has a relative server URL ('{server_url}'). "
            "Please provide a base_url parameter with the full URL "
            "(e.g., 'https://api.example.com/file-svc')."
        )

    raise ValueError(
        "Spec has no 'servers' entry and no base_url was provided. "
        "Please provide a base_url parameter."
    )


# ============================================================
# Internal — $ref Resolution
# ============================================================

def _follow_ref(ref: str, spec: Dict) -> Optional[Dict]:
    """Follow a JSON Pointer reference like '#/components/schemas/Foo'."""
    if not ref.startswith("#/"):
        logger.warning("External $ref not supported: %s", ref)
        return None

    parts = ref[2:].split("/")
    current: Any = spec
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            logger.warning(
                "Cannot resolve $ref: %s (missing key: %s)", ref, part,
            )
            return None

    return current if isinstance(current, dict) else None


def _resolve_schema(
    schema: Dict,
    spec: Dict,
    visited: Optional[Set[str]] = None,
    depth: int = 0,
) -> Dict:
    """
    Resolve $ref pointers in a schema recursively.

    Tracks visited refs per-branch to detect circular references.
    On circular ref or max depth, returns {}.
    """
    if visited is None:
        visited = set()

    if depth > _MAX_REF_DEPTH:
        return {}

    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in visited:
            return {}
        branch_visited = visited | {ref}
        resolved = _follow_ref(ref, spec)
        if resolved is None:
            return {}
        return _resolve_schema(resolved, spec, branch_visited, depth + 1)

    result = dict(schema)

    if "properties" in result and isinstance(result["properties"], dict):
        resolved_props = {}
        for prop_name, prop_schema in result["properties"].items():
            if isinstance(prop_schema, dict):
                resolved_props[prop_name] = _resolve_schema(
                    prop_schema, spec, visited, depth + 1,
                )
            else:
                resolved_props[prop_name] = prop_schema
        result["properties"] = resolved_props

    if "items" in result and isinstance(result["items"], dict):
        result["items"] = _resolve_schema(
            result["items"], spec, visited, depth + 1,
        )

    if ("additionalProperties" in result
            and isinstance(result["additionalProperties"], dict)):
        result["additionalProperties"] = _resolve_schema(
            result["additionalProperties"], spec, visited, depth + 1,
        )

    for keyword in ("allOf", "oneOf", "anyOf"):
        if keyword in result and isinstance(result[keyword], list):
            result[keyword] = [
                _resolve_schema(s, spec, visited, depth + 1)
                for s in result[keyword]
                if isinstance(s, dict)
            ]

    return result


# ============================================================
# Internal — Sample Data Generation
# ============================================================

def _generate_sample_value(
    schema: Dict,
    spec: Dict,
    visited: Optional[Set[str]] = None,
    depth: int = 0,
    for_request: bool = True,
) -> Any:
    """
    Generate a sample value from a JSON Schema definition.

    Priority: example → enum → format → type fallback.
    When for_request is True, readOnly properties are skipped in objects.
    """
    if depth > _MAX_REF_DEPTH:
        return None

    if visited is None:
        visited = set()

    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in visited:
            return {}
        branch_visited = visited | {ref}
        resolved = _follow_ref(ref, spec)
        if resolved is None:
            return None
        return _generate_sample_value(
            resolved, spec, branch_visited, depth + 1, for_request,
        )

    if "example" in schema:
        return schema["example"]

    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]

    schema_type = schema.get("type", "")
    schema_format = schema.get("format", "")

    if schema_format and schema_format in _FORMAT_SAMPLE_VALUES:
        return _FORMAT_SAMPLE_VALUES[schema_format]

    if (schema_type == "object"
            or "properties" in schema
            or "additionalProperties" in schema):
        return _generate_sample_object(
            schema, spec, visited, depth, for_request,
        )

    if schema_type == "array":
        items_schema = schema.get("items", {"type": "string"})
        if isinstance(items_schema, dict):
            item = _generate_sample_value(
                items_schema, spec, visited, depth + 1, for_request,
            )
            return [item] if item is not None else []
        return []

    if "allOf" in schema:
        return _generate_sample_object(
            schema, spec, visited, depth, for_request,
        )

    for keyword in ("oneOf", "anyOf"):
        if keyword in schema and schema[keyword]:
            first = schema[keyword][0]
            if isinstance(first, dict):
                return _generate_sample_value(
                    first, spec, visited, depth + 1, for_request,
                )

    if schema_type in _DEFAULT_SAMPLE_VALUES:
        return _DEFAULT_SAMPLE_VALUES[schema_type]

    if schema_type:
        return "sample_string"

    return ""


def _generate_sample_object(
    schema: Dict,
    spec: Dict,
    visited: Optional[Set[str]] = None,
    depth: int = 0,
    for_request: bool = True,
) -> Dict:
    """Generate a sample object from a schema with properties."""
    if depth > _MAX_REF_DEPTH:
        return {}

    if visited is None:
        visited = set()

    result: Dict[str, Any] = {}

    if "allOf" in schema:
        for sub_schema in schema["allOf"]:
            if not isinstance(sub_schema, dict):
                continue
            if "$ref" in sub_schema:
                ref = sub_schema["$ref"]
                if ref in visited:
                    continue
                resolved = _follow_ref(ref, spec)
                if resolved:
                    merged = _generate_sample_object(
                        resolved, spec, visited | {ref},
                        depth + 1, for_request,
                    )
                    result.update(merged)
            else:
                merged = _generate_sample_object(
                    sub_schema, spec, visited, depth + 1, for_request,
                )
                result.update(merged)
        return result

    for keyword in ("oneOf", "anyOf"):
        if keyword in schema and schema[keyword]:
            first = schema[keyword][0]
            if isinstance(first, dict):
                val = _generate_sample_value(
                    first, spec, visited, depth + 1, for_request,
                )
                return val if isinstance(val, dict) else {}

    properties = schema.get("properties", {})
    for prop_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            continue
        if for_request and prop_schema.get("readOnly"):
            continue
        result[prop_name] = _generate_sample_value(
            prop_schema, spec, visited, depth + 1, for_request,
        )

    additional = schema.get("additionalProperties")
    if isinstance(additional, dict) and not properties:
        sample_val = _generate_sample_value(
            additional, spec, visited, depth + 1, for_request,
        )
        result["key1"] = sample_val

    return result


# ============================================================
# Internal — Operation Extraction
# ============================================================

def _extract_operations(spec: Dict) -> List[Dict[str, Any]]:
    """
    Walk all paths and extract operations as a flat list.

    Each operation dict contains: path, method, tags, summary,
    operation_id, deprecated, parameters, request_body, responses.
    """
    operations: List[Dict[str, Any]] = []
    paths = spec.get("paths", {})

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        path_params = path_item.get("parameters", [])

        for method in _HTTP_METHODS:
            if method not in path_item:
                continue
            op = path_item[method]
            if not isinstance(op, dict):
                continue

            op_params = op.get("parameters", [])
            merged_params = _merge_parameters(path_params, op_params)

            operations.append({
                "path": path,
                "method": method.upper(),
                "tags": op.get("tags", []),
                "summary": op.get("summary", ""),
                "operation_id": op.get("operationId", ""),
                "deprecated": op.get("deprecated", False),
                "parameters": merged_params,
                "request_body": op.get("requestBody"),
                "responses": op.get("responses", {}),
            })

    operations.sort(
        key=lambda o: (
            o["path"],
            _METHOD_ORDER.get(o["method"].lower(), 99),
        ),
    )

    return operations


def _merge_parameters(
    path_params: List[Dict],
    op_params: List[Dict],
) -> List[Dict]:
    """Merge path-level and operation-level parameters.
    Operation-level wins on conflict (same name + in)."""
    by_key: Dict[Tuple[str, str], Dict] = {}
    for p in path_params:
        if isinstance(p, dict):
            key = (p.get("name", ""), p.get("in", ""))
            by_key[key] = p
    for p in op_params:
        if isinstance(p, dict):
            key = (p.get("name", ""), p.get("in", ""))
            by_key[key] = p
    return list(by_key.values())


# ============================================================
# Internal — Step Grouping
# ============================================================

def _validate_strategy(strategy: str) -> str:
    """Validate and normalize step strategy."""
    strategy = strategy.lower().strip()
    if strategy not in _VALID_STRATEGIES:
        raise ValueError(
            f"Unknown step strategy '{strategy}'. "
            f"Valid options: {', '.join(sorted(_VALID_STRATEGIES))}"
        )
    return strategy


def _group_by_tag(
    operations: List[Dict],
    step_prefix: str,
) -> Dict[str, List[Dict]]:
    """Group operations by their first tag. Untagged ops go to 'Untagged'."""
    buckets: Dict[str, List[Dict]] = {}
    tag_order: List[str] = []

    for op in operations:
        tags = op.get("tags", [])
        tag = tags[0] if tags else "Untagged"
        if tag not in buckets:
            tag_order.append(tag)
        buckets.setdefault(tag, []).append(op)

    grouped: Dict[str, List[Dict]] = {}
    for idx, tag in enumerate(tag_order, start=1):
        step_label = f"{step_prefix} {idx}: {tag}"
        grouped[step_label] = buckets[tag]

    return grouped


def _group_by_path(
    operations: List[Dict],
    step_prefix: str,
) -> Dict[str, List[Dict]]:
    """Group operations by first meaningful path segment."""
    buckets: Dict[str, List[Dict]] = {}
    segment_order: List[str] = []

    for op in operations:
        parts = op["path"].strip("/").split("/")
        first_segment = parts[0] if parts else "root"
        if first_segment.startswith("{") and len(parts) > 1:
            first_segment = parts[1]
        elif first_segment.startswith("{"):
            first_segment = "root"

        if first_segment not in buckets:
            segment_order.append(first_segment)
        buckets.setdefault(first_segment, []).append(op)

    grouped: Dict[str, List[Dict]] = {}
    for idx, segment in enumerate(segment_order, start=1):
        step_label = f"{step_prefix} {idx}: /{segment}"
        grouped[step_label] = buckets[segment]

    return grouped


def _group_single_step(
    operations: List[Dict],
    step_prefix: str,
) -> Dict[str, List[Dict]]:
    """Place all operations into a single step."""
    return {f"{step_prefix} 1: All Endpoints": operations}


# ============================================================
# Internal — URL and Header Construction
# ============================================================

def _build_url_and_headers(
    path: str,
    parameters: List[Dict],
    base_url: str,
    spec: Dict,
) -> Tuple[str, Dict[str, str]]:
    """
    Build the full URL and extract header parameters.

    Substitutes path parameters with sample values, appends query
    parameters, and collects header parameters separately.

    Returns:
        (url, header_params_dict)
    """
    url_path = path
    query_params: Dict[str, str] = {}
    header_params: Dict[str, str] = {}

    for param in parameters:
        param_in = param.get("in", "")
        param_name = param.get("name", "")
        schema = param.get("schema", {})
        if not param_name:
            continue

        value = _generate_sample_value(
            schema if isinstance(schema, dict) else {},
            spec,
            for_request=True,
        )
        if value is None:
            value = "sample"

        str_value = (
            ",".join(str(v) for v in value)
            if isinstance(value, list)
            else str(value)
        )

        if param_in == "path":
            url_path = url_path.replace(f"{{{param_name}}}", str_value)
        elif param_in == "query":
            query_params[param_name] = str_value
        elif param_in == "header":
            header_params[param_name.lower()] = str_value

    full_url = base_url.rstrip("/") + "/" + url_path.lstrip("/")
    if query_params:
        full_url += "?" + urlencode(query_params)

    return full_url, header_params


# ============================================================
# Internal — Entry Conversion
# ============================================================

def _convert_operation_to_capture_entry(
    operation: Dict,
    spec: Dict,
    base_url: str,
    step_number: int,
    step_label: str,
) -> Dict[str, Any]:
    """Convert a single extracted operation to the canonical capture format."""
    url, header_params = _build_url_and_headers(
        operation["path"], operation["parameters"], base_url, spec,
    )

    post_data = ""
    request_body = operation.get("request_body")
    if request_body and isinstance(request_body, dict):
        content = request_body.get("content", {})
        content_type, body_schema = _pick_content_type(content)
        if body_schema:
            resolved = _resolve_schema(body_schema, spec)
            sample = _generate_sample_value(
                resolved, spec, for_request=True,
            )
            if sample is not None:
                post_data = json.dumps(sample, ensure_ascii=False)
        header_params.setdefault("content-type", content_type)

    status_code, response_body = _get_success_response(
        operation.get("responses", {}), spec,
    )

    response_str = ""
    if response_body is not None:
        response_str = json.dumps(response_body, ensure_ascii=False)

    return {
        "request_id": str(uuid.uuid4()),
        "method": operation["method"],
        "url": url,
        "headers": header_params,
        "post_data": post_data,
        "step": _create_step_metadata(step_number, step_label),
        "response": response_str,
        "log_timestamp": datetime.utcnow().isoformat(),
        "status": status_code,
        "response_headers": {"content-type": "application/json"},
    }


def _pick_content_type(content: Dict) -> Tuple[str, Dict]:
    """Pick the preferred content type and schema from a content map."""
    preferred = ["application/json", "text/json"]
    for ct in preferred:
        if ct in content:
            return ct, content[ct].get("schema", {})
    for ct, media in content.items():
        if isinstance(media, dict):
            return ct, media.get("schema", {})
    return "application/json", {}


def _get_success_response(
    responses: Dict,
    spec: Dict,
) -> Tuple[int, Optional[Any]]:
    """
    Get the first success (2xx) response status and generated body.

    Returns:
        (status_code, sample_body_or_None)
    """
    for code in sorted(responses.keys()):
        try:
            code_int = int(code)
        except (ValueError, TypeError):
            continue

        if 200 <= code_int < 300:
            resp = responses[code]
            if not isinstance(resp, dict):
                return code_int, None
            content = resp.get("content", {})
            if content:
                _, resp_schema = _pick_content_type(content)
                if resp_schema:
                    resolved = _resolve_schema(resp_schema, spec)
                    sample = _generate_sample_value(
                        resolved, spec, for_request=False,
                    )
                    return code_int, sample
            return code_int, None

    return 200, None


# ============================================================
# Internal — Output (duplicated from playwright_adapter / har_adapter
# to avoid coupling to private internals)
# ============================================================

def _create_step_metadata(
    step_number: int, instructions: str,
) -> Dict[str, Any]:
    """Build the 'step' metadata payload used for each network entry."""
    return {
        "step_number": step_number,
        "instructions": instructions,
        "timestamp": datetime.utcnow().isoformat(),
    }


def _get_network_capture_output_path(
    run_id: str,
    timestamp: Optional[str] = None,
) -> str:
    """Compute the path for the network capture JSON file."""
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    base_dir = os.path.join(
        ARTIFACTS_PATH, str(run_id), "jmeter", "network-capture",
    )
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, f"network_capture_{timestamp}.json")


def _write_step_network_capture(
    per_step_requests: Dict[str, List[Dict[str, Any]]],
    run_id: str,
    timestamp: Optional[str] = None,
) -> str:
    """Write the step-aware mapping to a network capture JSON file."""
    output_path = _get_network_capture_output_path(run_id, timestamp)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(per_step_requests, f, indent=2, ensure_ascii=False)
    return output_path


# ============================================================
# Internal — Validation
# ============================================================

def _validate_capture_output(per_step_data: Dict[str, List[Dict]]) -> None:
    """
    Validate output against the canonical network capture schema.

    Raises:
        ValueError: With descriptive message if schema is invalid.
    """
    if not isinstance(per_step_data, dict):
        raise ValueError(
            "Capture output must be a dict (step_label -> entries)"
        )

    for step_label, entries in per_step_data.items():
        if not isinstance(step_label, str):
            raise ValueError(
                f"Step label must be a string, got: {type(step_label)}"
            )

        if not isinstance(entries, list):
            raise ValueError(
                f"Entries for '{step_label}' must be a list, "
                f"got: {type(entries)}"
            )

        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"Entry {idx} in '{step_label}' must be a dict, "
                    f"got: {type(entry)}"
                )
            for field, expected_type in _REQUIRED_ENTRY_FIELDS.items():
                if field not in entry:
                    raise ValueError(
                        f"Entry {idx} in '{step_label}' missing required "
                        f"field: '{field}'"
                    )
                if not isinstance(entry[field], expected_type):
                    raise ValueError(
                        f"Entry {idx} in '{step_label}' field '{field}' "
                        f"expected {expected_type.__name__}, "
                        f"got {type(entry[field]).__name__}"
                    )


# ============================================================
# Internal — Manifest
# ============================================================

def _write_capture_manifest(
    run_id: str,
    source_file: str,
    spec_version: str,
    spec_title: str,
    step_strategy: str,
    operations_total: int,
    operations_deprecated_skipped: int,
    operations_captured: int,
    base_url: str,
) -> str:
    """
    Write capture_manifest.json alongside the network capture file.

    Records provenance for the Swagger/OpenAPI conversion.
    """
    manifest = {
        "source_type": "openapi",
        "source_file": source_file,
        "conversion_tool": "convert_swagger_to_capture",
        "conversion_timestamp": datetime.utcnow().isoformat(),
        "step_strategy": step_strategy,
        "operations_total": operations_total,
        "operations_deprecated_skipped": operations_deprecated_skipped,
        "operations_captured": operations_captured,
        "spec_version": spec_version,
        "spec_title": spec_title,
        "base_url": base_url,
    }

    base_dir = os.path.join(ARTIFACTS_PATH, str(run_id), "jmeter")
    os.makedirs(base_dir, exist_ok=True)
    manifest_path = os.path.join(base_dir, "capture_manifest.json")

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    logger.info("Capture manifest written: %s", manifest_path)
    return manifest_path
