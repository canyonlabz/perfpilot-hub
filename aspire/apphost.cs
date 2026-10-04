#:sdk Aspire.AppHost.Sdk@13.6.0
#:package Aspire.Hosting.JavaScript@13.6.0
#:property AspireUseCliBundle=true
#:property UserSecretsId=perfpilot-aspire-apphost

var builder = DistributedApplication.CreateBuilder(args);

// =============================================================================
// Secrets (dotnet user-secrets locally; HashiCorp Vault in the cloud)
// =============================================================================

var postgresUser = builder.AddParameter("postgres-user");
var postgresPassword = builder.AddParameter("postgres-password", secret: true);
var postgresDb = builder.AddParameter("postgres-db");

var openAiApiKey = builder.AddParameter("openai-api-key", secret: true);

var playwrightCertPassphrase = builder.AddParameter("playwright-cert-passphrase", secret: true);
var playwrightCertAutoSelectCn = builder.AddParameter("playwright-cert-auto-select-cn");

var jmeterJksPwd = builder.AddParameter("jmeter-jks-pwd", secret: true);

var blazemeterApiKey = builder.AddParameter("blazemeter-api-key", secret: true);
var blazemeterApiSecret = builder.AddParameter("blazemeter-api-secret", secret: true);
var blazemeterAccountId = builder.AddParameter("blazemeter-account-id");
var blazemeterWorkspaceId = builder.AddParameter("blazemeter-workspace-id");

var ddApiKey = builder.AddParameter("dd-api-key", secret: true);
var ddAppKey = builder.AddParameter("dd-app-key", secret: true);
var ddApiBaseUrl = builder.AddParameter("dd-api-base-url");

var confluenceV2BaseUrl = builder.AddParameter("confluence-v2-base-url");
var confluenceV2User = builder.AddParameter("confluence-v2-user");
var confluenceV2ApiToken = builder.AddParameter("confluence-v2-api-token", secret: true);

// AI Assistant / SCM integration
var githubPersonalAccessToken = builder.AddParameter("github-personal-access-token", secret: true);

const string repoRoot = "../";
const string artifactsHost = "../mcp-perf-suite/artifacts";
const string artifactsContainer = "/app/artifacts";
const string deploymentMode = "local";

IResourceBuilder<ContainerResource> AddMcp(
    string name,
    string dockerfile,
    int port)
{
    return builder.AddDockerfile(name, repoRoot, dockerfile)
        .WithImageTag("latest")
        .WithHttpEndpoint(port: port, targetPort: port, name: "http")
        .WithBindMount(artifactsHost, artifactsContainer)
        .WithEnvironment("DEPLOYMENT_MODE", deploymentMode);
}

// =============================================================================
// Database — PostgreSQL 18 + pgvector + Apache AGE (local only)
// =============================================================================

// PGDATA is set explicitly to the version-specific PG18+ layout and points at
// a subdirectory of the bind mount — required so initdb has an empty target
// directory and so the storage layout matches the official PG Docker image's
// convention for v18+.
var database = builder.AddDockerfile("perfmem-pgvector-age", repoRoot, "docker/postgresql/Dockerfile")
    .WithImageTag("latest")
    .WithEndpoint(port: 5432, targetPort: 5432, name: "postgres", scheme: "tcp")
    .WithEnvironment("POSTGRES_USER", postgresUser)
    .WithEnvironment("POSTGRES_PASSWORD", postgresPassword)
    .WithEnvironment("POSTGRES_DB", postgresDb)
    .WithEnvironment("PGDATA", "/var/lib/postgresql/18/docker/pgdata")
    .WithBindMount("../docker/data/pgvectordb", "/var/lib/postgresql/18/docker")
    .WithLifetime(ContainerLifetime.Persistent);

// =============================================================================
// FastMCP servers — one HTTP container each (config baked in the image)
// =============================================================================

var blazemeter = AddMcp("perfpilot-mcp-blazemeter", "docker/blazemeter-mcp/Dockerfile", 8110)
    .WithEnvironment("BLAZEMETER_API_KEY", blazemeterApiKey)
    .WithEnvironment("BLAZEMETER_API_SECRET", blazemeterApiSecret)
    .WithEnvironment("BLAZEMETER_ACCOUNT_ID", blazemeterAccountId)
    .WithEnvironment("BLAZEMETER_WORKSPACE_ID", blazemeterWorkspaceId);

var datadog = AddMcp("perfpilot-mcp-datadog", "docker/datadog-mcp/Dockerfile", 8111)
    .WithEnvironment("DD_API_KEY", ddApiKey)
    .WithEnvironment("DD_APP_KEY", ddAppKey)
    .WithEnvironment("DD_API_BASE_URL", ddApiBaseUrl);

