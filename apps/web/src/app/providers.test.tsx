import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { COPILOTKIT_CHAT_RUNTIME_URL } from "@/lib/copilot-constants";

import { AppProviders } from "./providers";

type CopilotKitMockProps = {
  children: ReactNode;
  credentials?: string;
  onError?: (event: { code?: string; context: Record<string, unknown>; error: Error }) => void;
  runtimeUrl?: string;
  showDevConsole?: boolean;
  useSingleEndpoint?: boolean;
};

const copilotKitMock = vi.fn(({ children }: CopilotKitMockProps) => (
  <div data-testid="copilot-provider">{children}</div>
));

vi.mock("@copilotkit/react-core/v2", () => ({
  CopilotKit: (props: CopilotKitMockProps) => copilotKitMock(props),
}));

describe("AppProviders", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.clearAllMocks();
    vi.unstubAllEnvs();
  });

  it("mounts CopilotKit by default", () => {
    render(
      <AppProviders>
        <div>workspace</div>
      </AppProviders>
    );

    expect(screen.getByTestId("copilot-provider")).toBeInTheDocument();
    expect(copilotKitMock).toHaveBeenCalledTimes(1);
    expect(copilotKitMock.mock.calls[0]?.[0]).toMatchObject({
      credentials: "include",
      runtimeUrl: COPILOTKIT_CHAT_RUNTIME_URL,
      useSingleEndpoint: true,
    });
  });

  it("logs expected provider warmup delays as warnings", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    render(
      <AppProviders>
        <div>workspace</div>
      </AppProviders>
    );

    const props = copilotKitMock.mock.calls[0]?.[0];
    props.onError?.({
      code: undefined,
      context: {},
      error: new Error("The AI provider is taking longer than usual to start. Try again after the model warms up."),
    });

    expect(warnSpy).toHaveBeenCalledWith(
      "[strategy-copilotkit]",
      undefined,
      "The AI provider is taking longer than usual to start. Try again after the model warms up.",
      {}
    );
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
