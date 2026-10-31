"use client";

import { useState } from "react";
import { Folder, KeyRound, SlidersHorizontal, User } from "lucide-react";
import { Header } from "@/components/layout/header";
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
          {tab === "projects" && <ProjectsTab />}
          {tab === "secrets" && <SecretsTab />}
          {tab === "preferences" && <PreferencesTab />}
          {tab === "profile" && <ProfileTab />}
        </section>
      </div>
    </div>
  );
}
