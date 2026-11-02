"use client";

import { useState } from "react";
import { Info } from "lucide-react";
import {
  INTEGRATION_LABELS,
  NONE_SELECTED,
  integrationSelectOptions,
  type IntegrationKey,
  type SettingsSecret,
} from "@/lib/settings-catalog";
import {
  saveProjectBindings,
  type ProjectBinding,
} from "@/lib/api";

const INTEGRATIONS: IntegrationKey[] = [
  "confluence",
  "blazemeter",
  "datadog",
  "github",
];

const TOKEN_FIELDS: Record<IntegrationKey, keyof ProjectBinding> = {
  confluence: "confluence_token_name",
  blazemeter: "blazemeter_token_name",
  datadog: "datadog_token_name",
  github: "github_token_name",
};

function emptyToNull(value: string | null): string | null {
  if (!value || !value.trim()) return null;
  return value;
}

function selectOptions(
  secrets: SettingsSecret[],
  key: IntegrationKey,
  current: string | null
) {
  const options = integrationSelectOptions(secrets, key);
  if (current && !options.some((option) => option.value === current)) {
    options.push({ value: current, label: `${current} (missing)` });
  }
  return options;
}

export function ProjectsTab({
  secrets,
  projects,
  onProjectsChange,
}: {
  secrets: SettingsSecret[];
  projects: ProjectBinding[];
  onProjectsChange: (projects: ProjectBinding[]) => void;
}) {
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function updateRow(
    projectKey: string,
    patch: Partial<ProjectBinding>
  ): void {
    onProjectsChange(
      projects.map((row) => {
        if (row.project_key !== projectKey) {
          if (patch.is_active) {
            return { ...row, is_active: false };
          }
          return row;
        }
        const next = { ...row, ...patch };
        if (next.is_active) {
          next.is_enabled = true;
        }
        if (patch.is_enabled === false) {
          next.is_active = false;
        }
        return next;
      })
    );
  }

  async function handleSave() {
    setBusy(true);
    setNotice(null);
    setError(null);
    try {
      const saved = await saveProjectBindings(
        projects.map((row) => ({
          ...row,
          confluence_token_name: emptyToNull(row.confluence_token_name),
          blazemeter_token_name: emptyToNull(row.blazemeter_token_name),
          datadog_token_name: emptyToNull(row.datadog_token_name),
          github_token_name: emptyToNull(row.github_token_name),
        }))
      );
      onProjectsChange(saved);
      setNotice("Projects saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save projects.");
    } finally {
      setBusy(false);
    }
  }

  const enabledCount = projects.filter((row) => row.is_enabled).length;
  const active = projects.find((row) => row.is_active);

  return (
    <div>
      <div className="pp-page-title">
        <h1>Projects</h1>
        <p>
          Choose which projects appear in your project switcher and which one
          new chats default to. Project configuration itself (environments,
          auth, workspace IDs) lives on the backend in{" "}
          <span className="pp-mono">environments.yaml</span>.
        </p>
      </div>

      <div className="pp-note">
        <Info className="h-3.5 w-3.5 shrink-0 mt-0.5" />
        <span>
          Rows come from the backend project catalog. Bind Confluence,
          BlazeMeter, Datadog, and GitHub to a token name from Secrets, or
          leave that integration as <strong>None selected</strong>.
        </span>
      </div>

      {error && (
        <div className="pp-note" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      <div className="pp-card">
        <div className="pp-card-header">
          <div>
            <h3>Projects</h3>
            <p>
              Enable the projects you work on in parallel. The active project
              auto-tags any new chat you create.
            </p>
          </div>
        </div>
        <div className="pp-card-body pp-card-body-tight">
          {projects.length === 0 ? (
            <div className="pp-section-empty">
              No projects are available yet. Once the backend lists
              environments, each row will offer token-name options from
              Secrets.
            </div>
          ) : (
            projects.map((row) => (
              <div key={row.project_key} className="pp-secret-item" style={{ alignItems: "flex-start" }}>
                <div style={{ minWidth: 160 }}>
                  <div className="font-medium">{row.name}</div>
                  <div className="text-[11.5px] text-[var(--text-subtle)] pp-mono">
                    {row.project_key}
                  </div>
                  <label className="pp-field-label" style={{ marginTop: 8 }}>
                    <input
                      type="checkbox"
                      checked={row.is_enabled}
                      onChange={(event) =>
                        updateRow(row.project_key, {
                          is_enabled: event.target.checked,
                        })
                      }
                    />{" "}
                    Enabled
                  </label>
                  <label className="pp-field-label">
                    <input
                      type="radio"
                      name="active-project"
                      checked={row.is_active}
                      onChange={() =>
                        updateRow(row.project_key, { is_active: true })
                      }
                    />{" "}
                    Active
                  </label>
                </div>
                <div className="pp-integrations" style={{ flex: 1 }}>
                  {INTEGRATIONS.map((key) => {
                    const field = TOKEN_FIELDS[key];
                    return (
                      <IntegrationSelect
                        key={key}
                        projectKey={row.project_key}
                        integration={key}
                        options={selectOptions(secrets, key, row[field] as string | null)}
                        value={(row[field] as string | null) ?? NONE_SELECTED}
                        onChange={(next) =>
                          updateRow(row.project_key, {
                            [field]: next || null,
                          } as Partial<ProjectBinding>)
                        }
                      />
                    );
                  })}
                </div>
              </div>
            ))
          )}
        </div>
        <div
          className="pp-card-footer"
          style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
        >
          <span>
            {enabledCount} enabled · active:{" "}
            <span className="pp-mono">{active?.project_key ?? "none"}</span>
            {notice ? ` · ${notice}` : ""}
          </span>
          <button
            type="button"
            className="pp-btn pp-btn-primary"
            disabled={busy || projects.length === 0}
            onClick={() => void handleSave()}
          >
            Save projects
          </button>
        </div>
      </div>
    </div>
  );
}

function IntegrationSelect({
  projectKey,
  integration,
  options,
  value,
  onChange,
}: {
  projectKey: string;
  integration: IntegrationKey;
  options: { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
}) {
  const label = INTEGRATION_LABELS[integration];
  const selectId = `${projectKey}-${integration}`;
  return (
    <div>
      <label className="pp-field-label" htmlFor={selectId}>
        {label}
      </label>
      <select
        id={selectId}
        className="pp-field"
        value={value}
        aria-label={`${label} token`}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.value || "none"} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}
