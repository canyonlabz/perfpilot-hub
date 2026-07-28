"""
Microsoft Teams MCP Server

Tools:
  teams_login              — authenticate to MS Teams via browser-based SSO/manual login
  teams_status             — check authentication state and token health
  teams_list_channels      — list joined teams and their channels
  teams_send_message       — send a message (with optional template/target support)
  teams_get_me             — get the current user's profile (email, name, Teams ID)
  teams_search             — search messages across Teams conversations
  teams_search_people      — search for people by name or email
  teams_find_channel       — discover channels by name (org-wide + membership)
  teams_get_chat           — get 1:1 chat conversation ID for another user
  teams_create_group_chat  — create a new group chat with multiple members
"""

import json
import logging
import sys
from fastmcp import FastMCP
from utils.config import load_config
from services import (
    auth_manager,
    teams_api,
    substrate_api,
    token_extractor,
    template_manager,
    target_resolver,
)

config = load_config()
server_cfg = config.get("server", {})
general_cfg = config.get("general", {})

log_level = logging.DEBUG if general_cfg.get("enable_debug") else logging.INFO
logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("msteams-mcp")

mcp = FastMCP(server_cfg.get("name", "msteams-mcp"))


@mcp.tool()
async def teams_login(force: bool = False) -> str:
    """
    Authenticate to Microsoft Teams.

    Attempts SSO first (cached session), then headless browser refresh,
    then interactive browser login as a last resort.

    Args:
        force: Skip cached tokens and force a fresh browser login.

    Returns:
        JSON status with authentication result and user info.
    """
    result = await auth_manager.login(force=force)

    if result.ok:
        return json.dumps(result.value, indent=2)

    return json.dumps({
        "status": "error",
        "code": result.error.code.value,
        "message": result.error.message,
        "suggestions": result.error.suggestions,
    }, indent=2)


@mcp.tool()
async def teams_status() -> str:
    """
    Check the current authentication and session health.

    Returns token validity, session age, user info, and diagnostics.
    No network calls — reads from local cache only.

    Returns:
        JSON diagnostic snapshot of auth state.
    """
    status = auth_manager.get_status()
    return json.dumps(status, indent=2, default=str)


@mcp.tool()
async def teams_list_channels() -> str:
    """
    List all Teams and channels the authenticated user has access to.

    Returns a JSON array of teams, each containing their channels with IDs.
    Use the channel ID as conversation_id in teams_send_message.
    Call teams_login first if not yet authenticated.

    Returns:
        JSON array of teams with nested channel lists.
    """
    result = await teams_api.get_my_teams_and_channels()
    if not result.ok:
        return json.dumps({
            "status": "error",
            "code": result.error.code.value,
            "message": result.error.message,
            "suggestions": result.error.suggestions,
        }, indent=2)

    return json.dumps(result.value, indent=2)


