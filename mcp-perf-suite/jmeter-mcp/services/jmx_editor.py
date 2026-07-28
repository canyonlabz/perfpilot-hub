"""
services/jmx_editor.py

Core service for analysing, adding to, and editing existing JMeter JMX files.
Implements the three HITL (Human-in-the-Loop) operations:
  - analyze_jmx_file   -> parse, index, and summarise a JMX script
  - add_jmx_component  -> insert a new component into an existing JMX
  - edit_jmx_component -> apply patch operations to an existing component

All mutations are safe-by-default: backups are created before writes and
a dry_run flag lets the caller preview changes without persisting them.
"""

import glob
import hashlib
import os
import re
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from xml.dom import minidom

from utils.config import load_config
from utils.file_utils import get_jmeter_artifacts_dir
from services.helpers.analysis_export_helpers import export_structure_files

from services.jmx.component_registry import (
    build_component,
    list_supported_components,
    validate_component_config,
    COMPONENT_REGISTRY,
)

# Invalid XML character regex (same as file_utils.py)
_INVALID_XML_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]')

# JMeter variable reference pattern: ${varName} or ${__function(...)}
_VAR_REF_PATTERN = re.compile(r'\$\{([^}]+)\}')

# AI-generated script naming convention
_AI_GENERATED_PATTERN = "ai-generated_script_*.jmx"


# ============================================================
# Configuration helpers
# ============================================================

def _get_editing_config() -> dict:
    """Load jmx_editing config with safe defaults."""
    try:
        cfg = load_config()
        return cfg.get("jmx_editing", {})
    except Exception:
        return {}


def _should_create_backup() -> bool:
    return _get_editing_config().get("create_backup", True)


def _max_backup_count() -> int:
    return _get_editing_config().get("max_backup_count", 10)


# ============================================================
# JMX Discovery
# ============================================================

def discover_jmx_file(test_run_id: str, jmx_filename: str = "") -> str:
    """
    Locate the target JMX file inside artifacts/<test_run_id>/jmeter/.

    Resolution order:
      1. If jmx_filename is provided, look for that file in the jmeter dir.
      2. Otherwise, glob for ai-generated_script_*.jmx and pick the most
         recent one by modification time.

    Args:
        test_run_id: Test run identifier.
        jmx_filename: Optional filename (not full path) to look for.

    Returns:
        Absolute path to the discovered JMX file.

    Raises:
        FileNotFoundError: With a descriptive message if no file is found.
    """
    jmeter_dir = get_jmeter_artifacts_dir(test_run_id)

    if jmx_filename:
        target = os.path.join(jmeter_dir, jmx_filename)
        if os.path.isfile(target):
            return target
        raise FileNotFoundError(
            f"JMX file '{jmx_filename}' not found in {jmeter_dir}. "
            f"Please verify the filename and ensure it exists inside the "
            f"artifacts/{test_run_id}/jmeter/ directory."
        )

    pattern = os.path.join(jmeter_dir, _AI_GENERATED_PATTERN)
    matches = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if matches:
        return matches[0]

    all_jmx = sorted(
        glob.glob(os.path.join(jmeter_dir, "*.jmx")),
        key=os.path.getmtime,
        reverse=True,
    )
    if all_jmx:
        return all_jmx[0]

    raise FileNotFoundError(
        f"No JMX files found in {jmeter_dir}. "
        f"Please generate a script first using generate_jmeter_script, "
        f"or provide the jmx_filename parameter with the name of your "
        f"JMX script file."
    )


# ============================================================
# JMX Load / Save
# ============================================================

def load_jmx(jmx_path: str) -> Tuple[ET.ElementTree, ET.Element]:
    """Parse a JMX file and return (tree, root)."""
    tree = ET.parse(jmx_path)
    root = tree.getroot()
    return tree, root


def save_jmx(tree: ET.ElementTree, jmx_path: str) -> str:
    """Pretty-print and write a modified JMX back to disk."""
    root = tree.getroot()
    xml_bytes = ET.tostring(root, encoding="utf-8")
    xml_cleaned = _INVALID_XML_CHARS.sub('', xml_bytes.decode("utf-8"))
    pretty_xml = minidom.parseString(
        xml_cleaned.encode("utf-8")
    ).toprettyxml(indent="  ")

    with open(jmx_path, "w", encoding="utf-8") as f:
        f.write(pretty_xml)
    return jmx_path


