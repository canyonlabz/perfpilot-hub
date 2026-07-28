"""
HTTP client for Microsoft Teams APIs.

Uses the Skype token (from cookie-based auth) to call:
  - chatsvc: send/receive messages, create group chats (users/ME/conversations)
  - CSA: teams/channels listing (v3 API)

Endpoints are dynamically resolved from the user's session config
(DISCOVER-REGION-GTM) to support commercial, GCC, and DoD tenants.

All methods return Result[T] for consistent error handling.
"""

import asyncio
import json
import logging
import time
from typing import Any
from urllib.parse import quote, urlparse

from . import parsers

import httpx

from . import auth_manager, token_extractor
from .errors import (
    ErrorCode,
    Result,
    ok,
    err,
    create_error,
    classify_http_error,
)
from utils.config import load_config

logger = logging.getLogger("msteams-mcp.teams-api")

_config = load_config()
_teams_cfg = _config.get("teams", {})

HTTP_TIMEOUT = _teams_cfg.get("http_request_timeout_sec", 30)
RETRY_MAX = _teams_cfg.get("retry_max_attempts", 3)
RETRY_BASE_DELAY = _teams_cfg.get("retry_base_delay_sec", 1)
RETRY_MAX_DELAY = _teams_cfg.get("retry_max_delay_sec", 10)

# Fallback values if region config isn't available
_DEFAULT_REGION = "amer"
_DEFAULT_TEAMS_BASE = "https://teams.microsoft.com"

# Cached region config (populated on first API call)
_region_cache: dict[str, str] | None = None


def _get_region_config() -> dict[str, str]:
    """
    Extract region and base URLs from session localStorage.

    Returns dict with keys: region, teams_base_url
    """
    global _region_cache
    if _region_cache:
        return _region_cache

    config = token_extractor.extract_region_config()
    region = _DEFAULT_REGION
    teams_base = _DEFAULT_TEAMS_BASE

    if config:
        region = config.get("region", _DEFAULT_REGION)
        chat_svc_afd = config.get("chatServiceAfd", "")
        if chat_svc_afd:
            parsed = urlparse(chat_svc_afd)
            teams_base = f"{parsed.scheme}://{parsed.hostname}"

    _region_cache = {
        "region": region,
        "teams_base_url": teams_base,
    }

    logger.info("Region config: region=%s, base=%s", region, teams_base)
    return _region_cache


def _reset_region_cache() -> None:
    """Reset cached region config (e.g., after re-login)."""
    global _region_cache
    _region_cache = None


# ---------------------------------------------------------------------------
# URL builders (matching api-config.ts patterns)
# ---------------------------------------------------------------------------

def _chatsvc_messages_url(region: str, conversation_id: str, base: str, reply_to: str | None = None) -> str:
    """Build chatsvc messages URL. Mirrors CHATSVC_API.messages() from TS source."""
    conv_path = f"{conversation_id};messageid={reply_to}" if reply_to else conversation_id
    return f"{base}/api/chatsvc/{region}/v1/users/ME/conversations/{quote(conv_path, safe='')}/messages"


def _chatsvc_threads_url(region: str, base: str) -> str:
    """Build chatsvc threads URL for creating group chats."""
    return f"{base}/api/chatsvc/{region}/v1/threads"


def _csa_teams_list_url(region: str, base: str) -> str:
    """Build CSA teams list URL. Mirrors CSA_API.teamsList() — uses v3."""
    return f"{base}/api/csa/{region}/api/v3/teams/users/me?isPrefetch=false&enableMembershipSummary=true"


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------

def _get_common_headers(base_url: str) -> dict[str, str]:
    """Common headers matching getTeamsHeaders() from TS source."""
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": base_url,
        "Referer": f"{base_url}/",
    }