@mcp.tool()
async def teams_send_message(
    conversation_id: str = "",
    message: str = "",
    subject: str = "",
    content_type: str = "text",
    reply_to_message_id: str = "",
    template: str = "",
    variables: str = "",
    target: str = "",
    test_run_id: str = "",
    mentions: str = "",
) -> str:
    """
    Send a message to a Microsoft Teams conversation (channel, chat, or group).

    Supports plain text, markdown (auto-converted to Teams HTML), and
    template-based notifications with {{PLACEHOLDER}} interpolation.

    Args:
        conversation_id: The conversation/channel ID. Channels look like
                         "19:xxx@thread.tacv2". Not needed when target is set.
        message: The message content to send (text, markdown, or HTML).
                 When using a template, this fills the {{MESSAGE}} placeholder.
        subject: Optional subject line displayed as a bold title above the
                 message body in channels. Most useful for notifications.
        content_type: "text" for plain/markdown, "html" for pre-formatted HTML.
        reply_to_message_id: For channel thread replies, the root message ID.
        template: Template filename (e.g. "notification-start-test.md").
                  Falls back to "default-{name}" if custom not found.
        variables: JSON string of key-value pairs for template placeholders.
                   Example: '{"TEST_NAME": "Load Test", "ENVIRONMENT": "staging"}'
        target: Named target from config (e.g. "perf-channel") or raw
                conversation ID. Overrides conversation_id when set.
        test_run_id: Auto-populates template variables from
                     artifacts/<test_run_id>/ (BlazeMeter, Confluence links, etc.)
                     and logs the notification for context tracking.
        mentions: JSON string of people to @mention in the message.
                  Each entry needs "email" and/or "id" (Azure AD object ID),
                  plus optional "displayName". Emails are auto-resolved to
                  object IDs via teams_search_people. Merged with any
                  config-level mentions for the resolved target.
                  Example: '[{"email": "john@company.com"}]'

    Returns:
        JSON result with send status and message ID.
    """
    parsed_vars: dict[str, str] = {}
    if variables:
        try:
            parsed_vars = json.loads(variables)
            if not isinstance(parsed_vars, dict):
                return json.dumps({
                    "status": "error",
                    "code": "INVALID_INPUT",
                    "message": "variables must be a JSON object of key-value pairs",
                }, indent=2)
        except json.JSONDecodeError as exc:
            return json.dumps({
                "status": "error",
                "code": "INVALID_INPUT",
                "message": f"Invalid JSON in variables: {exc}",
            }, indent=2)

    parsed_mentions: list[dict[str, str]] = []
    if mentions:
        try:
            parsed_mentions = json.loads(mentions)
            if not isinstance(parsed_mentions, list):
                return json.dumps({
                    "status": "error",
                    "code": "INVALID_INPUT",
                    "message": "mentions must be a JSON array of objects",
                }, indent=2)
        except json.JSONDecodeError as exc:
            return json.dumps({
                "status": "error",
                "code": "INVALID_INPUT",
                "message": f"Invalid JSON in mentions: {exc}",
            }, indent=2)

    result = await teams_api.send_message(
        conversation_id=conversation_id,
        content=message,
        subject=subject or None,
        content_type=content_type,
        reply_to_message_id=reply_to_message_id or None,
        template=template or None,
        variables=parsed_vars or None,
        target=target or None,
        test_run_id=test_run_id or None,
        mentions=parsed_mentions or None,
    )

    if result.ok:
        return json.dumps({
            "status": "sent",
            "message": "Message delivered successfully",
            "details": result.value,
        }, indent=2)

    return json.dumps({
        "status": "error",
        "code": result.error.code.value,
        "message": result.error.message,
        "suggestions": result.error.suggestions,
    }, indent=2)


@mcp.tool()
async def teams_get_me() -> str:
    """
    Get the current user's profile information.

    Returns display name, email, Azure AD object ID, tenant ID, and Teams MRI.
    Useful for finding your own @mention or identifying the current user.
    No network calls — reads from local session tokens only.
    Call teams_login first if not yet authenticated.

    Returns:
        JSON user profile with identity fields.
    """
    profile = token_extractor.get_user_profile()

    if not profile:
        return json.dumps({
            "status": "error",
            "code": "AUTH_REQUIRED",
            "message": "No valid session. Please use teams_login first.",
            "suggestions": ["Call teams_login to authenticate"],
        }, indent=2)

    return json.dumps({
        "displayName": profile.display_name,
        "email": profile.email,
        "objectId": profile.object_id,
        "tenantId": profile.tenant_id,
        "mri": f"8:orgid:{profile.object_id}",
    }, indent=2)


@mcp.tool()
async def teams_search(
    query: str,
    max_results: int = 25,
    from_offset: int = 0,
    size: int = 25,
) -> str:
    """
    Search for messages in Microsoft Teams.

    Returns matching messages with sender, timestamp, content, conversationId
    (for replies), and pagination info. Results sorted by recency.

    Supported search operators:
      from:email       — filter by sender (email or name)
      to:name          — filter by recipient (use spaces not dots: "to:rob macdonald")
      sent:YYYY-MM-DD  — filter by date (also sent:>=YYYY-MM-DD, sent:today)
      is:Messages      — only messages (case-sensitive, plural required)
      is:Channels      — only channel posts
      is:Chats         — only chat messages
      hasattachment:true — only messages with attachments
      "Display Name"   — search for @mentions (quote the name)
      NOT              — exclude terms (e.g., "budget NOT draft")

    Note: in:channel only works reliably WITH content terms (e.g., "budget in:IT Support").
    Use teams_get_me first to get your email/displayName for from: queries.

    Args:
        query: Search query with optional operators.
        max_results: Maximum results to return (default: 25).
        from_offset: Pagination offset, 0-based (default: 0).
        size: Page size per request (default: 25).

    Returns:
        JSON with results array and pagination info.
    """
    result = await substrate_api.search_messages(
        query,
        from_=from_offset,
        size=size,
        max_results=max_results,
    )

    if not result.ok:
        return json.dumps({
            "status": "error",
            "code": result.error.code.value,
            "message": result.error.message,
            "suggestions": result.error.suggestions,
        }, indent=2)

    pagination = result.value["pagination"]
    return json.dumps({
        "query": query,
        "resultCount": len(result.value["results"]),
        "pagination": {
            "from": pagination["from"],
            "size": pagination["size"],
            "returned": pagination["returned"],
            "total": pagination["total"],
            "hasMore": pagination["hasMore"],
            "nextFrom": (
                pagination["from"] + pagination["returned"]
                if pagination["hasMore"]
                else None
            ),
        },
        "results": result.value["results"],
    }, indent=2)


