import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ArtifactPreviewFixture,
  ArtifactPreviewSchema,
  RunStatusFixture,
  buildChatRequest,
  parseSseMessages,
} from "./chat-fixtures";

describe("chat test schemas and client helpers", () => {
  it("builds a streaming chat request from a valid prompt", () => {
    expect(
      buildChatRequest({
        prompt: "Create a moving-average crossover strategy",
        target: "pine",
      })
    ).toEqual({
      endpoint: "/api/copilotkit-chat",
      body: {
        prompt: "Create a moving-average crossover strategy",
        target: "pine",
        stream: true,
      },
    });
  });

  it("rejects empty artifact identifiers", () => {
    expect(() =>
      ArtifactPreviewSchema.parse({
        id: "",
        title: "Draft strategy",
        kind: "pine",
        content: "plot(close)",
      })
    ).toThrow();
  });
});

describe("SSE parser fixture", () => {
  it("parses named events with ids and multi-line data", () => {
    expect(
      parseSseMessages(
        [
          ": keep-alive",
          "id: run-1",
          "event: artifact",
          "data: {\"title\":\"Draft\"}",
          "data: {\"kind\":\"pine\"}",
          "",
          "event: status",
          "data: completed",
          "",
        ].join("\n")
      )
    ).toEqual([
      {
        id: "run-1",
        event: "artifact",
        data: "{\"title\":\"Draft\"}\n{\"kind\":\"pine\"}",
      },
      {
        event: "status",
        data: "completed",
      },
    ]);
  });
});

describe("chat UI fixtures", () => {
  it("renders the current run status", () => {
    render(<RunStatusFixture status="running" />);

    expect(screen.getByRole("status", { name: "Run status" })).toHaveTextContent(
      "Running"
    );
  });

  it("renders an artifact preview", () => {
    render(
      <ArtifactPreviewFixture
        artifact={{
          id: "artifact-1",
          title: "Pine draft",
          kind: "pine",
          content: "plot(close)",
        }}
      />
    );

    expect(
      screen.getByRole("article", { name: "Artifact preview" })
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Pine draft" })).toBeVisible();
    expect(screen.getByText("PINE")).toBeVisible();
    expect(screen.getByText("plot(close)")).toBeVisible();
  });
});
