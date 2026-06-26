export type ChatInlineTableColumn = {
  key: string;
  label: string;
  align?: "left" | "right";
  tone?: "default" | "profit_loss" | "side";
};

export type ChatInlineTable = {
  kind: "backtest_trades";
  title: string;
  caption?: string;
  columns: ChatInlineTableColumn[];
  rows: Record<string, unknown>[];
  source_tool_id: string;
  run_id?: string;
  row_count?: number;
  truncated?: boolean;
};

export function backtestTradesTableFromToolOutput(output: unknown): ChatInlineTable | null {
  if (!output || typeof output !== "object") {
    return null;
  }
  const record = output as Record<string, unknown>;
  if (record.status !== undefined && record.status !== "ok") {
    return null;
  }
  const sourceRows = Array.isArray(record.trades) ? record.trades : [];
  const rows = sourceRows
    .map((row) => normalizeBacktestTradeRow(row))
    .filter((row): row is Record<string, unknown> => row !== null);
  if (rows.length === 0) {
    return null;
  }
  const runId = stringValue(record.run_id) ?? undefined;
  return {
    kind: "backtest_trades",
    title: "Backtest trades",
    columns: backtestTradesColumns(),
    rows,
    source_tool_id: "query_backtest_trades",
    run_id: runId,
    row_count: rows.length,
    truncated: Boolean(record.truncated),
  };
}

export function normalizeBacktestTradeRow(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const row = value as Record<string, unknown>;
  const trade = row.trade && typeof row.trade === "object" ? (row.trade as Record<string, unknown>) : row;
  return {
    trade_rank: row.trade_rank ?? trade.trade_rank ?? trade.number ?? null,
    bucket: stringValue(row.bucket) ?? null,
    side: stringValue(trade.side) ?? stringValue(trade.direction) ?? null,
    pnl_cost: numberValue(row.pnl_cost) ?? numberValue(trade.pnl_cost),
    pnl_percentage: numberValue(row.pnl_percentage) ?? numberValue(trade.pnl_percentage),
    opened_at:
      stringValue(row.opened_at) ??
      stringValue(trade.opened_at) ??
      stringValue(trade.entry_time) ??
      stringValue(trade.entry_timestamp) ??
      null,
    closed_at:
      stringValue(row.closed_at) ??
      stringValue(trade.closed_at) ??
      stringValue(trade.exit_time) ??
      stringValue(trade.exit_timestamp) ??
      null,
  };
}

export function backtestTradesColumns(): ChatInlineTableColumn[] {
  return [
    { key: "trade_rank", label: "#", align: "right" },
    { key: "side", label: "Side", tone: "side" },
    { key: "bucket", label: "Bucket" },
    { key: "pnl_cost", label: "P&L", align: "right", tone: "profit_loss" },
    { key: "pnl_percentage", label: "P&L %", align: "right", tone: "profit_loss" },
    { key: "opened_at", label: "Entry" },
    { key: "closed_at", label: "Exit" },
  ];
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function numberValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}
