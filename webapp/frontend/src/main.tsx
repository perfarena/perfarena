import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { applyTheme, resolveTheme } from "./lib/theme";
import "./styles.css";

applyTheme(resolveTheme());

const meta = document.querySelector('meta[name="theme-color"]') as HTMLMetaElement | null;
if (meta) {
  const updateMetaThemeColor = () => {
    const isLight = document.documentElement.getAttribute("data-theme") === "light";
    meta.content = isLight ? "#f7f8fb" : "#0b0f17";
  };
  updateMetaThemeColor();
  new MutationObserver(updateMetaThemeColor).observe(document.documentElement, {
    attributes: true, attributeFilter: ["data-theme"],
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
