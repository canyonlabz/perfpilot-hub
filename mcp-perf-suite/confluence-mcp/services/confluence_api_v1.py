# Confluence v1 APIs (On-Prem)
import os
import json
import httpx
import base64
from typing import Union
from fastmcp import Context
from dotenv import load_dotenv
from utils.config import load_config

# Load environment variables from .env file such as API keys and secrets
load_dotenv()

# Load the config.yaml which contains path folder settings. NOTE: OS specific yaml files will override default config.yaml
config = load_config()
cnf_config = config.get('confluence', {})
artifacts_base = config['artifacts']['artifacts_path']

# --- On‑Prem (v1) ---
CONFLUENCE_V1_BASE_URL = os.getenv("CONFLUENCE_V1_BASE_URL")
CONFLUENCE_V1_PAT = os.getenv("CONFLUENCE_V1_PAT")
CONFLUENCE_V1_USER = os.getenv("CONFLUENCE_V1_USER")

# CA bundle path for SSL verification
CA_BUNDLE = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")

# -----------------------------
# Confluence v1 API functions
# -----------------------------
async def list_spaces_v1(ctx: Context) -> list:
    """
    Lists all spaces in the on-prem Confluence instance.
    Args:
        ctx (Context): FastMCP invocation context.
    Returns:
        List of spaces with 'space_ref', 'name', 'type', 'status', and 'url'.
    """
    # Load environment/config as needed
    base_url = CONFLUENCE_V1_BASE_URL
    url = f"{base_url}/rest/api/space"
    headers = get_headers({"Accept": "application/json"})
    verify_ssl = get_ssl_verify_setting()

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.get(url, headers=headers)
        data = response.json()

    spaces = []
    for item in data["results"]:
        spaces.append({
            "space_ref": item.get("key"),
            "name": item.get("name"),
            "type": item.get("type", "global"),
            "status": item.get("status"),
            "url": f"{base_url}/spaces/{item.get('key')}/overview",
        })
    return spaces

async def get_space_details_v1(space_ref: str, ctx: Context) -> dict:
    """
    Retrieves metadata and configuration details for a specific on-prem Confluence space.
    Args:
        space_ref (str): The space key identifier.
        ctx (Context): FastMCP context for workflow chaining and error reporting.
    Returns:
        dict: Space metadata including 'space_ref', 'name', 'type', 'description', 'status', and additional metadata.
    """
    base_url = CONFLUENCE_V1_BASE_URL
    url = f"{base_url}/rest/api/space/{space_ref}"
    headers = get_headers({"Accept": "application/json"})
    verify_ssl = get_ssl_verify_setting()

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.get(url, headers=headers)
        item = response.json()

    details = {
        "space_ref": item.get("key"),
        "id": item.get("id"),
        "name": item.get("name"),
        "type": item.get("type"),
        "status": item.get("status"),
        "description": item.get("_expandable", {}).get("description", ""),
        "creator": item.get("creator", {}).get("displayName"),
        "created_at": item.get("creationDate"),
        "last_modified_by": item.get("lastModifier", {}).get("displayName"),
        "last_modified_at": item.get("lastModificationDate"),
        "web_url": base_url + item.get("_links", {}).get("webui", ""),
        "homepage_id": item.get("_expandable", {}).get("homepage"),
    }
    return details

async def list_pages_v1(space_ref: str, ctx: Context) -> list:
    """
    Lists all pages in a specific on-prem Confluence space.
    
    Args:
        space_ref (str): Space key for on-prem (v1).
        ctx (Context): FastMCP invocation context.
    
    Returns:
        List of pages with 'page_ref', 'title', 'status', 'url', and 'type'.
    """
    base_url = CONFLUENCE_V1_BASE_URL
    url = f"{base_url}/rest/api/content"
    params = {"spaceKey": space_ref, "type": "page"}
    headers = get_headers({"Accept": "application/json"})
    verify_ssl = get_ssl_verify_setting()
    
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.get(url, headers=headers, params=params)
        data = response.json()
    
    pages = []
    for item in data["results"]:
        pages.append({
            "page_ref": item.get("id"),
            "title": item.get("title"),
            "status": item.get("status"),
            "type": item.get("type"),
            "url": base_url + item.get("_links", {}).get("webui", ""),
        })
    
    return pages