# ============================================================
# Backup
# ============================================================

def create_backup(jmx_path: str, test_run_id: str) -> str:
    """
    Create a numbered backup of the JMX in artifacts/<test_run_id>/jmeter/backups/.

    Naming convention mirrors JMeter's own backup style:
      <original_stem>-000001.jmx, <original_stem>-000002.jmx, ...

    Returns:
        Absolute path to the backup file.
    """
    jmeter_dir = get_jmeter_artifacts_dir(test_run_id)
    backup_dir = os.path.join(jmeter_dir, "backups")
    os.makedirs(backup_dir, exist_ok=True)

    stem = os.path.splitext(os.path.basename(jmx_path))[0]

    existing = sorted(glob.glob(os.path.join(backup_dir, f"{stem}-*.jmx")))
    if existing:
        last = existing[-1]
        last_num_str = os.path.splitext(os.path.basename(last))[0].rsplit("-", 1)[-1]
        try:
            next_num = int(last_num_str) + 1
        except ValueError:
            next_num = 1
    else:
        next_num = 1

    backup_name = f"{stem}-{next_num:06d}.jmx"
    backup_path = os.path.join(backup_dir, backup_name)
    shutil.copy2(jmx_path, backup_path)

    max_count = _max_backup_count()
    all_backups = sorted(glob.glob(os.path.join(backup_dir, f"{stem}-*.jmx")))
    if len(all_backups) > max_count:
        for old in all_backups[:-max_count]:
            os.remove(old)

    return backup_path


# ============================================================
# Node Index
# ============================================================

def _generate_node_id(testclass: str, testname: str, path: str, index: int) -> str:
    """Generate a stable 10-char hex node_id."""
    raw = f"{testclass}|{testname}|{path}|{index}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def _is_test_element(elem: ET.Element) -> bool:
    """Return True if the element is a JMeter test element (has testclass or is a known tag)."""
    if elem.tag == "hashTree":
        return False
    if elem.get("testclass"):
        return True
    if elem.tag in ("jmeterTestPlan",):
        return False
    return False


def _extract_element_props(elem: ET.Element) -> dict:
    """Extract key properties from a JMeter element for the node index."""
    props = {}
    for child in elem:
        prop_name = child.get("name", "")
        if child.tag == "stringProp" and child.text:
            if any(kw in prop_name.lower() for kw in [
                "method", "domain", "protocol", "path", "port",
                "script", "condition", "loops", "filename",
                "variablenames", "variable", "delay", "duration",
                "throughput", "jsonpath", "regex", "refname",
                "scriptlanguage",
            ]):
                short_name = prop_name.rsplit(".", 1)[-1] if "." in prop_name else prop_name
                props[short_name] = child.text
        elif child.tag == "boolProp" and child.text:
            if any(kw in prop_name.lower() for kw in [
                "enabled", "postbodyraw", "continue_forever",
                "evaluateall", "useexpression",
            ]):
                short_name = prop_name.rsplit(".", 1)[-1] if "." in prop_name else prop_name
                props[short_name] = child.text
    return props


