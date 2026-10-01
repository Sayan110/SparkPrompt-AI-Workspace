"use client";

import { ThemeProvider } from "@/lib/theme";
import { ToastProvider } from "@/lib/toast";
import { WorkspaceProvider } from "@/lib/workspace";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <ToastProvider>
        <WorkspaceProvider>{children}</WorkspaceProvider>
      </ToastProvider>
    </ThemeProvider>
  );
}
