import "@testing-library/jest-dom/vitest";

// jsdom no implementa matchMedia -- App.tsx lo usa para el valor inicial del
// modo oscuro (prefers-color-scheme). Mock minimo, sin matches reales.
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
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
