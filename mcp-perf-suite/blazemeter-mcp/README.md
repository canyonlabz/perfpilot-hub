# BlazeMeter MCP Server

Welcome to the BlazeMeter MCP Server! 🎉 This is a Python-based MCP server built with **FastMCP** to interact easily with BlazeMeter’s API for performance testing lifecycle management.

---

## ✨ Features

- **List workspaces, projects, and tests**: Discover all available test suites organized by workspace and project.
- **Start BlazeMeter load tests**: Trigger test runs for any configured test, directly via MCP actions.
- **Fetch detailed run summaries**: Retrieve key metrics—including response time aggregates—after each test run.
- **Download and manage test artifacts**: Fully automate retrieval, extraction, and processing of test result artifacts (`artifacts.zip`, JMeter logs, KPIs).
- **Flexible configuration loading**: Centralized `config.yaml` management for all paths and parameters.
- **Extensible utilities**: Modular codebase supporting new MCP server integrations (e.g. Datadog, test analysis/reporting).
- **Shared folder management**: List, inspect, and upload files to BlazeMeter shared folders via the API with automatic extension filtering.
- **Defensive error handling**: Robust input validation and artifact management for reliable automation.

---

## 🏁 Prerequisites

- Python 3.12.4 or higher installed  
- BlazeMeter API Key (set in `.env`)  

---

## 🚀 Getting Started

### 1. Clone the Repository

```
git clone https://github.com/canyonlabz/mcp-perf-suite.git
cd blazemeter_mcp_server
```

### 2. Create & Activate a Python Virtual Environment

This ensures the MCP server dependencies do not affect your global Python environment.

#### On macOS / Linux

```

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

```

#### On Windows (PowerShell)

```
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Configure Environment Variables

Create a `.env` file in the project root with your BlazeMeter API key:

```
BLAZEMETER_API_KEY=your_blazemeter_api_key_here
BLAZEMETER_API_SECRET=your_blazemeter_api_secret_here
BLAZEMETER_ACCOUNT_ID=your_blazemeter_account_id_here
BLAZEMETER_WORKSPACE_ID=your_blazemeter_workspace_id_here
```

---

## ▶️ Running the MCP Server

### Option 1: Run Directly with Python

```
python blazemeter.py
```

This runs the MCP server with the default `stdio` transport — ideal for running locally or integrating with Cursor AI.

---

### Option 2: Run Using `uv` (Recommended) ⚡️

You can use **uv** to simplify setup and execution. It manages dependencies and environments automatically.

#### Install `uv` (macOS, Linux, Windows PowerShell)

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

#### Run the MCP Server with `uv`

```
uv run blazemeter.py
```

---

## ⚙️ MCP Server Configuration (`mcp.json`)

You can create an `mcp.json` file to configure how Cursor or other MCP hosts start the server:

```
{
    "mcpServers": {
        "blazemeter": {
            "command": "uv",
            "args": [
                "--directory",
                "/path/to/your/blazemeter_mcp_server",
                "run",
                "blazemeter.py"
            ]
        }
    }
}
```

Replace `/path/to/your/blazemeter_mcp_server` with your local path.

---

## 🛠️ Usage

Your MCP server exposes these primary tools for Cursor, agents, or other MCP clients (with a short description of each):

| Tool | Description |
| :-- | :-- |
| `get_workspaces` | List all workspaces in your BlazeMeter account |
| `get_projects` | List projects for a specified workspace |
| `get_tests` | List all tests in a given project |
| `start_test` | Initiate a new BlazeMeter test run |
| `check_test_status` | Check the status breakdown of a BlazeMeter test run (running, completed, or error states) |
| `get_public_report_url` | Generate a shareable public BlazeMeter report URL for a completed test run |
| `list_test_runs` | List past runs (masters) for a test within a time range, with session IDs |
| `get_artifacts_path` | Return the configured local path for storing all test artifacts |
| `get_artifact_file_list` | Get downloadable artifact/log files for a specific session |
| `process_session_artifacts` | Downloads, extracts, and processes artifact ZIPs for all sessions of a run. Handles single and multi-session runs, with built-in retry and idempotent design |
| `get_run_results` | Fetch summary metrics and key performance indicators for a test run |
| `get_shared_folders` | List all shared folders in a workspace |
| `get_shared_folder_file_list` | List files inside a shared folder (name, size, last modified) |
| `upload_to_shared_folder` | Upload a file or directory of files to a shared folder (with extension filtering) |

#### Deprecated Tools (disabled by default)

The following tools have been replaced by `process_session_artifacts` and are disabled by default. They can be re-enabled by setting `enabled=True` on their `@mcp.tool()` decorator if needed for backwards compatibility.

| Tool | Description |
| :-- | :-- |
| `download_artifacts_zip` | Download `artifacts.zip` for a single session |
| `extract_artifact_zip` | Unpack the ZIP file and list all extracted files |
| `process_extracted_files` | Move/rename key files (e.g. `kpi.jtl` to `test-results.csv`) |

---

## 🔁 Typical Workflow

A standard BlazeMeter MCP workflow uses these tools in sequence for automated, robust performance testing:

1. **Start a Test**
    - `start_test`: Initiate a new BlazeMeter load test run.
2. **Poll for Status**
    - `check_test_status`: Monitor the test status until it completes or ends.
3. **Get Test Run Results**
    - `get_run_results`: Fetch summary metrics, session IDs, and key performance indicators for the run.
4. **Retrieve and Process Test Artifacts**
    - `process_session_artifacts`: Pass the `run_id` and `sessions_id` list from step 3. This single tool handles downloading, extracting, and processing all session artifacts.
      - **Single-session runs**: Produces `test-results.csv` and `jmeter.log`.
      - **Multi-session runs**: Combines JTL files into a single `test-results.csv` and produces numbered logs (`jmeter-1.log` through `jmeter-N.log`).
      - Built-in retry (up to 3 attempts per download) and idempotent — re-run with the same parameters to retry only failed sessions.
5. **Get Public Report URL (optional)**
    - `get_public_report_url`: Generate a shareable report link for stakeholders.
6. **List Past Runs (optional)**
    - `list_test_runs`: View previous completed runs for a given test within a time range.

---

## 📊 Result Summary Example

When you call `get_run_results(run_id)`, the server returns a user-friendly summary including:

- Test name, test and run IDs  
- Start and end times, duration  
- Max virtual users  
- Sample counts (total, passed, failed, errors)  
- Aggregate response times (min, max, avg, 90th percentile)  

Example summary snippet:

```

