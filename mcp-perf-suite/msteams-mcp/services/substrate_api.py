"""
Substrate API client for search and people operations.

Uses Bearer JWT authentication (Substrate token) to call:
  - v2/query: full-text message search across Teams
  - v1/suggestions: people search and channel discovery (org-wide)

Channel search uses a dual strategy: Substrate org-wide discovery
merged with CSA user membership data for reliable results.

All methods return Result[T] for consistent error handling.

Ported from the TypeScript substrate-api.ts and auth-guards.ts modules.
"""

import asyncio
import logging
import uuid
from typing import Any

import httpx

from . import auth_manager, teams_api
from .errors import (
    ErrorCode,
    Result,
    ok,
    err,
    create_error,
    classify_http_error,
)
from .parsers import (
    parse_search_results,
    parse_people_results,
    parse_channel_results,
    filter_channels_by_name,
)
from utils.config import load_config

logger = logging.getLogger("msteams-mcp.substrate-api")

_config = load_config()
_teams_cfg = _config.get("teams", {})

HTTP_TIMEOUT = _teams_cfg.get("http_request_timeout_sec", 30)
RETRY_MAX = _teams_cfg.get("retry_max_attempts", 3)
RETRY_BASE_DELAY = _teams_cfg.get("retry_base_delay_sec", 1)
RETRY_MAX_DELAY = _teams_cfg.get("retry_max_delay_sec", 10)
DEFAULT_PAGE_SIZE = _teams_cfg.get("default_page_size", 25)
DEFAULT_PEOPLE_LIMIT = _teams_cfg.get("default_people_limit", 10)
DEFAULT_CHANNEL_LIMIT = _teams_cfg.get("default_channel_limit", 10)
MAX_CHANNEL_LIMIT = 50

# Substrate endpoint URLs (commercial cloud)
_SUBSTRATE_BASE = "https://substrate.office.com"
SUBSTRATE_SEARCH_URL = f"{_SUBSTRATE_BASE}/searchservice/api/v2/query"
SUBSTRATE_PEOPLE_URL = f"{_SUBSTRATE_BASE}/search/api/v1/suggestions?scenario=powerbar"
SUBSTRATE_CHANNEL_URL = (
    f"{_SUBSTRATE_BASE}/search/api/v1/suggestions"
    "?scenario=powerbar"
    "&setflight=TurnOffMPLSuppressionTeams,EnableTeamsChannelDomainPowerbar"
    "&domain=TeamsChannel"
)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _bearer_headers(token: str) -> dict[str, str]:
    """Build Bearer auth headers for Substrate API calls."""
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }


async def _substrate_request(
    url: str,
    *,
    token: str,
    json_body: dict[str, Any],
) -> Result[Any]:
    """Make a POST request to a Substrate endpoint with retry logic."""
    headers = _bearer_headers(token)
    last_error = None

    for attempt in range(1, RETRY_MAX + 1):
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                response = await client.post(url, headers=headers, json=json_body)

            if response.status_code in (200, 201):
                try:
                    return ok(response.json())
                except Exception:
                    return ok(response.text)

            error_code = classify_http_error(response.status_code)

            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", RETRY_BASE_DELAY))
                logger.warning(
                    "Rate limited, waiting %.1fs (attempt %d/%d)",
                    retry_after, attempt, RETRY_MAX,
                )
                await asyncio.sleep(retry_after)
                continue

            if response.status_code >= 500 and attempt < RETRY_MAX:
                delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
                logger.warning(
                    "Server error %d, retrying in %.1fs (attempt %d/%d)",
                    response.status_code, delay, attempt, RETRY_MAX,
                )
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


async def _get_substrate_token() -> Result[str]:
    """
    Get a valid Substrate token with proactive refresh.

    Wraps auth_manager.get_substrate_token() which handles the
    3-layer resolution (cache -> session -> browser).
    """
    return await auth_manager.get_substrate_token()


# ---------------------------------------------------------------------------
# Message search (Substrate v2/query)
# ---------------------------------------------------------------------------

async def search_messages(
    query: str,
    *,
    from_: int = 0,
    size: int = DEFAULT_PAGE_SIZE,
    max_results: int | None = None,
) -> Result[dict[str, Any]]:
    """
    Search Teams messages using the Substrate v2 query API.

    Args:
        query: Search query with optional operators (from:, to:, sent:, etc.)
        from_: Starting offset for pagination (0-based)
        size: Page size (results per request)
        max_results: Cap on returned results (defaults to size)
    """
    token_result = await _get_substrate_token()
    if not token_result.ok:
        return err(token_result.error)
    token = token_result.value

    effective_max = max_results if max_results is not None else size

    cvid = str(uuid.uuid4())
    logical_id = str(uuid.uuid4())

    body = {
        "entityRequests": [{
            "entityType": "Message",
            "contentSources": ["Teams"],
            "propertySet": "Optimized",
            "fields": [
                "Extension_SkypeSpaces_ConversationPost_Extension_FromSkypeInternalId_String",
                "Extension_SkypeSpaces_ConversationPost_Extension_ThreadType_String",
                "Extension_SkypeSpaces_ConversationPost_Extension_SkypeGroupId_String",
            ],
            "query": {
                "queryString": f"{query} AND NOT (isClientSoftDeleted:TRUE)",
                "displayQueryString": query,
            },
            "from": from_,
            "size": size,
            "topResultsCount": 5,
        }],
        "QueryAlterationOptions": {
            "EnableAlteration": True,
            "EnableSuggestion": True,
            "SupportedRecourseDisplayTypes": ["Suggestion"],
        },
        "cvid": cvid,
        "logicalId": logical_id,
        "scenario": {
            "Dimensions": [
                {"DimensionName": "QueryType", "DimensionValue": "Messages"},
                {"DimensionName": "FormFactor", "DimensionValue": "general.web.reactSearch"},
            ],
            "Name": "powerbar",
        },
    }

    result = await _substrate_request(SUBSTRATE_SEARCH_URL, token=token, json_body=body)
    if not result.ok:
        if result.error.code == ErrorCode.AUTH_EXPIRED:
            logger.warning("Substrate token expired during search — clearing cache")
        return err(result.error)

    data = result.value
    entity_sets = data.get("EntitySets") if isinstance(data, dict) else None
    parsed = parse_search_results(entity_sets)

    all_results = parsed["results"]
    limited = all_results[:effective_max]
    total = parsed["total"]

    return ok({
        "results": limited,
        "pagination": {
            "from": from_,
            "size": size,
            "returned": len(limited),
            "total": total,
            "hasMore": (
                (from_ + len(all_results) < total)
                if total is not None
                else len(all_results) >= size
            ),
        },
    })


