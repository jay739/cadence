import { useEffect, useState } from "react";
import "./ThemeToggle.css";

type Theme = "light" | "dark";

// Keep in sync with the pre-paint script in index.html, which reads this same key so the
// stored theme applies before first paint instead of flashing the default first.
const STORAGE_KEY = "cadence-theme";

function systemPrefersLight(): boolean {
  return window.matchMedia("(prefers-color-scheme: light)").matches;
}

function readInitialTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return systemPrefersLight() ? "light" : "dark";
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readInitialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  function toggle() {
    setTheme((current) => {
      const next: Theme = current === "dark" ? "light" : "dark";
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // Storage inaccessible: the choice just won't survive a reload.
      }
      return next;
    });
  }

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={toggle}
      aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      aria-pressed={theme === "light"}
    >
      <span className="theme-toggle__icon">
        <svg className="theme-toggle__sun" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="4.5" stroke="currentColor" strokeWidth="1.8" />
          <g stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <line x1="12" y1="2.5" x2="12" y2="5" />
            <line x1="12" y1="19" x2="12" y2="21.5" />
            <line x1="2.5" y1="12" x2="5" y2="12" />
            <line x1="19" y1="12" x2="21.5" y2="12" />
            <line x1="4.9" y1="4.9" x2="6.6" y2="6.6" />
            <line x1="17.4" y1="17.4" x2="19.1" y2="19.1" />
            <line x1="4.9" y1="19.1" x2="6.6" y2="17.4" />
            <line x1="17.4" y1="6.6" x2="19.1" y2="4.9" />
          </g>
        </svg>
        <svg className="theme-toggle__moon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.6 6.6 0 0 0 10.5 10.5Z" fill="currentColor" />
        </svg>
      </span>
    </button>
  );
}
