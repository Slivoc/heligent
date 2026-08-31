import React from "react";
import { createRoot } from "react-dom/client";
import { Dashboard } from "../app/Dashboard";
import { MobileDemo } from "../app/MobileDemo";
import "../app/globals.css";

const isDemo = window.location.pathname === "/demo" || window.location.pathname === "/demo/";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {isDemo ? <MobileDemo /> : <Dashboard />}
  </React.StrictMode>,
);
