import { useEffect, useState } from "react";
import { resolveTheme, setTheme, Theme } from "../lib/theme";

export default function ThemeToggle() {
  const [theme, setLocalTheme] = useState<Theme>(() => resolveTheme());

  useEffect(() => {
    const mql = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => setLocalTheme(resolveTheme());
    mql.addEventListener?.("change", onChange);
    return () => mql.removeEventListener?.("change", onChange);
  }, []);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    setLocalTheme(next);
  }

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={toggle}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
      title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
    >
      {theme === "dark" ? "☀" : "☾"}
    </button>
  );
}
