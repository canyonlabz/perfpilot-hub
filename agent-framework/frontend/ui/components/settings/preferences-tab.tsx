"use client";

import { useEffect, useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import type { PerfPilotThemeChoice } from "@/lib/theme-config";
import { readThemeChoice, writeThemeChoice } from "@/lib/theme-preference";

const CHOICES: {
  id: PerfPilotThemeChoice;
  label: string;
  icon: typeof Sun;
}[] = [
  { id: "auto", label: "Auto", icon: Monitor },
  { id: "light", label: "Light", icon: Sun },
  { id: "dark", label: "Dark", icon: Moon },
];

export function PreferencesTab() {
  const [choice, setChoice] = useState<PerfPilotThemeChoice>("auto");

  useEffect(() => {
    setChoice(readThemeChoice());
  }, []);

  function select(next: PerfPilotThemeChoice) {
    writeThemeChoice(next);
    setChoice(next);
  }

  return (
    <div>
      <div className="pp-page-title">
        <h1>Preferences</h1>
        <p>Look-and-feel options for your PerfPilot workspace.</p>
      </div>

      <div className="pp-card">
        <div className="pp-card-header">
          <div>
            <h3>Appearance</h3>
            <p>These settings apply to your account only.</p>
          </div>
        </div>
        <div className="pp-card-body pp-card-body-tight">
          <div className="pp-pref-row">
            <div>
              <div className="pp-pref-label">Theme</div>
              <div className="pp-pref-hint">
                <strong>Auto</strong> follows your operating system. The operator
                default is set in <span className="pp-mono">conversation-colors.yaml</span>.
              </div>
            </div>
            <div className="pp-segmented" role="radiogroup" aria-label="Theme">
              {CHOICES.map(({ id, label, icon: Icon }) => (
                <button
                  key={id}
                  type="button"
                  data-theme-choice={id}
                  role="radio"
                  aria-pressed={choice === id}
                  onClick={() => select(id)}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
