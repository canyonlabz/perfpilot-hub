"use client";

import { useEffect, useState } from "react";
import { Folder, KeyRound, SlidersHorizontal, User } from "lucide-react";
import { Header } from "@/components/layout/header";
import type { SettingsSecret } from "@/lib/settings-catalog";
import {
  fetchProjectBindings,
  fetchSecrets,
  type ProjectBinding,
} from "@/lib/api";
import { ProjectsTab } from "./projects-tab";
import { SecretsTab } from "./secrets-tab";
import { PreferencesTab } from "./preferences-tab";
import { ProfileTab } from "./profile-tab";

type SettingsTabId = "projects" | "secrets" | "preferences" | "profile";

const TABS: { id: SettingsTabId; label: string; icon: typeof Folder }[] = [
  { id: "projects", label: "Projects", icon: Folder },
  { id: "secrets", label: "Secrets", icon: KeyRound },
  { id: "preferences", label: "Preferences", icon: SlidersHorizontal },
  { id: "profile", label: "Profile", icon: User },
];

export function SettingsShell() {
  const [tab, setTab] = useState<SettingsTabId>("projects");
  const [secrets, setSecrets] = useState<SettingsSecret[]>([]);
  const [projects, setProjects] = useState<ProjectBinding[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  async function reload() {
    const [secretRows, projectRows] = await Promise.all([
      fetchSecrets(),
      fetchProjectBindings(),
    ]);
    setSecrets(secretRows);
    setProjects(projectRows);
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await reload();
        if (!cancelled) setLoadError(null);
      } catch (err) {
        if (!cancelled) {
          setLoadError(
            err instanceof Error ? err.message : "Failed to load Settings."
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex flex-col h-screen">
      <Header settingsActive />
      <div className="pp-settings-main">
        <aside className="pp-settings-nav" aria-label="Settings sections">
          <h2>Settings</h2>
          <div className="pp-tab-list" role="tablist">
            {TABS.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                type="button"
                className="pp-tab"
                role="tab"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
              >
                <Icon className="h-[15px] w-[15px]" />
                {label}
              </button>
            ))}
          </div>
        </aside>
        <section className="pp-settings-content">
          {loadError && (
            <div className="pp-note" style={{ marginBottom: 16 }}>
              {loadError}
            </div>
          )}
          {tab === "projects" && (
            <ProjectsTab
              secrets={secrets}
              projects={projects}
              onProjectsChange={setProjects}
            />
          )}
          {tab === "secrets" && (
            <SecretsTab
              secrets={secrets}
              onSecretsChange={setSecrets}
              onReload={reload}
            />
          )}
          {tab === "preferences" && <PreferencesTab />}
          {tab === "profile" && <ProfileTab />}
        </section>
      </div>
    </div>
  );
}
