"use client";

import { useMemo, useState } from "react";
import { DEMO_PROJECTS, type DemoProject } from "@/lib/settings-demo";

type ProjectState = DemoProject & {
  confluenceValue: string;
  blazemeterValue: string;
  githubValue: string;
};

function toState(project: DemoProject): ProjectState {
  return {
    ...project,
    confluenceValue: project.confluence[0],
    blazemeterValue: project.blazemeter[0],
    githubValue: project.github[0],
  };
}

export function ProjectsTab() {
  const [projects, setProjects] = useState<ProjectState[]>(() =>
    DEMO_PROJECTS.map(toState)
  );
  const [activeId, setActiveId] = useState("project1");

  const enabledCount = useMemo(
    () => projects.filter((project) => project.enabled).length,
    [projects]
  );

  function toggleEnabled(id: string, enabled: boolean) {
    setProjects((current) => {
      const next = current.map((project) =>
        project.id === id ? { ...project, enabled } : project
      );
      if (!enabled && activeId === id) {
        const fallback = next.find((project) => project.id !== id && project.enabled);
        setActiveId(fallback?.id ?? "");
      }
      return next;
    });
  }

  return (
    <div>
      <div className="pp-page-title">
        <h1>Projects</h1>
        <p>
          Choose which projects appear in your project switcher and which one new
          chats default to. Project configuration itself (environments, auth,
          workspace IDs) lives on the backend in{" "}
          <span className="pp-mono">environments.yaml</span>.
        </p>
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
          {projects.map((project) => {
            const isActive = project.enabled && project.id === activeId;
            return (
              <div
                key={project.id}
                className="pp-project-row"
                data-disabled={!project.enabled}
                data-active={isActive}
              >
                <div className="pp-project-head">
                  <input
                    type="checkbox"
                    className="pp-check"
                    checked={project.enabled}
                    aria-label={`Enable ${project.name}`}
                    onChange={(event) =>
                      toggleEnabled(project.id, event.target.checked)
                    }
                  />
                  <div className="pp-project-info">
                    <div className="pp-project-name">
                      {project.name}{" "}
                      <span className="pp-mono">{project.slug}</span>
                      {isActive && <span className="pp-pill-active">Active</span>}
                    </div>
                    <div className="pp-project-hint">
                      {project.environmentCount === 0
                        ? "No environments configured"
                        : `${project.environmentCount} environment${
                            project.environmentCount === 1 ? "" : "s"
                          }`}
                      {" · aliases: "}
                      {project.aliases.map((alias, index) => (
                        <span key={alias}>
                          {index > 0 && ", "}
                          <span className="pp-mono">{alias}</span>
                        </span>
                      ))}
                    </div>
                    <div className="pp-integrations">
                      <IntegrationSelect
                        label="Confluence"
                        options={project.confluence}
                        value={project.confluenceValue}
                        disabled={!project.enabled}
                        onChange={(value) =>
                          setProjects((current) =>
                            current.map((item) =>
                              item.id === project.id
                                ? { ...item, confluenceValue: value }
                                : item
                            )
                          )
                        }
                      />
                      <IntegrationSelect
                        label="BlazeMeter"
                        options={project.blazemeter}
                        value={project.blazemeterValue}
                        disabled={!project.enabled}
                        onChange={(value) =>
                          setProjects((current) =>
                            current.map((item) =>
                              item.id === project.id
                                ? { ...item, blazemeterValue: value }
                                : item
                            )
                          )
                        }
                      />
                      <IntegrationSelect
                        label="GitHub"
                        options={project.github}
                        value={project.githubValue}
                        disabled={!project.enabled}
                        onChange={(value) =>
                          setProjects((current) =>
                            current.map((item) =>
                              item.id === project.id
                                ? { ...item, githubValue: value }
                                : item
                            )
                          )
                        }
                      />
                    </div>
                  </div>
                </div>
                <label className="pp-radio-active">
                  <input
                    type="radio"
                    className="pp-check"
                    name="active-project"
                    checked={isActive}
                    disabled={!project.enabled}
                    onChange={() => setActiveId(project.id)}
                  />
                  Make active
                </label>
              </div>
            );
          })}
        </div>
        <div className="pp-card-footer">
          {enabledCount} enabled · active:{" "}
          <span className="pp-mono">{activeId || "none"}</span>
        </div>
      </div>
    </div>
  );
}

function IntegrationSelect({
  label,
  options,
  value,
  disabled,
  onChange,
}: {
  label: string;
  options: string[];
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <div>
      <label className="pp-field-label">{label}</label>
      <select
        className="pp-field"
        disabled={disabled}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </div>
  );
}
