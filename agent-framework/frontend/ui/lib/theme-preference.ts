import {
  THEME_STORAGE_KEY,
  type PerfPilotThemeChoice,
} from "@/lib/theme-config";

export function readThemeChoice(): PerfPilotThemeChoice {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* private mode */
  }
  return "auto";
}

export function writeThemeChoice(choice: PerfPilotThemeChoice): void {
  try {
    if (choice === "auto") localStorage.removeItem(THEME_STORAGE_KEY);
    else localStorage.setItem(THEME_STORAGE_KEY, choice);
  } catch {
    /* private mode */
  }
  window.__perfpilotTheme?.apply(window.__perfpilotTheme.resolve());
}