async def _get_skype_auth_headers() -> Result[dict[str, str]]:
    """Build auth headers using skypetoken. Mirrors getSkypeAuthHeaders()."""
    auth_result = await auth_manager.get_message_auth()
    if not auth_result.ok:
        return err(auth_result.error)

    tokens = auth_result.value
    rc = _get_region_config()
    base = rc["teams_base_url"]

    headers = _get_common_headers(base)
    headers["Authentication"] = f"skypetoken={tokens['skype_token']}"
    if tokens.get("auth_token"):
        headers["Authorization"] = f"Bearer {tokens['auth_token']}"

    return ok(headers)


async def _get_messaging_headers() -> Result[dict[str, str]]:
    """Build messaging headers. Mirrors getMessagingHeaders()."""
    result = await _get_skype_auth_headers()
    if not result.ok:
        return result

    headers = result.value
    headers["X-Ms-Client-Version"] = "1415/1.0.0.2025010401"
    return ok(headers)


# ---------------------------------------------------------------------------
# HTTP client with retry
# ---------------------------------------------------------------------------

async def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict | None = None,
    params: dict | None = None,
) -> Result[Any]:
    """Make an HTTP request with retry logic. Returns parsed JSON on success."""
    if headers is None:
        h_result = await _get_skype_auth_headers()
        if not h_result.ok:
            return err(h_result.error)
        headers = h_result.value

    last_error = None

    for attempt in range(1, RETRY_MAX + 1):
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                response = await client.request(
                    method, url, headers=headers, json=json_body, params=params,
                )

            if response.status_code in (200, 201):
                try:
                    return ok(response.json())
                except Exception:
                    return ok(response.text)

            error_code = classify_http_error(response.status_code)

            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", RETRY_BASE_DELAY))
                logger.warning("Rate limited, waiting %.1fs (attempt %d/%d)", retry_after, attempt, RETRY_MAX)
                await asyncio.sleep(retry_after)
                continue

            if response.status_code >= 500 and attempt < RETRY_MAX:
                delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
                logger.warning("Server error %d, retrying in %.1fs (attempt %d/%d)", response.status_code, delay, attempt, RETRY_MAX)
                await asyncio.sleep(delay)
                continue

            body_text = response.text[:500]
            last_error = create_error(error_code, f"HTTP {response.status_code}: {body_text}")
            break

        except httpx.TimeoutException:
            last_error = create_error(ErrorCode.TIMEOUT, f"Request timed out after {HTTP_TIMEOUT}s")
            if attempt < RETRY_MAX:
                await asyncio.sleep(RETRY_BASE_DELAY)
                continue
            break

        except httpx.ConnectError as exc:
            last_error = create_error(ErrorCode.NETWORK_ERROR, f"Connection failed: {exc}")
            if attempt < RETRY_MAX:
                await asyncio.sleep(RETRY_BASE_DELAY)
                continue
            break

        except Exception as exc:
            last_error = create_error(ErrorCode.UNKNOWN, f"Unexpected error: {exc}")
            break

    return err(last_error)


# ---------------------------------------------------------------------------
# Teams / Channel discovery (CSA v3 API)
# ---------------------------------------------------------------------------

async def _get_csa_headers() -> Result[dict[str, str]]:
    """Build CSA headers: skypetoken Authentication + CSA Bearer token."""
    auth_result = await auth_manager.get_message_auth()
    if not auth_result.ok:
        return err(auth_result.error)

    csa_token = token_extractor.extract_csa_token()
    if not csa_token:
        return err(create_error(
            ErrorCode.AUTH_REQUIRED,
            "No CSA token found — call teams_login to authenticate",
        ))

    tokens = auth_result.value
    rc = _get_region_config()
    base = rc["teams_base_url"]

    headers = _get_common_headers(base)
    headers["Authentication"] = f"skypetoken={tokens['skype_token']}"
    headers["Authorization"] = f"Bearer {csa_token}"

    return ok(headers)