// JMeter MCP also mounts the shared .playwright-mcp/ output folder so it can
// parse Playwright trace files produced by perfpilot-mcp-playwright.
var jmeter = AddMcp("perfpilot-mcp-jmeter", "docker/jmeter-mcp/Dockerfile", 8112)
    .WithBindMount("../.playwright-mcp", "/app/.playwright-mcp")
    .WithEnvironment("JMETER_JKS_PWD", jmeterJksPwd);

var perfanalysis = AddMcp("perfpilot-mcp-perfanalysis", "docker/perfanalysis-mcp/Dockerfile", 8113);

var perfreport = AddMcp("perfpilot-mcp-perfreport", "docker/perfreport-mcp/Dockerfile", 8114);

var confluence = AddMcp("perfpilot-mcp-confluence", "docker/confluence-mcp/Dockerfile", 8115)
    .WithEnvironment("CONFLUENCE_V2_BASE_URL", confluenceV2BaseUrl)
    .WithEnvironment("CONFLUENCE_V2_USER", confluenceV2User)
    .WithEnvironment("CONFLUENCE_V2_API_TOKEN", confluenceV2ApiToken);

var perfmemory = AddMcp("perfpilot-mcp-perfmemory", "docker/perfmemory-mcp/Dockerfile", 8116)
    .WithEnvironment("POSTGRES_HOST", "perfmem-pgvector-age")
    .WithEnvironment("POSTGRES_PORT", "5432")
    .WithEnvironment("POSTGRES_USER", postgresUser)
    .WithEnvironment("POSTGRES_PASSWORD", postgresPassword)
    .WithEnvironment("POSTGRES_DB", "perfmemory")
    .WithEnvironment("EMBEDDING_PROVIDER", "openai")
    .WithEnvironment("OPENAI_API_KEY", openAiApiKey)
    .WaitFor(database);

// Custom GitHub MCP — pushes generated artifacts (e.g., JMX scripts) to a
// non-main branch on behalf of the Orchestrator / A2A sender. Stateless HTTP
// MCP, routed behind the gateway alongside the other FastMCP servers.
var github = AddMcp("perfpilot-mcp-github", "docker/github-mcp/Dockerfile", 8118)
    .WithEnvironment("GITHUB_PERSONAL_ACCESS_TOKEN", githubPersonalAccessToken);

// =============================================================================
// Playwright MCP — @playwright/mcp Streamable HTTP /mcp on 8117
// Direct agent URL — NOT gateway-mounted (stateful browser sessions).
// =============================================================================

var playwright = builder.AddDockerfile("perfpilot-mcp-playwright", repoRoot, "docker/playwright-mcp/Dockerfile")
    .WithImageTag("latest")
    .WithHttpEndpoint(port: 8117, targetPort: 8117, name: "mcp")
    .WithBindMount("../.playwright-mcp", "/home/node/output")
    .WithEnvironment("DEPLOYMENT_MODE", deploymentMode)
    .WithEnvironment("PERFPILOT_DOCKER", "true")
    .WithEnvironment("HTTP_PORT", "8117")
    .WithEnvironment("PLAYWRIGHT_CERT_PASSPHRASE", playwrightCertPassphrase)
    .WithEnvironment("PLAYWRIGHT_CERT_AUTO_SELECT_CN", playwrightCertAutoSelectCn);

// =============================================================================
// Gateway — HTTP mounts of MCP_URL_* (Cursor / agents use this only)
// =============================================================================

var gateway = builder.AddDockerfile("perfpilot-mcp-gateway", repoRoot, "docker/gateway-mcp/Dockerfile")
    .WithImageTag("latest")
    .WithHttpEndpoint(port: 8125, targetPort: 8125, name: "gateway")
    .WithEnvironment("DEPLOYMENT_MODE", deploymentMode)
    .WithEnvironment("FASTMCP_CHECK_FOR_UPDATES", "off")
    .WithEnvironment("MCP_URL_BLAZEMETER", "http://perfpilot-mcp-blazemeter:8110/perfpilot-mcp-blazemeter/mcp")
    .WithEnvironment("MCP_URL_DATADOG", "http://perfpilot-mcp-datadog:8111/perfpilot-mcp-datadog/mcp")
    .WithEnvironment("MCP_URL_JMETER", "http://perfpilot-mcp-jmeter:8112/perfpilot-mcp-jmeter/mcp")
    .WithEnvironment("MCP_URL_PERFANALYSIS", "http://perfpilot-mcp-perfanalysis:8113/perfpilot-mcp-perfanalysis/mcp")
    .WithEnvironment("MCP_URL_PERFREPORT", "http://perfpilot-mcp-perfreport:8114/perfpilot-mcp-perfreport/mcp")
    .WithEnvironment("MCP_URL_CONFLUENCE", "http://perfpilot-mcp-confluence:8115/perfpilot-mcp-confluence/mcp")
    .WithEnvironment("MCP_URL_PERFMEMORY", "http://perfpilot-mcp-perfmemory:8116/perfpilot-mcp-perfmemory/mcp")
    .WithEnvironment("MCP_URL_GITHUB", "http://perfpilot-mcp-github:8118/perfpilot-mcp-github/mcp")
    .WithExternalHttpEndpoints()
    .WaitFor(blazemeter)
    .WaitFor(datadog)
    .WaitFor(jmeter)
    .WaitFor(perfanalysis)
    .WaitFor(perfreport)
    .WaitFor(confluence)
    .WaitFor(perfmemory)
    .WaitFor(github);