def build_node_index(root: ET.Element) -> Tuple[Dict[str, dict], list]:
    """
    Walk the JMX paired tree (element + hashTree) and build a flat node index
    plus a hierarchical outline.

    Returns:
        (node_index, hierarchy)
        - node_index: {node_id: {node_id, path, type, testname, enabled, props, children_count, children_by_type}}
        - hierarchy: nested list of dicts with "node_id", "type", "testname", "children"
    """
    node_index: Dict[str, dict] = {}
    hierarchy: list = []

    def _walk(parent_element: ET.Element, parent_path: str, output_list: list):
        children = list(parent_element)
        i = 0
        sibling_counts: Dict[str, int] = {}

        while i < len(children):
            elem = children[i]

            if not _is_test_element(elem):
                i += 1
                continue

            testclass = elem.get("testclass", elem.tag)
            testname = elem.get("testname", "")
            enabled = elem.get("enabled", "true")

            idx = sibling_counts.get(testclass, 0)
            sibling_counts[testclass] = idx + 1

            path = f"{parent_path} > {testclass}[{idx}]" if parent_path else f"{testclass}[{idx}]"
            node_id = _generate_node_id(testclass, testname, path, idx)

            props = _extract_element_props(elem)

            hash_tree = children[i + 1] if (i + 1 < len(children) and children[i + 1].tag == "hashTree") else None

            child_nodes: list = []
            children_by_type: Dict[str, int] = {}

            if hash_tree is not None:
                _walk(hash_tree, path, child_nodes)
                for cn in child_nodes:
                    ctype = cn["type"]
                    children_by_type[ctype] = children_by_type.get(ctype, 0) + 1

            node_info = {
                "node_id": node_id,
                "path": path,
                "type": testclass,
                "testname": testname,
                "enabled": enabled == "true",
                "props": props,
                "children_count": len(child_nodes),
                "children_by_type": children_by_type,
            }
            node_index[node_id] = node_info

            outline_entry = {
                "node_id": node_id,
                "type": testclass,
                "testname": testname,
                "enabled": enabled == "true",
                "children": child_nodes,
            }
            output_list.append(outline_entry)

            i += 2 if hash_tree is not None else 1

    top_hash_tree = root.find("hashTree")
    if top_hash_tree is not None:
        _walk(top_hash_tree, "", hierarchy)

    return node_index, hierarchy


def find_element_by_node_id(
    root: ET.Element,
    target_node_id: str,
    node_index: Dict[str, dict]
) -> Optional[Tuple[ET.Element, Optional[ET.Element], ET.Element]]:
    """
    Find the (element, hashTree, parent_hashTree) triple for a given node_id.

    Walks the paired tree with full path accumulation (mirroring
    build_node_index) so that regenerated node_ids are identical to the
    ones stored in the index.

    Returns None if not found.
    """
    if target_node_id not in node_index:
        return None

    def _search(parent_ht: ET.Element, parent_path: str):
        children = list(parent_ht)
        i = 0
        sibling_counts: Dict[str, int] = {}

        while i < len(children):
            elem = children[i]
            if not _is_test_element(elem):
                i += 1
                continue

            testclass = elem.get("testclass", elem.tag)
            testname = elem.get("testname", "")

            idx = sibling_counts.get(testclass, 0)
            sibling_counts[testclass] = idx + 1

            path = f"{parent_path} > {testclass}[{idx}]" if parent_path else f"{testclass}[{idx}]"
            nid = _generate_node_id(testclass, testname, path, idx)

            hash_tree = children[i + 1] if (i + 1 < len(children) and children[i + 1].tag == "hashTree") else None

            if nid == target_node_id:
                return (elem, hash_tree, parent_ht)

            if hash_tree is not None:
                result = _search(hash_tree, path)
                if result:
                    return result

            i += 2 if hash_tree is not None else 1
        return None

    top_hash_tree = root.find("hashTree")
    if top_hash_tree is not None:
        return _search(top_hash_tree, "")
    return None


# ============================================================
# Variable Scanning
# ============================================================

