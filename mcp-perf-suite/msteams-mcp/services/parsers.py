"""
Pure parsing functions for Teams API responses.

Handles HTML stripping, link extraction, and structured parsing of
Substrate search results, people suggestions, and channel suggestions.
No I/O or auth dependencies — all functions are stateless.

Ported from the TypeScript parsers-search.ts, parsers-people.ts,
and parsers-channels.ts modules in the msteams-mcp reference project.
"""

import base64
import re
import uuid
from html import unescape
from html import escape as escape_html
from typing import Any

MIN_CONTENT_LENGTH = 5
MRI_TYPE_PREFIX = "8:"
ORGID_PREFIX = "orgid:"
MRI_ORGID_PREFIX = f"{MRI_TYPE_PREFIX}{ORGID_PREFIX}"

# ---------------------------------------------------------------------------
# HTML utilities
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    """Remove HTML tags and decode entities, collapsing whitespace."""
    cleaned = _TAG_RE.sub(" ", text)
    cleaned = unescape(cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def extract_links(html: str) -> list[dict[str, str]]:
    """Extract href/text pairs from <a> tags before HTML is stripped."""
    links: list[dict[str, str]] = []
    for match in _HREF_RE.finditer(html):
        href = match.group(1).strip()
        text = strip_html(match.group(2)).strip()
        if href:
            links.append({"url": href, "text": text or href})
    return links


# ---------------------------------------------------------------------------
# Search result parsing (Substrate v2/query)
# ---------------------------------------------------------------------------


def _extract_conversation_id(source: dict[str, Any]) -> str | None:
    """
    Extract conversationId with the same priority as the TS reference:
    ClientThreadId > Extensions.SkypeGroupId > ClientConversationId (strip ;messageid=).
    """
    client_thread_id = source.get("ClientThreadId")
    if isinstance(client_thread_id, str) and client_thread_id:
        return client_thread_id

    extensions = source.get("Extensions")
    if isinstance(extensions, dict):
        group_id = extensions.get("SkypeSpaces_ConversationPost_Extension_SkypeGroupId")
        if isinstance(group_id, str) and group_id:
            return group_id

    client_conv_id = source.get("ClientConversationId")
    if isinstance(client_conv_id, str) and client_conv_id:
        return client_conv_id.split(";")[0]

    return None


def _extract_message_timestamp(source: dict[str, Any], fallback_ts: str | None) -> str | None:
    """Extract a numeric message timestamp for deep links and thread identification."""
    for key in ("ComposeTime", "OriginalArrivalTime"):
        val = source.get(key)
        if isinstance(val, str) and val.isdigit():
            return val

    if fallback_ts:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(fallback_ts.replace("Z", "+00:00"))
            return str(int(dt.timestamp() * 1000))
        except Exception:
            pass

    return None


def parse_v2_result(item: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a single v2 query result item into a search result dict."""
    content = item.get("HitHighlightedSummary") or item.get("Summary") or ""
    if len(content) < MIN_CONTENT_LENGTH:
        return None

    result_id = (
        item.get("Id")
        or item.get("ReferenceId")
        or f"v2-{uuid.uuid4()}"
    )

    links = extract_links(content)
    clean_content = strip_html(content)

    source: dict[str, Any] = item.get("Source") or {}

    conversation_id = _extract_conversation_id(source)

    timestamp = (
        source.get("DateTimeReceived")
        or source.get("DateTimeSent")
        or source.get("DateTimeCreated")
        or source.get("ReceivedTime")
        or source.get("CreatedDateTime")
    )

    message_timestamp = _extract_message_timestamp(source, timestamp)

    parent_message_id: str | None = None
    client_conv_id = source.get("ClientConversationId")
    if isinstance(client_conv_id, str) and ";messageid=" in client_conv_id:
        match = re.search(r";messageid=(\d+)", client_conv_id)
        if match:
            parent_message_id = match.group(1)

    result: dict[str, Any] = {
        "id": result_id,
        "type": "message",
        "content": clean_content,
        "sender": source.get("From") or source.get("Sender"),
        "timestamp": timestamp,
        "channelName": source.get("ChannelName") or source.get("Topic"),
        "teamName": source.get("TeamName") or source.get("GroupName"),
        "conversationId": conversation_id,
        "messageId": message_timestamp or item.get("ReferenceId"),
    }

    if links:
        result["links"] = links
    if parent_message_id:
        result["parentMessageId"] = parent_message_id

    return result


def parse_search_results(
    entity_sets: list[Any] | None,
) -> dict[str, Any]:
    """
    Parse Substrate v2/query EntitySets response into search results.

    Returns {"results": [...], "total": int | None}.
    """
    results: list[dict[str, Any]] = []
    total: int | None = None

    if not isinstance(entity_sets, list):
        return {"results": results, "total": total}

    for entity_set in entity_sets:
        if not isinstance(entity_set, dict):
            continue
        result_sets = entity_set.get("ResultSets")
        if not isinstance(result_sets, list):
            continue

        for result_set in result_sets:
            if not isinstance(result_set, dict):
                continue

            rs_total = (
                result_set.get("Total")
                or result_set.get("TotalCount")
                or result_set.get("TotalEstimate")
            )
            if isinstance(rs_total, (int, float)):
                total = int(rs_total)

            items = result_set.get("Results")
            if not isinstance(items, list):
                continue

            for item in items:
                if not isinstance(item, dict):
                    continue
                parsed = parse_v2_result(item)
                if parsed:
                    results.append(parsed)

    return {"results": results, "total": total}


# ---------------------------------------------------------------------------
# People result parsing (Substrate v1/suggestions)
# ---------------------------------------------------------------------------

def _extract_object_id(raw_id: str) -> str | None:
    """
    Convert a raw ID to a proper GUID format.
    Handles plain GUIDs, base64-encoded GUIDs, and email-suffixed IDs.
    """
    id_part = raw_id.split("@")[0] if "@" in raw_id else raw_id

    guid_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )
    if guid_re.match(id_part):
        return id_part

    try:
        decoded = base64.b64decode(id_part + "=" * (-len(id_part) % 4))
        if len(decoded) == 16:
            return str(uuid.UUID(bytes_le=decoded))
    except Exception:
        pass

    return None


def parse_person_suggestion(item: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a single person suggestion from Substrate API response."""
    raw_id = item.get("Id")
    if not isinstance(raw_id, str) or not raw_id:
        return None

    object_id = _extract_object_id(raw_id)
    if not object_id:
        return None

    mri = item.get("MRI") or f"8:orgid:{object_id}"
    if "orgid:" in mri and "-" not in mri:
        mri = f"8:orgid:{object_id}"

    email_addresses = item.get("EmailAddresses")
    email = email_addresses[0] if isinstance(email_addresses, list) and email_addresses else None

    result: dict[str, Any] = {
        "id": object_id,
        "mri": mri,
        "displayName": item.get("DisplayName") or "",
        "email": email,
    }

    for field in ("GivenName", "Surname", "JobTitle", "Department", "CompanyName"):
        val = item.get(field)
        if val:
            py_field = field[0].lower() + field[1:]
            result[py_field] = val

    return result


def parse_people_results(groups: list[Any] | None) -> list[dict[str, Any]]:
    """Parse Groups/Suggestions structure from Substrate people search."""
    results: list[dict[str, Any]] = []
    if not isinstance(groups, list):
        return results

    for group in groups:
        if not isinstance(group, dict):
            continue
        suggestions = group.get("Suggestions")
        if not isinstance(suggestions, list):
            continue
        for suggestion in suggestions:
            if not isinstance(suggestion, dict):
                continue
            parsed = parse_person_suggestion(suggestion)
            if parsed:
                results.append(parsed)

    return results


# ---------------------------------------------------------------------------
# Channel result parsing (Substrate suggestions + CSA teams list)
# ---------------------------------------------------------------------------

def parse_channel_suggestion(suggestion: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a single channel suggestion from Substrate API response."""
    name = suggestion.get("Name")
    thread_id = suggestion.get("ThreadId")
    team_name = suggestion.get("TeamName")
    group_id = suggestion.get("GroupId")

    if not all((name, thread_id, team_name, group_id)):
        return None

    return {
        "channelId": thread_id,
        "channelName": name,
        "teamName": team_name,
        "teamId": group_id,
        "channelType": suggestion.get("ChannelType") or "Standard",
        "description": suggestion.get("Description"),
    }


def parse_channel_results(groups: list[Any] | None) -> list[dict[str, Any]]:
    """Parse Groups/Suggestions for ChannelSuggestion entities from Substrate."""
    results: list[dict[str, Any]] = []
    if not isinstance(groups, list):
        return results

    for group in groups:
        if not isinstance(group, dict):
            continue
        suggestions = group.get("Suggestions")
        if not isinstance(suggestions, list):
            continue
        for suggestion in suggestions:
            if not isinstance(suggestion, dict):
                continue
            if suggestion.get("EntityType") != "ChannelSuggestion":
                continue
            parsed = parse_channel_suggestion(suggestion)
            if parsed:
                results.append(parsed)

    return results


def filter_channels_by_name(
    teams_data: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """
    Filter channels from CSA teams list by name (case-insensitive partial match).

    Expects teams_data in the format returned by teams_api.get_my_teams_and_channels():
    [{"id": ..., "displayName": ..., "channels": [{"id": ..., "displayName": ...}, ...]}]
    """
    lower_query = query.lower()
    results: list[dict[str, Any]] = []

    for team in teams_data:
        if not isinstance(team, dict):
            continue
        team_name = team.get("displayName", "")
        for channel in team.get("channels", []):
            if not isinstance(channel, dict):
                continue
            ch_name = channel.get("displayName", "")
            if lower_query in ch_name.lower():
                results.append({
                    "channelId": channel.get("id", ""),
                    "channelName": ch_name,
                    "teamName": team_name,
                    "teamId": team.get("id", ""),
                    "channelType": channel.get("membershipType", "Standard"),
                    "description": channel.get("description"),
                    "isMember": True,
                })

    return results


# ---------------------------------------------------------------------------
# Markdown to Teams HTML conversion
# (ported from TypeScript parsers-markdown.ts)
# ---------------------------------------------------------------------------

_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BOLD_STAR_RE = re.compile(r"\*\*(.+?)\*\*")
_BOLD_UNDER_RE = re.compile(r"__(.+?)__")
_ITALIC_STAR_RE = re.compile(r"\*(.+?)\*")
_ITALIC_UNDER_RE = re.compile(r"(?<!\w)_(.+?)_(?!\w)")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_FENCED_BLOCK_RE = re.compile(r"```(\w*)\n?([\s\S]*?)```")
_UNORDERED_LINE_RE = re.compile(r"^\s*[-*]\s+")
_ORDERED_LINE_RE = re.compile(r"^\s*\d+[.)]\s+")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")
_TABLE_ROW_RE = re.compile(r"^\|.+\|")
_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")

_HAS_MD_RE = re.compile(
    r"```[\s\S]*```|`[^`]+`|\*\*.+?\*\*|__.+?__|(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)"
    r"|~~.+?~~|^\s*[-*]\s+|^\s*\d+[.)]\s+|\n",
    re.MULTILINE,
)


def _convert_inline_formatting(line: str) -> str:
    """Convert inline markdown to Teams HTML within a single line."""
    link_placeholders: list[str] = []

    def _stash_link(m: re.Match) -> str:
        label = escape_html(m.group(1))
        url = m.group(2).replace('"', '&quot;')
        tag = f'<a href="{url}">{label}</a>'
        idx = len(link_placeholders)
        link_placeholders.append(tag)
        return f"\uE010LINK{idx}\uE011"

    line = _LINK_RE.sub(_stash_link, line)

    parts = _INLINE_CODE_RE.split(line)
    result_parts: list[str] = []

    for i, part in enumerate(parts):
        if i % 2 == 1:
            result_parts.append(f"<code>{escape_html(part)}</code>")
        else:
            segment = escape_html(part)
            segment = _BOLD_STAR_RE.sub(r"<b>\1</b>", segment)
            segment = _BOLD_UNDER_RE.sub(r"<b>\1</b>", segment)
            segment = _ITALIC_STAR_RE.sub(r"<i>\1</i>", segment)
            segment = _ITALIC_UNDER_RE.sub(r"<i>\1</i>", segment)
            segment = _STRIKE_RE.sub(r"<s>\1</s>", segment)
            result_parts.append(segment)

    result = "".join(result_parts)

    for idx, tag in enumerate(link_placeholders):
        result = result.replace(f"\uE010LINK{idx}\uE011", tag)

    return result


def has_markdown_formatting(text: str) -> bool:
    """Check whether text contains markdown formatting worth converting."""
    return bool(_HAS_MD_RE.search(text))


def _parse_table_cells(line: str) -> list[str]:
    """Split a pipe-delimited table row into trimmed cell strings."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_table_paragraph(lines: list[str]) -> bool:
    """Return True if the line block is a valid markdown table (header + separator + rows)."""
    if len(lines) < 3:
        return False
    if not _TABLE_ROW_RE.match(lines[0].strip()):
        return False
    if not _TABLE_SEP_RE.match(lines[1].strip()):
        return False
    return all(_TABLE_ROW_RE.match(ln.strip()) for ln in lines[2:])


def _convert_table_to_html(lines: list[str]) -> str:
    """Convert markdown table lines to a Teams-compatible HTML <table>."""
    headers = _parse_table_cells(lines[0])
    data_rows = [_parse_table_cells(ln) for ln in lines[2:]]

    thead = "<tr>" + "".join(
        f"<th><b>{_convert_inline_formatting(h)}</b></th>" for h in headers
    ) + "</tr>"
    tbody = "".join(
        "<tr>" + "".join(
            f"<td>{_convert_inline_formatting(cell)}</td>" for cell in row
        ) + "</tr>"
        for row in data_rows
    )
    return f"<table>{thead}{tbody}</table>"


def markdown_to_teams_html(text: str) -> str:
    """
    Convert markdown-formatted text to Teams-compatible HTML.

    Supports bold, italic, strikethrough, inline code, fenced code blocks,
    ordered/unordered lists, markdown tables, paragraph breaks, and line breaks.
    """
    segments: list[dict[str, str]] = []
    last_index = 0

    for match in _FENCED_BLOCK_RE.finditer(text):
        if match.start() > last_index:
            segments.append({"type": "text", "content": text[last_index:match.start()]})
        segments.append({"type": "codeblock", "content": match.group(2)})
        last_index = match.end()

    if last_index < len(text):
        segments.append({"type": "text", "content": text[last_index:]})

    html_parts: list[str] = []

    for segment in segments:
        if segment["type"] == "codeblock":
            escaped = escape_html(segment["content"].rstrip("\n"))
            html_parts.append(f"<pre><code>{escaped}</code></pre>")
            continue

        paragraphs = re.split(r"\n{2,}", segment["content"])

        for para in paragraphs:
            trimmed = para.strip()
            if not trimmed:
                continue

            lines = trimmed.split("\n")

            is_ul = all(_UNORDERED_LINE_RE.match(ln) for ln in lines)
            is_ol = all(_ORDERED_LINE_RE.match(ln) for ln in lines)

            if is_ul:
                items = "".join(
                    f"<li>{_convert_inline_formatting(_UNORDERED_LINE_RE.sub('', ln))}</li>"
                    for ln in lines
                )
                html_parts.append(f"<ul>{items}</ul>")
            elif is_ol:
                items = "".join(
                    f"<li>{_convert_inline_formatting(_ORDERED_LINE_RE.sub('', ln))}</li>"
                    for ln in lines
                )
                html_parts.append(f"<ol>{items}</ol>")
            elif _is_table_paragraph(lines):
                html_parts.append(_convert_table_to_html(lines))
            elif len(lines) == 1 and (heading_m := _HEADING_RE.match(trimmed)):
                level = len(heading_m.group(1))
                text = _convert_inline_formatting(heading_m.group(2))
                html_parts.append(f"<h{level}>{text}</h{level}>")
            else:
                html_lines = [_convert_inline_formatting(ln) for ln in lines]
                html_parts.append(f"<p>{'<br>'.join(html_lines)}</p>")

    result = "".join(html_parts)
    result = result.replace("</table><table>", "</table><br><table>")
    result = result.replace("</table><p>", "</table><br><p>")
    return result or "<p></p>"


# ---------------------------------------------------------------------------
# 1:1 chat conversation ID builder
# ---------------------------------------------------------------------------

def build_one_on_one_conversation_id(
    user_id_1: str,
    user_id_2: str,
) -> str | None:
    """
    Build a deterministic 1:1 conversation ID from two user identifiers.

    Format: 19:{sortedId1}_{sortedId2}@unq.gbl.spaces
    IDs are sorted lexicographically so both participants produce the same result.
    Accepts MRI, object ID with tenant, or raw GUID.
    """
    id1 = _extract_object_id(user_id_1)
    id2 = _extract_object_id(user_id_2)

    if not id1 or not id2:
        return None

    sorted_ids = sorted([id1, id2])
    return f"19:{sorted_ids[0]}_{sorted_ids[1]}@unq.gbl.spaces"
