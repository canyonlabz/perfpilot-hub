"""
SharePoint REST API client.

Interacts with SharePoint's native _api/ endpoints for:
- Form Digest management (X-RequestDigest for write operations)
- File upload (single file, up to max_upload_size_mb)
- Folder upload (recursive, uploads all files in a local directory)
- Folder operations (create, list, check existence)

All methods return Result[T] for consistent error handling.
Tokens are obtained from auth_manager.get_bearer_token().
"""

import asyncio
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from . import auth_manager
from .errors import (
    ErrorCode,
    Result,
    ok,
    err,
    create_error,
    classify_http_error,
)
from utils.config import load_config

logger = logging.getLogger("sharepoint-mcp.api")

_config = load_config()
_sp_cfg = _config.get("sharepoint", {})

HTTP_TIMEOUT = _sp_cfg.get("http_request_timeout_sec", 60)
RETRY_MAX = _sp_cfg.get("retry_max_attempts", 3)
RETRY_BASE_DELAY = _sp_cfg.get("retry_base_delay_sec", 1)
RETRY_MAX_DELAY = _sp_cfg.get("retry_max_delay_sec", 10)
MAX_UPLOAD_SIZE_MB = _sp_cfg.get("max_upload_size_mb", 250)
CHUNK_SIZE_MB = _sp_cfg.get("chunk_size_mb", 10)
CHUNK_SIZE_BYTES = CHUNK_SIZE_MB * 1024 * 1024

# Form Digest cache (valid ~30 minutes, we refresh at 25 min)
_digest_cache: dict[str, Any] = {}
_DIGEST_REFRESH_SEC = 25 * 60


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def _get_auth_headers() -> Result[dict[str, str]]:
    """Build authorization headers using dual-mode auth (Bearer or cookies)."""
    return await auth_manager.get_auth_headers()