def _scan_variables(root: ET.Element) -> dict:
    """
    Scan the JMX for defined and referenced variables.

    Returns:
        {
            "defined": {var_name: source_description, ...},
            "used": [var_name, ...],
            "undefined": [var_name, ...]  (used but not defined)
        }
    """
    defined: Dict[str, str] = {}
    used_set: set = set()

    def _scan_element(elem: ET.Element):
        testclass = elem.get("testclass", "")

        # UDV definitions
        if testclass == "Arguments":
            for arg_prop in elem.iter("elementProp"):
                if arg_prop.get("elementType") == "Argument":
                    name_prop = arg_prop.find("stringProp[@name='Argument.name']")
                    if name_prop is not None and name_prop.text:
                        defined[name_prop.text] = f"UDV: {elem.get('testname', 'User Defined Variables')}"

        # CSV Data Set variable definitions
        if testclass == "CSVDataSet":
            var_names_prop = elem.find("stringProp[@name='variableNames']")
            if var_names_prop is not None and var_names_prop.text:
                for vn in var_names_prop.text.split(","):
                    vn = vn.strip()
                    if vn:
                        defined[vn] = f"CSV: {elem.get('testname', 'CSV Data Set')}"

        # Scan all text content for ${...} references
        for child in elem.iter():
            if child.text:
                for match in _VAR_REF_PATTERN.finditer(child.text):
                    ref = match.group(1)
                    if not ref.startswith("__"):
                        used_set.add(ref)
            if child.tail:
                for match in _VAR_REF_PATTERN.finditer(child.tail):
                    ref = match.group(1)
                    if not ref.startswith("__"):
                        used_set.add(ref)

        # Scan attribute values
        for attr_val in elem.attrib.values():
            for match in _VAR_REF_PATTERN.finditer(attr_val):
                ref = match.group(1)
                if not ref.startswith("__"):
                    used_set.add(ref)

    for elem in root.iter():
        _scan_element(elem)

    used_list = sorted(used_set)
    undefined = sorted(used_set - set(defined.keys()))

    return {
        "defined": defined,
        "used": used_list,
        "undefined": undefined,
    }


# ============================================================
# Analyze
# ============================================================

async def analyze_jmx_file(
    test_run_id: str,
    jmx_filename: str,
    detail_level: str,
    ctx,
    export_structure: bool = True,
    output_format: str = "both",
) -> dict:
    """
    Parse and analyse a JMX file, returning structure, summary, and variable info.

    Args:
        test_run_id: Test run identifier.
        jmx_filename: Optional filename override (empty for auto-discover).
        detail_level: "summary", "detailed", or "full".
        ctx: FastMCP context.
        export_structure: If True, persist analysis to versioned files.
        output_format: "json", "markdown", or "both" (only when export_structure is True).

    Returns:
        dict with status, hierarchy, node_index, summary, variables, exported_files.
    """
    try:
        jmx_path = discover_jmx_file(test_run_id, jmx_filename)
    except FileNotFoundError as e:
        return {
            "status": "ERROR",
            "message": str(e),
            "test_run_id": test_run_id,
        }

    try:
        tree, root = load_jmx(jmx_path)
    except ET.ParseError as e:
        return {
            "status": "ERROR",
            "message": f"Failed to parse JMX file: {e}",
            "test_run_id": test_run_id,
            "jmx_path": jmx_path,
        }

    node_index, hierarchy = build_node_index(root)

    # Build summary counts
    type_counts: Dict[str, int] = {}
    for info in node_index.values():
        t = info["type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    summary = {
        "total_elements": len(node_index),
        "by_type": dict(sorted(type_counts.items())),
    }

    # When exporting, ensure node_index is built even if caller requested "summary"
    export_detail = detail_level
    if export_structure and detail_level == "summary":
        export_detail = "detailed"

    # Build node_index_output for detailed/full (or when export needs it)
    node_index_output: Optional[dict] = None
    if detail_level in ("detailed", "full") or export_detail in ("detailed", "full"):
        node_index_output = {}
        for nid, info in node_index.items():
            entry = {
                "node_id": info["node_id"],
                "path": info["path"],
                "type": info["type"],
                "testname": info["testname"],
                "enabled": info["enabled"],
                "children_count": info["children_count"],
                "children_by_type": info["children_by_type"],
            }
            if detail_level == "full":
                entry["props"] = info["props"]
            node_index_output[nid] = entry

    # Build variables for detailed/full (or when export needs it)
    variables: Optional[dict] = None
    if detail_level in ("detailed", "full") or export_detail in ("detailed", "full"):
        variables = _scan_variables(root)

    # Build human-readable outline
    outline = _build_outline_text(hierarchy)

    # Assemble result dict — summary-only response to minimise context usage.
    # Full hierarchy, node_index, and variables are persisted to the exported
    # JSON file; agents should read that file for detailed lookups.
    result: Dict[str, Any] = {
        "status": "OK",
        "message": f"Analysis complete for {os.path.basename(jmx_path)}",
        "test_run_id": test_run_id,
        "jmx_path": jmx_path,
        "jmx_filename": os.path.basename(jmx_path),
        "detail_level": detail_level,
        "summary": summary,
        "outline": outline,
    }

    # Export structure files when requested
    if export_structure:
        exported = export_structure_files(
            test_run_id=test_run_id,
            jmx_path=jmx_path,
            detail_level=export_detail,
            summary=summary,
            hierarchy=hierarchy,
            node_index_output=node_index_output,
            variables=variables,
            outline=outline,
            output_format=output_format,
        )
        result["exported_files"] = exported

    return result


