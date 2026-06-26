import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { access, readFile, stat, writeFile } from "node:fs/promises";
import { constants } from "node:fs";
import { join } from "node:path";

type BacktestRequest = {
  job_id: string;
  run_id: string;
  config: {
    symbol: string;
    timeframe: string;
    candle_timeframe?: string;
    initial_capital: number;
    fee_bps: number;
    slippage_bps: number;
  };
  market_metadata?: Record<string, unknown> | null;
  pine_code_path: string;
  ohlcv_csv_path: string;
  output_dir: string;
  limits?: {
    timeout_ms?: number;
    max_bars?: number;
    max_output_bytes?: number;
    equity_downsample_points?: number;
  };
};

type RunnerResult =
  | {
      status: "pass";
      runner: "pineforge-runner";
      runner_version: string;
      report: Record<string, unknown>;
      trades: Record<string, unknown>[];
      equity_curve: Record<string, unknown>[];
      compile: Record<string, unknown>;
      artifact_manifest: Record<string, unknown>;
      stats: Record<string, number>;
    }
  | { status: "fail"; error: { code: string; message: string; diagnostics?: Record<string, unknown> } };

const PORT = Number(process.env.PINEFORGE_RUNNER_PORT ?? 8080);
const MODE = process.env.PINEFORGE_RUNNER_MODE ?? "native";
const VERSION = process.env.PINEFORGE_RUNNER_VERSION ?? "pineforge-runner-native-contract-v1";
const NATIVE_COMMAND = process.env.PINEFORGE_NATIVE_COMMAND?.trim() || "/app/bin/pineforge-native-runner.py";
const NATIVE_ARGS = (process.env.PINEFORGE_NATIVE_ARGS ?? "").split(/\s+/).map((part) => part.trim()).filter(Boolean);

const server = createServer(async (request, response) => {
  try {
    if (request.method === "GET" && request.url === "/health") {
      sendJson(response, 200, { status: "ok", service: "pineforge-runner", version: VERSION });
      return;
    }
    if (request.method === "GET" && request.url === "/ready") {
      const nativeReady = MODE === "fixture" || await isNativeReady();
      sendJson(response, nativeReady ? 200 : 503, {
        status: nativeReady ? "ok" : "unavailable",
        service: "pineforge-runner",
        version: VERSION,
        engine_version: process.env.PINEFORGE_ENGINE_VERSION ?? null,
        codegen_version: process.env.PINEFORGE_CODEGEN_VERSION ?? null,
        native_engine: MODE === "native",
        native_ready: nativeReady,
        license_state: process.env.PINEFORGE_LICENSE_STATE ?? "not_configured",
        mode: MODE,
      });
      return;
    }
    if (request.method === "POST" && request.url === "/v1/pineforge/backtests") {
      const payload = parseRequest(await readBody(request));
      const result = MODE === "fixture" ? await runFixture(payload) : await runNative(payload);
      sendJson(response, result.status === "pass" ? 200 : 422, result);
      return;
    }
    sendJson(response, 404, { status: "fail", error: { code: "not_found", message: "Not found" } });
  } catch (error) {
    sendJson(response, 500, {
      status: "fail",
      error: {
        code: "pineforge_runner_error",
        message: error instanceof Error ? error.message : String(error),
      },
    });
  }
});

server.listen(PORT, () => {
  process.stdout.write(JSON.stringify({ level: "info", service: "pineforge-runner", port: PORT, mode: MODE }) + "\n");
});

async function runNative(input: BacktestRequest): Promise<RunnerResult> {
  validateInput(input);
  if (!await isNativeReady()) {
    return {
      status: "fail",
      error: {
        code: "pineforge_native_unavailable",
        message: `PineForge native command is unavailable: ${NATIVE_COMMAND}.`,
      },
    };
  }
  const started = Date.now();
  const child = spawn(NATIVE_COMMAND, NATIVE_ARGS, { stdio: ["pipe", "pipe", "pipe"] });
  const timeoutMs = input.limits?.timeout_ms ?? 120_000;
  let stdout = "";
  let stderr = "";
  let settled = false;
  child.stdin.end(JSON.stringify(input));
  return await new Promise((resolve) => {
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      resolve({
        status: "fail",
        error: { code: "pineforge_native_timeout", message: `Native PineForge timeout exceeded: ${timeoutMs}ms` },
      });
    }, timeoutMs);
    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString("utf8");
    });
    child.on("close", () => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      const parsed = parseRunnerResult(stdout);
      if (parsed) {
        resolve(parsed);
        return;
      }
      resolve({
        status: "fail",
        error: {
          code: "pineforge_native_invalid_output",
          message: stderr || stdout || "Native PineForge command returned no structured result",
          diagnostics: { elapsed_ms: Date.now() - started },
        },
      });
    });
  });
}

