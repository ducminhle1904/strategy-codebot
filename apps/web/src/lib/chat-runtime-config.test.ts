import { afterEach, describe, expect, it, vi } from "vitest";

import {
  configuredStrategyChatRuntime,
  isAgUiDebugEnabled,
  isCopilotKitRuntimeEnabled,
} from "./chat-runtime-config";

describe("chat runtime config", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("uses CopilotKit as the only chat runtime", () => {
    expect(configuredStrategyChatRuntime()).toBe("copilotkit");
    expect(isCopilotKitRuntimeEnabled()).toBe(true);
  });

  it("enables AG-UI debug only with the debug flag", () => {
    vi.stubEnv("NEXT_PUBLIC_DEBUG_AG_UI", "false");

    expect(isAgUiDebugEnabled()).toBe(false);

    vi.stubEnv("NEXT_PUBLIC_DEBUG_AG_UI", "true");

    expect(isAgUiDebugEnabled()).toBe(true);
  });
});