def _build_outline_text(hierarchy: list, indent: int = 0) -> str:
    """Build a human-readable indented outline string."""
    lines = []
    prefix = "  " * indent
    for node in hierarchy:
        enabled_marker = "" if node["enabled"] else " [DISABLED]"
        line = f"{prefix}- [{node['type']}] {node['testname']}{enabled_marker}  (id: {node['node_id']})"
        lines.append(line)
        if node.get("children"):
            lines.append(_build_outline_text(node["children"], indent + 1))
    return "\n".join(lines)


# ============================================================
# Add Component
# ============================================================

async def add_jmx_component(
    test_run_id: str,
    component_type: str,
    parent_node_id: str,
    component_config: dict,
    jmx_filename: str,
    position: str,
    dry_run: bool,
    ctx,
) -> dict:
    """
    Add a new JMeter component under a target parent node.

    Args:
        test_run_id: Test run identifier.
        component_type: Registry key (e.g., "loop_controller").
        parent_node_id: node_id of the parent element (from analyze output).
        component_config: Dict of component configuration.
        jmx_filename: Optional filename override.
        position: "first" or "last" (insertion position within parent's hashTree).
        dry_run: If True, validate and preview without saving.
        ctx: FastMCP context.

    Returns:
        dict with status, change summary, backup path, new node_id.
    """
    # Validate component type and config first
    is_valid, errors = validate_component_config(component_type, component_config)
    if not is_valid:
        return {
            "status": "ERROR",
            "message": f"Invalid component configuration: {'; '.join(errors)}",
            "test_run_id": test_run_id,
        }

    try:
        jmx_path = discover_jmx_file(test_run_id, jmx_filename)
    except FileNotFoundError as e:
        return {
            "status": "ERROR",
            "message": str(e),
            "test_run_id": test_run_id,
        }

    try:
        tree, root = load_jmx(jmx_path)
    except ET.ParseError as e:
        return {
            "status": "ERROR",
            "message": f"Failed to parse JMX file: {e}",
            "test_run_id": test_run_id,
            "jmx_path": jmx_path,
        }

    node_index, _ = build_node_index(root)

    # Find parent element
    found = find_element_by_node_id(root, parent_node_id, node_index)
    if found is None:
        available_ids = [
            f"  {nid}: [{info['type']}] {info['testname']}"
            for nid, info in list(node_index.items())[:20]
        ]
        hint = "\n".join(available_ids)
        return {
            "status": "ERROR",
            "message": (
                f"Parent node_id '{parent_node_id}' not found. "
                f"Run analyze_jmeter_script first to get valid node_ids. "
                f"Available nodes (first 20):\n{hint}"
            ),
            "test_run_id": test_run_id,
            "jmx_path": jmx_path,
        }

    parent_elem, parent_hash_tree, _ = found
    if parent_hash_tree is None:
        return {
            "status": "ERROR",
            "message": (
                f"Parent node '{parent_node_id}' ({parent_elem.get('testname', '')}) "
                f"does not have a hashTree for children. This component type may not "
                f"support child elements."
            ),
            "test_run_id": test_run_id,
            "jmx_path": jmx_path,
        }

    # Build the new component
    try:
        new_elem, new_hash_tree = build_component(component_type, component_config)
    except ValueError as e:
        return {
            "status": "ERROR",
            "message": str(e),
            "test_run_id": test_run_id,
            "jmx_path": jmx_path,
        }

    new_testname = new_elem.get("testname", component_type)
    new_testclass = new_elem.get("testclass", new_elem.tag)

    change_summary = {
        "operation": "add",
        "component_type": component_type,
        "testclass": new_testclass,
        "testname": new_testname,
        "parent_node_id": parent_node_id,
        "parent_testname": parent_elem.get("testname", ""),
        "position": position,
    }

    if dry_run:
        return {
            "status": "OK",
            "message": f"[DRY RUN] Would add '{new_testname}' ({component_type}) "
                       f"as {position} child of '{parent_elem.get('testname', '')}'.",
            "test_run_id": test_run_id,
            "jmx_path": jmx_path,
            "dry_run": True,
            "change_summary": change_summary,
        }

    # Insert into parent's hashTree
    if position == "first":
        parent_hash_tree.insert(0, new_hash_tree)
        parent_hash_tree.insert(0, new_elem)
    else:
        parent_hash_tree.append(new_elem)
        parent_hash_tree.append(new_hash_tree)

    # Backup and save
    backup_path = None
    if _should_create_backup():
        backup_path = create_backup(jmx_path, test_run_id)

    save_jmx(tree, jmx_path)

    # Rebuild index to get the new node_id
    _, _ = build_node_index(root)

    return {
        "status": "OK",
        "message": f"Added '{new_testname}' ({component_type}) to "
                   f"'{parent_elem.get('testname', '')}' (position: {position}).",
        "test_run_id": test_run_id,
        "jmx_path": jmx_path,
        "jmx_filename": os.path.basename(jmx_path),
        "backup_path": backup_path,
        "dry_run": False,
        "change_summary": change_summary,
    }


