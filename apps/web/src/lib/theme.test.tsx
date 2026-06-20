import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { THEME_STORAGE_KEY, ThemeProvider, useTheme } from "./theme";

function ThemeProbe() {
  const { resolvedTheme, setTheme, theme } = useTheme();
  return (
    <div>
      <span data-testid="theme">{theme}</span>
      <span data-testid="resolved-theme">{resolvedTheme}</span>
      <button onClick={() => setTheme("system")} type="button">
        system
      </button>
      <button onClick={() => setTheme("light")} type="button">
        light
      </button>
      <button onClick={() => setTheme("dark")} type="button">
        dark
      </button>
    </div>
  );
}

function renderThemeProbe() {
  return render(
    <ThemeProvider>
      <ThemeProbe />
    </ThemeProvider>
  );
}

function mockMatchMedia(initialMatches: boolean) {
  let listener: ((event: MediaQueryListEvent) => void) | null = null;
  const mediaQueryList = {
    addEventListener: vi.fn((_event: string, callback: (event: MediaQueryListEvent) => void) => {
      listener = callback;
    }),
    dispatch(nextMatches: boolean) {
      mediaQueryList.matches = nextMatches;
      listener?.({ matches: nextMatches } as MediaQueryListEvent);
    },
    matches: initialMatches,
    media: "(prefers-color-scheme: dark)",
    removeEventListener: vi.fn(),
  };

  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => mediaQueryList),
  });

  return mediaQueryList;
}

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.className = "";
  document.documentElement.style.colorScheme = "";
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ThemeProvider", () => {
  it("defaults to system and resolves from prefers-color-scheme", async () => {
    mockMatchMedia(true);

    renderThemeProbe();

    expect(screen.getByTestId("theme")).toHaveTextContent("system");
    await waitFor(() => expect(screen.getByTestId("resolved-theme")).toHaveTextContent("dark"));
    expect(document.documentElement).toHaveClass("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  it("uses hydration-safe server snapshots before client preference loads", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    mockMatchMedia(false);

    const html = renderToString(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    );

    expect(html).toContain(">system<");
    expect(html).toContain(">light<");
  });

  it("uses stored theme in client renders", async () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    mockMatchMedia(false);

    renderThemeProbe();

    expect(screen.getByTestId("theme")).toHaveTextContent("dark");
    expect(screen.getByTestId("resolved-theme")).toHaveTextContent("dark");
    expect(document.documentElement).toHaveClass("dark");
  });

  it("hydrates without text mismatch when stored theme differs from the server default", async () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    mockMatchMedia(false);
    const html = renderToString(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    );
    const container = document.createElement("div");
    container.innerHTML = html;
    document.body.append(container);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    let root: ReturnType<typeof hydrateRoot> | null = null;
    await act(async () => {
      root = hydrateRoot(
        container,
        <ThemeProvider>
          <ThemeProbe />
        </ThemeProvider>
      );
    });

    expect(consoleError.mock.calls.join("\n")).not.toMatch(/Hydration|did not match|#418/);
    await act(async () => {
      root?.unmount();
    });
    container.remove();
    consoleError.mockRestore();
  });

  it("stores dark preference and applies the dark class", async () => {
    mockMatchMedia(false);

    renderThemeProbe();
    fireEvent.click(screen.getByRole("button", { name: "dark" }));

    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    await waitFor(() => expect(screen.getByTestId("resolved-theme")).toHaveTextContent("dark"));
    expect(document.documentElement).toHaveClass("dark");
  });

  it("stores light preference and removes the dark class", async () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    mockMatchMedia(true);

    renderThemeProbe();
    await waitFor(() => expect(document.documentElement).toHaveClass("dark"));
    fireEvent.click(screen.getByRole("button", { name: "light" }));

    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    await waitFor(() => expect(screen.getByTestId("resolved-theme")).toHaveTextContent("light"));
    expect(document.documentElement).not.toHaveClass("dark");
    expect(document.documentElement.style.colorScheme).toBe("light");
  });

  it("follows system changes when preference is system", async () => {
    const mediaQueryList = mockMatchMedia(false);

    renderThemeProbe();
    await waitFor(() => expect(screen.getByTestId("resolved-theme")).toHaveTextContent("light"));

    act(() => {
      mediaQueryList.dispatch(true);
    });

    await waitFor(() => expect(screen.getByTestId("resolved-theme")).toHaveTextContent("dark"));
    expect(document.documentElement).toHaveClass("dark");
  });
});