async def get_my_teams_and_channels() -> Result[list[dict[str, Any]]]:
    """
    Get all teams and channels the user is a member of.

    Uses the CSA v3 teams/users/me endpoint — returns the full list,
    not a search. Mirrors getMyTeamsAndChannels() from csa-api.ts.
    """
    headers_result = await _get_csa_headers()
    if not headers_result.ok:
        return err(headers_result.error)

    rc = _get_region_config()
    url = _csa_teams_list_url(rc["region"], rc["teams_base_url"])

    result = await _request("GET", url, headers=headers_result.value)
    if not result.ok:
        return result

    data = result.value

    # Parse the response — structure varies, extract teams with channels
    raw_teams = []
    if isinstance(data, dict):
        raw_teams = data.get("teams", data.get("value", []))
    elif isinstance(data, list):
        raw_teams = data

    teams = []
    for team in raw_teams:
        if not isinstance(team, dict):
            continue

        channels = []
        raw_channels = team.get("channels", [])
        if isinstance(raw_channels, list):
            for ch in raw_channels:
                if not isinstance(ch, dict):
                    continue
                channels.append({
                    "id": ch.get("id", ""),
                    "displayName": ch.get("displayName", ch.get("name", "")),
                    "description": ch.get("description", ""),
                    "membershipType": ch.get("membershipType", ""),
                })

        teams.append({
            "id": team.get("id", ""),
            "displayName": team.get("displayName", team.get("name", "")),
            "description": team.get("description", ""),
            "channels": channels,
        })

    return ok(teams)


# ---------------------------------------------------------------------------
# Mention resolution
# ---------------------------------------------------------------------------

async def resolve_mentions(
    mention_entries: list[dict[str, str]],
) -> Result[list[dict[str, Any]]]:
    """
    Resolve a list of mention entries to fully-qualified mention objects.

    Each entry may contain:
      - ``email`` (required unless ``id`` is provided)
      - ``id`` — Azure AD object ID (skips search when provided)
      - ``displayName`` — fallback display name

    Resolution logic per entry:
      1. If ``id`` is already provided, use it directly.
      2. Otherwise search by ``email`` via Substrate API.
      3. If the search returns exactly 1 match, use that result.
      4. If 0 matches → collect a warning.
      5. If >1 matches → collect a warning with the ambiguous results.

    Returns Ok with resolved list, or Err if any entry could not be
    unambiguously resolved (the message should NOT be sent).
    """
    from . import substrate_api

    resolved: list[dict[str, Any]] = []
    warnings: list[str] = []

    for entry in mention_entries:
        email = entry.get("email", "")
        object_id = entry.get("id", "")
        display_name = entry.get("displayName", "")

        if object_id:
            resolved.append({
                "objectId": object_id,
                "mri": f"8:orgid:{object_id}",
                "displayName": display_name or email or object_id,
                "email": email,
            })
            continue

        if not email:
            warnings.append(
                "Mention entry missing both 'email' and 'id': "
                f"{json.dumps(entry)}"
            )
            continue

        search_result = await substrate_api.search_people(email, limit=5)
        if not search_result.ok:
            warnings.append(
                f"Failed to search for '{email}': {search_result.error.message}"
            )
            continue

        people = search_result.value.get("results", [])

        exact = [
            p for p in people
            if (p.get("email") or "").lower() == email.lower()
        ]

        if len(exact) == 1:
            match = exact[0]
            resolved.append({
                "objectId": match["id"],
                "mri": match.get("mri") or f"8:orgid:{match['id']}",
                "displayName": match.get("displayName") or display_name or email,
                "email": match.get("email", email),
            })
        elif len(exact) == 0:
            candidates = ", ".join(
                f'{p.get("displayName")} <{p.get("email")}>' for p in people
            )
            if candidates:
                warnings.append(
                    f"No exact email match for '{email}'. "
                    f"Possible matches: {candidates}"
                )
            else:
                warnings.append(
                    f"No results found for '{email}'. "
                    "Verify the email address is correct."
                )
        else:
            candidates = ", ".join(
                f'{p.get("displayName")} <{p.get("email")}> '
                f'(id: {p.get("id")})' for p in exact
            )
            warnings.append(
                f"Ambiguous: multiple people match '{email}': {candidates}. "
                "Please pass the specific 'id' to disambiguate."
            )

    if warnings:
        return err(create_error(
            ErrorCode.INVALID_INPUT,
            "Could not resolve all mentions. Message NOT sent.\n"
            + "\n".join(f"  - {w}" for w in warnings),
            suggestions=[
                "Fix the email addresses or provide 'id' directly",
                "Use teams_search_people to find the correct person",
            ],
        ))

    return ok(resolved)


