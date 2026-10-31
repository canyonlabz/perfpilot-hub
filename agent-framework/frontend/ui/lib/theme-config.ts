/**
 * Runtime theme contract for the PerfPilot Web UI.
 *
 * Values match the approved HTML mocks (`chat-mock.html` / `settings-mock.html`)
 * and the palette documented in `.cursor/docs/backlog/web-ui/conversation-colors.yaml`.
 * The YAML uses a `web_ui` wrapper and names the shell canvas `canvas`; the
 * mocks (and this module) use no wrapper and name that token `bg` so the
 * applier can set `--bg`. This file is the runtime source — it is not fetched
 * from `/api/settings` or `/api/theme`.
 */

export type PerfPilotThemeName = "light" | "dark";
export type PerfPilotThemeChoice = "auto" | PerfPilotThemeName;

export interface ThemeUserPreference {
  storage: "localStorage" | "server" | "both";
  key: string;
}

export type ShellPalette = {
  bg: string;
  surface: string;
  surface_2: string;
  border: string;
  border_strong: string;
  text: string;
  text_muted: string;
  text_subtle: string;
  accent: string;
  accent_weak: string;
  accent_strong: string;
  success: string;
  success_weak: string;
  danger: string;
};

export type ConversationSection = Record<string, string>;

export interface ThemeConfig {
  default_theme: PerfPilotThemeChoice;
  allow_user_override: boolean;
  user_preference: ThemeUserPreference;
  shell: {
    themes: Record<PerfPilotThemeName, ShellPalette>;
  };
  conversation: {
    themes: Record<PerfPilotThemeName, Record<string, ConversationSection>>;
  };
}

export const THEME_STORAGE_KEY = "perfpilot.theme";

export const THEME_CONFIG: ThemeConfig = {
  default_theme: "auto",
  allow_user_override: true,
  user_preference: { storage: "localStorage", key: THEME_STORAGE_KEY },

  shell: {
    themes: {
      light: {
        bg: "#fafaf9",
        surface: "#ffffff",
        surface_2: "#f5f5f4",
        border: "#e7e5e4",
        border_strong: "#d6d3d1",
        text: "#0c0a09",
        text_muted: "#57534e",
        text_subtle: "#78716c",
        accent: "#4f46e5",
        accent_weak: "#eef2ff",
        accent_strong: "#4338ca",
        success: "#16a34a",
        success_weak: "#dcfce7",
        danger: "#dc2626",
      },
      dark: {
        bg: "#09090b",
        surface: "#18181b",
        surface_2: "#27272a",
        border: "#27272a",
        border_strong: "#3f3f46",
        text: "#fafafa",
        text_muted: "#a1a1aa",
        text_subtle: "#71717a",
        accent: "#818cf8",
        accent_weak: "#1e1b4b",
        accent_strong: "#a5b4fc",
        success: "#22c55e",
        success_weak: "#052e16",
        danger: "#ef4444",
      },
    },
  },

  conversation: {
    themes: {
      light: {
        canvas: { background: "#fafaf9" },
        user: {
          background: "#4f46e5",
          text: "#ffffff",
          code_background: "rgba(255,255,255,0.15)",
          code_text: "#ffffff",
        },
        agent: {
          background: "#ffffff",
          text: "#0c0a09",
          border: "#e7e5e4",
          code_background: "#f5f5f4",
          code_text: "#0c0a09",
        },
        tool_chip: {
          background: "#eef2ff",
          text: "#4338ca",
          border: "#e0e7ff",
        },
        divider: { line: "#e7e5e4", text: "#78716c" },
      },
      dark: {
        canvas: { background: "#09090b" },
        user: {
          background: "#6366f1",
          text: "#ffffff",
          code_background: "rgba(255,255,255,0.18)",
          code_text: "#ffffff",
        },
        agent: {
          background: "#18181b",
          text: "#fafafa",
          border: "#27272a",
          code_background: "#27272a",
          code_text: "#fafafa",
        },
        tool_chip: {
          background: "#1e1b4b",
          text: "#c7d2fe",
          border: "#3730a3",
        },
        divider: { line: "#27272a", text: "#a1a1aa" },
      },
    },
  },
};

export interface PerfPilotThemeApi {
  cfg: ThemeConfig;
  apply: (theme: PerfPilotThemeName) => void;
  resolve: () => PerfPilotThemeName;
  key: string;
}

declare global {
  interface Window {
    __perfpilotTheme?: PerfPilotThemeApi;
  }
}
