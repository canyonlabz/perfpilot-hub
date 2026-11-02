/**
 * Frontend catalogs for the Settings visual shell.
 *
 * No project rows or secret values live here. Persistence and credential
 * storage belong to the later BYOT / backend integration work.
 */

export const NONE_SELECTED = "";
export const NONE_SELECTED_LABEL = "None selected";

export type SecretComponent =
  | "ado"
  | "jira"
  | "confluence-cloud"
  | "confluence-onprem"
  | "blazemeter"
  | "datadog"
  | "github";

export const COMPONENT_LABELS: Record<SecretComponent, string> = {
  ado: "Azure DevOps",
  jira: "Jira",
  "confluence-cloud": "Confluence Cloud",
  "confluence-onprem": "Confluence On-Prem",
  blazemeter: "BlazeMeter",
  datadog: "Datadog",
  github: "GitHub",
};

export type SecretValueField = {
  id: string;
  label: string;
  placeholder: string;
};

export const COMPONENT_VALUE_FIELDS: Record<
  SecretComponent,
  SecretValueField[]
> = {
  ado: [{ id: "token", label: "Token", placeholder: "paste the PAT" }],
  jira: [{ id: "token", label: "Token", placeholder: "paste the token" }],
  "confluence-cloud": [
    { id: "token", label: "Token", placeholder: "paste the API token" },
  ],
  "confluence-onprem": [
    { id: "token", label: "Token", placeholder: "paste the PAT" },
  ],
  blazemeter: [
    { id: "api_key", label: "API key", placeholder: "paste the API key" },
    {
      id: "api_secret",
      label: "API secret",
      placeholder: "paste the API secret",
    },
  ],
  datadog: [
    { id: "api_key", label: "API key", placeholder: "paste the API key" },
    { id: "app_key", label: "Application key", placeholder: "paste the app key" },
  ],
  github: [{ id: "token", label: "Token", placeholder: "paste the PAT" }],
};

export type FocusAreaId = "tracking" | "docs" | "load" | "observability" | "scm";

export type FocusArea = {
  id: FocusAreaId;
  title: string;
  hint: string;
  components: SecretComponent[];
};

export const FOCUS_AREAS: FocusArea[] = [
  {
    id: "tracking",
    title: "Project tracking",
    hint: "Access tokens for Azure DevOps and Jira integrations.",
    components: ["ado", "jira"],
  },
  {
    id: "docs",
    title: "Documentation",
    hint: "Access tokens for Confluence (cloud or on-prem).",
    components: ["confluence-cloud", "confluence-onprem"],
  },
  {
    id: "load",
    title: "Load testing",
    hint: "API credentials for cloud load-testing vendors (e.g. BlazeMeter).",
    components: ["blazemeter"],
  },
  {
    id: "observability",
    title: "Observability",
    hint: "API and application keys for Datadog metrics, logs, and APM.",
    components: ["datadog"],
  },
  {
    id: "scm",
    title: "Source control",
    hint: "Personal access tokens for repository hosts.",
    components: ["github"],
  },
];

export type IntegrationKey = "confluence" | "blazemeter" | "datadog" | "github";

export const INTEGRATION_COMPONENTS: Record<
  IntegrationKey,
  SecretComponent[]
> = {
  confluence: ["confluence-cloud", "confluence-onprem"],
  blazemeter: ["blazemeter"],
  datadog: ["datadog"],
  github: ["github"],
};

export const INTEGRATION_LABELS: Record<IntegrationKey, string> = {
  confluence: "Confluence",
  blazemeter: "BlazeMeter",
  datadog: "Datadog",
  github: "GitHub",
};

export type SettingsSecret = {
  id: string;
  sectionId: FocusAreaId;
  name: string;
  component: SecretComponent;
  masked: string;
  added: string;
};

export function focusAreaById(id: FocusAreaId): FocusArea {
  const area = FOCUS_AREAS.find((item) => item.id === id);
  if (!area) {
    throw new Error(`Unknown focus area: ${id}`);
  }
  return area;
}

export function tokenNamesForIntegration(
  secrets: SettingsSecret[],
  key: IntegrationKey
): string[] {
  const allowed = new Set(INTEGRATION_COMPONENTS[key]);
  const names = secrets
    .filter((secret) => allowed.has(secret.component))
    .map((secret) => secret.name);
  return [...new Set(names)];
}

export function integrationSelectOptions(
  secrets: SettingsSecret[],
  key: IntegrationKey
): { value: string; label: string }[] {
  return [
    { value: NONE_SELECTED, label: NONE_SELECTED_LABEL },
    ...tokenNamesForIntegration(secrets, key).map((name) => ({
      value: name,
      label: name,
    })),
  ];
}