# ============================================================
# Edit Component
# ============================================================

# Supported edit operation types
_SUPPORTED_OPS = {"rename", "set_prop", "replace_in_body", "toggle_enabled"}


async def edit_jmx_component(
    test_run_id: str,
    target_node_id: str,
    operations: list,
    jmx_filename: str,
    dry_run: bool,
    ctx,
) -> dict:
    """
    Apply one or more edit operations to an existing JMeter component.

    Args:
        test_run_id: Test run identifier.
        target_node_id: node_id of the component to edit (from analyze output).
        operations: List of operation dicts. Each must have an "op" key.
            Supported ops:
              - {"op": "rename", "value": "New Name"}
              - {"op": "set_prop", "name": "HTTPSampler.method", "value": "POST"}
              - {"op": "replace_in_body", "find": "old_text", "replace": "new_text"}
              - {"op": "toggle_enabled", "value": true/false}
        jmx_filename: Optional filename override.
        dry_run: If True, validate and preview without saving.
        ctx: FastMCP context.

    Returns:
        dict with status, change summary (before/after), backup path.
    """
    # Validate operations
    if not operations:
        return {
            "status": "ERROR",
            "message": "No operations provided. Supply at least one operation.",
            "test_run_id": test_run_id,
        }

    for i, op in enumerate(operations):
        if not isinstance(op, dict) or "op" not in op:
            return {
                "status": "ERROR",
                "message": f"Operation at index {i} is missing the 'op' field.",
                "test_run_id": test_run_id,
            }
        if op["op"] not in _SUPPORTED_OPS:
            return {
                "status": "ERROR",
                "message": (
                    f"Unsupported operation '{op['op']}' at index {i}. "
                    f"Supported: {sorted(_SUPPORTED_OPS)}"
                ),
                "test_run_id": test_run_id,
            }

    try:
        jmx_path = discover_jmx_file(test_run_id, jmx_filename)
    except FileNotFoundError as e:
        return {
            "status": "ERROR",
            "message": str(e),
            "test_run_id": test_run_id,
        }

    try:
        tree, root = load_jmx(jmx_path)
    except ET.ParseError as e:
        return {
            "status": "ERROR",
            "message": f"Failed to parse JMX file: {e}",
            "test_run_id": test_run_id,
            "jmx_path": jmx_path,
        }

    node_index, _ = build_node_index(root)

    found = find_element_by_node_id(root, target_node_id, node_index)
    if found is None:
        available_ids = [
            f"  {nid}: [{info['type']}] {info['testname']}"
            for nid, info in list(node_index.items())[:20]
        ]
        hint = "\n".join(available_ids)
        return {
            "status": "ERROR",
            "message": (
                f"Target node_id '{target_node_id}' not found. "
                f"Run analyze_jmeter_script first to get valid node_ids. "
                f"Available nodes (first 20):\n{hint}"
            ),
            "test_run_id": test_run_id,
            "jmx_path": jmx_path,
        }

    target_elem, target_hash_tree, _ = found
    changes: List[dict] = []

    for op in operations:
        op_type = op["op"]

        if op_type == "rename":
            old_name = target_elem.get("testname", "")
            new_name = op.get("value", "")
            target_elem.set("testname", new_name)
            changes.append({
                "op": "rename",
                "before": old_name,
                "after": new_name,
            })

        elif op_type == "set_prop":
            prop_name = op.get("name", "")
            prop_value = str(op.get("value", ""))
            prop_type = op.get("prop_type", "stringProp")

            _PROP_TYPES = ("stringProp", "intProp", "boolProp", "longProp")

            existing = target_elem.find(f"{prop_type}[@name='{prop_name}']")
            if existing is None:
                for alt_type in _PROP_TYPES:
                    if alt_type == prop_type:
                        continue
                    existing = target_elem.find(f"{alt_type}[@name='{prop_name}']")
                    if existing is not None:
                        break

            if existing is not None:
                old_val = existing.text or ""
                old_tag = existing.tag
                existing.text = prop_value
                note = {}
                if old_tag != prop_type:
                    note["note"] = f"found as {old_tag}, updated in place"
                changes.append({
                    "op": "set_prop",
                    "name": prop_name,
                    "before": old_val,
                    "after": prop_value,
                    **note,
                })
            else:
                ET.SubElement(target_elem, prop_type, attrib={
                    "name": prop_name
                }).text = prop_value
                changes.append({
                    "op": "set_prop",
                    "name": prop_name,
                    "before": None,
                    "after": prop_value,
                    "note": "property created (did not exist)",
                })

        elif op_type == "replace_in_body":
            find_str = op.get("find", "")
            replace_str = op.get("replace", "")
            replaced = False

            for arg_value in target_elem.iter("stringProp"):
                if arg_value.get("name") == "Argument.value" and arg_value.text:
                    if find_str in arg_value.text:
                        old_body = arg_value.text
                        arg_value.text = arg_value.text.replace(find_str, replace_str)
                        changes.append({
                            "op": "replace_in_body",
                            "find": find_str,
                            "replace": replace_str,
                            "occurrences": old_body.count(find_str),
                        })
                        replaced = True

            if not replaced:
                changes.append({
                    "op": "replace_in_body",
                    "find": find_str,
                    "replace": replace_str,
                    "note": f"Pattern '{find_str}' not found in request body",
                    "occurrences": 0,
                })

        elif op_type == "toggle_enabled":
            old_enabled = target_elem.get("enabled", "true")
            new_enabled = str(op.get("value", True)).lower()
            target_elem.set("enabled", new_enabled)
            changes.append({
                "op": "toggle_enabled",
                "before": old_enabled,
                "after": new_enabled,
            })

    change_summary = {
        "target_node_id": target_node_id,
        "target_testname": target_elem.get("testname", ""),
        "target_type": target_elem.get("testclass", target_elem.tag),
        "operations_applied": len(changes),
        "changes": changes,
    }

    if dry_run:
        return {
            "status": "OK",
            "message": (
                f"[DRY RUN] Would apply {len(changes)} operation(s) to "
                f"'{target_elem.get('testname', '')}' ({target_elem.get('testclass', '')})."
            ),
            "test_run_id": test_run_id,
            "jmx_path": jmx_path,
            "dry_run": True,
            "change_summary": change_summary,
        }

    # Backup and save
    backup_path = None
    if _should_create_backup():
        backup_path = create_backup(jmx_path, test_run_id)

    save_jmx(tree, jmx_path)

    return {
        "status": "OK",
        "message": (
            f"Applied {len(changes)} operation(s) to "
            f"'{target_elem.get('testname', '')}' ({target_elem.get('testclass', '')})."
        ),
        "test_run_id": test_run_id,
        "jmx_path": jmx_path,
        "jmx_filename": os.path.basename(jmx_path),
        "backup_path": backup_path,
        "dry_run": False,
        "change_summary": change_summary,
    }
