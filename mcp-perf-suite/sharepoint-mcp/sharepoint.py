"""
SharePoint MCP Server

Tools:
  sharepoint_login          — authenticate to SharePoint via browser-based SSO/manual login
  sharepoint_status         — check authentication state and token health
  sharepoint_upload_file    — upload a single file to a SharePoint document library
  sharepoint_upload_folder  — upload an entire local folder (recursive) to SharePoint
  sharepoint_create_folder  — create a folder in a SharePoint document library
  sharepoint_list_folder    — list contents of a SharePoint folder
  sharepoint_list_libraries — list document libraries in a SharePoint site
  sharepoint_get_me         — get the current user's profile from the Bearer token
  sharepoint_search         — search SharePoint content using KQL
  sharepoint_download_file  — download a file from SharePoint to local disk
"""

import json
import logging
import sys
from fastmcp import FastMCP
from utils.config import load_config
from services import (
    auth_manager,
    sharepoint_api,
    token_extractor,
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
logger = logging.getLogger("sharepoint-mcp")

mcp = FastMCP(server_cfg.get("name", "sharepoint-mcp"))


@mcp.tool()
async def sharepoint_login(force: bool = False) -> str:
    """
    Authenticate to SharePoint.

    Attempts SSO first (cached session), then headless browser refresh,
    then interactive browser login as a last resort. The tenant is
    auto-detected from the browser URL after login.

    Args:
        force: Skip cached tokens and force a fresh browser login.

    Returns:
        JSON status with authentication result, user info, and tenant.
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
async def sharepoint_status() -> str:
    """
    Check the current authentication and session health.

    Returns token validity, session age, tenant info, user info,
    and diagnostics. No network calls — reads from local cache only.

    Returns:
        JSON diagnostic snapshot of auth state.
    """
    status = auth_manager.get_status()
    return json.dumps(status, indent=2, default=str)


@mcp.tool()
async def sharepoint_upload_file(
    site_url: str,
    destination_folder: str,
    local_file_path: str,
) -> str:
    """
    Upload a single file to a SharePoint document library folder.

    Both site_url and destination_folder are required — there is no
    default upload location.

    Args:
        site_url: Full SharePoint site URL.
                  Example: "https://contoso.sharepoint.com/sites/PerfTesting"
        destination_folder: Server-relative folder path in the document library.
                           Example: "/sites/PerfTesting/Shared Documents/Results/2026-05-11"
        local_file_path: Absolute path to the local file to upload.
                        Example: "C:/artifacts/run-01/report.pdf"

    Returns:
        JSON with upload result (file name, SharePoint URL, size).
    """
    if not site_url or not site_url.strip():
        return json.dumps({
            "status": "warning",
            "message": "site_url is required. Provide the full SharePoint site URL "
                       "(e.g. https://contoso.sharepoint.com/sites/PerfTesting).",
            "suggestions": [
                "Call sharepoint_status to check if tenant is auto-detected",
                "Ask the user for their SharePoint site URL",
            ],
        }, indent=2)

    if not destination_folder or not destination_folder.strip():
        return json.dumps({
            "status": "warning",
            "message": "destination_folder is required. Provide the server-relative folder path "
                       "(e.g. /sites/PerfTesting/Shared Documents/Results).",
            "suggestions": [
                "Call sharepoint_list_folder to browse available folders",
                "Ask the user where they want to upload the file",
            ],
        }, indent=2)

    if not local_file_path or not local_file_path.strip():
        return json.dumps({
            "status": "warning",
            "message": "local_file_path is required. Provide the full path to the local file.",
        }, indent=2)

    result = await sharepoint_api.upload_file(
        site_url=site_url.strip(),
        destination_folder=destination_folder.strip(),
        local_file_path=local_file_path.strip(),
    )

    if result.ok:
        return json.dumps({
            "status": "uploaded",
            "message": f"File '{result.value['name']}' uploaded successfully",
            "details": result.value,
        }, indent=2)

    return json.dumps({
        "status": "error",
        "code": result.error.code.value,
        "message": result.error.message,
        "suggestions": result.error.suggestions,
    }, indent=2)


@mcp.tool()
async def sharepoint_upload_folder(
    site_url: str,
    destination_folder: str,
    local_folder_path: str,
) -> str:
    """
    Upload an entire local folder (recursive) to a SharePoint document library.

    All files in the local folder and its subfolders are uploaded,
    preserving the directory structure. Both site_url and
    destination_folder are required.

    Args:
        site_url: Full SharePoint site URL.
                  Example: "https://contoso.sharepoint.com/sites/PerfTesting"
        destination_folder: Server-relative folder path for the upload root.
                           Example: "/sites/PerfTesting/Shared Documents/Results/run-01"
        local_folder_path: Absolute path to the local folder to upload.
                          Example: "C:/artifacts/run-01"

    Returns:
        JSON with upload summary (files uploaded, total size, errors).
    """
    if not site_url or not site_url.strip():
        return json.dumps({
            "status": "warning",
            "message": "site_url is required. Provide the full SharePoint site URL "
                       "(e.g. https://contoso.sharepoint.com/sites/PerfTesting).",
            "suggestions": [
                "Call sharepoint_status to check if tenant is auto-detected",
                "Ask the user for their SharePoint site URL",
            ],
        }, indent=2)

    if not destination_folder or not destination_folder.strip():
        return json.dumps({
            "status": "warning",
            "message": "destination_folder is required. Provide the server-relative folder path "
                       "(e.g. /sites/PerfTesting/Shared Documents/Results/run-01).",
            "suggestions": [
                "Call sharepoint_list_folder to browse available folders",
                "Ask the user where they want to upload the folder",
            ],
        }, indent=2)

    if not local_folder_path or not local_folder_path.strip():
        return json.dumps({
            "status": "warning",
            "message": "local_folder_path is required. Provide the full path to the local folder.",
        }, indent=2)

    result = await sharepoint_api.upload_folder(
        site_url=site_url.strip(),
        destination_folder=destination_folder.strip(),
        local_folder_path=local_folder_path.strip(),
    )

    if result.ok:
        value = result.value
        msg = (
            f"Uploaded {value['filesUploaded']}/{value['filesTotal']} files "
            f"({value['totalSizeBytes'] / (1024*1024):.1f} MB) "
            f"to {value['destinationFolder']}"
        )
        if value.get("errors"):
            msg += f" ({len(value['errors'])} errors)"

        return json.dumps({
            "status": "completed" if not value.get("errors") else "completed_with_errors",
            "message": msg,
            "details": value,
        }, indent=2)

    return json.dumps({
        "status": "error",
        "code": result.error.code.value,
        "message": result.error.message,
        "suggestions": result.error.suggestions,
    }, indent=2)


@mcp.tool()
async def sharepoint_create_folder(
    site_url: str,
    folder_path: str,
) -> str:
    """
    Create a folder in a SharePoint document library.

    Creates the full folder path (including parent folders if needed).
    If the folder already exists, returns success without error.

    Args:
        site_url: Full SharePoint site URL.
                  Example: "https://contoso.sharepoint.com/sites/PerfTesting"
        folder_path: Server-relative folder path to create.
                    Example: "/sites/PerfTesting/Shared Documents/Results/2026-05-11"

    Returns:
        JSON with folder creation result.
    """
    if not site_url or not site_url.strip():
        return json.dumps({
            "status": "warning",
            "message": "site_url is required.",
        }, indent=2)

    if not folder_path or not folder_path.strip():
        return json.dumps({
            "status": "warning",
            "message": "folder_path is required.",
        }, indent=2)

    result = await sharepoint_api.create_folder(
        site_url=site_url.strip(),
        folder_path=folder_path.strip(),
    )

    if result.ok:
        value = result.value
        status = "already_exists" if value.get("alreadyExisted") else "created"
        return json.dumps({
            "status": status,
            "message": f"Folder '{value['name']}' {'already exists' if value.get('alreadyExisted') else 'created successfully'}",
            "details": value,
        }, indent=2)

    return json.dumps({
        "status": "error",
        "code": result.error.code.value,
        "message": result.error.message,
        "suggestions": result.error.suggestions,
    }, indent=2)


@mcp.tool()
async def sharepoint_list_folder(
    site_url: str,
    folder_path: str,
) -> str:
    """
    List contents of a SharePoint folder (files and subfolders).

    Returns files with name, URL, size, and last modified date.
    Returns subfolders with name, URL, and item count.

    Args:
        site_url: Full SharePoint site URL.
                  Example: "https://contoso.sharepoint.com/sites/PerfTesting"
        folder_path: Server-relative folder path to list.
                    Example: "/sites/PerfTesting/Shared Documents"

    Returns:
        JSON with files and folders arrays.
    """
    if not site_url or not site_url.strip():
        return json.dumps({
            "status": "warning",
            "message": "site_url is required.",
        }, indent=2)

    if not folder_path or not folder_path.strip():
        return json.dumps({
            "status": "warning",
            "message": "folder_path is required.",
        }, indent=2)

    result = await sharepoint_api.list_folder(
        site_url=site_url.strip(),
        folder_path=folder_path.strip(),
    )

    if result.ok:
        return json.dumps(result.value, indent=2)

    return json.dumps({
        "status": "error",
        "code": result.error.code.value,
        "message": result.error.message,
        "suggestions": result.error.suggestions,
    }, indent=2)


@mcp.tool()
async def sharepoint_list_libraries(
    site_url: str,
) -> str:
    """
    List all document libraries in a SharePoint site.

    Returns library names, URLs, item counts, and descriptions.
    Useful for discovering where to upload files before calling
    sharepoint_upload_file or sharepoint_upload_folder.

    Args:
        site_url: Full SharePoint site URL.
                  Example: "https://contoso.sharepoint.com/sites/PerfTesting"

    Returns:
        JSON array of document libraries.
    """
    if not site_url or not site_url.strip():
        return json.dumps({
            "status": "warning",
            "message": "site_url is required.",
        }, indent=2)

    result = await sharepoint_api.list_libraries(site_url=site_url.strip())

    if result.ok:
        return json.dumps({
            "siteUrl": site_url.strip(),
            "libraryCount": len(result.value),
            "libraries": result.value,
        }, indent=2)

    return json.dumps({
        "status": "error",
        "code": result.error.code.value,
        "message": result.error.message,
        "suggestions": result.error.suggestions,
    }, indent=2)


@mcp.tool()
async def sharepoint_get_me() -> str:
    """
    Get the current user's profile information.

    Returns display name, email, Azure AD object ID, and tenant ID.
    No network calls — reads from the cached Bearer token's JWT claims.
    Call sharepoint_login first if not yet authenticated.

    Returns:
        JSON user profile with identity fields.
    """
    profile = token_extractor.get_user_profile_from_token()

    if not profile:
        return json.dumps({
            "status": "error",
            "code": "AUTH_REQUIRED",
            "message": "No valid session. Please use sharepoint_login first.",
            "suggestions": ["Call sharepoint_login to authenticate"],
        }, indent=2)

    return json.dumps({
        "displayName": profile.display_name,
        "email": profile.email,
        "objectId": profile.object_id,
        "tenantId": profile.tenant_id,
        "tenant": auth_manager.get_tenant(),
    }, indent=2)


@mcp.tool()
async def sharepoint_search(
    site_url: str,
    query: str,
    max_results: int = 25,
) -> str:
    """
    Search SharePoint content using KQL (Keyword Query Language).

    Returns matching items with title, path, content type, author,
    and last modified date. Supports KQL operators for filtering.

    Common KQL examples:
      "load test results"               — full-text search
      "FileExtension:pdf"               — only PDF files
      "Author:homer"                    — by author name
      "path:sites/PerfTesting"          — within a specific site
      "LastModifiedTime>2026-05-01"     — modified after a date

    Args:
        site_url: Full SharePoint site URL (used as API base).
        query: KQL search query string.
        max_results: Maximum results to return (default: 25).

    Returns:
        JSON with search results and total count.
    """
    if not site_url or not site_url.strip():
        return json.dumps({
            "status": "warning",
            "message": "site_url is required.",
        }, indent=2)

    if not query or not query.strip():
        return json.dumps({
            "status": "warning",
            "message": "query is required. Provide a KQL search query.",
        }, indent=2)

    result = await sharepoint_api.search(
        site_url=site_url.strip(),
        query_text=query.strip(),
        max_results=max_results,
    )

    if result.ok:
        return json.dumps(result.value, indent=2)

    return json.dumps({
        "status": "error",
        "code": result.error.code.value,
        "message": result.error.message,
        "suggestions": result.error.suggestions,
    }, indent=2)


@mcp.tool()
async def sharepoint_download_file(
    site_url: str,
    server_relative_url: str,
    local_destination: str,
) -> str:
    """
    Download a file from SharePoint to a local path.

    Args:
        site_url: Full SharePoint site URL.
                  Example: "https://contoso.sharepoint.com/sites/PerfTesting"
        server_relative_url: Server-relative URL of the file.
                            Example: "/sites/PerfTesting/Shared Documents/report.pdf"
        local_destination: Local file path to save to (directories created automatically).
                          Example: "C:/downloads/report.pdf"

    Returns:
        JSON with download result (file name, size, local path).
    """
    if not site_url or not site_url.strip():
        return json.dumps({
            "status": "warning",
            "message": "site_url is required.",
        }, indent=2)

    if not server_relative_url or not server_relative_url.strip():
        return json.dumps({
            "status": "warning",
            "message": "server_relative_url is required. Provide the server-relative URL of the file.",
        }, indent=2)

    if not local_destination or not local_destination.strip():
        return json.dumps({
            "status": "warning",
            "message": "local_destination is required. Provide the local file path to save to.",
        }, indent=2)

    result = await sharepoint_api.download_file(
        site_url=site_url.strip(),
        server_relative_url=server_relative_url.strip(),
        local_destination=local_destination.strip(),
    )

    if result.ok:
        return json.dumps({
            "status": "downloaded",
            "message": f"File '{result.value['name']}' downloaded successfully",
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
        print("Shutting down SharePoint MCP…")
