import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { StoreProvider } from "./lib/store";
import "./styles.css";

document.documentElement.dataset.theme =
  localStorage.getItem("md-theme") === "dark" ||
  (localStorage.getItem("md-theme") !== "light" && window.matchMedia?.("(prefers-color-scheme: dark)").matches)
    ? "dark"
    : "light";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <StoreProvider>
        <App />
      </StoreProvider>
    </BrowserRouter>
  </StrictMode>,
);