def build_mention_tags(
    resolved_mentions: list[dict[str, Any]],
) -> tuple[str, str]:
    """
    Build HTML mention tags and the chatsvc ``properties.mentions`` JSON string.

    Uses the schema.skype.com/Mention format required by the chatsvc API:
    HTML uses ``<readonly><span>`` wrappers, and the mentions array is a
    JSON-encoded string placed inside ``properties.mentions``.

    Returns:
        (html_snippet, mentions_json_string)
    """
    if not resolved_mentions:
        return "", ""

    at_tags: list[str] = []
    mentions_list: list[dict[str, Any]] = []

    for idx, person in enumerate(resolved_mentions):
        display = person["displayName"]
        mri = person.get("mri") or f"8:orgid:{person['objectId']}"

        at_tags.append(
            f'<readonly class="skipProofing" contenteditable="false" '
            f'spellcheck="false" itemtype="http://schema.skype.com/Mention">'
            f'<span itemtype="http://schema.skype.com/Mention" '
            f'itemscope itemid="{idx}">{display}</span></readonly>'
        )

        mentions_list.append({
            "@type": "http://schema.skype.com/Mention",
            "itemid": str(idx),
            "mri": mri,
            "mentionType": "person",
            "displayName": display,
        })

    html_snippet = "<p>" + "&nbsp;".join(at_tags) + "</p>"
    mentions_json = json.dumps(mentions_list)
    return html_snippet, mentions_json


# ---------------------------------------------------------------------------
# Messaging (chatsvc API)
# ---------------------------------------------------------------------------