BlazeMeter Test Run Summary
===========================
Test Name: My Load Test
Test ID: 98765
Run ID: 1234567

Start Time: 2025-08-21 19:08:00 UTC
End Time: 2025-08-21 19:38:00 UTC
Duration: 1800s
Max Virtual Users: 1000

Samples Total: 256000
Pass Count: 254788
Fail Count: 1212
Error Count: 1212

Response Time (ms):
Min: 88
Max: 8500
Avg: 340
90th Percentile: 560

```

---

## 📂 Shared Folder Tools

The shared folder tools allow you to manage BlazeMeter shared folders and upload test data files (CSVs, Excel sheets, Java KeyStores, JMeter properties files, etc.) directly via the API.

### What Are Shared Folders?

Shared folders are workspace-level containers in BlazeMeter used to store files that can be attached to one or more tests. When a test runs, files in the linked shared folder are deployed alongside the JMX script on every load-generator engine. This makes them ideal for test data files, certificates, and configuration that multiple tests share.

### How Uploads Work

BlazeMeter shared folders use a **two-step signed-URL upload process**:

1. **GET** a signed upload URL from BlazeMeter (`/api/v4/folders/{folderId}/s3/sign?fileName=...`)
2. **PUT** the file binary to the signed URL with `Content-Type: application/octet-stream`

The tools handle URL-encoding for file names with spaces/special characters, SSL configuration, and large file transfers automatically.

### File Extension Filtering

Uploads are filtered by an allowlist of file extensions defined in `config.yaml` under `blazemeter.shared_folders.allowed_extensions`. By default this includes:

`.csv`, `.xlsx`, `.xls`, `.pdf`, `.jmx`, `.properties`, `.jks`, `.p12`, `.pem`, `.json`, `.xml`, `.txt`, `.jar`, `.groovy`

Files with extensions not on the allowlist (e.g. `.DS_Store`, `Thumbs.db`, temp files) are automatically skipped and reported in the response. You can customise the list in your local `config.yaml` without code changes.

### Shared Folder Workflow

#### 1. List shared folders in your workspace

```python
result = await get_shared_folders(workspace_id="123456")
# Returns: [{"id": "abc123def456...", "name": "my_test_files", "workspace_id": "123456"}, ...]
```

#### 2. Check what files are already in a folder

```python
result = await get_shared_folder_file_list(folder_id="abc123def456789")
# Returns: {"folder_name": "my_test_files", "file_count": 3, "files": [...]}
```

#### 3a. Upload a single file

```python
result = await upload_to_shared_folder(
    folder_id="abc123def456789",
    path="C:/TestData/my_large_file.xlsx"
)
# Returns: {"status": "success", "mode": "single_file", "uploaded": 1, ...}
```

#### 3b. Upload all files from a directory

```python
result = await upload_to_shared_folder(
    folder_id="abc123def456789",
    path="C:/TestData/my_test_files"
)
# Returns: {"status": "success", "mode": "directory", "uploaded": 8, "skipped_files": [...], ...}
```

### Key Features

- **No file size restriction**: Uploads via signed URLs bypass the BlazeMeter UI's file-size limit.
- **Smart path detection**: Pass a file path to upload one file, or a directory path to upload all allowed files.
- **Extension allowlist**: Only uploads file types relevant to JMeter testing. Configurable in `config.yaml`.
- **Transparency**: Skipped files are reported with reasons so you can review what was excluded.
- **Space/special character support**: File names like `PerformanceTesting 10K Asset.xlsx` are URL-encoded automatically.
- **Idempotent**: Re-uploading the same file name overwrites the existing file in the shared folder.
- **Progress logging**: When called with an MCP context, logs per-file upload progress.
- **Error resilience**: Continues uploading remaining files even if one fails, and reports per-file status.

### Using Shared Folders in JMeter Tests

When you configure a BlazeMeter test to use a shared folder, the folder contents are deployed alongside your JMX script on the cloud engine. Reference them using relative paths in your JMX:

```
# In JMX (CSV Data Set Config filename field):
testdata_csv/Environment_QA.csv

