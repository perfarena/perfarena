export type Theme = "light" | "dark";

const KEY = "arena.theme";

export function getStoredTheme(): Theme | null {
  const v = localStorage.getItem(KEY);
  return v === "light" || v === "dark" ? v : null;
}

export function systemTheme(): Theme {
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function resolveTheme(): Theme {
  return getStoredTheme() ?? systemTheme();
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
}

export function setTheme(theme: Theme): void {
  localStorage.setItem(KEY, theme);
  applyTheme(theme);
}

export function clearStoredTheme(): void {
  localStorage.removeItem(KEY);
  applyTheme(systemTheme());
}
