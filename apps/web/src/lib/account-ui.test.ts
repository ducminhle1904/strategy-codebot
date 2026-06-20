import { describe, expect, it } from "vitest";

import type { MeResponse, ProviderStatusResponse } from "./backend-schemas";
import {
  accountInitial,
  accountName,
  formatUsageCost,
  providerDisplay,
} from "./account-ui";

const me: MeResponse = {
  capability: {
    allowed_message_modes: ["deterministic", "agent"],
    allowed_run_modes: ["dry-run", "agent", "live-generation"],
    role: "owner",
    tier: "free",
    tier_label: "Free",
    user_id: "user_1",
    workspace_id: "workspace_1",
  },
  user: { id: "user_1" },
  workspace: { id: "workspace_1", role: "owner" },
};

function provider(overrides: Partial<ProviderStatusResponse> = {}): ProviderStatusResponse {
  return {
    allowed_message_modes: ["deterministic", "agent"],
    allowed_run_modes: ["dry-run", "agent", "live-generation"],
    available: true,
    configured: true,
    fallback_mode: "deterministic",
    reason: null,
    status: "ready",
    tier: "free",
    tier_label: "Free",
    ...overrides,
  };
}

describe("account UI helpers", () => {
  it("prefers Clerk identity before backend fallback", () => {
    expect(accountName(me, "Duc Le", "duc@example.com")).toBe("Duc Le");
    expect(accountName(me, null, "duc@example.com")).toBe("duc@example.com");
    expect(accountName(me, null, null)).toBe("user_1");
    expect(accountInitial("duc@example.com")).toBe("D");
  });

  it("maps provider status to user-facing copy", () => {
    expect(providerDisplay(provider(), me)).toMatchObject({
      status: "ready",
      title: "Ready",
    });
    expect(providerDisplay(provider({ available: false }), me)).toMatchObject({
      status: "limited",
      title: "Limited",
    });
    expect(providerDisplay(provider({ configured: false }), me)).toMatchObject({
      status: "needs-setup",
      title: "Needs setup",
    });
  });

  it("formats missing usage cost without implying billing precision", () => {
    expect(formatUsageCost()).toBe("Not estimated");
  });
});
