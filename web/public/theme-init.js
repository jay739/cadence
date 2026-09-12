// Applies a stored theme choice before first paint, so picking light mode doesn't flash dark
// on every load. Matches the storage key ThemeToggle.tsx writes to. No stored choice: leave
// the attribute unset and let tokens.css's prefers-color-scheme media query decide.
//
// A real file rather than an inline <script> on purpose: the backend sends
// `script-src 'self'` (see app/core/middleware.py), which blocks inline execution outright,
// so the inline version was silently refused in production and logged a CSP violation on
// every page load while the flash it existed to prevent happened anyway. Served from /public
// it is same-origin, so it runs.
(function () {
  try {
    var stored = localStorage.getItem("cadence-theme");
    if (stored === "light" || stored === "dark") {
      document.documentElement.dataset.theme = stored;
    }
  } catch {
    // Storage inaccessible (private browsing, disabled storage): fall back silently.
  }
})();