async def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict | None = None,
    data: bytes | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> Result[httpx.Response]:
    """Make an HTTP request with auth headers, retries, and error classification."""
    auth_result = await _get_auth_headers()
    if not auth_result.ok:
        return err(auth_result.error)

    req_headers = {**auth_result.value}
    if headers:
        req_headers.update(headers)
    if extra_headers:
        req_headers.update(extra_headers)

    effective_timeout = timeout or HTTP_TIMEOUT
    last_error: Exception | None = None

    for attempt in range(RETRY_MAX):
        try:
            async with httpx.AsyncClient(timeout=effective_timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers=req_headers,
                    json=json_body,
                    content=data,
                )

            if response.status_code < 400:
                return ok(response)

            error_code = classify_http_error(response.status_code)

            # 429 — respect Retry-After
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", RETRY_BASE_DELAY))
                if attempt < RETRY_MAX - 1:
                    logger.warning("Rate limited, retrying after %.1fs", retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                return err(create_error(
                    error_code,
                    f"Rate limited after {RETRY_MAX} attempts",
                    retry_after_sec=retry_after,
                ))

            # 401 — try cookie fallback if we were using Bearer auth
            if response.status_code == 401 and "Authorization" in req_headers:
                cookie_headers = auth_manager._get_cookie_auth_headers()
                if cookie_headers:
                    logger.info("Bearer auth returned 401, retrying with cookie auth")
                    retry_headers = {**cookie_headers}
                    if headers:
                        retry_headers.update(headers)
                    if extra_headers:
                        retry_headers.update(extra_headers)
                    try:
                        async with httpx.AsyncClient(timeout=effective_timeout) as client:
                            retry_resp = await client.request(
                                method,
                                url,
                                headers=retry_headers,
                                json=json_body,
                                content=data,
                            )
                        if retry_resp.status_code < 400:
                            return ok(retry_resp)
                        logger.warning("Cookie fallback also returned %d", retry_resp.status_code)
                    except Exception as cookie_exc:
                        logger.warning("Cookie fallback request failed: %s", cookie_exc)

            # 5xx — retry with exponential backoff
            if response.status_code >= 500 and attempt < RETRY_MAX - 1:
                delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                logger.warning("Server error %d, retrying in %.1fs", response.status_code, delay)
                await asyncio.sleep(delay)
                continue

            # 401/403/other 4xx — don't retry further
            error_msg = f"SharePoint API error {response.status_code}"
            try:
                body = response.json()
                sp_error = body.get("error", {})
                if isinstance(sp_error, dict):
                    error_msg = sp_error.get("message", {}).get("value", error_msg)
            except Exception:
                pass

            return err(create_error(error_code, error_msg))

        except httpx.TimeoutException:
            last_error = None
            if attempt < RETRY_MAX - 1:
                delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                logger.warning("Request timed out, retrying in %.1fs", delay)
                await asyncio.sleep(delay)
                continue
            return err(create_error(ErrorCode.TIMEOUT, f"Request timed out after {RETRY_MAX} attempts"))

        except Exception as exc:
            last_error = exc
            if attempt < RETRY_MAX - 1:
                delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                logger.warning("Request error: %s, retrying in %.1fs", exc, delay)
                await asyncio.sleep(delay)
                continue

    return err(create_error(
        ErrorCode.NETWORK_ERROR,
        f"Request failed after {RETRY_MAX} attempts: {last_error}",
    ))


# ---------------------------------------------------------------------------
# Form Digest (X-RequestDigest — required for all write operations)
# ---------------------------------------------------------------------------

async def get_form_digest(site_url: str) -> Result[str]:
    """Get a valid Form Digest value for write operations.

    The digest is cached per site_url and refreshed every 25 minutes
    (SharePoint digests expire after ~30 minutes).
    """
    cached = _digest_cache.get(site_url)
    if cached and (time.time() - cached["fetched_at"]) < _DIGEST_REFRESH_SEC:
        return ok(cached["digest"])

    result = await _request("POST", f"{site_url}/_api/contextinfo")
    if not result.ok:
        return err(result.error)

    try:
        body = result.value.json()
        digest = body["d"]["GetContextWebInformation"]["FormDigestValue"]
    except (KeyError, TypeError) as exc:
        return err(create_error(
            ErrorCode.API_ERROR,
            f"Failed to parse Form Digest from contextinfo response: {exc}",
        ))

    _digest_cache[site_url] = {"digest": digest, "fetched_at": time.time()}
    return ok(digest)


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------

async def upload_file(
    site_url: str,
    destination_folder: str,
    local_file_path: str,
) -> Result[dict[str, Any]]:
    """Upload a single file to a SharePoint document library folder.

    Args:
        site_url: Full SharePoint site URL (e.g. https://contoso.sharepoint.com/sites/PerfTesting)
        destination_folder: Server-relative folder path (e.g. /sites/PerfTesting/Shared Documents/Results)
        local_file_path: Absolute path to the local file to upload

    Returns:
        Result with file metadata (name, url, size) on success.
    """
    local_path = Path(local_file_path)
    if not local_path.is_file():
        return err(create_error(
            ErrorCode.INVALID_INPUT,
            f"Local file not found: {local_file_path}",
        ))

    file_size_mb = local_path.stat().st_size / (1024 * 1024)
    if file_size_mb > MAX_UPLOAD_SIZE_MB:
        logger.info(
            "File %.1f MB exceeds standard limit (%d MB), using chunked upload",
            file_size_mb, MAX_UPLOAD_SIZE_MB,
        )
        return await upload_file_chunked(site_url, destination_folder, local_file_path)

    digest_result = await get_form_digest(site_url)
    if not digest_result.ok:
        return err(digest_result.error)

    filename = local_path.name
    encoded_folder = quote(destination_folder, safe="/")
    upload_url = (
        f"{site_url}/_api/web/GetFolderByServerRelativeUrl('{encoded_folder}')"
        f"/Files/add(url='{quote(filename)}',overwrite=true)"
    )

    file_content = local_path.read_bytes()

    result = await _request(
        "POST",
        upload_url,
        data=file_content,
        extra_headers={
            "X-RequestDigest": digest_result.value,
            "Content-Length": str(len(file_content)),
        },
        timeout=max(HTTP_TIMEOUT, 120),
    )

    if not result.ok:
        return err(result.error)

    try:
        body = result.value.json()
        file_info = body.get("d", {})
        return ok({
            "name": file_info.get("Name", filename),
            "serverRelativeUrl": file_info.get("ServerRelativeUrl", ""),
            "size": file_info.get("Length", len(file_content)),
            "timeLastModified": file_info.get("TimeLastModified", ""),
        })
    except Exception:
        return ok({
            "name": filename,
            "serverRelativeUrl": f"{destination_folder}/{filename}",
            "size": len(file_content),
        })


# ---------------------------------------------------------------------------
# Chunked file upload (for files > MAX_UPLOAD_SIZE_MB)
# ---------------------------------------------------------------------------

async def upload_file_chunked(
    site_url: str,
    destination_folder: str,
    local_file_path: str,
) -> Result[dict[str, Any]]:
    """Upload a large file using SharePoint's chunked upload API.

    Uses the StartUpload / ContinueUpload / FinishUpload flow:
    1. Create an empty placeholder file via Files/add
    2. Get the file's UniqueId
    3. Upload chunks using startupload -> continueupload -> finishupload

    Args:
        site_url: Full SharePoint site URL
        destination_folder: Server-relative folder path
        local_file_path: Absolute path to the local file

    Returns:
        Result with file metadata (name, url, size) on success.
    """
    local_path = Path(local_file_path)
    if not local_path.is_file():
        return err(create_error(
            ErrorCode.INVALID_INPUT,
            f"Local file not found: {local_file_path}",
        ))

    file_size = local_path.stat().st_size
    filename = local_path.name

    digest_result = await get_form_digest(site_url)
    if not digest_result.ok:
        return err(digest_result.error)
    digest = digest_result.value

    write_headers = {"X-RequestDigest": digest}

    # Step 1: Create an empty placeholder file
    encoded_folder = quote(destination_folder, safe="/")
    placeholder_url = (
        f"{site_url}/_api/web/GetFolderByServerRelativeUrl('{encoded_folder}')"
        f"/Files/add(url='{quote(filename)}',overwrite=true)"
    )

    placeholder_result = await _request(
        "POST",
        placeholder_url,
        data=b"",
        extra_headers=write_headers,
    )
    if not placeholder_result.ok:
        return err(placeholder_result.error)

    # Step 2: Get the file's UniqueId
    file_server_relative = f"{destination_folder.rstrip('/')}/{filename}"
    encoded_file_path = quote(file_server_relative, safe="/")
    file_info_url = (
        f"{site_url}/_api/web/GetFileByServerRelativePath("
        f"decodedurl='{encoded_file_path}')?$select=UniqueId"
    )

    info_result = await _request("POST", file_info_url, extra_headers=write_headers)
    if not info_result.ok:
        return err(create_error(
            ErrorCode.UPLOAD_ERROR,
            f"Failed to get UniqueId for placeholder file: {info_result.error.message}",
        ))

    try:
        unique_id = info_result.value.json()["d"]["UniqueId"]
    except (KeyError, TypeError) as exc:
        return err(create_error(
            ErrorCode.UPLOAD_ERROR,
            f"Could not parse UniqueId from response: {exc}",
        ))

    # Step 3: Upload in chunks
    upload_guid = str(uuid.uuid4())
    file_offset = 0
    chunk_number = 0
    total_chunks = (file_size + CHUNK_SIZE_BYTES - 1) // CHUNK_SIZE_BYTES

    try:
        with open(local_file_path, "rb") as f:
            while True:
                chunk_data = f.read(CHUNK_SIZE_BYTES)
                if not chunk_data:
                    break

                chunk_number += 1
                is_last = (file_offset + len(chunk_data)) >= file_size

                if chunk_number == 1 and is_last:
                    # Single chunk that exceeds threshold but fits in one read
                    # (edge case near the boundary)
                    chunk_url = (
                        f"{site_url}/_api/web/getfilebyid('{unique_id}')"
                        f"/finishupload(uploadId=guid'{upload_guid}',fileOffset={file_offset})"
                    )
                elif chunk_number == 1:
                    chunk_url = (
                        f"{site_url}/_api/web/getfilebyid('{unique_id}')"
                        f"/startupload(uploadId=guid'{upload_guid}')"
                    )
                elif is_last:
                    chunk_url = (
                        f"{site_url}/_api/web/getfilebyid('{unique_id}')"
                        f"/finishupload(uploadId=guid'{upload_guid}',fileOffset={file_offset})"
                    )
                else:
                    chunk_url = (
                        f"{site_url}/_api/web/getfilebyid('{unique_id}')"
                        f"/continueupload(uploadId=guid'{upload_guid}',fileOffset={file_offset})"
                    )

                chunk_result = await _request(
                    "POST",
                    chunk_url,
                    data=chunk_data,
                    extra_headers=write_headers,
                    timeout=max(HTTP_TIMEOUT, 120),
                )

                if not chunk_result.ok:
                    return err(create_error(
                        ErrorCode.UPLOAD_ERROR,
                        f"Chunk {chunk_number}/{total_chunks} failed: {chunk_result.error.message}",
                    ))

                # Update offset from response for accuracy
                try:
                    resp_body = chunk_result.value.json()
                    d = resp_body.get("d", {})
                    if "StartUpload" in d:
                        file_offset = int(d["StartUpload"])
                    elif "ContinueUpload" in d:
                        file_offset = int(d["ContinueUpload"])
                    else:
                        file_offset += len(chunk_data)
                except Exception:
                    file_offset += len(chunk_data)

                logger.info(
                    "Chunk %d/%d uploaded (%.1f%%)",
                    chunk_number, total_chunks,
                    min(file_offset / file_size * 100, 100),
                )

    except Exception as exc:
        return err(create_error(
            ErrorCode.UPLOAD_ERROR,
            f"Chunked upload failed at chunk {chunk_number}: {exc}",
        ))

    return ok({
        "name": filename,
        "serverRelativeUrl": file_server_relative,
        "size": file_size,
        "uploadMethod": "chunked",
        "totalChunks": chunk_number,
        "chunkSizeMb": CHUNK_SIZE_MB,
    })


# ---------------------------------------------------------------------------
# Folder upload (recursive)
# ---------------------------------------------------------------------------

async def upload_folder(
    site_url: str,
    destination_folder: str,
    local_folder_path: str,
) -> Result[dict[str, Any]]:
    """Upload an entire local folder (recursive) to a SharePoint document library.

    Creates the destination folder structure in SharePoint and uploads
    all files, preserving the directory hierarchy.

    Args:
        site_url: Full SharePoint site URL
        destination_folder: Server-relative destination folder path
        local_folder_path: Absolute path to the local folder to upload

    Returns:
        Result with upload summary (file count, total size, errors).
    """
    local_root = Path(local_folder_path)
    if not local_root.is_dir():
        return err(create_error(
            ErrorCode.INVALID_INPUT,
            f"Local folder not found: {local_folder_path}",
        ))

    # Collect all files to upload
    files_to_upload: list[tuple[Path, str]] = []
    for file_path in local_root.rglob("*"):
        if not file_path.is_file():
            continue
        relative = file_path.relative_to(local_root)
        sp_folder = destination_folder.rstrip("/")
        if relative.parent != Path("."):
            sp_folder = f"{sp_folder}/{relative.parent.as_posix()}"
        files_to_upload.append((file_path, sp_folder))

    if not files_to_upload:
        return ok({
            "status": "empty",
            "message": "No files found in the local folder",
            "filesUploaded": 0,
            "totalSizeBytes": 0,
            "errors": [],
        })

    # Collect unique folders to create
    unique_folders = sorted(set(sp_folder for _, sp_folder in files_to_upload))

    # Create all necessary folders
    for folder_path in unique_folders:
        folder_result = await create_folder(site_url, folder_path)
        if not folder_result.ok:
            logger.warning("Failed to create folder %s: %s", folder_path, folder_result.error.message)

    # Upload files sequentially
    uploaded_count = 0
    total_size = 0
    errors: list[dict[str, str]] = []

    for file_path, sp_folder in files_to_upload:
        result = await upload_file(site_url, sp_folder, str(file_path))
        if result.ok:
            uploaded_count += 1
            total_size += file_path.stat().st_size
            logger.info("Uploaded: %s -> %s", file_path.name, sp_folder)
        else:
            errors.append({
                "file": str(file_path),
                "error": result.error.message,
            })
            logger.error("Failed to upload %s: %s", file_path.name, result.error.message)

    return ok({
        "status": "completed",
        "filesUploaded": uploaded_count,
        "filesTotal": len(files_to_upload),
        "totalSizeBytes": total_size,
        "destinationFolder": destination_folder,
        "errors": errors,
    })


# ---------------------------------------------------------------------------
# Folder operations
# ---------------------------------------------------------------------------

async def create_folder(
    site_url: str,
    folder_path: str,
) -> Result[dict[str, Any]]:
    """Create a folder in a SharePoint document library.

    Creates the full path recursively (creates parent folders if needed).

    Args:
        site_url: Full SharePoint site URL
        folder_path: Server-relative folder path to create

    Returns:
        Result with folder metadata on success.
    """
    digest_result = await get_form_digest(site_url)
    if not digest_result.ok:
        return err(digest_result.error)

    encoded_path = quote(folder_path, safe="/")
    url = f"{site_url}/_api/web/folders/add('{encoded_path}')"

    result = await _request(
        "POST",
        url,
        extra_headers={"X-RequestDigest": digest_result.value},
    )

    if not result.ok:
        # Folder may already exist — treat 500 with "already exists" as success
        if result.error.code == ErrorCode.API_ERROR and "already exists" in result.error.message.lower():
            return ok({
                "name": folder_path.rsplit("/", 1)[-1],
                "serverRelativeUrl": folder_path,
                "alreadyExisted": True,
            })
        return err(result.error)

    try:
        body = result.value.json()
        folder_info = body.get("d", {})
        return ok({
            "name": folder_info.get("Name", ""),
            "serverRelativeUrl": folder_info.get("ServerRelativeUrl", folder_path),
            "alreadyExisted": False,
        })
    except Exception:
        return ok({
            "name": folder_path.rsplit("/", 1)[-1],
            "serverRelativeUrl": folder_path,
            "alreadyExisted": False,
        })


async def list_folder(
    site_url: str,
    folder_path: str,
) -> Result[dict[str, Any]]:
    """List contents of a SharePoint folder (files and subfolders).

    Args:
        site_url: Full SharePoint site URL
        folder_path: Server-relative folder path to list

    Returns:
        Result with files and folders arrays.
    """
    encoded_path = quote(folder_path, safe="/")

    # Fetch files and folders in parallel
    files_url = (
        f"{site_url}/_api/web/GetFolderByServerRelativeUrl('{encoded_path}')"
        f"/Files?$select=Name,ServerRelativeUrl,Length,TimeLastModified"
    )
    folders_url = (
        f"{site_url}/_api/web/GetFolderByServerRelativeUrl('{encoded_path}')"
        f"/Folders?$select=Name,ServerRelativeUrl,ItemCount"
    )

    files_result, folders_result = await asyncio.gather(
        _request("GET", files_url),
        _request("GET", folders_url),
    )

    if not files_result.ok:
        return err(files_result.error)
    if not folders_result.ok:
        return err(folders_result.error)

    files = []
    try:
        for item in files_result.value.json().get("d", {}).get("results", []):
            files.append({
                "name": item.get("Name", ""),
                "serverRelativeUrl": item.get("ServerRelativeUrl", ""),
                "size": item.get("Length", 0),
                "lastModified": item.get("TimeLastModified", ""),
                "type": "file",
            })
    except Exception as exc:
        logger.warning("Failed to parse files response: %s", exc)

    folders = []
    try:
        for item in folders_result.value.json().get("d", {}).get("results", []):
            name = item.get("Name", "")
            if name in ("Forms",):
                continue
            folders.append({
                "name": name,
                "serverRelativeUrl": item.get("ServerRelativeUrl", ""),
                "itemCount": item.get("ItemCount", 0),
                "type": "folder",
            })
    except Exception as exc:
        logger.warning("Failed to parse folders response: %s", exc)

    return ok({
        "folderPath": folder_path,
        "fileCount": len(files),
        "folderCount": len(folders),
        "files": files,
        "folders": folders,
    })


async def folder_exists(
    site_url: str,
    folder_path: str,
) -> bool:
    """Check if a folder exists in SharePoint. Returns True/False."""
    encoded_path = quote(folder_path, safe="/")
    url = (
        f"{site_url}/_api/web/GetFolderByServerRelativeUrl('{encoded_path}')"
        f"?$select=Exists"
    )
    result = await _request("GET", url)
    if not result.ok:
        return False
    try:
        return result.value.json().get("d", {}).get("Exists", False)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Document library listing
# ---------------------------------------------------------------------------

async def list_libraries(
    site_url: str,
) -> Result[list[dict[str, Any]]]:
    """List all document libraries in a SharePoint site.

    Filters by BaseTemplate=101 (document libraries only, excludes
    system lists, task lists, etc.).

    Args:
        site_url: Full SharePoint site URL

    Returns:
        Result with a list of libraries (title, url, item count, description).
    """
    url = (
        f"{site_url}/_api/web/lists"
        f"?$filter=BaseTemplate eq 101 and Hidden eq false"
        f"&$select=Title,RootFolder/ServerRelativeUrl,ItemCount,Description"
        f"&$expand=RootFolder"
    )

    result = await _request("GET", url)
    if not result.ok:
        return err(result.error)

    libraries = []
    try:
        for item in result.value.json().get("d", {}).get("results", []):
            libraries.append({
                "title": item.get("Title", ""),
                "serverRelativeUrl": item.get("RootFolder", {}).get("ServerRelativeUrl", ""),
                "itemCount": item.get("ItemCount", 0),
                "description": item.get("Description", ""),
            })
    except Exception as exc:
        return err(create_error(
            ErrorCode.API_ERROR,
            f"Failed to parse libraries response: {exc}",
        ))

    return ok(libraries)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def search(
    site_url: str,
    query_text: str,
    max_results: int = 25,
) -> Result[dict[str, Any]]:
    """Search SharePoint content using KQL (Keyword Query Language).

    Args:
        site_url: Full SharePoint site URL (used as the API base)
        query_text: KQL search query
        max_results: Maximum number of results to return

    Returns:
        Result with search results (title, path, content type, author).
    """
    encoded_query = quote(query_text)
    url = (
        f"{site_url}/_api/search/query"
        f"?querytext='{encoded_query}'"
        f"&selectproperties='Title,Path,ContentType,Author,LastModifiedTime,Size'"
        f"&rowlimit={max_results}"
    )

    result = await _request("GET", url)
    if not result.ok:
        return err(result.error)

    results = []
    try:
        body = result.value.json()
        query_result = (
            body.get("d", {})
            .get("query", {})
            .get("PrimaryQueryResult", {})
            .get("RelevantResults", {})
        )
        total = query_result.get("TotalRows", 0)

        rows = query_result.get("Table", {}).get("Rows", {}).get("results", [])
        for row in rows:
            cells = row.get("Cells", {}).get("results", [])
            item = {cell["Key"]: cell["Value"] for cell in cells if "Key" in cell}
            results.append({
                "title": item.get("Title", ""),
                "path": item.get("Path", ""),
                "contentType": item.get("ContentType", ""),
                "author": item.get("Author", ""),
                "lastModified": item.get("LastModifiedTime", ""),
                "size": item.get("Size", ""),
            })
    except Exception as exc:
        return err(create_error(
            ErrorCode.API_ERROR,
            f"Failed to parse search response: {exc}",
        ))

    return ok({
        "query": query_text,
        "totalResults": total,
        "returnedResults": len(results),
        "results": results,
    })


# ---------------------------------------------------------------------------
# File download
# ---------------------------------------------------------------------------

async def download_file(
    site_url: str,
    server_relative_url: str,
    local_destination: str,
) -> Result[dict[str, Any]]:
    """Download a file from SharePoint to a local path.

    Args:
        site_url: Full SharePoint site URL
        server_relative_url: Server-relative URL of the file to download
        local_destination: Local file path to save to (directories are created if needed)

    Returns:
        Result with download details (name, size, local path).
    """
    encoded_url = quote(server_relative_url, safe="/")
    url = f"{site_url}/_api/web/GetFileByServerRelativeUrl('{encoded_url}')/$value"

    auth_result = await _get_auth_headers()
    if not auth_result.ok:
        return err(auth_result.error)

    try:
        dest_path = Path(local_destination)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient(timeout=max(HTTP_TIMEOUT, 120)) as client:
            response = await client.get(url, headers=auth_result.value)

        if response.status_code >= 400:
            error_code = classify_http_error(response.status_code)
            return err(create_error(
                error_code,
                f"Download failed with status {response.status_code}",
            ))

        dest_path.write_bytes(response.content)

        filename = server_relative_url.rsplit("/", 1)[-1]
        return ok({
            "name": filename,
            "serverRelativeUrl": server_relative_url,
            "localPath": str(dest_path),
            "sizeBytes": len(response.content),
        })

    except httpx.TimeoutException:
        return err(create_error(ErrorCode.TIMEOUT, "Download timed out"))
    except Exception as exc:
        return err(create_error(
            ErrorCode.NETWORK_ERROR,
            f"Download failed: {exc}",
        ))
