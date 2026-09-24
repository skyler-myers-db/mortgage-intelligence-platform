import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router/dom";
import { createAppRouter } from "./appRouter";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { installClientErrorListeners, rootErrorOptions } from "./lib/clientErrorLog";
import { hasRenderBlockedChunkLoad } from "./lib/lazyPreload";
import { createMipQueryClient } from "./lib/queryClient";
import { setRumRouteSource } from "./lib/rumBridge";
import { installStaleChunkRecovery } from "./lib/staleChunkRecovery";
import "./design-system/tokens.css";
import "./design-system/components.css";
import "./design-system/print.css";

// Errors React never sees (event handlers, timers, unawaited promises) go
// through the same message-free report as the boundaries' catches. Installed
// first, before the router and createRoot, so a boot-time throw is recorded.
installClientErrorListeners();

const queryClient = createMipQueryClient();

// A data router with one catch-all route around the unchanged <Routes> tree
// in app.tsx, so the unsaved-changes guard can use useBlocker (audit
// states-05). See appRouter.tsx: no loaders, actions or route objects.
const router = createAppRouter();

// RUM (lib/rum, lazy and off by default) reports a route_change only when the
// router COMMITS a new location: a Back the unsaved-changes guard blocks
// never changes router.state.location, so it records nothing.
setRumRouteSource((listener) => router.subscribe((state) => listener(state.location.pathname)));

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
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>
);
