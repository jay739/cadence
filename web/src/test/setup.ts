import "@testing-library/jest-dom/vitest";

// jsdom does not implement matchMedia; ThemeToggle (and anything else checking
// prefers-color-scheme) needs a stub so it doesn't throw in tests.
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