async def get_page_by_id_v1(page_ref: str, ctx: Context) -> dict:
    """
    Retrieves metadata for a specific page by ID in on-prem Confluence.
    
    Args:
        page_ref (str): Page ID.
        ctx (Context): FastMCP invocation context.
    
    Returns:
        dict: Page metadata including id, title, status, space info, version, history, and URLs.
    """
    base_url = CONFLUENCE_V1_BASE_URL
    url = f"{base_url}/rest/api/content/{page_ref}"
    headers = get_headers({"Accept": "application/json"})
    verify_ssl = get_ssl_verify_setting()
    
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.get(url, headers=headers)
        item = response.json()
    
    page_data = {
        "page_ref": item.get("id"),
        "title": item.get("title"),
        "type": item.get("type"),
        "status": item.get("status"),
        "space_key": item.get("space", {}).get("key"),
        "space_name": item.get("space", {}).get("name"),
        "version": item.get("version", {}).get("number"),
        "version_message": item.get("version", {}).get("message"),
        "last_modified_by": item.get("version", {}).get("by", {}).get("displayName"),
        "last_modified_at": item.get("version", {}).get("when"),
        "created_by": item.get("history", {}).get("createdBy", {}).get("displayName"),
        "created_at": item.get("history", {}).get("createdDate"),
        "url": base_url + item.get("_links", {}).get("webui", ""),
    }
    
    return page_data

async def get_page_content_v1(page_ref: str, ctx: Context) -> dict:
    """
    Retrieves the full content body of a Confluence page in storage format (XHTML).
    
    Args:
        page_ref (str): Page ID.
        ctx (Context): FastMCP invocation context.
    
    Returns:
        dict: Page content including:
            - page_ref: Page ID
            - title: Page title
            - storage_xhtml: Full XHTML content in Confluence storage format
            - status: Page status
            - url: Page URL
    """
    base_url = CONFLUENCE_V1_BASE_URL
    url = f"{base_url}/rest/api/content/{page_ref}"
    params = {"expand": "body.storage"}
    headers = get_headers({"Accept": "application/json"})
    verify_ssl = get_ssl_verify_setting()
    
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.get(url, headers=headers, params=params)
        item = response.json()
    
    content_data = {
        "page_ref": item.get("id"),
        "title": item.get("title"),
        "status": item.get("status"),
        "storage_xhtml": item.get("body", {}).get("storage", {}).get("value", ""),
        "representation": item.get("body", {}).get("storage", {}).get("representation"),
        "url": base_url + item.get("_links", {}).get("webui", ""),
    }
    
    return content_data

async def create_page_v1(space_ref: str, title: str, storage_xhtml: str, ctx: Context, parent_id: str) -> dict:
    """
    Creates a new Confluence page in on-prem instance.
    
    Args:
        space_ref (str): Space key for on-prem (v1).
        title (str): Page title.
        storage_xhtml (str): Page content in Confluence storage format (XHTML).
        ctx (Context): FastMCP invocation context.
        parent_id (str): Parent page ID to nest this page under.
    
    Returns:
        dict: Created page details including:
            - page_ref: Created page ID
            - title: Page title
            - url: Page URL
            - status: Result status ("created" or "error")
    """
    base_url = CONFLUENCE_V1_BASE_URL
    url = f"{base_url}/rest/api/content"
    headers = get_headers({"Content-Type": "application/json", "Accept": "application/json"})
    verify_ssl = get_ssl_verify_setting()
    
    # Build request payload
    payload = {
        "type": "page",
        "title": title,
        "space": {
            "key": space_ref
        },
        "parentId": parent_id,
        "body": {
            "storage": {
                "value": storage_xhtml,
                "representation": "storage"
            }
        }
    }
    
    # Add parent if specified
    if parent_id:
        payload["ancestors"] = [{"id": parent_id}]
    
    try:
        async with httpx.AsyncClient(verify=verify_ssl) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
        
        page_data = {
            "page_ref": result.get("id"),
            "title": result.get("title"),
            "url": base_url + result.get("_links", {}).get("webui", ""),
            "status": "created"
        }
        
        await ctx.info(f"Successfully created page '{title}' in space {space_ref} (v1).")
        return page_data
        
    except httpx.HTTPStatusError as e:
        error_msg = f"Failed to create page: {e.response.status_code} - {e.response.text}"
        return {"error": error_msg, "status": "error"}
    except Exception as e:
        error_msg = f"Failed to create page: {str(e)}"
        return {"error": error_msg, "status": "error"}

