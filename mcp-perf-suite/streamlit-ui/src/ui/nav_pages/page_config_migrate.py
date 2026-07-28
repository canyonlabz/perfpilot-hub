"""
Config Migration Page - Transfer settings between MCP suite repo instances.

Extracts user-customized settings from a source repo and applies them
to a fresh destination repo, with diff preview, path adjustment, and
flagged-field detection.
"""

import streamlit as st
import pandas as pd
from pathlib import Path

from src.ui.page_header import render_page_header
from src.ui.page_utils import render_page_title
from src.ui.page_styles import inject_migration_styles
from src.utils.state import (
    MIGRATION_SOURCE_PATH,
    MIGRATION_DEST_PATH,
)
from src.services.config_migrator import (
    scan_source,
    scan_destination,
    compute_deltas,
    adjust_paths,
    preview_migration,
    apply_migration,
    detect_flagged_fields,
)


# Session state keys specific to this page
_SCAN_SOURCE = "migration_scan_source"
_SCAN_DEST = "migration_scan_dest"
_DELTAS = "migration_deltas"
_PREVIEW = "migration_preview"
_RESULTS = "migration_results"


def render_ui():
    render_page_header()
    render_page_title(
        "Configuration Migration",
        "Transfer settings from an existing repo to a new instance"
    )
    inject_migration_styles()

    # ── Path Inputs ──
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### Source Repo (existing, configured)")
        source_path = st.text_input(
            "Source repo path",
            value=st.session_state.get(MIGRATION_SOURCE_PATH, ""),
            key="source_path_input",
            placeholder="C:\\Users\\...\\mcp-perf-suite (configured instance)",
            label_visibility="collapsed",
        )
        st.session_state[MIGRATION_SOURCE_PATH] = source_path
        if source_path and Path(source_path).exists():
            st.caption(":material/check_circle: Path exists")
        elif source_path:
            st.caption(":material/error: Path does not exist")

    with col2:
        st.markdown("#### Destination Repo (new, unconfigured)")
        dest_path = st.text_input(
            "Destination repo path",
            value=st.session_state.get(MIGRATION_DEST_PATH, ""),
            key="dest_path_input",
            placeholder="C:\\Users\\...\\mcp-perf-suite-new (fresh copy)",
            label_visibility="collapsed",
        )
        st.session_state[MIGRATION_DEST_PATH] = dest_path
        if dest_path and Path(dest_path).exists():
            st.caption(":material/check_circle: Path exists")
        elif dest_path:
            st.caption(":material/error: Path does not exist")

    # ── Action Buttons ──
    st.markdown("---")

    btn_col1, btn_col2, btn_col3, btn_spacer = st.columns([0.12, 0.15, 0.18, 0.55])

    with btn_col1:
        scan_clicked = st.button("Scan", key="btn_scan", use_container_width=True)

    with btn_col2:
        preview_clicked = st.button("Preview", key="btn_preview", use_container_width=True)

    with btn_col3:
        apply_clicked = st.button(
            "Apply Migration", key="btn_apply", use_container_width=True,
            type="primary",
        )

    # ── Scan ──
    if scan_clicked:
        if not source_path or not dest_path:
            st.error("Please provide both source and destination repo paths.")
        elif not Path(source_path).exists():
            st.error(f"Source path does not exist: `{source_path}`")
        elif not Path(dest_path).exists():
            st.error(f"Destination path does not exist: `{dest_path}`")
        else:
            with st.spinner("Scanning source and destination repos..."):
                source_results = scan_source(source_path)
                dest_results = scan_destination(dest_path)

                st.session_state[_SCAN_SOURCE] = source_results
                st.session_state[_SCAN_DEST] = dest_results

                # Compute deltas
                deltas = compute_deltas(source_results, source_path)
                # Adjust paths
                deltas = adjust_paths(deltas, source_path, dest_path)
                st.session_state[_DELTAS] = deltas

                # Clear previous preview / results
                st.session_state.pop(_PREVIEW, None)
                st.session_state.pop(_RESULTS, None)

            st.success("Scan complete!")

    # ── Display Scan Results ──
    source_results = st.session_state.get(_SCAN_SOURCE)
    dest_results = st.session_state.get(_SCAN_DEST)
    deltas = st.session_state.get(_DELTAS)

    if source_results:
        _render_scan_results(source_results, dest_results)

    # ── Preview ──
    if preview_clicked:
        if not deltas:
            st.warning("Scan the repos first to compute deltas.")
        else:
            preview = preview_migration(deltas, dest_results or {})
            st.session_state[_PREVIEW] = preview

    preview_data = st.session_state.get(_PREVIEW)
    if preview_data:
        _render_preview(preview_data, deltas)

    # ── Apply ──
    if apply_clicked:
        if not deltas:
            st.warning("Scan and preview the migration first.")
        else:
            with st.spinner("Applying migration..."):
                results = apply_migration(deltas, dest_path)
                st.session_state[_RESULTS] = results

    results = st.session_state.get(_RESULTS)
    if results:
        _render_results(results)

    # ── Instructions ──
    st.markdown("---")
    with st.expander("How does migration work?", expanded=False):
        st.markdown("""
        **Config Migration** transfers your customized settings from one MCP suite
        instance to another. This is useful when:

        - You receive a fresh copy of the repo and need to reconfigure it
        - You're setting up a new team member's environment
        - You're migrating to a new machine

        **How it works:**

        1. **Scan** - Discovers all configured YAML/JSON files in the source repo
           and compares them against the example templates to detect customizations
        2. **Preview** - Shows what will change per server/file, with path values
           auto-adjusted for the new location, and flagged fields for manual review
        3. **Apply** - Writes the migrated config files to the destination repo
           (existing files are backed up with timestamps)

        **What gets migrated:**
        - All user-customized settings (tuning parameters, thresholds, etc.)
        - Environment configurations (Datadog environments, SLA profiles, etc.)
        - Path values are auto-adjusted to the destination repo location

        **What gets flagged for review:**
        - Service-specific IDs (SharePoint site IDs, Teams channel IDs, etc.)
        - These may differ between environments and need manual verification
        """)


