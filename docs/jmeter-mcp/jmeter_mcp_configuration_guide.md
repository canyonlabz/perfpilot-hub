# 📘 JMeter MCP Configuration Guide

### *This guide explains how to configure the JMeter MCP (Model Context Protocol) server using YAML configuration files. The JMeter MCP automates the creation of JMeter test scripts from browser automation recordings.*

---

## 📁 1. Configuration Files Overview

The JMeter MCP uses **two configuration files**:

| File | Purpose |
|------|---------|
| `config.yaml` (or `config.windows.yaml`, `config.mac.yaml`) | Environment and path settings for your system |
| `jmeter_config.yaml` | JMeter script generation settings |

> 💡 **Tip**: The system automatically selects the correct platform-specific config file based on your operating system. On Windows, it loads `config.windows.yaml`; on macOS, it loads `config.mac.yaml`. If neither exists, it falls back to `config.yaml`.

---

## 🔧 2. config.yaml — Environment Configuration

This file contains system-level settings for paths, directories, and network capture behavior.

### 📦 2.1 Artifacts Configuration

**Example:**
```yaml
artifacts:
  artifacts_path: "C:\\Users\\<username>\\Repos\\mcp-perf-suite\\artifacts"
```

| Setting | Description |
|---------|-------------|
| `artifacts_path` | 📂 The root directory where all output files are stored, including generated JMX scripts, network captures, JTL results, logs, and reports. Each test run creates a subfolder here (e.g., `artifacts/my-test-run/jmeter/`). |

> ⚠️ **Important**: Use full absolute paths. On Windows, escape backslashes with `\\` or use forward slashes `/`.

---

### ☕ 2.2 JMeter Configuration

**Example:**
```yaml
jmeter:
  jmeter_home: "C:\\opt\\apache-jmeter-5.6.3"
  jmeter_bin_path: "C:\\opt\\apache-jmeter-5.6.3\\bin"
  jmeter_start_exe: "jmeter.bat"
  jmeter_stop_exe: "stoptest.cmd"
```

| Setting | Description |
|---------|-------------|
| `jmeter_home` | 🏠 The root installation directory of Apache JMeter |
| `jmeter_bin_path` | 📁 The `bin` directory inside your JMeter installation |
| `jmeter_start_exe` | ▶️ The executable to start JMeter. Use `jmeter.bat` on Windows or `jmeter` on Linux/macOS |
| `jmeter_stop_exe` | ⏹️ The executable to stop running tests. Use `stoptest.cmd` on Windows or `stoptest.sh` on Linux/macOS |

> 📝 **Note**: These settings are required for executing JMeter tests through the MCP. Ensure JMeter is properly installed and the paths are correct.

---

### 📋 2.3 Browser Automation Specifications

```yaml
test_specs:
  web_flows_path: "test-specs\\web-flows"
  api_flows_path: "test-specs\\api-flows"
  examples_path: "test-specs\\examples"
```

| Setting | Description |
|---------|-------------|
| `web_flows_path` | 🌐 Directory containing Markdown specification files for web UI automation flows |
| `api_flows_path` | 🔌 Directory containing Markdown specification files for API automation flows |
| `examples_path` | 📚 Directory containing example specification files for reference |

> 💡 **Tip**: Test spec files are Markdown documents that describe browser automation steps. The MCP parses these to orchestrate Playwright browser automation and capture network traffic.

---

### 🌐 2.4 Network Capture Configuration

This section controls which HTTP requests are captured during browser automation. These settings are crucial for creating accurate JMeter scripts.

```yaml
network_capture:
  capture_api_requests: True          # Always true – critical for JMeter
  capture_static_assets: False        # CSS, JS, PNG, JPG, etc.
  capture_fonts: False                # WOFF, WOFF2, TTF, etc.
  capture_video_streams: False        # HLS/video streaming files
  capture_third_party: True           # Google Fonts, CDN, Ads
  capture_cookies: True               # Always true – critical for JMeter
  capture_domain: ""                  # Domain filter (empty = capture all)
```

| Setting | Description | Recommended |
|---------|-------------|-------------|
| `capture_api_requests` | 🔗 Capture API/XHR requests | ✅ Always `True` |
| `capture_static_assets` | 🖼️ Capture images, CSS, JavaScript files | ❌ Usually `False` |
| `capture_fonts` | 🔤 Capture web font files | ❌ Usually `False` |
| `capture_video_streams` | 🎬 Capture video/streaming content | ❌ Usually `False` |
| `capture_third_party` | 🌍 Capture requests to third-party domains | Depends on test scope |
| `capture_cookies` | 🍪 Capture cookie headers for session management | ✅ Always `True` |
| `capture_domain` | 🎯 Limit capture to a specific domain (e.g., `example.com`). Leave empty to capture all domains | Context-dependent |