async def search_content_v1(query: str, space_key: str = None, ctx: Context = None) -> list:
    """
    Search Confluence content using CQL (v1 API for on-prem).
    
    Args:
        query (str): Search query (will be used in title and text search).
        space_key (str, optional): Limit search to specific space.
        ctx (Context): FastMCP context.
    
    Returns:
        List of matching pages with title, id, url, space, and excerpt.
    """
    base_url = CONFLUENCE_V1_BASE_URL
    
    # Build CQL query
    cql_parts = [f'type=page AND (title~"{query}" OR text~"{query}")']
    if space_key:
        cql_parts.append(f'space={space_key}')
    
    cql = " AND ".join(cql_parts)
    
    url = f"{base_url}/rest/api/content/search"
    params = {"cql": cql, "limit": 50}
    headers = get_headers({"Accept": "application/json"})
    verify_ssl = get_ssl_verify_setting()
    
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.get(url, headers=headers, params=params)
        data = response.json()
    
    results = []
    for item in data.get("results", []):
        results.append({
            "page_ref": item.get("content", {}).get("id") or item.get("id"),
            "title": item.get("title"),
            "type": item.get("content", {}).get("type") or item.get("type"),
            "space_key": item.get("space", {}).get("key"),
            "space_name": item.get("space", {}).get("name"),
            "url": item.get("url") or (base_url + item.get("content", {}).get("_links", {}).get("webui", "")),
            "excerpt": item.get("excerpt", ""),
            "last_modified": item.get("lastModified", ""),
        })
    
    if ctx:
        await ctx.info(f"Found {len(results)} results for query: {query}")
    
    return results


async def attach_file_v1(page_ref: str, file_path: str, ctx: Context) -> dict:
    """
    Attaches a file (typically a PNG chart image) to an existing Confluence page.
    
    Uses the v1 API endpoint: POST /rest/api/content/{pageId}/child/attachment
    with multipart/form-data encoding.
    
    Args:
        page_ref (str): Page ID to attach the file to.
        file_path (str): Full path to the file to upload.
        ctx (Context): FastMCP invocation context.
    
    Returns:
        dict: Attachment result including:
            - attachment_id: ID of the created attachment
            - filename: Name of the attached file
            - page_ref: Page the file was attached to
            - download_url: URL to download the attachment
            - status: "attached" on success, "error" on failure
    
    Example:
        result = await attach_file_v1("123456789", "/path/to/CPU_UTILIZATION_MULTILINE.png", ctx)
    """
    from pathlib import Path
    
    file_path_obj = Path(file_path)
    if not file_path_obj.exists():
        error_msg = f"File not found: {file_path}"
        return {"error": error_msg, "status": "error", "filename": file_path_obj.name}
    
    base_url = CONFLUENCE_V1_BASE_URL
    url = f"{base_url}/rest/api/content/{page_ref}/child/attachment"
    
    # Headers for multipart upload - no Content-Type header (httpx sets it with boundary)
    # X-Atlassian-Token: nocheck is required to bypass XSRF protection
    headers = get_headers({
        "Accept": "application/json",
        "X-Atlassian-Token": "nocheck"
    })
    
    verify_ssl = get_ssl_verify_setting()
    filename = file_path_obj.name
    
    try:
        # Read file content
        with open(file_path, 'rb') as f:
            file_content = f.read()
        
        # Determine content type
        content_type = "image/png" if filename.lower().endswith('.png') else "application/octet-stream"
        
        # Create multipart form data
        files = {
            'file': (filename, file_content, content_type)
        }
        
        async with httpx.AsyncClient(verify=verify_ssl, timeout=60.0) as client:
            response = await client.post(url, headers=headers, files=files)
            response.raise_for_status()
            result = response.json()
        
        # Parse response - v1 API returns results array
        results = result.get("results", [result])
        if results:
            attachment = results[0]
            attachment_data = {
                "attachment_id": attachment.get("id"),
                "filename": attachment.get("title", filename),
                "page_ref": page_ref,
                "download_url": base_url + attachment.get("_links", {}).get("download", ""),
                "status": "attached"
            }
            await ctx.info(f"Successfully attached '{filename}' to page {page_ref} (v1).")
            return attachment_data
        else:
            error_msg = "No attachment returned in response"
            return {"error": error_msg, "status": "error", "filename": filename}
            
    except httpx.HTTPStatusError as e:
        error_msg = f"Failed to attach file: {e.response.status_code} - {e.response.text}"
        return {"error": error_msg, "status": "error", "filename": filename}
    except Exception as e:
        error_msg = f"Failed to attach file: {str(e)}"
        return {"error": error_msg, "status": "error", "filename": filename}