@mcp.tool()
async def teams_search_people(
    query: str,
    limit: int = 10,
) -> str:
    """
    Search for people in Microsoft Teams by name or email.

    Returns matching users with display name, email, job title, department,
    and Teams MRI (for @mentions in messages). Useful for finding someone
    to message or resolving a name to an email address.

    Args:
        query: Search term — name, email address, or partial match.
        limit: Maximum number of results (default: 10).

    Returns:
        JSON with results array and count.
    """
    result = await substrate_api.search_people(query, limit=limit)

    if not result.ok:
        return json.dumps({
            "status": "error",
            "code": result.error.code.value,
            "message": result.error.message,
            "suggestions": result.error.suggestions,
        }, indent=2)

    return json.dumps({
        "query": query,
        "returned": result.value["returned"],
        "results": result.value["results"],
    }, indent=2)


@mcp.tool()
async def teams_find_channel(
    query: str,
    limit: int = 10,
) -> str:
    """
    Find Teams channels by name.

    Searches both (1) channels in teams you are a member of (reliable) and
    (2) channels across the organisation (discovery). Results indicate
    whether you are already a member via the isMember field.

    Use this when you know part of a channel name but need the channel ID.
    Use teams_list_channels instead to browse all your channels.

    Channel IDs can be used as conversation_id in teams_send_message.

    Args:
        query: Channel name to search for (partial match).
        limit: Maximum number of results (default: 10, max: 50).

    Returns:
        JSON with matching channels including channelId, channelName,
        teamName, and isMember status.
    """
    result = await substrate_api.search_channels(query, limit=limit)

    if not result.ok:
        return json.dumps({
            "status": "error",
            "code": result.error.code.value,
            "message": result.error.message,
            "suggestions": result.error.suggestions,
        }, indent=2)

    return json.dumps({
        "query": query,
        "count": result.value["returned"],
        "channels": result.value["results"],
    }, indent=2)


@mcp.tool()
async def teams_get_chat(
    user_identifier: str,
) -> str:
    """
    Get the 1:1 chat conversation ID for another user.

    The conversation ID is deterministic — the chat is implicitly created
    when the first message is sent. Use the returned conversationId with
    teams_send_message.

    Args:
        user_identifier: The other user's MRI (8:orgid:guid), object ID,
                         or raw GUID. Use teams_search_people to find it.

    Returns:
        JSON with conversationId, otherUserId, and currentUserId.
    """
    result = teams_api.get_one_on_one_chat_id(user_identifier)

    if result.ok:
        return json.dumps(result.value, indent=2)

    return json.dumps({
        "status": "error",
        "code": result.error.code.value,
        "message": result.error.message,
        "suggestions": result.error.suggestions,
    }, indent=2)


@mcp.tool()
async def teams_create_group_chat(
    member_identifiers: str,
    topic: str = "",
) -> str:
    """
    Create a new group chat with multiple members.

    Requires at least 2 other members (you are added automatically).
    Use teams_search_people to find user MRIs/IDs first.

    Args:
        member_identifiers: JSON array of user identifiers (MRIs, object IDs,
                            or GUIDs). Example: '["8:orgid:abc...", "8:orgid:def..."]'
        topic: Optional chat topic/name shown in Teams.

    Returns:
        JSON with conversationId and member list.
    """
    try:
        members = json.loads(member_identifiers)
        if not isinstance(members, list):
            return json.dumps({
                "status": "error",
                "code": "INVALID_INPUT",
                "message": "member_identifiers must be a JSON array of strings",
            }, indent=2)
    except json.JSONDecodeError as exc:
        return json.dumps({
            "status": "error",
            "code": "INVALID_INPUT",
            "message": f"Invalid JSON in member_identifiers: {exc}",
        }, indent=2)

    result = await teams_api.create_group_chat(
        member_identifiers=members,
        topic=topic or None,
    )

    if result.ok:
        return json.dumps({
            "status": "created",
            "message": "Group chat created successfully",
            "details": result.value,
        }, indent=2)

    return json.dumps({
        "status": "error",
        "code": result.error.code.value,
        "message": result.error.message,
        "suggestions": result.error.suggestions,
    }, indent=2)


if __name__ == "__main__":
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        print("Shutting down MS Teams MCP…")
