"use client";

import { TooltipProvider } from "@/components/ui/tooltip";
import { ToastProvider } from "@/components/ui/toast";
import { isAgUiDebugEnabled } from "@/lib/chat-runtime-config";
import { COPILOTKIT_CHAT_RUNTIME_URL } from "@/lib/copilot-constants";
import { LanguageProvider } from "@/lib/language";
import { ThemeProvider } from "@/lib/theme";
import { CopilotKit } from "@copilotkit/react-core/v2";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

export function AppProviders({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      })
  );
  const content = (
    <ThemeProvider>
      <LanguageProvider>
        <ToastProvider>
          <TooltipProvider delayDuration={250}>{children}</TooltipProvider>
        </ToastProvider>
      </LanguageProvider>
    </ThemeProvider>
  );

  return (
    <QueryClientProvider client={queryClient}>
      <CopilotKit
        credentials="include"
        onError={(event) => {
          const code = eventField(event, "code");
          const context = eventField(event, "context") ?? {};
          const error = event.error;
          const log = isExpectedCopilotProviderDelay(error) ? console.warn : console.error;
          log(
            "[strategy-copilotkit]",
            code,
            error instanceof Error ? error.message : String(error ?? "unknown error"),
            context
          );
        }}
        runtimeUrl={COPILOTKIT_CHAT_RUNTIME_URL}
        showDevConsole={isAgUiDebugEnabled()}
        useSingleEndpoint
      >
        {content}
      </CopilotKit>
    </QueryClientProvider>
  );
}

function eventField(event: unknown, key: string) {
  return typeof event === "object" && event !== null && key in event
    ? (event as Record<string, unknown>)[key]
    : undefined;
}

function isExpectedCopilotProviderDelay(error: unknown) {
  const message = error instanceof Error ? error.message : String(error ?? "");
  return (
    message.includes("AI provider is taking longer than usual to start") ||
    message.includes("AI provider khởi động lâu hơn bình thường")
  );
}
