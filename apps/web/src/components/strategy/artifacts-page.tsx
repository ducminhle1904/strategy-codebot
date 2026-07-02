"use client";

import { ArtifactPreviewContent } from "@/components/strategy/artifact-preview-content";
import {
  getArtifactCardPreviewLines,
  getWorkspaceInventoryArtifacts,
} from "@/components/strategy/artifacts-page-helpers";
import { ConversationSidebar } from "@/components/strategy/workspace";
import { Button } from "@/components/ui/button";
import type { Artifact, ArtifactListResponse } from "@/lib/backend-schemas";
import { getArtifactUserSummary } from "@/lib/artifact-workspace";
import { useI18n } from "@/lib/language";
import { useTheme } from "@/lib/theme";
import {
  DEFAULT_ARTIFACT_PREVIEW_BYTES,
  getArtifactPreviewForViewer,
  useBrowserBackendClient,
} from "@/lib/use-browser-backend-client";
import { cn } from "@/lib/utils";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import {
  ExternalLink,
  FileCode2,
  FileStack,
  FileText,
  Loader2,
  RefreshCcw,
  Search,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

const ARTIFACT_PAGE_SIZE = 30;

function artifactKindLabel(artifact: Artifact) {
  return getArtifactUserSummary(artifact, "en").label;
}

function artifactIcon(artifact: Artifact) {
  if (artifact.presentation.user_kind === "code") {
    return <FileCode2 className="size-4" />;
  }
  if (artifact.presentation.viewer_kind === "json") {
    return <FileText className="size-4" />;
  }
  return <FileStack className="size-4" />;
}

function formatArtifactDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function previewSummaryLines(artifact: Artifact) {
  return getArtifactCardPreviewLines(artifact, 4);
}

function artifactSearchText(artifact: Artifact) {
  return [
    artifact.display_name,
    artifact.kind,
    artifactKindLabel(artifact),
    artifact.presentation.viewer_kind,
    ...getArtifactCardPreviewLines(artifact, 8),
  ]
    .join(" ")
    .toLowerCase();
}

export function ArtifactsPage() {
  const client = useBrowserBackendClient();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { language, setLanguage } = useI18n();
  const { setTheme, theme } = useTheme();
  const [searchQuery, setSearchQuery] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const loadMoreRef = useRef<HTMLDivElement | null>(null);
  const scrollRootRef = useRef<HTMLDivElement | null>(null);
  const sidebar = useQuery({
    queryFn: () => client.listConversationSidebar(),
    queryKey: ["conversation-sidebar"],
  });
  const sidebarItems = useMemo(() => sidebar.data?.items ?? [], [sidebar.data?.items]);
  const artifactsQuery = useInfiniteQuery({
    getNextPageParam: (lastPage: ArtifactListResponse) => lastPage.next_cursor,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      client.listWorkspaceArtifacts({
        cursor: pageParam,
        limit: ARTIFACT_PAGE_SIZE,
      }),
    queryKey: ["workspace-artifacts"],
  });
  const {
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = artifactsQuery;
  const artifacts = useMemo(
    () => getWorkspaceInventoryArtifacts((artifactsQuery.data?.pages ?? []).flatMap((page) => page.items)),
    [artifactsQuery.data?.pages]
  );
  const filteredArtifacts = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) {
      return artifacts;
    }
    return artifacts.filter((artifact) => artifactSearchText(artifact).includes(query));
  }, [artifacts, searchQuery]);
  const selectedArtifactId = searchParams.get("artifact");
  const selectedArtifact = useMemo(
    () => artifacts.find((artifact) => artifact.id === selectedArtifactId) ?? null,
    [artifacts, selectedArtifactId]
  );
  const preview = useQuery({
    enabled: Boolean(selectedArtifactId),
    queryFn: () => {
      if (!selectedArtifactId) {
        return Promise.reject(new Error("No artifact selected"));
      }
      return selectedArtifact
        ? getArtifactPreviewForViewer(client, selectedArtifact)
        : client.getArtifactPreview(selectedArtifactId, {
            maxBytes: DEFAULT_ARTIFACT_PREVIEW_BYTES,
          });
    },
    queryKey: ["artifact-preview", selectedArtifactId],
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  });
  const activeArtifact = selectedArtifact ?? preview.data ?? null;
  const artifactDrawerOpen = Boolean(selectedArtifactId);
  const selectArtifact = (artifactId: string | null) => {
    router.replace(
      artifactId ? `/artifacts?artifact=${encodeURIComponent(artifactId)}` : "/artifacts",
      { scroll: false }
    );
  };

  useEffect(() => {
    const sentinel = loadMoreRef.current;
    if (!sentinel || !hasNextPage) {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (entry?.isIntersecting && !isFetchingNextPage) {
          void fetchNextPage();
        }
      },
      {
        root: scrollRootRef.current,
        rootMargin: "640px 0px",
        threshold: 0,
      }
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [fetchNextPage, hasNextPage, isFetchingNextPage]);

  return (
    <main className="apple-page-shell flex h-[100dvh] overflow-hidden text-foreground">
      <ConversationSidebar
        activeView="artifacts"
        collapsed={sidebarCollapsed}
        conversations={sidebarItems}
        isCreating={false}
        isLoading={sidebar.isLoading}
        isNewChatDisabled={false}
        language={language}
        onCreate={() => router.push("/")}
        onDelete={() => undefined}
        onLanguageChange={setLanguage}
        onOpenAccountDialog={() => undefined}
        onOpenArtifacts={() => undefined}
        onOpenPaperBots={() => router.push("/paper-bots")}
        onOpenSettingsTab={() => undefined}
        onRename={() => undefined}
        onSelect={(conversationId) => router.push(`/c/${encodeURIComponent(conversationId)}`)}
        onThemeChange={setTheme}
        onToggleCollapsed={() => setSidebarCollapsed((collapsed) => !collapsed)}
        selectedConversationId={null}
        theme={theme}
      />
      <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-transparent lg:flex-row">
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto" ref={scrollRootRef}>
          <div
            className={cn(
              "mx-auto w-full px-4 py-8 transition-[max-width,padding] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] md:px-8 md:py-12",
              artifactDrawerOpen ? "max-w-none" : "max-w-7xl"
            )}
          >
          <header className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <h1 className="apple-section-title">Artifacts</h1>
              <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
                Code, reports, and strategy outputs saved from your chats.
              </p>
            </div>
            <Button
              className="self-start sm:self-auto"
              disabled={artifactsQuery.isFetching}
              onClick={() => artifactsQuery.refetch()}
              size="sm"
              type="button"
              variant="outline"
            >
              {artifactsQuery.isFetching ? <Loader2 className="size-4 animate-spin" /> : <RefreshCcw className="size-4" />}
              Refresh
            </Button>
          </header>

          <label className="apple-search-shell mt-6 flex h-11 items-center gap-3 px-4 text-sm text-muted-foreground focus-within:border-primary/40">
            <Search className="size-4 shrink-0" />
            <span className="sr-only">Search artifacts</span>
            <input
              className="h-full min-w-0 flex-1 bg-transparent text-foreground outline-none placeholder:text-muted-foreground"
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Search artifacts..."
              type="search"
              value={searchQuery}
            />
          </label>

          <section className="mt-7">
            {artifactsQuery.isLoading ? (
              <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
                <Loader2 className="mr-2 size-4 animate-spin" />
                Loading artifacts
              </div>
            ) : artifactsQuery.isError ? (
              <div className="rounded-[8px] border border-destructive/25 bg-destructive/5 p-4 text-sm text-destructive">
                Unable to load artifacts.
              </div>
            ) : artifacts.length === 0 ? (
              <div className="apple-utility-card border-dashed p-5 text-sm text-muted-foreground">
                No artifacts yet. Generated Pine code, backtest reports, and review outputs will appear here.
              </div>
            ) : filteredArtifacts.length === 0 ? (
              <div className="apple-utility-card border-dashed p-8 text-sm text-muted-foreground">
                No artifacts match your search.
              </div>
            ) : (
              <div className="space-y-6">
                <div
                  className={cn(
                    "grid grid-cols-1 gap-5 transition-[gap] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] sm:grid-cols-2",
                    artifactDrawerOpen ? "xl:grid-cols-2 2xl:grid-cols-3" : "xl:grid-cols-3"
                  )}
                >
                  {filteredArtifacts.map((artifact) => {
                  const summaryLines = previewSummaryLines(artifact);
                  const isSelected = selectedArtifactId === artifact.id;
                  return (
                    <button
                      className={cn(
                        "apple-utility-card group relative grid h-[192px] overflow-hidden text-left text-card-foreground transition hover:-translate-y-0.5 hover:border-primary/35 active:translate-y-0",
                        isSelected && "border-primary/45 ring-1 ring-primary/25"
                      )}
                      key={artifact.id}
                      onClick={() => selectArtifact(artifact.id)}
                      type="button"
                    >
                      <span className="pointer-events-none absolute right-0 top-0 size-5 border-b border-l border-border bg-muted transition group-hover:bg-muted/80" />
                      <span className="min-h-0 border-b border-border/70 px-4 py-3">
                        <span className="mb-2 flex items-center gap-2 text-[11px] font-medium uppercase text-muted-foreground">
                          {artifactIcon(artifact)}
                          {artifactKindLabel(artifact)}
                        </span>
                        <span
                          className={cn(
                            "block space-y-1 overflow-hidden text-xs leading-5 text-muted-foreground",
                            artifact.presentation.user_kind === "code" ? "font-mono" : "font-sans"
                          )}
                        >
                          {summaryLines.slice(0, 5).map((line) => (
                            <span className="block truncate" key={line}>
                              {line}
                            </span>
                          ))}
                        </span>
                      </span>
                      <span className="min-w-0 px-4 py-3">
                        <span className="block truncate text-sm font-medium">{artifact.display_name}</span>
                        <span className="mt-2 block truncate text-xs text-muted-foreground">
                          {formatArtifactDate(artifact.created_at)}
                          {artifact.conversation_id ? " · Private" : ""}
                        </span>
                      </span>
                    </button>
                  );
                  })}
                </div>
                {hasNextPage ? (
                  <div
                    aria-live="polite"
                    className="flex min-h-16 items-center justify-center gap-2 text-sm text-muted-foreground"
                    ref={loadMoreRef}
                  >
                    {isFetchingNextPage ? (
                      <>
                        <Loader2 className="size-4 animate-spin" />
                        Loading older artifacts
                      </>
                    ) : (
                      "More artifacts load automatically as you scroll."
                    )}
                  </div>
                ) : null}
              </div>
            )}
          </section>
          </div>
        </div>
        <aside
          aria-hidden={!artifactDrawerOpen}
          className={cn(
            "apple-product-tile shrink-0 overflow-hidden transition-[width,max-height,opacity,transform,border-color] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
            artifactDrawerOpen
              ? "max-h-[82dvh] translate-y-0 border-t border-border opacity-100 lg:h-auto lg:max-h-none lg:w-[min(980px,62vw)] lg:translate-x-0 lg:border-l lg:border-t-0 2xl:w-[980px]"
              : "max-h-0 translate-y-3 border-t border-transparent opacity-0 lg:max-h-none lg:w-0 lg:translate-x-4 lg:border-l lg:border-t-0"
          )}
        >
          <div
            className={cn(
              "grid h-full min-h-0 grid-rows-[auto_1fr] transition-opacity duration-200 ease-out",
              artifactDrawerOpen ? "opacity-100 delay-100" : "pointer-events-none opacity-0"
            )}
          >
            {artifactDrawerOpen ? (
              <>
                <div className="apple-frosted border-b p-4 md:p-5">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <p className="text-xs font-medium uppercase text-muted-foreground">
                    {activeArtifact ? artifactKindLabel(activeArtifact) : "Artifact"}
                  </p>
                  <h2 className="mt-1 truncate text-base font-semibold">
                    {activeArtifact?.display_name ?? "Loading artifact"}
                  </h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {activeArtifact ? formatArtifactDate(activeArtifact.created_at) : selectedArtifactId}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {activeArtifact?.conversation_id ? (
                    <Button asChild className="self-start" size="sm" variant="outline">
                      <Link href={`/c/${encodeURIComponent(activeArtifact.conversation_id)}`}>
                        <ExternalLink className="size-4" />
                        Open chat
                      </Link>
                    </Button>
                  ) : null}
                  <Button
                    aria-label="Close artifact preview"
                    onClick={() => selectArtifact(null)}
                    size="icon-sm"
                    type="button"
                    variant="ghost"
                  >
                    <X className="size-4" />
                  </Button>
                </div>
              </div>
                </div>
                <div className="min-h-0 overflow-auto bg-background/40 p-4 md:p-5">
              {preview.isLoading ? (
                <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
                  <Loader2 className="mr-2 size-4 animate-spin" />
                  Loading preview
                </div>
              ) : preview.isError ? (
                <div className="rounded-[8px] border border-destructive/25 bg-destructive/5 p-4 text-sm text-destructive">
                  Unable to load preview.
                </div>
              ) : !preview.data ? (
                <div className="apple-utility-card p-4 text-sm text-muted-foreground">
                  Preview unavailable.
                </div>
              ) : (
                <ArtifactPreviewContent preview={preview.data} />
              )}
                </div>
              </>
            ) : null}
          </div>
        </aside>
      </section>
    </main>
  );
}
