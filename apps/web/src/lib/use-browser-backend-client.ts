"use client";

import { useCallback, useMemo } from "react";

import { BackendClient } from "@/lib/backend-client";
import type { Artifact, ArtifactPreviewResponse } from "@/lib/backend-schemas";

const BROWSER_API_BASE_URL = "/api/backend";
export const DEFAULT_ARTIFACT_PREVIEW_BYTES = 64 * 1024;

type ClerkBrowserWindow = Window & {
  Clerk?: {
    session?: {
      getToken?: () => Promise<string | null>;
    };
  };
};

async function getBrowserClerkToken() {
  if (typeof window === "undefined") {
    return null;
  }
  const clerk = (window as ClerkBrowserWindow).Clerk;
  return (await clerk?.session?.getToken?.().catch(() => null)) ?? null;
}

export function useBrowserBackendClient() {
  const authenticatedFetch = useCallback<typeof fetch>(
    async (input, init) => {
      const headers = new Headers(init?.headers);
      const token = await getBrowserClerkToken();
      if (token) {
        headers.set("Authorization", `Bearer ${token}`);
      }
      return fetch(input, {
        ...init,
        credentials: init?.credentials ?? "same-origin",
        headers,
      });
    },
    []
  );

  return useMemo(
    () =>
      new BackendClient({
        baseUrl: BROWSER_API_BASE_URL,
        fetcher: authenticatedFetch,
      }),
    [authenticatedFetch]
  );
}

export async function getArtifactPreviewForViewer(
  client: BackendClient,
  artifact: Artifact,
  options: { maxBytes?: number } = {}
): Promise<ArtifactPreviewResponse> {
  if (artifact.presentation.viewer_kind !== "backtest_dashboard") {
    return client.getArtifactPreview(artifact.id, {
      maxBytes: options.maxBytes ?? DEFAULT_ARTIFACT_PREVIEW_BYTES,
    });
  }
  const content = await client.getArtifactContent(artifact.id);
  return {
    ...content,
    language: null,
    line_count: null,
    preview: content.content,
    raw_available: true,
    truncated: false,
  };
}
