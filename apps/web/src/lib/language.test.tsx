import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getChatSuggestions, getUiCopy, LANGUAGE_STORAGE_KEY, uiCopy } from "./i18n";
import { LanguageProvider, useI18n } from "./language";

function LanguageProbe() {
  const { language, setLanguage, t } = useI18n();
  return (
    <div>
      <span data-testid="language">{language}</span>
      <span data-testid="new-chat">{t.newChat}</span>
      <button onClick={() => setLanguage("vi")} type="button">
        vi
      </button>
      <button onClick={() => setLanguage("en")} type="button">
        en
      </button>
    </div>
  );
}

function renderLanguageProbe() {
  return render(
    <LanguageProvider>
      <LanguageProbe />
    </LanguageProvider>
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe("LanguageProvider", () => {
  it("defaults to English", () => {
    renderLanguageProbe();

    expect(screen.getByTestId("language")).toHaveTextContent("en");
    expect(screen.getByTestId("new-chat")).toHaveTextContent("New chat");
  });

  it("stores Vietnamese preference and returns Vietnamese copy", () => {
    renderLanguageProbe();
    fireEvent.click(screen.getByRole("button", { name: "vi" }));

    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("vi");
    expect(screen.getByTestId("language")).toHaveTextContent("vi");
    expect(screen.getByTestId("new-chat")).toHaveTextContent("Chat mới");
  });

  it("uses a hydration-safe server snapshot before client preference loads", () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "vi");

    const html = renderToString(
      <LanguageProvider>
        <LanguageProbe />
      </LanguageProvider>
    );

    expect(html).toContain(">en<");
    expect(html).toContain(">New chat<");
  });

  it("uses stored language in client renders", () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "vi");

    renderLanguageProbe();

    expect(screen.getByTestId("language")).toHaveTextContent("vi");
    expect(screen.getByTestId("new-chat")).toHaveTextContent("Chat mới");
  });

  it("hydrates without text mismatch when stored language differs from the server default", async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "vi");
    const html = renderToString(
      <LanguageProvider>
        <LanguageProbe />
      </LanguageProvider>
    );
    const container = document.createElement("div");
    container.innerHTML = html;
    document.body.append(container);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    let root: ReturnType<typeof hydrateRoot> | null = null;
    await act(async () => {
      root = hydrateRoot(
        container,
        <LanguageProvider>
          <LanguageProbe />
        </LanguageProvider>
      );
    });

    expect(consoleError.mock.calls.join("\n")).not.toMatch(/Hydration|did not match|#418/);
    await act(async () => {
      root?.unmount();
    });
    container.remove();
    consoleError.mockRestore();
  });
});

describe("i18n dictionary", () => {
  it("keeps English and Vietnamese dictionaries complete", () => {
    expect(Object.keys(uiCopy.vi).sort()).toEqual(Object.keys(uiCopy.en).sort());
  });

  it("returns localized chat suggestions", () => {
    expect(getChatSuggestions("en")[0]?.label).toBe("Turn into strategy spec");
    expect(getChatSuggestions("vi")[0]?.label).toBe("Chuyển thành strategy spec");
    expect(getChatSuggestions("vi").every((suggestion) => suggestion.prompt.length > 0)).toBe(true);
    expect(getUiCopy("vi").askPlaceholder).toContain("Pine v6");
  });

  it("keeps frontend user-facing copy routed through the dictionary", () => {
    const targetFiles = [
      "src/components/strategy/workspace.tsx",
      "src/lib/account-ui.ts",
      "src/lib/artifact-workspace.ts",
      "src/lib/chat-activity.ts",
      "src/lib/chat-ui.ts",
    ];

    for (const file of targetFiles) {
      const source = readFileSync(join(process.cwd(), file), "utf8");
      expect(source, file).not.toContain('language === "vi"');
    }
  });

  it("keeps artifact preview controls localized through copy keys", () => {
    const source = readFileSync(
      join(process.cwd(), "src/components/strategy/workspace.tsx"),
      "utf8"
    );

    expect(source).not.toContain("Preparing preview...");
    expect(source).not.toContain("Download started");
    expect(source).not.toContain("Download raw");
    expect(source).not.toContain("Could not copy artifact content.");
    expect(getUiCopy("vi").artifactPreviewLoading).toBe("Đang chuẩn bị preview...");
    expect(getUiCopy("vi").downloadRaw).toBe("Tải raw");
    expect(getUiCopy("vi").truncated).toBe("đã cắt bớt");
  });
});
