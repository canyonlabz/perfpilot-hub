/**
 * Frontend-only placeholder data for the Settings visual shell.
 * Nothing here is persisted or sent to a backend. Secret values are
 * pre-masked dummy strings — never real credentials.
 */

export type DemoComponent =
  | "ado"
  | "jira"
  | "confluence"
  | "blazemeter"
  | "github";

export const COMPONENT_LABELS: Record<DemoComponent, string> = {
  ado: "Azure DevOps",
  jira: "Jira",
  confluence: "Confluence",
  blazemeter: "BlazeMeter",
  github: "GitHub",
};

export type DemoProject = {
  id: string;
  name: string;
  slug: string;
  aliases: string[];
  environmentCount: number;
  enabled: boolean;
  confluence: string[];
  blazemeter: string[];
  github: string[];
};

export const DEMO_PROJECTS: DemoProject[] = [
  {
    id: "project1",
    name: "Project1",
    slug: "project1",
    aliases: ["AIPTE", "PerfPilotOS"],
    environmentCount: 2,
    enabled: true,
    confluence: ["Performance Test Engineering (PTE)", "Project1 UAT Reports"],
    blazemeter: ["project1-ws (1234567)", "shared-perf (9876543)"],
    github: ["example/project1-tests", "None"],
  },
  {
    id: "project2",
    name: "Project2",
    slug: "project2",
    aliases: ["HALO"],
    environmentCount: 3,
    enabled: true,
    confluence: ["HALO Reports", "Performance Test Engineering (PTE)"],
    blazemeter: ["halo-nightly (2345678)"],
    github: ["None", "example/halo-regression"],
  },
  {
    id: "project3",
    name: "Project3",
    slug: "project3",
    aliases: ["Payments"],
    environmentCount: 0,
    enabled: false,
    confluence: ["— not configured —"],
    blazemeter: ["payments-ws (3456789)"],
    github: ["example/payments-api"],
  },
  {
    id: "project4",
    name: "Project4",
    slug: "project4",
    aliases: ["VIPR"],
    environmentCount: 1,
    enabled: false,
    confluence: ["Valuation Insights"],
    blazemeter: ["— not configured —"],
    github: ["None"],
  },
];

export type DemoSecret = {
  id: string;
  name: string;
  component: DemoComponent;
  masked: string;
  added: string;
};

export type DemoSecretSection = {
  id: string;
  title: string;
  hint: string;
  defaultComponent: DemoComponent;
  secrets: DemoSecret[];
};

export const DEMO_SECRET_SECTIONS: DemoSecretSection[] = [
  {
    id: "tracking",
    title: "Project tracking",
    hint: "Access tokens for Azure DevOps and Jira integrations.",
    defaultComponent: "ado",
    secrets: [
      {
        id: "s1",
        name: "project1-ado-pat",
        component: "ado",
        masked: "••••••••••••3f2a",
        added: "Added Oct 15, 2026",
      },
      {
        id: "s2",
        name: "project2-jira-token",
        component: "jira",
        masked: "••••••••••7b1c",
        added: "Added Oct 20, 2026",
      },
    ],
  },
  {
    id: "docs",
    title: "Documentation",
    hint: "Access tokens for Confluence (cloud or on-prem).",
    defaultComponent: "confluence",
    secrets: [
      {
        id: "s3",
        name: "confluence-cloud",
        component: "confluence",
        masked: "••••••••••••a9d4",
        added: "Added Oct 1, 2026",
      },
    ],
  },
  {
    id: "load",
    title: "Load testing",
    hint: "API credentials for cloud load-testing vendors (e.g. BlazeMeter).",
    defaultComponent: "blazemeter",
    secrets: [
      {
        id: "s4",
        name: "blazemeter-api-key",
        component: "blazemeter",
        masked: "••••••••••15e2",
        added: "Added Sep 28, 2026",
      },
      {
        id: "s5",
        name: "blazemeter-api-secret",
        component: "blazemeter",
        masked: "••••••••••••••••cc92",
        added: "Added Sep 28, 2026",
      },
    ],
  },
  {
    id: "scm",
    title: "Source control",
    hint: "Personal access tokens for repository hosts.",
    defaultComponent: "github",
    secrets: [],
  },
];
