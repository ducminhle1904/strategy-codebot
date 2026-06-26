import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

type Candle = {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

type BacktestConfig = {
  symbol: string;
  timeframe: string;
  candle_timeframe?: string;
  initial_capital: number;
  fee_bps: number;
  slippage_bps: number;
};

type PineForgeCommandInput = {
  job_id: string;
  config: BacktestConfig;
  pine_code_path: string;
  candles_path: string;
  output_dir: string;
};

type PineForgeCommandResult =
  | {
      status: "pass";
      report: Record<string, unknown>;
      trades?: Record<string, unknown>[];
      equity_curve?: Record<string, unknown>[];
      compile?: Record<string, unknown>;
    }
  | { status: "fail"; error: { code: string; message: string; diagnostics?: Record<string, unknown> } };

type PineForgeBacktestArguments = {
  source: string;
  ohlcv_csv_path: string;
  overrides: Record<string, unknown>;
  runtime: Record<string, unknown>;
};

const DEFAULT_MCP_IMAGE = "ghcr.io/pineforge-4pass/pineforge-codegen-mcp:latest";

async function main() {
  try {
    const input = parseInput(await readStdin());
    const result = await runPineForgeMcp(input);
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    const result: PineForgeCommandResult = {
      status: "fail",
      error: {
        code: "pineforge_mcp_adapter_failed",
        message: error instanceof Error ? error.message : String(error),
      },
    };
    process.stdout.write(`${JSON.stringify(result)}\n`);
    process.exitCode = 1;
  }
}

async function runPineForgeMcp(input: PineForgeCommandInput): Promise<PineForgeCommandResult> {
  const pineCode = await readFile(input.pine_code_path, "utf8");
  const candles = await readCandles(input.candles_path);
  const csvPath = join(input.output_dir, "ohlcv.csv");
  await writeFile(csvPath, candlesToCsv(candles), "utf8");

  const workDir = input.output_dir;
  const client = new Client({ name: "strategy-codebot-pineforge-adapter", version: "0.1.0" });
  const transport = new StdioClientTransport({
    command: process.env.BACKTEST_PINEFORGE_MCP_COMMAND?.trim() || "docker",
    args: pineForgeMcpArgs(workDir),
    env: pineForgeMcpEnv(workDir),
    stderr: "pipe",
    cwd: workDir,
  });

  try {
    await client.connect(transport);
    const response = await client.callTool(
      {
        name: "backtest_pine",
        arguments: pineForgeBacktestArguments(input.config, pineCode, "/work/ohlcv.csv"),
      },
      undefined,
      { timeout: pineForgeMcpRequestTimeoutMs() },
    );
    if (isRecord(response) && response.isError === true) {
      return {
        status: "fail",
        error: {
          code: "pineforge_mcp_tool_error",
          message: mcpTextContent(response) || "PineForge MCP backtest_pine returned an error",
        },
      };
    }
    const payload = await hydratePineForgePayload(extractBacktestPayload(response), input.output_dir);
    return {
      status: "pass",
      report: normalizePineForgeReport(payload),
      trades: normalizeTrades(payload, input.config),
      equity_curve: normalizeEquityCurve(payload),
      compile: pineForgeCompileSummary(payload),
    };
  } catch (error) {
    return {
      status: "fail",
      error: {
        code: "pineforge_mcp_failed",
        message: error instanceof Error ? error.message : String(error),
      },
    };
  } finally {
    await client.close().catch(() => undefined);
  }
}

function pineForgeBacktestArguments(config: BacktestConfig, source: string, ohlcvCsvPath: string): PineForgeBacktestArguments {
  return {
    source,
    ohlcv_csv_path: ohlcvCsvPath,
    overrides: {
      initial_capital: config.initial_capital,
      commission_type: "percent",
      commission_value: bpsToPercent(config.fee_bps),
      slippage: 0,
      pyramiding: 0,
      process_orders_on_close: true,
      close_entries_rule: "ANY",
    },
    runtime: {
      input_tf: pineForgeTimeframe(config.candle_timeframe ?? "1m"),
      script_tf: pineForgeTimeframe(config.timeframe),
      bar_magnifier: true,
      magnifier_samples: 8,
      magnifier_dist: "endpoints",
    },
  };
}

function pineForgeMcpArgs(workDir: string): string[] {
  const configured = process.env.BACKTEST_PINEFORGE_MCP_ARGS?.trim();
  if (configured) {
    return configured
      .split(/\s+/)
      .map((arg) => arg.replaceAll("{workdir}", workDir).replaceAll("{image}", pineForgeMcpImage()));
  }
  return [
    "run",
    "--rm",
    "-i",
    "-v",
    `${workDir}:/work`,
    "-e",
    `PINEFORGE_HOST_WORKDIR=${workDir}`,
    "-e",
    `PINEFORGE_DOCKER_TIMEOUT_MS=${pineForgeMcpRequestTimeoutMs()}`,
    pineForgeMcpImage(),
  ];
}

function pineForgeMcpEnv(workDir: string): Record<string, string> {
  return {
    ...process.env,
    PINEFORGE_ALLOW_ANYWHERE: process.env.PINEFORGE_ALLOW_ANYWHERE ?? "0",
    PINEFORGE_HOST_WORKDIR: process.env.PINEFORGE_HOST_WORKDIR ?? workDir,
    PINEFORGE_DOCKER_TIMEOUT_MS: process.env.PINEFORGE_DOCKER_TIMEOUT_MS ?? String(pineForgeMcpRequestTimeoutMs()),
  } as Record<string, string>;
}

function pineForgeMcpImage(): string {
  return process.env.BACKTEST_PINEFORGE_MCP_IMAGE?.trim() || DEFAULT_MCP_IMAGE;
}

function pineForgeMcpRequestTimeoutMs(): number {
  const parsed = Number(process.env.BACKTEST_PINEFORGE_MCP_TIMEOUT_MS ?? process.env.BACKTEST_WORKER_TIMEOUT_MS ?? "120000");
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 120_000;
}

async function readCandles(path: string): Promise<Candle[]> {
  const parsed = JSON.parse(await readFile(path, "utf8")) as unknown;
  if (!Array.isArray(parsed)) {
    throw new Error("PineForge adapter expected candles_path to contain a candle array");
  }
  return parsed.filter(isCandle).sort((a, b) => a.timestamp - b.timestamp);
}

function candlesToCsv(candles: Candle[]): string {
  const rows = ["timestamp,open,high,low,close,volume"];
  for (const candle of candles) {
    rows.push([
      candle.timestamp,
      candle.open,
      candle.high,
      candle.low,
      candle.close,
      candle.volume,
    ].join(","));
  }
  return `${rows.join("\n")}\n`;
}

function extractBacktestPayload(response: unknown): Record<string, unknown> {
  if (isRecord(response) && isRecord(response.structuredContent)) {
    return response.structuredContent;
  }
  if (isRecord(response) && Array.isArray(response.content)) {
    for (const part of response.content) {
      if (!isRecord(part) || part.type !== "text" || typeof part.text !== "string") {
        continue;
      }
      const parsed = parseJsonObject(part.text);
      if (parsed) {
        return parsed;
      }
    }
  }
  if (isRecord(response)) {
    return response;
  }
  throw new Error("PineForge MCP returned an unsupported response shape");
}

async function hydratePineForgePayload(payload: Record<string, unknown>, outputDir: string): Promise<Record<string, unknown>> {
  if (payload.truncated !== true || typeof payload.report_path !== "string") {
    return payload;
  }
  const reportPath = payload.report_path.startsWith("/") ? payload.report_path : join(outputDir, payload.report_path);
  try {
    const parsed = parseJsonObject(await readFile(reportPath, "utf8"));
    return parsed ? { ...parsed, _inline_summary: payload } : payload;
  } catch {
    return payload;
  }
}

function mcpTextContent(response: Record<string, unknown>): string | null {
  if (!Array.isArray(response.content)) {
    return null;
  }
  const lines = response.content
    .map((part) => isRecord(part) && part.type === "text" && typeof part.text === "string" ? part.text : "")
    .filter(Boolean);
  return lines.length ? lines.join("\n") : null;
}

function normalizePineForgeReport(payload: Record<string, unknown>): Record<string, unknown> {
  const summary = isRecord(payload.summary) ? payload.summary : {};
  return {
    ...payload,
    metrics: {
      trade_count: numeric(summary.total_trades) ?? numeric(payload.total_trades),
      net_profit: numeric(summary.net_pnl) ?? numeric(payload.net_pnl),
      max_drawdown: numeric(summary.max_drawdown) ?? numeric(payload.max_drawdown),
      win_rate: numeric(summary.win_rate) ?? numeric(payload.win_rate),
    },
  };
}

function normalizeEquityCurve(payload: Record<string, unknown>): Record<string, unknown>[] {
  for (const key of ["equity_curve", "equity", "series"]) {
    const value = payload[key];
    if (Array.isArray(value)) {
      let peak = 0;
      return value.filter(isRecord).map((point, index) => {
        const equity = numeric(point.equity) ?? numeric(point.value) ?? 0;
        peak = Math.max(peak, equity);
        return {
          index,
          timestamp: timestampToIso(point.timestamp ?? point.time ?? point.time_ms),
          equity,
          pnl_cost: numeric(point.pnl_cost) ?? numeric(point.net_pnl) ?? 0,
          drawdown_pct: peak <= 0 ? 0 : Number((((peak - equity) / peak) * 100).toFixed(6)),
        };
      });
    }
  }
  return [];
}

function normalizeTrades(payload: Record<string, unknown>, config: BacktestConfig): Record<string, unknown>[] {
  if (!Array.isArray(payload.trades)) {
    return [];
  }
  return payload.trades.filter(isRecord).map((trade, index) => {
    const rawPnlCost = numeric(trade.pnl_cost)
      ?? numeric(trade.profit)
      ?? numeric(trade.net_profit)
      ?? numeric(trade.net_pnl);
    const rawPnlPercentage = numeric(trade.pnl_percentage)
      ?? numeric(trade.profit_percent)
      ?? numeric(trade.net_profit_percent);
    return {
      id: typeof trade.id === "string" ? trade.id : `pineforge-${index + 1}`,
      symbol: config.symbol,
      side: typeof trade.side === "string" ? trade.side : null,
      close_reason: typeof trade.close_reason === "string" ? trade.close_reason : null,
      opened_at: timestampToIso(trade.opened_at ?? trade.entry_time ?? trade.entry_time_ms),
      closed_at: timestampToIso(trade.closed_at ?? trade.exit_time ?? trade.exit_time_ms),
      entry_price: numeric(trade.entry_price) ?? numeric(trade.entry),
      exit_price: numeric(trade.exit_price) ?? numeric(trade.exit),
      raw_pnl_percentage: rawPnlPercentage,
      raw_pnl_cost: rawPnlCost,
      pnl_percentage: rawPnlPercentage,
      pnl_cost: rawPnlCost,
      cost: numeric(trade.cost) ?? numeric(trade.qty) ?? null,
      fee_cost: numeric(trade.fee_cost) ?? 0,
      slippage_cost: numeric(trade.slippage_cost) ?? 0,
      cost_model: {
        version: "pineforge-overrides-v1",
        fee_bps: config.fee_bps,
        slippage_bps: config.slippage_bps,
        applied_to_metrics: true,
        basis: "pineforge_strategy_overrides",
      },
    };
  });
}

function pineForgeCompileSummary(payload: Record<string, unknown>): Record<string, unknown> {
  const meta = isRecord(payload._meta) ? payload._meta : {};
  return {
    status: "pass",
    engine: payload.engine ?? "pineforge",
    elapsed_seconds: payload.elapsed_seconds ?? null,
    meta,
  };
}

function pineForgeTimeframe(value: string): string {
  const normalized = value.trim().toLowerCase();
  const match = /^(\d+)([mhd])$/.exec(normalized);
  if (!match) {
    throw new Error(`Unsupported PineForge timeframe: ${value}`);
  }
  const amount = Number(match[1]);
  const unit = match[2];
  if (unit === "m") {
    return String(amount);
  }
  if (unit === "h") {
    return String(amount * 60);
  }
  return `${amount}D`;
}

function bpsToPercent(value: number): number {
  return Number((value / 100).toFixed(8));
}

function numeric(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function timestampToIso(value: unknown): string | null {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return new Date(value).toISOString();
  }
  return null;
}

function parseJsonObject(value: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(value) as unknown;
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function parseInput(raw: string): PineForgeCommandInput {
  const parsed = JSON.parse(raw) as unknown;
  if (!isRecord(parsed) || !isRecord(parsed.config)) {
    throw new Error("Invalid PineForge adapter input");
  }
  for (const key of ["job_id", "pine_code_path", "candles_path", "output_dir"] as const) {
    if (typeof parsed[key] !== "string" || !parsed[key]) {
      throw new Error(`Invalid PineForge adapter input: missing ${key}`);
    }
  }
  return parsed as PineForgeCommandInput;
}

function isCandle(value: unknown): value is Candle {
  if (!isRecord(value)) {
    return false;
  }
  return ["timestamp", "open", "high", "low", "close", "volume"].every((key) =>
    typeof value[key] === "number" && Number.isFinite(value[key]),
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readStdin(): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk: string) => {
      data += chunk;
    });
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

export const __test = {
  bpsToPercent,
  candlesToCsv,
  extractBacktestPayload,
  pineForgeBacktestArguments,
  pineForgeTimeframe,
};

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error: unknown) => {
    process.stderr.write(`${error instanceof Error ? error.stack ?? error.message : String(error)}\n`);
    process.exit(1);
  });
}
