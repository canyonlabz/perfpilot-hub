"use client";

import { Info } from "lucide-react";
import {
  integrationSelectOptions,
  type IntegrationKey,
  type SettingsSecret,
} from "@/lib/settings-catalog";

export function ProjectsTab({ secrets }: { secrets: SettingsSecret[] }) {
  const confluenceOptions = integrationSelectOptions(secrets, "confluence");
  const blazemeterOptions = integrationSelectOptions(secrets, "blazemeter");
  const githubOptions = integrationSelectOptions(secrets, "github");

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
          Project rows will come from the backend. Each enabled row will bind
          Confluence, BlazeMeter, and GitHub to a token name from Secrets, or
          leave that integration as <strong>None selected</strong>.
        </span>
      </div>

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
          <div className="pp-section-empty">
            No projects are available yet. Once the backend lists environments,
            each row will offer these token-name options:
          </div>
          <div className="pp-integrations pp-integrations-preview">
            <IntegrationSelect
              label="Confluence"
              options={confluenceOptions}
              disabled
            />
            <IntegrationSelect
              label="BlazeMeter"
              options={blazemeterOptions}
              disabled
            />
            <IntegrationSelect
              label="GitHub"
              options={githubOptions}
              disabled
            />
          </div>
        </div>
        <div className="pp-card-footer">
          0 enabled · active: <span className="pp-mono">none</span>
        </div>
      </div>
    </div>
  );
}

function IntegrationSelect({
  label,
  options,
  disabled,
}: {
  label: string;
  options: { value: string; label: string }[];
  disabled: boolean;
}) {
  const key = label.toLowerCase() as IntegrationKey;
  return (
    <div>
      <label className="pp-field-label" htmlFor={`preview-${key}`}>
        {label}
      </label>
      <select
        id={`preview-${key}`}
        className="pp-field"
        disabled={disabled}
        defaultValue=""
        aria-label={`${label} token`}
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