# ---------------------------------------------------------------------------
# Scan Results
# ---------------------------------------------------------------------------

def _render_scan_results(source_results: dict, dest_results: dict):
    """Display scan results with server/file breakdown."""
    st.markdown("#### Scan Results")

    src_col, dst_col = st.columns(2)

    with src_col:
        st.markdown("##### Source Repo")
        total_source_files = sum(len(v) for v in source_results.values())
        st.caption(f"{len(source_results)} servers, {total_source_files} config files found")

        for server_name, configs in source_results.items():
            with st.expander(f"{server_name} ({len(configs)} files)"):
                for cfg in configs:
                    icon = ":material/check_circle:" if cfg.get("has_example") else ":material/info:"
                    st.markdown(
                        f"{icon} **{cfg['file']}** "
                        f"{'(has example)' if cfg.get('has_example') else '(no example - full copy)'}"
                    )

    with dst_col:
        st.markdown("##### Destination Repo")
        if dest_results:
            total_dest = sum(len(v) for v in dest_results.values())
            st.caption(f"{len(dest_results)} servers, {total_dest} template slots found")

            for server_name, templates in dest_results.items():
                with st.expander(f"{server_name} ({len(templates)} files)"):
                    for tmpl in templates:
                        if tmpl.get("config_exists"):
                            icon = ":material/warning:"
                            note = "(config exists - will be backed up)"
                        elif tmpl.get("example_exists"):
                            icon = ":material/check_circle:"
                            note = "(example available)"
                        else:
                            icon = ":material/info:"
                            note = "(no example)"
                        st.markdown(f"{icon} **{tmpl['file']}** {note}")
        else:
            st.info("No destination repo scanned yet.")


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def _render_preview(preview_data: list[dict], deltas: dict):
    """Display migration preview with actions and flagged fields."""
    st.markdown("---")
    st.markdown("#### Migration Preview")

    # Summary table
    rows = []
    for action in preview_data:
        rows.append({
            "Server": action["server"],
            "File": action["file"],
            "Action": action["action"].upper(),
            "Details": action["details"],
            "Dest Exists": "Yes" if action.get("dest_exists") else "No",
            "Flagged Fields": len(action.get("flagged_fields", [])),
        })

    preview_df = pd.DataFrame(rows)

    def _color_action(val):
        if val == "MERGE":
            return "color: #2ecc40; font-weight: bold"
        elif val == "COPY":
            return "color: #ffdc00; font-weight: bold"
        elif val == "SKIP":
            return "color: #aaaaaa"
        return ""

    styled = preview_df.style.map(_color_action, subset=["Action"])
    st.dataframe(styled, use_container_width=True, hide_index=True)

    # Flagged fields
    all_flagged = []
    for action in preview_data:
        for ff in action.get("flagged_fields", []):
            all_flagged.append({
                "Server": action["server"],
                "File": action["file"],
                "Key": ff["key"],
                "Value": str(ff["value"]),
                "Reason": ff["reason"],
            })

    if all_flagged:
        st.markdown("##### Flagged Fields (Require Manual Review)")
        st.warning(
            "The following fields contain environment-specific IDs that may "
            "differ between source and destination. Please verify after migration."
        )
        st.dataframe(pd.DataFrame(all_flagged), use_container_width=True, hide_index=True)

    # Per-file delta details
    with st.expander("Detailed Changes Per File"):
        for server_name, server_deltas in deltas.items():
            st.markdown(f"##### {server_name}")
            for delta in server_deltas:
                st.markdown(f"**{delta['file']}**")
                if not delta["deltas"]:
                    st.caption("No changes detected")
                elif delta["deltas"][0].get("type") == "full_copy":
                    st.caption(f"Full copy: {delta['deltas'][0].get('reason', '')}")
                else:
                    for change in delta["deltas"]:
                        change_type = change.get("type", "unknown")
                        path = change.get("path", "")
                        if change_type == "modified":
                            st.markdown(
                                f"- :material/edit: `{path}`: "
                                f"`{change.get('old_value')}` -> `{change.get('new_value')}`"
                            )
                        elif change_type == "added":
                            st.markdown(f"- :material/add: `{path}`: `{change.get('new_value')}`")
                        elif change_type == "removed":
                            st.markdown(f"- :material/remove: `{path}`: `{change.get('old_value')}`")
                st.markdown("")


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

def _render_results(results: list[dict]):
    """Display migration results."""
    st.markdown("---")
    st.markdown("#### Migration Results")

    success_count = sum(1 for r in results if r["status"] == "success")
    error_count = sum(1 for r in results if r["status"] == "error")

    if error_count == 0:
        st.success(f"Migration complete! {success_count} file(s) written successfully.")
    elif success_count > 0:
        st.warning(f"Partial migration: {success_count} succeeded, {error_count} failed.")
    else:
        st.error(f"Migration failed: {error_count} error(s).")

    rows = []
    for r in results:
        rows.append({
            "Server": r["server"],
            "File": r["file"],
            "Status": r["status"].upper(),
            "Message": r["message"],
        })

    result_df = pd.DataFrame(rows)

    def _color_status(val):
        if val == "SUCCESS":
            return "color: #2ecc40; font-weight: bold"
        elif val == "ERROR":
            return "color: #ff4136; font-weight: bold"
        return ""

    styled = result_df.style.map(_color_status, subset=["Status"])
    st.dataframe(styled, use_container_width=True, hide_index=True)


render_ui()
