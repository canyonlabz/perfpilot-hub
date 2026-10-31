"use client";

import { useLayoutEffect } from "react";

/**
 * Re-applies the resolved theme after React hydrates.
 *
 * The blocking `<head>` script already painted the correct tokens. In
 * development, React Strict Mode remounts `<html>` and can clear attributes
 * the script set; this effect restores them before the next paint. It also
 * follows `prefers-color-scheme` when the operator default is `auto` and the
 * user has not stored an explicit choice.
 */
export function ThemeRuntime() {
  useLayoutEffect(() => {
    const api = window.__perfpilotTheme;
    if (!api) return;

    api.apply(api.resolve());

    if (api.cfg.default_theme !== "auto") return;

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      try {
        if (localStorage.getItem(api.key)) return;
      } catch {
        /* private mode — keep following the operator default */
      }
      api.apply(api.resolve());
    };

    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  return null;
}
