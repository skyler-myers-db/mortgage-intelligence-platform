import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./app";
import { createMipQueryClient } from "./lib/queryClient";
import "./design-system/tokens.css";
import "./design-system/components.css";
import "./design-system/print.css";

const queryClient = createMipQueryClient();
void import("./lib/rum")
  .then(({ installRum }) => installRum())
  .catch(() => {
    // RUM is best-effort. A telemetry chunk load failure must never
    // block the app shell or change user-visible behavior.
  });

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
