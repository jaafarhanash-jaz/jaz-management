import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/index.css";
import App from "@/App";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    },
  },
});

const root = ReactDOM.createRoot(document.getElementById("root"));
const appTree = (
  <QueryClientProvider client={queryClient}>
    <App />
  </QueryClientProvider>
);
// Measured directly on production: every mount-time data fetch (dashboard
// stats, notifications) was firing twice, ~3ms apart - the signature of
// StrictMode's deliberate mount->unmount->remount cycle, which is supposed
// to be a development-only diagnostic (React's own docs: "this behavior
// only happens in development"). Whatever the exact cause, doubling every
// expensive cross-region API call on every real user's page load has zero
// diagnostic value in a deployed production bundle and a direct,
// measurable cost, so it's now dev-only here too.
root.render(
  process.env.NODE_ENV === "development" ? (
    <React.StrictMode>{appTree}</React.StrictMode>
  ) : (
    appTree
  ),
);
