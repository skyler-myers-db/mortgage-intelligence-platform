import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router";
import App from "./app";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { rootErrorOptions } from "./lib/clientErrorLog";
import { hasRenderBlockedChunkLoad } from "./lib/lazyPreload";
import { createMipQueryClient } from "./lib/queryClient";
import { installStaleChunkRecovery } from "./lib/staleChunkRecovery";
import "./design-system/tokens.css";
import "./design-system/components.css";
import "./design-system/print.css";

const queryClient = createMipQueryClient();

// A tab left open across a deploy asks for chunks the new build retired.
// Reload once (guarded) when a render is blocked on such a chunk; see
// lib/staleChunkRecovery for the rules.
installStaleChunkRecovery({ isRenderBlocked: hasRenderBlockedChunkLoad });

// The root boundary sits outside every provider so a throw in the router,
// the query client or the shell still renders a recovery surface instead of
// unmounting to a white page. Per-route recovery lives in app.tsx.
ReactDOM.createRoot(document.getElementById("root")!, rootErrorOptions()).render(
  <React.StrictMode>
    <ErrorBoundary boundary="root" variant="page">
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>
);
