"""
YAML Editor Component - Reusable form + raw editor widget.

Provides both structured form-based editing and raw YAML text editing
for configuration files. Used by the Config Editor page.
"""

import streamlit as st
from typing import Any, Optional


def render_yaml_form_field(
    key: str,
    value: Any,
    label: Optional[str] = None,
    help_text: Optional[str] = None,
    disabled: bool = False,
) -> Any:
    """
    Render an appropriate Streamlit input widget based on the value type.

    Args:
        key: Unique key for the widget.
        value: Current value (determines widget type).
        label: Display label. Defaults to the key name.
        help_text: Optional help text shown on hover.
        disabled: Whether the field is read-only.

    Returns:
        The new value from the widget.
    """
    display_label = label or key.replace("_", " ").title()

    if isinstance(value, bool):
        return st.toggle(display_label, value=value, key=key, help=help_text, disabled=disabled)

    elif isinstance(value, int):
        return st.number_input(
            display_label, value=value, step=1, key=key, help=help_text, disabled=disabled
        )

    elif isinstance(value, float):
        return st.number_input(
            display_label, value=value, step=0.1, format="%.2f",
            key=key, help=help_text, disabled=disabled,
        )

    elif isinstance(value, list):
        # Render as a text area with one item per line
        list_text = "\n".join(str(item) for item in value)
        new_text = st.text_area(
            display_label, value=list_text, key=key, help=help_text, disabled=disabled,
            height=min(150, max(68, len(value) * 25)),
        )
        return [line.strip() for line in new_text.split("\n") if line.strip()]

    elif isinstance(value, dict):
        # Nested dict - render as expandable section
        with st.expander(f"{display_label}", expanded=False):
            result = {}
            for sub_key, sub_value in value.items():
                result[sub_key] = render_yaml_form_field(
                    key=f"{key}__{sub_key}",
                    value=sub_value,
                    label=sub_key.replace("_", " ").title(),
                    disabled=disabled,
                )
            return result

    else:
        # Default: string input
        str_value = str(value) if value is not None else ""
        # Use text_area for long strings, text_input for short
        if len(str_value) > 100 or "\n" in str_value:
            return st.text_area(
                display_label, value=str_value, key=key,
                help=help_text, disabled=disabled,
            )
        return st.text_input(
            display_label, value=str_value, key=key,
            help=help_text, disabled=disabled,
        )


def render_raw_yaml_editor(
    content: str,
    key: str,
    height: int = 500,
) -> str:
    """
    Render a raw YAML text editor with monospace font.

    Args:
        content: Current YAML content.
        key: Unique key for the widget.
        height: Editor height in pixels.

    Returns:
        The edited YAML content.
    """
    return st.text_area(
        "YAML Content",
        value=content,
        height=height,
        key=key,
        label_visibility="collapsed",
    )
