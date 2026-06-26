import { afterEach, describe, expect, it, vi } from "vitest";

import { agentLog, formatLogfmt } from "./agent-log";

describe("agent-log", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("formats logfmt with escaped values and redaction", () => {
    expect(
      formatLogfmt({
        event: "copilot.run.requested",
        trace_id: "trace_1",
        label: 'hello "world"\nnext',
        content: "do not log this prompt",
        api_key: "secret",
      })
    ).toBe(
      'event=copilot.run.requested trace_id=trace_1 label="hello \\"world\\" next" content="[redacted len=22]" api_key=[redacted]'
    );
  });

  it("emits a single stdout line", () => {
    const infoSpy = vi.spyOn(console, "info").mockImplementation(() => undefined);

    agentLog("info", "copilot.run.finished", {
      component: "copilotkit",
      trace_id: "trace_1",
      request_id: "req_1",
    });

    expect(infoSpy).toHaveBeenCalledWith(
      expect.stringContaining("svc=web component=copilotkit event=copilot.run.finished")
    );
    expect(infoSpy.mock.calls[0]?.[0]).not.toContain("\n");
  });

  it("does not let caller fields override reserved log fields", () => {
    const infoSpy = vi.spyOn(console, "info").mockImplementation(() => undefined);

    agentLog("info", "copilot.debug", {
      component: "copilotkit",
      event: { type: "CUSTOM" },
      lvl: "error",
      svc: "other",
      trace_id: "trace_1",
    });

    const line = String(infoSpy.mock.calls[0]?.[0] ?? "");
    expect(line).toContain("svc=web");
    expect(line).toContain("component=copilotkit");
    expect(line).toContain("event=copilot.debug");
    expect(line).toContain("lvl=info");
    expect(line).not.toContain("keys:type");
  });
});