> 🎯 **Best Practice**: For performance testing, typically only capture API requests and cookies. Static assets can be handled by CDN servers and are often excluded from load tests.

---

### 🚫 2.5 Domain Exclusion List

The `exclude_domains` list filters out requests to APM, analytics, advertising, and other non-essential domains. These are excluded from **both** network capture AND correlation analysis.

```yaml
network_capture:
  exclude_domains:
    # APM / Monitoring
    - datadoghq.com
    - newrelic.com
    - appdynamics.com
    # Analytics / Tracking
    - google-analytics.com
    - googletagmanager.com
    - mixpanel.com
    # Advertising
    - doubleclick.net
    - googlesyndication.com
    # ... and more
```

| Category | Examples | Why Exclude? |
|----------|----------|--------------|
| 🔍 APM/Monitoring | Datadog, New Relic, Dynatrace | Infrastructure monitoring, not user flows |
| 📊 Analytics | Google Analytics, Mixpanel, Segment | Tracking pixels, not business logic |
| 📢 Advertising | DoubleClick, Google Ads | Ad networks, irrelevant to performance |
| 🐛 Error Tracking | Sentry, Bugsnag | Error reporting, not user transactions |
| 🔐 Security | WAF tokens (AWS WAF) | Security infrastructure |

> ➕ **Extending**: You can add custom domains to this list to exclude company-specific services, internal tooling, or any other traffic that shouldn't be included in your JMeter scripts.

---

## ⚙️ 3. jmeter_config.yaml — JMeter Script Settings

This file controls how JMeter test scripts are generated. Each section corresponds to specific JMeter test plan elements.

---

### 👥 3.1 Thread Group Configuration

The Thread Group defines the virtual user load profile for your test.

```yaml
thread_group:
  num_threads: 10        # Number of virtual users
  ramp_time: 100         # Ramp-up time in seconds
  loops: 10              # Number of iterations per thread
```

| Setting | Description | Example |
|---------|-------------|---------|
| `num_threads` | 👤 Number of concurrent virtual users (threads) | `10` = 10 simultaneous users |
| `ramp_time` | ⏱️ Time (seconds) to start all threads | `100` = add ~1 user every 10 seconds |
| `loops` | 🔄 Number of times each thread repeats the test | `10` = each user runs 10 iterations |

> 📊 **Example**: With `num_threads: 10`, `ramp_time: 100`, and `loops: 10`, the test will gradually spin up 10 users over ~100 seconds, and each user will execute the script 10 times.

---

### 🍪 3.2 Cookie Manager

The HTTP Cookie Manager automatically handles cookies across requests, essential for session-based applications.

```yaml
cookie_manager:
  enabled: true
```

| Setting | Description |
|---------|-------------|
| `enabled` | 🔘 When `true`, adds an HTTP Cookie Manager to the test plan. JMeter will automatically store and send cookies for each virtual user, maintaining session state. |

> ✅ **Best Practice**: Always enable for web applications that use sessions or authentication cookies.

---

### 📝 3.3 User Defined Variables

User Defined Variables (UDV) allow you to define reusable values that can be referenced throughout your test plan.

```yaml
user_defined_variables:
  enabled: true
  variables:
    "thinkTime": 5000       # Think time between actions (milliseconds)
    "pacing": 10000         # Pacing between iterations (milliseconds)
```

| Setting | Description |
|---------|-------------|
| `enabled` | 🔘 When `true`, adds a User Defined Variables element to the test plan |
| `variables` | 📋 Key-value pairs defining variables accessible as `${variableName}` |

**Common Variables:**

| Variable | Purpose | Example Value |
|----------|---------|---------------|
| `thinkTime` | ⏸️ Simulated user thinking/reading time between steps | `5000` (5 seconds) |
| `pacing` | ⏰ Controlled delay between test iterations | `10000` (10 seconds) |
| `bearer_token` | 🔑 OAuth/JWT token (empty placeholder for dynamic extraction) | `""` |

> 💡 **Tip**: Variables defined here can be referenced anywhere in the test plan using `${variableName}` syntax. They're also used as default values for orphan correlations detected during analysis.