# ---------------------------------------------------------------------------
# People search (Substrate v1/suggestions)
# ---------------------------------------------------------------------------

async def search_people(
    query: str,
    limit: int = DEFAULT_PEOPLE_LIMIT,
) -> Result[dict[str, Any]]:
    """
    Search for people by name or email using the Substrate suggestions API.

    Args:
        query: Name, email, or partial match
        limit: Maximum results to return
    """
    token_result = await _get_substrate_token()
    if not token_result.ok:
        return err(token_result.error)
    token = token_result.value

    cvid = str(uuid.uuid4())
    logical_id = str(uuid.uuid4())

    body = {
        "EntityRequests": [{
            "Query": {
                "QueryString": query,
                "DisplayQueryString": query,
            },
            "EntityType": "People",
            "Size": limit,
            "Fields": [
                "Id",
                "MRI",
                "DisplayName",
                "EmailAddresses",
                "GivenName",
                "Surname",
                "JobTitle",
                "Department",
                "CompanyName",
            ],
        }],
        "cvid": cvid,
        "logicalId": logical_id,
    }

    result = await _substrate_request(SUBSTRATE_PEOPLE_URL, token=token, json_body=body)
    if not result.ok:
        return err(result.error)

    data = result.value
    groups = data.get("Groups") if isinstance(data, dict) else None
    people = parse_people_results(groups)

    return ok({
        "results": people,
        "returned": len(people),
    })


# ---------------------------------------------------------------------------
# Channel search (Substrate suggestions + CSA merge)
# ---------------------------------------------------------------------------

async def _search_channels_org_wide(
    query: str,
    limit: int,
    token: str,
) -> Result[list[dict[str, Any]]]:
    """Search channels org-wide via Substrate suggestions API."""
    cvid = str(uuid.uuid4())
    logical_id = str(uuid.uuid4())

    body = {
        "EntityRequests": [{
            "Query": {
                "QueryString": query,
                "DisplayQueryString": query,
            },
            "EntityType": "TeamsChannel",
            "Size": min(limit, MAX_CHANNEL_LIMIT),
        }],
        "cvid": cvid,
        "logicalId": logical_id,
    }

    result = await _substrate_request(SUBSTRATE_CHANNEL_URL, token=token, json_body=body)
    if not result.ok:
        return err(result.error)

    data = result.value
    groups = data.get("Groups") if isinstance(data, dict) else None
    channels = parse_channel_results(groups)

    for ch in channels:
        ch["isMember"] = False

    return ok(channels)


async def search_channels(
    query: str,
    limit: int = DEFAULT_CHANNEL_LIMIT,
) -> Result[dict[str, Any]]:
    """
    Search for Teams channels by name using a dual strategy:

    1. User's own teams/channels (CSA API) — reliable, shows membership
    2. Organisation-wide discovery (Substrate suggestions) — broader reach

    Results are merged and deduplicated, with isMember status indicated.
    """
    token_result = await _get_substrate_token()
    if not token_result.ok:
        return err(token_result.error)
    token = token_result.value

    org_result, my_teams_result = await asyncio.gather(
        _search_channels_org_wide(query, limit, token),
        teams_api.get_my_teams_and_channels(),
        return_exceptions=True,
    )

    member_channel_ids: set[str] = set()
    my_channels_matching: list[dict[str, Any]] = []

    if not isinstance(my_teams_result, BaseException) and my_teams_result.ok:
        my_teams_data = my_teams_result.value
        my_channels_matching = filter_channels_by_name(my_teams_data, query)
        for ch in my_channels_matching:
            member_channel_ids.add(ch["channelId"])
        for team in my_teams_data:
            for ch in team.get("channels", []):
                member_channel_ids.add(ch.get("id", ""))

    org_channels: list[dict[str, Any]] = []
    if not isinstance(org_result, BaseException) and org_result.ok:
        for ch in org_result.value:
            ch["isMember"] = ch.get("channelId", "") in member_channel_ids
            org_channels.append(ch)

    seen_ids: set[str] = set()
    merged: list[dict[str, Any]] = []

    for ch in my_channels_matching:
        ch_id = ch.get("channelId", "")
        if ch_id not in seen_ids:
            seen_ids.add(ch_id)
            merged.append(ch)

    for ch in org_channels:
        ch_id = ch.get("channelId", "")
        if ch_id not in seen_ids:
            seen_ids.add(ch_id)
            merged.append(ch)

    limited = merged[:limit]

    return ok({
        "results": limited,
        "returned": len(limited),
    })