async function isNativeReady(): Promise<boolean> {
  if (!NATIVE_COMMAND) {
    return false;
  }
  if (!NATIVE_COMMAND.includes("/")) {
    return true;
  }
  try {
    await access(NATIVE_COMMAND, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

async function runFixture(input: BacktestRequest): Promise<RunnerResult> {
  validateInput(input);
  const started = Date.now();
  const [pineCode, csv] = await Promise.all([
    readFile(input.pine_code_path, "utf8"),
    readFile(input.ohlcv_csv_path, "utf8"),
  ]);
  const bars = Math.max(0, csv.trim().split(/\r?\n/).length - 1);
  if (bars > (input.limits?.max_bars ?? 1_578_240)) {
    return { status: "fail", error: { code: "pineforge_max_bars_exceeded", message: `bars ${bars} exceeds max_bars` } };
  }
  const report = {
    engine: "pineforge",
    evidence_label: "PineForge local Pine preview evidence",
    execution_semantics: "model_generated_pine_pineforge",
    input: { bars },
    applied_runtime: {
      input_tf: pineTimeframe(input.config.candle_timeframe ?? "1m"),
      script_tf: pineTimeframe(input.config.timeframe),
      bar_magnifier: true,
    },
    summary: {
      total_trades: 0,
      wins: 0,
      losses: 0,
      win_rate_pct: null,
      net_pnl: 0,
      max_drawdown: 0,
      bars_processed: bars,
    },
    metrics: {
      trade_count: 0,
      net_profit: 0,
      max_drawdown: 0,
      win_rate: null,
    },
    reproducibility_hash: createHash("sha256").update(pineCode).update(csv.slice(0, 1024)).digest("hex"),
  };
  const compile = { status: "pass", native_engine: MODE === "native", fixture: true };
  const trades: Record<string, unknown>[] = [];
  const equityCurve = [{ index: 0, timestamp: null, equity: input.config.initial_capital, pnl_cost: 0, drawdown_pct: 0 }];
  await writeRunnerArtifacts(input, report, trades, equityCurve, compile);
  const outputBytes = await artifactBytes(input.output_dir);
  return {
    status: "pass",
    runner: "pineforge-runner",
    runner_version: VERSION,
    report,
    trades,
    equity_curve: equityCurve,
    compile,
    artifact_manifest: manifestFor(input),
    stats: {
      bars_processed: bars,
      compile_ms: 0,
      run_ms: Date.now() - started,
      output_bytes: outputBytes,
    },
  };
}

async function writeRunnerArtifacts(
  input: BacktestRequest,
  report: Record<string, unknown>,
  trades: Record<string, unknown>[],
  equityCurve: Record<string, unknown>[],
  compile: Record<string, unknown>,
) {
  await Promise.all([
    writeJson(join(input.output_dir, "pineforge-report-full.json"), report),
    writeJson(join(input.output_dir, "trades.json"), trades),
    writeJson(join(input.output_dir, "equity-curve.json"), equityCurve),
    writeJson(join(input.output_dir, "compile.json"), compile),
  ]);
}

function manifestFor(input: BacktestRequest): Record<string, unknown> {
  return {
    report: join(input.output_dir, "pineforge-report-full.json"),
    trades: join(input.output_dir, "trades.json"),
    equity_curve: join(input.output_dir, "equity-curve.json"),
    compile: join(input.output_dir, "compile.json"),
  };
}

function parseRequest(raw: string): BacktestRequest {
  const parsed = JSON.parse(raw) as unknown;
  if (!isRecord(parsed)) {
    throw new Error("Request body must be an object");
  }
  return parsed as BacktestRequest;
}

function validateInput(input: BacktestRequest) {
  for (const key of ["job_id", "run_id", "pine_code_path", "ohlcv_csv_path", "output_dir"] as const) {
    if (typeof input[key] !== "string" || !input[key]) {
      throw new Error(`Missing ${key}`);
    }
  }
  if (!isRecord(input.config)) {
    throw new Error("Missing config");
  }
}

function pineTimeframe(value: string): string {
  const match = /^(\d+)([mh])$/.exec(value.trim().toLowerCase());
  if (!match) {
    return value;
  }
  const amount = Number(match[1]);
  return match[2] === "h" ? String(amount * 60) : String(amount);
}

function parseRunnerResult(output: string): RunnerResult | null {
  for (const line of output.trim().split(/\r?\n/).reverse()) {
    try {
      const parsed = JSON.parse(line) as RunnerResult;
      if (parsed.status === "pass" || parsed.status === "fail") {
        return parsed;
      }
    } catch {
      continue;
    }
  }
  return null;
}

async function artifactBytes(outputDir: string): Promise<number> {
  let total = 0;
  for (const file of ["pineforge-report-full.json", "trades.json", "equity-curve.json", "compile.json"]) {
    total += (await stat(join(outputDir, file))).size;
  }
  return total;
}

async function writeJson(path: string, value: unknown) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function readBody(request: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      body += chunk;
      if (body.length > 2_000_000) {
        request.destroy(new Error("Request body too large"));
      }
    });
    request.on("end", () => resolve(body));
    request.on("error", reject);
  });
}

function sendJson(response: ServerResponse, statusCode: number, payload: unknown) {
  response.writeHead(statusCode, { "content-type": "application/json" });
  response.end(JSON.stringify(payload));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