async def send_message(
    conversation_id: str,
    content: str,
    *,
    subject: str | None = None,
    content_type: str = "text",
    reply_to_message_id: str | None = None,
    template: str | None = None,
    variables: dict[str, str] | None = None,
    target: str | None = None,
    test_run_id: str | None = None,
    mentions: list[dict[str, str]] | None = None,
) -> Result[dict[str, Any]]:
    """
    Send a message to a Teams conversation (channel, chat, or group).

    Supports optional template rendering: when `template` is provided,
    the template is loaded, placeholders are interpolated, markdown is
    converted to Teams HTML, and the result is sent as rich content.

    Args:
        conversation_id: Channel/chat ID. Ignored when `target` is provided.
        content: Message body (plain text, markdown, or HTML).
        subject: Optional subject line (bold title above message in channels).
        content_type: "text" for plain text, "html" for rich content.
        reply_to_message_id: For channel thread replies, the root message ID.
        template: Template filename (e.g. "notification-start-test.md").
        variables: Key-value pairs to interpolate into the template.
        target: Named target from config or raw conversation ID.
        test_run_id: Loads context variables from artifacts/<test_run_id>/.
        mentions: People to @mention. Each entry has ``email`` and/or ``id``
                  (object ID) plus optional ``displayName``. Merged with
                  any config-level mentions for the resolved target.
    """
    from . import template_manager, target_resolver

    resolved_conversation_id = conversation_id
    channel_template: str | None = None
    config_mentions: list[str] = []

    if target:
        resolved = target_resolver.resolve_target(target)
        if resolved["conversation_id"]:
            resolved_conversation_id = resolved["conversation_id"]
        channel_template = resolved.get("template")
        config_mentions = resolved.get("mentions", [])

    if not resolved_conversation_id:
        return err(create_error(
            ErrorCode.INVALID_INPUT,
            "No conversation_id resolved. Provide conversation_id or a valid target name.",
        ))

    final_content = content
    final_content_type = content_type
    used_template: str | None = None

    if template:
        merged_vars = {}

        if test_run_id:
            merged_vars.update(template_manager.load_context_variables(test_run_id))

        if variables:
            merged_vars.update(variables)

        if content and "MESSAGE" not in merged_vars:
            merged_vars["MESSAGE"] = content

        try:
            tmpl_content, used_template = template_manager.load_template(
                template, channel_template=channel_template,
            )
        except FileNotFoundError as exc:
            return err(create_error(ErrorCode.NOT_FOUND, str(exc)))

        rendered_md = template_manager.render_template(tmpl_content, merged_vars)
        final_content = parsers.markdown_to_teams_html(rendered_md)
        final_content_type = "html"

    elif content_type == "text" and parsers.has_markdown_formatting(content):
        final_content = parsers.markdown_to_teams_html(content)
        final_content_type = "html"

    # -----------------------------------------------------------------------
    # Merge and resolve @mentions (config-driven + caller-provided)
    # -----------------------------------------------------------------------
    all_mention_entries: list[dict[str, str]] = []
    seen_emails: set[str] = set()

    for email in config_mentions:
        key = email.lower()
        if key not in seen_emails:
            seen_emails.add(key)
            all_mention_entries.append({"email": email})

    for entry in (mentions or []):
        key = (entry.get("email") or entry.get("id") or "").lower()
        if key and key not in seen_emails:
            seen_emails.add(key)
            all_mention_entries.append(entry)

    mentions_html = ""
    mentions_json = ""

    if all_mention_entries:
        resolve_result = await resolve_mentions(all_mention_entries)
        if not resolve_result.ok:
            return err(resolve_result.error)
        mentions_html, mentions_json = build_mention_tags(resolve_result.value)

    if mentions_html:
        if final_content_type != "html":
            final_content = parsers.markdown_to_teams_html(final_content)
            final_content_type = "html"
        final_content += mentions_html

    # -----------------------------------------------------------------------
    # Build and send POST body
    # -----------------------------------------------------------------------
    headers_result = await _get_messaging_headers()
    if not headers_result.ok:
        return err(headers_result.error)

    rc = _get_region_config()
    url = _chatsvc_messages_url(
        rc["region"], resolved_conversation_id, rc["teams_base_url"],
        reply_to=reply_to_message_id,
    )

    display_name = token_extractor.get_user_display_name()

    body: dict[str, Any] = {
        "content": final_content,
        "messagetype": "RichText/Html" if final_content_type == "html" else "Text",
        "contenttype": "text",
        "imdisplayname": display_name,
        "clientmessageid": str(int(time.time() * 1000)),
    }

    properties: dict[str, str] = {
        "importance": "",
        "subject": subject or "",
        "cards": "[]",
        "links": "[]",
        "mentions": mentions_json or "[]",
    }
    body["properties"] = properties

    result = await _request("POST", url, headers=headers_result.value, json_body=body)
    if not result.ok:
        return result

    message_id = body["clientmessageid"]

    if test_run_id and used_template:
        try:
            template_manager.log_notification(
                test_run_id,
                template=used_template,
                target=resolved_conversation_id,
                variables=variables or {},
                rendered_content=final_content,
                message_id=message_id,
            )
        except Exception as log_exc:
            logger.warning("Failed to log notification: %s", log_exc)

    return ok({
        "messageId": message_id,
        "timestamp": result.value.get("OriginalArrivalTime") if isinstance(result.value, dict) else None,
        "template": used_template,
        "target": resolved_conversation_id,
    })


# ---------------------------------------------------------------------------
# 1:1 Chat (deterministic conversation ID)
# ---------------------------------------------------------------------------

