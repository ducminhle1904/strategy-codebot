import { describe, expect, it } from "vitest";

import { parseSseMessages, splitCompleteSseFrames } from "./sse";

describe("SSE parser", () => {
  it("parses CRLF frames and multiline data", () => {
    const messages = parseSseMessages(
      'id: evt_1\r\nevent: message.delta\r\ndata: {"text":"hello"\r\ndata: ,"extra":true}\r\n\r\n'
    );

    expect(messages).toEqual([
      {
        data: { extra: true, text: "hello" },
        event: "message.delta",
        id: "evt_1",
      },
    ]);
  });

  it("skips empty frames and preserves malformed JSON as raw data", () => {
    expect(parseSseMessages("\n\nevent: run.failed\ndata: not-json\n\n")).toEqual([
      {
        data: { raw: "not-json" },
        event: "run.failed",
        id: undefined,
      },
    ]);
  });

  it("splits only complete CRLF-delimited frames for streaming consumers", () => {
    expect(
      splitCompleteSseFrames(
        'event: progress.snapshot\r\ndata: {"type":"progress.snapshot"}\r\n\r\nevent: progress.update\r\ndata:'
      )
    ).toEqual({
      frames: ['event: progress.snapshot\ndata: {"type":"progress.snapshot"}'],
      remaining: "event: progress.update\ndata:",
    });
  });
});
