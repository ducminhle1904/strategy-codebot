import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageMarkdown, MessageResponse } from "./message";

describe("MessageResponse", () => {
  it("renders rich markdown for assistant responses", () => {
    render(
      <MessageResponse>
        {[
          "## Strategy options",
          "",
          "- Pine v6 review script",
          "- MQL5 expert advisor draft",
          "",
          "1. Clarify entry rules",
          "2. Generate review artifact",
          "",
          "| Platform | Best for |",
          "| --- | --- |",
          "| Pine v6 | TradingView review scripts |",
          "",
          "```pine",
          "plot(close)",
          "```",
        ].join("\n")}
      </MessageResponse>
    );

    expect(screen.getByRole("heading", { name: "Strategy options" })).toBeVisible();
    expect(screen.getAllByRole("list")).toHaveLength(2);
    expect(screen.getByText("Pine v6 review script")).toBeVisible();
    expect(screen.getByText("Clarify entry rules")).toBeVisible();

    const table = screen.getByRole("table");
    expect(within(table).getByRole("columnheader", { name: "Platform" })).toBeVisible();
    expect(within(table).getByText("TradingView review scripts")).toBeVisible();
    expect(screen.getByText("plot(close)")).toBeVisible();
  });

  it("includes typography rules for lists, tables, and code blocks", () => {
    const { container } = render(<MessageResponse>Plain response</MessageResponse>);
    const response = container.firstElementChild;

    expect(response).toHaveClass("[&_ul]:list-disc");
    expect(response).toHaveClass("[&_table]:w-full");
    expect(response).toHaveClass("[&_table]:table-fixed");
    expect(response).toHaveClass("[&_table]:border-collapse");
    expect(response).toHaveClass("[&_table:first-child]:mt-0");
    expect(response).toHaveClass("[&_table:last-child]:mb-0");
    expect(response).not.toHaveClass("[&_table]:border");
    expect(response).toHaveClass("[&_pre]:overflow-x-auto");
  });

  it("can render without default table chrome for artifact previews", () => {
    const { container } = render(
      <MessageResponse tableStyle="plain">Plain response</MessageResponse>
    );
    const response = container.firstElementChild;

    expect(response).not.toHaveClass("[&_table]:border");
    expect(response).not.toHaveClass("[&_table]:rounded-[4px]");
    expect(response).toHaveClass("[&_pre]:overflow-x-auto");
  });

  it("renders MessageMarkdown through the same response renderer", () => {
    render(<MessageMarkdown content="## Streaming title" />);

    expect(screen.getByRole("heading", { name: "Streaming title" })).toBeVisible();
  });
});
