"use client";

import { create } from "zustand";

import type { ArtifactWorkspaceTab } from "@/lib/artifact-workspace";

type StrategyUiState = {
  artifactPanelOpen: boolean;
  artifactWorkspaceTab: ArtifactWorkspaceTab;
  selectedArtifactId: string | null;
  setArtifactPanelOpen: (open: boolean) => void;
  setArtifactWorkspaceTab: (tab: ArtifactWorkspaceTab) => void;
  setSelectedArtifactId: (artifactId: string | null) => void;
};

export const useStrategyUiStore = create<StrategyUiState>((set) => ({
  artifactPanelOpen: false,
  artifactWorkspaceTab: "strategy",
  selectedArtifactId: null,
  setArtifactPanelOpen: (artifactPanelOpen) =>
    set((state) =>
      state.artifactPanelOpen === artifactPanelOpen ? state : { artifactPanelOpen }
    ),
  setArtifactWorkspaceTab: (artifactWorkspaceTab) =>
    set((state) =>
      state.artifactWorkspaceTab === artifactWorkspaceTab
        ? state
        : { artifactWorkspaceTab }
    ),
  setSelectedArtifactId: (selectedArtifactId) =>
    set((state) =>
      state.selectedArtifactId === selectedArtifactId ? state : { selectedArtifactId }
    ),
}));
