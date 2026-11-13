import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import { ThemeProvider } from "./lib/theme";
import { Toaster } from "./components/ui/Toast";
import { AppErrorBoundary } from "./components/AppErrorBoundary";
import { WorkStatusProvider } from "./hooks/useWorkStatus";

function mountApp() {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <ThemeProvider>
        <AppErrorBoundary>
          <WorkStatusProvider>
            <App />
          </WorkStatusProvider>
        </AppErrorBoundary>
        <Toaster />
      </ThemeProvider>
    </StrictMode>,
  );
}

mountApp();