async def update_page_v1(page_ref: str, storage_xhtml: str, ctx: Context) -> dict:
    """
    Updates an existing Confluence page with new content.
    
    Uses the v1 API endpoint: PUT /rest/api/content/{pageId}
    Automatically increments the version number.
    
    Args:
        page_ref (str): Page ID to update.
        storage_xhtml (str): New page content in Confluence storage format (XHTML).
                            Must be flattened (no newlines) for API submission.
        ctx (Context): FastMCP invocation context.
    
    Returns:
        dict: Update result including:
            - page_ref: Updated page ID
            - title: Page title
            - version: New version number
            - url: Page URL
            - status: "updated" on success, "error" on failure
    
    Example:
        result = await update_page_v1("123456789", "<p>Updated content</p>", ctx)
    """
    base_url = CONFLUENCE_V1_BASE_URL
    verify_ssl = get_ssl_verify_setting()
    
    # First, get current page info to retrieve version and title
    try:
        get_url = f"{base_url}/rest/api/content/{page_ref}"
        headers = get_headers({"Accept": "application/json"})
        
        async with httpx.AsyncClient(verify=verify_ssl) as client:
            response = await client.get(get_url, headers=headers)
            response.raise_for_status()
            current_page = response.json()
        
        current_version = current_page.get("version", {}).get("number", 1)
        title = current_page.get("title", "")
        space_key = current_page.get("space", {}).get("key", "")
        
    except httpx.HTTPStatusError as e:
        error_msg = f"Failed to get current page info: {e.response.status_code} - {e.response.text}"
        return {"error": error_msg, "status": "error", "page_ref": page_ref}
    except Exception as e:
        error_msg = f"Failed to get current page info: {str(e)}"
        return {"error": error_msg, "status": "error", "page_ref": page_ref}
    
    # Now update the page with incremented version
    try:
        update_url = f"{base_url}/rest/api/content/{page_ref}"
        headers = get_headers({"Content-Type": "application/json", "Accept": "application/json"})
        
        payload = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {
                "storage": {
                    "value": storage_xhtml,
                    "representation": "storage"
                }
            },
            "version": {
                "number": current_version + 1
            }
        }
        
        async with httpx.AsyncClient(verify=verify_ssl, timeout=60.0) as client:
            response = await client.put(update_url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
        
        page_data = {
            "page_ref": result.get("id"),
            "title": result.get("title"),
            "version": result.get("version", {}).get("number"),
            "url": base_url + result.get("_links", {}).get("webui", ""),
            "status": "updated"
        }
        
        await ctx.info(f"Successfully updated page '{title}' to version {page_data['version']} (v1).")
        return page_data
        
    except httpx.HTTPStatusError as e:
        error_msg = f"Failed to update page: {e.response.status_code} - {e.response.text}"
        return {"error": error_msg, "status": "error", "page_ref": page_ref}
    except Exception as e:
        error_msg = f"Failed to update page: {str(e)}"
        return {"error": error_msg, "status": "error", "page_ref": page_ref}


# -----------------------------
# Helper functions
# -----------------------------

def get_ssl_verify_setting() -> Union[str, bool]:
    """
    Determines SSL verification setting based on config.yaml.
    
    Returns:
        Union[str, bool]: 
            - Path to CA bundle (str) if ssl_verification is "ca_bundle" and certs are available
            - False if ssl_verification is "disabled"
            - True as fallback (use system certs)
    """
    ssl_verification = cnf_config.get('ssl_verification', 'ca_bundle').lower()
    
    if ssl_verification == 'disabled':
        return False
    elif ssl_verification == 'ca_bundle':
        # Use CA bundle if available, otherwise default to True
        return CA_BUNDLE or True
    else:
        # Default to system cert verification
        return True


def get_headers(extra: dict = None):
    """
    Generates authorization headers for Confluence on-prem (v1) API.
    Uses Bearer token authentication.
    
    Args:
        extra (dict, optional): Additional headers to include.
    
    Returns:
        dict: Headers dictionary with Authorization and any extra headers.
    """
    h = {
        "Authorization": f"Bearer {CONFLUENCE_V1_PAT}",
    }
    if extra:
        h.update(extra)
    return h