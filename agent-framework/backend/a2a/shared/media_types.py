"""A2A v1 MIME type registry — single source of truth.

Defines all supported media types for A2A Part content, following the
standard MIME type convention for required types and the vendor extension
convention (``application/vnd.{vendor}.{type}+json``) for Azure DevOps
content types.

This module is a **leaf dependency** — it imports only stdlib modules.
Both ``a2a_models`` and ``a2a_parts_parser`` import from here, avoiding
circular dependencies.

Usage::

    from .media_types import MEDIA_TEXT_PLAIN, SUPPORTED_MEDIA_TYPES
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# =============================================================================
# Registry entry
# =============================================================================


@dataclass(frozen=True)
class MimeTypeEntry:
    """Descriptor for a supported MIME type in the A2A Part model.

    Attributes:
        mime_type:   The MIME type string (e.g. ``"text/plain"``).
        part_field:  The Part content field this type maps to.
        category:    ``"required"`` for core types, ``"vendor"`` for
                     vendor-specific extensions.
        description: Human-readable description for documentation.
    """

    mime_type: str
    part_field: Literal["text", "data", "raw", "url"]
    category: Literal["required", "vendor"]
    description: str


# =============================================================================
# Registry
# =============================================================================

MIME_TYPE_REGISTRY: tuple[MimeTypeEntry, ...] = (
    # Required
    MimeTypeEntry("text/plain", "text", "required",
                  "Plain text prompt or message"),
    MimeTypeEntry("text/markdown", "text", "required",
                  "Markdown-formatted content"),
    MimeTypeEntry("application/json", "data", "required",
                  "Generic structured JSON"),
    # Vendor — Azure DevOps
    MimeTypeEntry("application/vnd.azure.devops.pbi+json", "data", "vendor",
                  "ADO Product Backlog Item"),
    MimeTypeEntry("application/vnd.azure.devops.feature+json", "data", "vendor",
                  "ADO Feature work item"),
    MimeTypeEntry("application/vnd.azure.devops.testcase+json", "data", "vendor",
                  "ADO Test Case (structured steps)"),
)

SUPPORTED_MEDIA_TYPES: frozenset[str] = frozenset(
    entry.mime_type for entry in MIME_TYPE_REGISTRY
)

# =============================================================================
# Convenience constants
# =============================================================================

# Required MIME types
MEDIA_TEXT_PLAIN = "text/plain"
MEDIA_TEXT_MARKDOWN = "text/markdown"
MEDIA_JSON = "application/json"

# Vendor MIME types — Azure DevOps
MEDIA_ADO_PBI = "application/vnd.azure.devops.pbi+json"
MEDIA_ADO_FEATURE = "application/vnd.azure.devops.feature+json"
MEDIA_ADO_TESTCASE = "application/vnd.azure.devops.testcase+json"