const string gatewayMcpUrl = "http://perfpilot-mcp-gateway:8125/perfpilot-mcp-gateway/mcp";
const string playwrightMcpUrl = "http://perfpilot-mcp-playwright:8117/mcp";

// =============================================================================
// A2A Server — Agent-to-Agent protocol surface (Python/FastAPI, port 8101)
// Container built from docker/agent-backend/Dockerfile; default CMD runs the
// A2A server.
// =============================================================================

var a2aServer = builder.AddDockerfile("perfpilot-a2a", repoRoot, "docker/agent-backend/Dockerfile")
    .WithImageTag("latest")
    .WithHttpEndpoint(port: 8101, targetPort: 8101, name: "a2a")
    .WithBindMount(artifactsHost, artifactsContainer)
    .WithEnvironment("DEPLOYMENT_MODE", deploymentMode)
    .WithEnvironment("A2A_PORT", "8101")
    .WithEnvironment("HEALTHCHECK_PORT", "8101")
    .WithEnvironment("PERFAGENT_STATE_HOST", "perfmem-pgvector-age")
    .WithEnvironment("PERFAGENT_STATE_PORT", "5432")
    .WithEnvironment("PERFAGENT_STATE_DB", "perfagent_state")
    .WithEnvironment("PERFAGENT_STATE_USER", postgresUser)
    .WithEnvironment("PERFAGENT_STATE_PASSWORD", postgresPassword)
    .WithEnvironment("PERFAGENT_STATE_SSLMODE", "disable")
    .WithEnvironment("LLM_PROVIDER", "openai")
    .WithEnvironment("OPENAI_API_KEY", openAiApiKey)
    .WithEnvironment("GATEWAY_MCP_URL", gatewayMcpUrl)
    .WithEnvironment("PLAYWRIGHT_MCP_URL", playwrightMcpUrl)
    .WithExternalHttpEndpoints()
    .WaitFor(database)
    .WaitFor(gateway)
    .WaitFor(playwright);

// =============================================================================
// AG-UI Server — CopilotKit bridge (Python/FastAPI, port 8102)
// Same image as A2A; CMD is overridden to run agui_server.py.
// =============================================================================

var aguiServer = builder.AddDockerfile("perfpilot-agui", repoRoot, "docker/agent-backend/Dockerfile")
    .WithImageTag("latest")
    .WithHttpEndpoint(port: 8102, targetPort: 8102, name: "agui")
    .WithArgs("python", "agui_server.py")
    .WithBindMount(artifactsHost, artifactsContainer)
    .WithEnvironment("DEPLOYMENT_MODE", deploymentMode)
    .WithEnvironment("AGUI_PORT", "8102")
    .WithEnvironment("HEALTHCHECK_PORT", "8102")
    .WithEnvironment("PERFAGENT_STATE_HOST", "perfmem-pgvector-age")
    .WithEnvironment("PERFAGENT_STATE_PORT", "5432")
    .WithEnvironment("PERFAGENT_STATE_DB", "perfagent_state")
    .WithEnvironment("PERFAGENT_STATE_USER", postgresUser)
    .WithEnvironment("PERFAGENT_STATE_PASSWORD", postgresPassword)
    .WithEnvironment("PERFAGENT_STATE_SSLMODE", "disable")
    .WithEnvironment("LLM_PROVIDER", "openai")
    .WithEnvironment("OPENAI_API_KEY", openAiApiKey)
    .WithEnvironment("GATEWAY_MCP_URL", gatewayMcpUrl)
    .WithEnvironment("PLAYWRIGHT_MCP_URL", playwrightMcpUrl)
    .WaitFor(database)
    .WaitFor(gateway)
    .WaitFor(playwright)
    .WaitFor(a2aServer);

// =============================================================================
// Frontend — CopilotKit / React / Next.js (host-side npm run dev)
// Reaches the agent backends via localhost since Aspire publishes 8101/8102
// to the host.
// =============================================================================

var frontend = builder.AddJavaScriptApp("perfpilot-ui", "../agent-framework/frontend/ui", "dev")
    .WithHttpEndpoint(env: "PORT")
    .WithEnvironment("DEPLOYMENT_MODE", deploymentMode)
    .WithEnvironment("AGUI_BACKEND_URL", "http://localhost:8102")
    .WithEnvironment("A2A_BACKEND_URL", "http://localhost:8101")
    .WithExternalHttpEndpoints()
    .WaitFor(aguiServer);

builder.Build().Run();
