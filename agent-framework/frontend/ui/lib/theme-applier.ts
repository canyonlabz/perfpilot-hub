/**
 * Blocking pre-paint theme script. Inlined into `app/layout.tsx` `<head>` so
 * it runs while the HTML is parsed — the same IIFE the approved mocks use.
 * Keep this string free of imports; the browser executes it as raw JS.
 */
export const THEME_APPLIER_SOURCE = `(function () {
  var el = document.getElementById('theme-config');
  if (!el || !el.textContent) return;
  var cfg = JSON.parse(el.textContent);
  var PREF_KEY = (cfg.user_preference && cfg.user_preference.key) || 'perfpilot.theme';

  function resolveTheme() {
    if (cfg.allow_user_override) {
      try {
        var stored = localStorage.getItem(PREF_KEY);
        if (stored === 'light' || stored === 'dark') return stored;
      } catch (_) {}
    }
    if (cfg.default_theme === 'auto') {
      return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    if (cfg.default_theme === 'light' || cfg.default_theme === 'dark') return cfg.default_theme;
    return 'light';
  }

  function conversationVarName(section, key) {
    if (section === 'canvas' && key === 'background') return '--conv-canvas-bg';
    if (section === 'tool_chip') return '--conv-chip-' + key.replace(/_/g, '-');
    if (section === 'divider') return '--conv-divider-' + key;
    return '--conv-' + section + '-' + key.replace(/background/g, 'bg').replace(/_/g, '-');
  }

  function applyTheme(theme) {
    var root = document.documentElement;
    root.setAttribute('data-theme', theme);
    if (theme === 'dark') root.classList.add('dark');
    else root.classList.remove('dark');
    root.style.colorScheme = theme;

    var shell = (cfg.shell && cfg.shell.themes && cfg.shell.themes[theme]) || {};
    Object.keys(shell).forEach(function (k) {
      root.style.setProperty('--' + k.replace(/_/g, '-'), shell[k]);
    });

    var conv = (cfg.conversation && cfg.conversation.themes && cfg.conversation.themes[theme]) || {};
    Object.keys(conv).forEach(function (section) {
      var palette = conv[section] || {};
      Object.keys(palette).forEach(function (k) {
        root.style.setProperty(conversationVarName(section, k), palette[k]);
      });
    });
  }

  window.__perfpilotTheme = {
    cfg: cfg,
    apply: applyTheme,
    resolve: resolveTheme,
    key: PREF_KEY
  };
  applyTheme(resolveTheme());
})();
`;