# In JMX (Groovy script building file paths):
def basePath = scriptPath + File.separator + "my_test_files" + File.separator
vars.put("assetFilePath", basePath + assetFileName)
```

---

## 📁 Project Structure

```
blazemeter-mcp/
├── blazemeter.py                  # MCP server entrypoint (FastMCP)
├── services/
│   └── blazemeter_api.py          # BlazeMeter API & helper functions
├── utils/
│   └── config.py                  # Utility for loading config.yaml
├── config.yaml                    # Centralized, environment-agnostic config
├── pyproject.toml                 # Modern Python project metadata & dependencies
├── requirements.txt*              # (if present) for legacy dependency management
├── README.md                      # This file
└── .env                           # Local environment variables (API keys, secrets)
```

\*If you're exclusively using modern tools like `uv` with `pyproject.toml`, you may omit `requirements.txt`.

---

## 🔒 Security Considerations (Shared Folder Uploads)

The shared folder upload tools provide direct API access to BlazeMeter's file storage. The following considerations should be reviewed by your Security and DevOps teams before production use.

### 1. Executable File Uploads

The allowed extensions include `.jar`, `.groovy`, and `.jmx` — files that JMeter **executes** on cloud load-generator engines at runtime. A malicious actor with valid API credentials could upload weaponized code (e.g. reverse shells, credential harvesting, lateral movement) disguised as test components.

**Recommendation**: Establish code review policies for executable uploads (`.jar`, `.groovy`, `.jmx`). Consider a separate approval workflow for these file types.

### 2. No Content Validation

The extension allowlist filters by file name only. It does not inspect file contents. A file named `testdata.csv` could contain binary payloads, injection strings, or scripts. There is no magic-byte validation, virus scanning, or content-type verification at the tool level.

**Recommendation**: Evaluate whether BlazeMeter performs server-side content validation or malware scanning on shared folder uploads. Implement scanning at the pipeline level if not.

### 3. API Credentials Scope

The BlazeMeter API key/secret used by these tools has write access to shared folders. If credentials are compromised (leaked `.env` file, exposed in logs, etc.), an attacker can upload arbitrary files to any shared folder in the workspace without UI interaction.

**Recommendation**: Use least-privilege API keys scoped to specific workspaces. Enforce secrets rotation policies. Monitor BlazeMeter API audit logs for unexpected upload activity.

### 4. Bypasses UI Controls

The BlazeMeter UI enforces a file-size restriction on uploads (approximately 50 MB). These tools intentionally bypass that restriction via the direct API. Any other UI-level validations or approval workflows are also bypassed.

**Recommendation**: Verify with BlazeMeter whether UI-side restrictions serve a security purpose or are purely UX limits. Ensure equivalent controls exist at the API level.

### 5. No Audit Logging at the Tool Level

The tools log progress via FastMCP context but do not produce a persistent audit trail of uploads (file name, size, hash, timestamp, user identity). In an incident investigation, there would be no local record.

**Recommendation**: Implement upload audit logging with file hashes (SHA-256) to a persistent log. Integrate with SIEM if available.

### 6. Shared Folder Blast Radius

Files uploaded to a shared folder are available to **all tests** linked to that folder within the workspace. A malicious file in a widely-shared folder could impact multiple test configurations and teams.

**Recommendation**: Restrict shared folder access by team or project where possible. Periodically review which tests are linked to each shared folder.

---

## 🚧 Future Enhancements

- Expanded artifact processing \& analytics workflows
- Integration with Datadog and other monitoring MCP servers
- Automated test result analysis via LLMs
- Enhanced error reporting and system diagnostics

---

## 🤝 Contributing

Feel free to open issues or submit pull requests!

---

Created with ❤️ using FastMCP and BlazeMeter APIs