---

### 📊 3.4 CSV Data Set Configuration

CSV Data Set Config allows you to parameterize tests with external data files (e.g., user credentials, test data).

```yaml
csv_dataset_config:
  enabled: false                        # Enable/disable CSV data feeding
  csv_file_path: "testdata_csv"         # Relative folder path
  filename: "test_data.csv"             # CSV file name
  ignore_first_line: true               # Skip header row
  variable_names: "userId,password"     # Column names (comma-separated)
  delimiter: ","                        # Field delimiter
  recycle_on_end: true                  # Restart from beginning when file ends
  stop_thread_on_error: false           # Continue if CSV read fails
  sharing_mode: "shareMode.all"         # How threads share data
  generate_mock_data: false             # (Future) LLM-generated test data
```

| Setting | Description |
|---------|-------------|
| `enabled` | 🔘 When `true`, adds CSV Data Set Config to the test plan |
| `csv_file_path` | 📁 Subfolder containing CSV files (relative to JMX location) |
| `filename` | 📄 Name of the CSV file to load |
| `ignore_first_line` | ⏭️ Skip header row (`true`) or treat as data (`false`) |
| `variable_names` | 🏷️ Column names that become JMeter variables |
| `delimiter` | ✂️ Character separating columns (`,`, `;`, `\t`, etc.) |
| `recycle_on_end` | 🔄 When all rows are consumed, start over from the beginning |
| `stop_thread_on_error` | ⛔ Stop thread if CSV reading fails |
| `sharing_mode` | 🤝 Data sharing strategy (see below) |

**Sharing Modes:**

| Mode | Behavior |
|------|----------|
| `shareMode.all` | All threads share data sequentially (each row used once globally) |
| `shareMode.group` | Threads in same group share data |
| `shareMode.thread` | Each thread gets its own copy of the data |

> 📂 **File Location**: CSV files should be placed in `artifacts/<test_run_id>/jmeter/testdata_csv/` (or your configured path).

---

### 🎮 3.5 Controller Configuration

Controllers organize samplers into logical groups (test cases, transactions).

```yaml
controller_config:
  enabled: true
  controller_type: "simple"             # Options: simple, transaction
```

| Setting | Description |
|---------|-------------|
| `enabled` | 🔘 When `true`, wraps requests in controller elements |
| `controller_type` | 📦 Type of controller to create |

**Controller Types:**

| Type | Use Case |
|------|----------|
| `simple` | Basic grouping with no special behavior |
| `transaction` | Groups requests as a single transaction (useful for measuring end-to-end response times) |

> 🎯 **Best Practice**: Use `transaction` controllers when you want to measure the total time for a business flow (e.g., "Login Process" containing multiple API calls).

---

### 🌐 3.6 HTTP Sampler Configuration

Controls how HTTP requests are generated in the JMX script.

```yaml
http_sampler:
  auto_redirects: true
  post_body_raw: true
```

| Setting | Description |
|---------|-------------|
| `auto_redirects` | 🔀 When `true`, JMeter automatically follows HTTP redirects (3xx responses) |
| `post_body_raw` | 📨 When `true`, POST/PUT request bodies are sent as raw text (JSON, XML) rather than form-encoded |

> 💡 **Tip**: Keep `post_body_raw: true` for modern REST APIs that use JSON payloads.

---

### 🏷️ 3.7 Sampler Naming Convention

Controls the naming pattern for HTTP Request samplers, making test results easier to read.

```yaml
sampler_naming:
  enabled: true
```

| Setting | Description |
|---------|-------------|
| `enabled` | 🔘 When `true`, applies structured naming: `TC##_S##_METHOD /path` |

**Naming Examples:**

| `enabled` | Sampler Name Example |
|-----------|---------------------|
| `true` | `TC01_S01_GET /api/users` |
| `true` | `TC01_S02_POST /api/login` |
| `true` | `TC02_S01_GET /dashboard` |
| `false` | `GET /api/users` |

> 📊 **Benefit**: Structured naming makes aggregate reports more readable and allows easy filtering by test case (TC) or step (S).

---

### 🔒 3.8 HTTP/2 Headers Configuration

Controls handling of HTTP/2 pseudo-headers that may cause issues with HTTP/1.1 backends.

```yaml
http2_headers:
  exclude_pseudo_headers: true
```