def get_one_on_one_chat_id(
    other_user_identifier: str,
) -> Result[dict[str, Any]]:
    """
    Get the conversation ID for a 1:1 chat with another user.

    The ID is deterministic (19:{sorted_id1}_{sorted_id2}@unq.gbl.spaces).
    The conversation is implicitly created when the first message is sent.

    Args:
        other_user_identifier: MRI (8:orgid:guid), object-id, or raw GUID.

    Returns:
        Result with conversationId, otherUserId, and currentUserId.
    """
    profile = token_extractor.get_user_profile()
    if not profile or not profile.object_id:
        return err(create_error(
            ErrorCode.AUTH_REQUIRED,
            "No valid session. Please use teams_login first.",
        ))

    current_user_id = profile.object_id

    conversation_id = parsers.build_one_on_one_conversation_id(
        current_user_id, other_user_identifier,
    )

    if not conversation_id:
        return err(create_error(
            ErrorCode.INVALID_INPUT,
            f"Invalid user identifier: {other_user_identifier}. "
            "Expected MRI (8:orgid:guid), ID with tenant (guid@tenant), or raw GUID.",
        ))

    other_user_id = parsers._extract_object_id(other_user_identifier) or other_user_identifier

    return ok({
        "conversationId": conversation_id,
        "otherUserId": other_user_id,
        "currentUserId": current_user_id,
    })


# ---------------------------------------------------------------------------
# Group Chat (API-created conversation)
# ---------------------------------------------------------------------------

MRI_ORGID_PREFIX = "8:orgid:"

async def create_group_chat(
    member_identifiers: list[str],
    topic: str | None = None,
) -> Result[dict[str, Any]]:
    """
    Create a new group chat with multiple members.

    Unlike 1:1 chats, group chat IDs are server-assigned and require
    an API call. Requires at least 2 other members (3+ total with you).

    Args:
        member_identifiers: User MRIs, object IDs, or GUIDs.
        topic: Optional chat topic/name shown in Teams.

    Returns:
        Result with conversationId, members list, and optional topic.
    """
    if not member_identifiers or len(member_identifiers) < 2:
        return err(create_error(
            ErrorCode.INVALID_INPUT,
            "Group chat requires at least 2 other members. "
            "For 1:1 chats, use teams_get_chat instead.",
        ))

    unique = set(member_identifiers)
    if len(unique) != len(member_identifiers):
        return err(create_error(
            ErrorCode.INVALID_INPUT,
            "Duplicate members detected in group chat request.",
        ))

    if len(member_identifiers) > 250:
        return err(create_error(
            ErrorCode.INVALID_INPUT,
            "Group chat cannot have more than 250 members.",
        ))

    auth_result = await auth_manager.get_message_auth()
    if not auth_result.ok:
        return err(auth_result.error)

    tokens = auth_result.value
    current_mri = tokens.get("user_mri", "")

    member_mris: list[str] = [current_mri]

    for identifier in member_identifiers:
        obj_id = parsers._extract_object_id(identifier)
        if not obj_id:
            return err(create_error(
                ErrorCode.INVALID_INPUT,
                f"Invalid user identifier: {identifier}. "
                "Expected MRI (8:orgid:guid), ID with tenant (guid@tenant), or raw GUID.",
            ))
        mri = identifier if identifier.startswith(MRI_ORGID_PREFIX) else f"{MRI_ORGID_PREFIX}{obj_id}"
        member_mris.append(mri)

    body: dict[str, Any] = {
        "members": [{"id": mri, "role": "Admin"} for mri in member_mris],
        "properties": {"threadType": "chat"},
    }

    if topic:
        body["properties"]["topic"] = topic

    headers_result = await _get_messaging_headers()
    if not headers_result.ok:
        return err(headers_result.error)

    rc = _get_region_config()
    url = _chatsvc_threads_url(rc["region"], rc["teams_base_url"])

    result = await _request("POST", url, headers=headers_result.value, json_body=body)
    if not result.ok:
        return result

    conversation_id = None
    if isinstance(result.value, dict):
        tr = result.value.get("threadResource", {})
        conversation_id = (
            (tr.get("id") if isinstance(tr, dict) else None)
            or result.value.get("id")
            or result.value.get("threadId")
        )

    if not conversation_id:
        return ok({
            "conversationId": "(created — check Teams for conversation ID)",
            "members": member_mris,
            "topic": topic,
            "note": "Group chat created but API did not return the conversation ID.",
        })

    return ok({
        "conversationId": conversation_id,
        "members": member_mris,
        "topic": topic,
    })