| Setting | Description |
|---------|-------------|
| `exclude_pseudo_headers` | 🚫 When `true`, removes HTTP/2 pseudo-headers (`:method`, `:path`, `:scheme`, `:authority`, `:status`) from the Header Manager |

> ⚠️ **Why This Matters**: When capturing traffic from HTTP/2 websites, browsers include pseudo-headers that are invalid in HTTP/1.1. Since JMeter uses HTTP/1.1 by default, these headers can cause errors. Keep this `true` unless your target server specifically uses HTTP/2.

---

### ⏸️ 3.9 Test Action (Think Time) Configuration

Configures automatic think time pauses between steps to simulate realistic user behavior.

```yaml
test_action_config:
  enabled: true
  action: "pause"                       # Pause execution
  duration: 5000                        # Default duration (milliseconds)
  test_action_name: "Think Time"        # Display name in test plan
```

| Setting | Description |
|---------|-------------|
| `enabled` | 🔘 When `true`, adds Test Action elements between steps |
| `action` | ⏸️ Action type: `pause`, `stop`, `stop_now`, `restart_next_loop` |
| `duration` | ⏱️ Default pause duration in milliseconds (used if `${thinkTime}` variable isn't set) |
| `test_action_name` | 📝 Display name for the element in JMeter GUI |

> 🎯 **Best Practice**: Use the `${thinkTime}` variable (from User Defined Variables) for flexible think time control without editing the script.

---

### 📊 3.10 Results Collector Configuration

Configures the listeners (results collectors) added to the test plan for viewing and storing results.

```yaml
results_collector_config:
  view_results_tree: true
  view_results_tree_settings:
    save_response_data: false
    save_request_headers: false
    save_response_headers: false
  aggregate_report: true
  aggregate_report_settings:
    save_response_data: false
    save_request_headers: false
    save_response_headers: false
  response_time_graph: true
  summary_report: true
```

| Listener | Purpose |
|----------|---------|
| `view_results_tree` | 🌳 Detailed view of individual request/response data (debugging) |
| `aggregate_report` | 📈 Statistical summary grouped by sampler (throughput, error %, latency) |
| `response_time_graph` | 📉 Visual graph of response times over time |
| `summary_report` | 📋 Tabular summary of all samplers |

**Listener Settings:**

| Setting | Impact | Recommendation |
|---------|--------|----------------|
| `save_response_data` | 💾 Stores full response body | `false` for load tests (saves memory) |
| `save_request_headers` | 💾 Stores request headers | `false` for load tests |
| `save_response_headers` | 💾 Stores response headers | `false` for load tests |

> ⚠️ **Performance Warning**: Enabling response data saving significantly increases memory usage during load tests. Only enable for debugging with small loads.

---

### 🏠 3.11 Hostname Parameterization

Automatically extracts hostnames from captured traffic and creates environment-specific CSV files for easy environment switching.

```yaml
hostname_parameterization:
  enabled: true
  csv_filename: "environment.csv"
  csv_subfolder: "testdata_csv"

  default_patterns:
    auth_internal:
      patterns: ["internal.login", "internal.auth", "login.internal"]
      variable_prefix: "authInternalHostname"
    auth:
      patterns: ["login", "auth", "sso", "oauth", "identity", "accounts"]
      variable_prefix: "authHostname"
    static:
      patterns: ["cdn", "static", "assets", "media", "images"]
      variable_prefix: "staticHostname"
    app:
      variable_prefix: "envHostname"    # Default for uncategorized hostnames
```

| Setting | Description |
|---------|-------------|
| `enabled` | 🔘 When `true`, replaces hardcoded hostnames with JMeter variables |
| `csv_filename` | 📄 Name of the generated CSV file |
| `csv_subfolder` | 📁 Subfolder for the CSV file (relative to JMX) |
| `default_patterns` | 🏷️ Rules for categorizing hostnames |

**How It Works:**

1. 📡 Extracts all unique hostnames from captured traffic
2. 🏷️ Categorizes each hostname using pattern matching
3. 📝 Generates JMeter variables (e.g., `${envHostname}`, `${authHostname}`)
4. 📊 Creates `environment.csv` with hostname values
5. 🔄 Replaces hardcoded URLs with variables in the JMX script

**Example Output CSV (`environment.csv`):**
```csv
envHostname,authHostname,staticHostname
app.example.com,login.example.com,cdn.example.com
```

> 🎯 **Benefit**: To switch environments, simply edit the CSV file instead of modifying the JMX script!

---

### 🔍 3.12 Extractor Placement Configuration

Controls how correlation extractors (for dynamic values) are added to the JMX script.

```yaml
extractor_placement:
  mode: "first_occurrence"

  always_all_occurrences:
    - "oauth_state"
    - "oauth_nonce"
    - "oauth_code"
    - "access_token"
    - "bearer_token"
    - "id_token"
```

| Setting | Description |
|---------|-------------|
| `mode` | 🎯 Extractor placement strategy |
| `always_all_occurrences` | 📋 Variables that always need extractors on every occurrence |

**Placement Modes:**

| Mode | Behavior |
|------|----------|
| `first_occurrence` | Add extractor only on the first request that returns the value |
| `all_occurrences` | Add extractors on every request that returns the value |

**Why OAuth Variables Need All Occurrences:**

OAuth/SSO flows often involve redirect chains where values like `state`, `nonce`, or `code` change between redirects. Using `first_occurrence` would capture an outdated value. The `always_all_occurrences` list overrides the mode for these critical security tokens.

> 💡 **Tip**: Add company-specific SSO tokens (e.g., `cdssotoken`, `sso_nonce`) to the `always_all_occurrences` list.

---

### 🔐 3.13 OAuth Configuration

Configures automatic handling of OAuth/SSO tokens in the generated JMX scripts.

```yaml
oauth_config:
  enabled: true

  token_headers:
    authorization: "bearer_token"       # Authorization: Bearer ${bearer_token}

  token_url_params:
    cdssotoken: "cdssotoken"            # ?cdssotoken=${cdssotoken}

  nonce_cookie_keywords:
    - "nonce"
    - "csrftoken"

  token_json_fields:
    cdssoToken: "cdssotoken"
    tokenId: "token_id"
    access_token: "access_token"
    id_token: "bearer_token"
```

| Setting | Description |
|---------|-------------|
| `enabled` | 🔘 When `true`, applies OAuth token parameterization |
| `token_headers` | 🎫 HTTP headers containing tokens → JMeter variable mapping |
| `token_url_params` | 🔗 URL query parameters containing tokens → variable mapping |
| `nonce_cookie_keywords` | 🍪 Keywords to identify nonce cookies for extraction |
| `token_json_fields` | 📦 JSON response field names → variable mapping |

**How It Works:**

1. **Headers**: `Authorization: Bearer eyJ0eXA...` → `Authorization: Bearer ${bearer_token}`
2. **URLs**: `?cdssotoken=Lcdf6Rs...` → `?cdssotoken=${cdssotoken}`
3. **JSON Extraction**: Automatically creates extractors for token fields in responses

> 🔧 **Customization**: Add your company's specific SSO token names to `token_url_params` and `token_json_fields`.

---

## 🚀 4. Quick Start Checklist

Before running the JMeter MCP, verify these settings:

### ✅ config.yaml

- [ ] `artifacts.artifacts_path` points to a valid directory with write permissions
- [ ] `jmeter.jmeter_home` points to your JMeter installation
- [ ] `jmeter.jmeter_bin_path` contains `jmeter.bat` (or `jmeter` on Linux/macOS)
- [ ] `network_capture.capture_api_requests` is `true`
- [ ] `network_capture.capture_cookies` is `true`

### ✅ jmeter_config.yaml

- [ ] `cookie_manager.enabled` is `true` for session-based apps
- [ ] `thread_group` values are appropriate for your test scenario
- [ ] `hostname_parameterization.enabled` is `true` for environment flexibility
- [ ] `oauth_config.enabled` is `true` if testing authenticated flows

---

## 📚 5. Related Documentation

- **JMeter Official Documentation**: [https://jmeter.apache.org/usermanual/](https://jmeter.apache.org/usermanual/)
- **Playwright Browser Automation**: See `.cursor/rules/playwright-browser-automation.mdc`
- **Correlation Naming Rules**: See `.cursor/rules/jmeter-correlations.mdc`

---

## ❓ 6. Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| JMeter not starting | Verify `jmeter_bin_path` and `jmeter_start_exe` are correct |
| Missing cookies in test | Ensure `cookie_manager.enabled: true` and `capture_cookies: true` |
| Wrong hostname in requests | Check `hostname_parameterization` settings and CSV file |
| OAuth tokens not extracted | Add token field names to `oauth_config.token_json_fields` |
| Too much data captured | Add domains to `exclude_domains` list |

---

*Happy Performance Testing! 🎉*
