import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { chmod, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

test("pineforge runner fixture mode exposes readiness and writes artifact files", async () => {
  const port = 19_000 + Math.floor(Math.random() * 1000);
  const child = spawn(process.execPath, ["dist/server.js"], {
    env: { ...process.env, PINEFORGE_RUNNER_PORT: String(port), PINEFORGE_RUNNER_MODE: "fixture" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  try {
    await waitForReady(port);
    const dir = await mkdtemp(join(tmpdir(), "pineforge-runner-test-"));
    const pinePath = join(dir, "strategy.pine");
    const csvPath = join(dir, "ohlcv.csv");
    const outDir = dir;
    await writeFile(pinePath, '//@version=6\nstrategy("x")\nstrategy.entry("Long", strategy.long)\n', "utf8");
    await writeFile(csvPath, "timestamp,open,high,low,close,volume\n1,1,1,1,1,1\n", "utf8");

    const response = await fetch(`http://127.0.0.1:${port}/v1/pineforge/backtests`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        job_id: "job_test",
        run_id: "run_test",
        config: {
          symbol: "BTCUSDT",
          timeframe: "1h",
          candle_timeframe: "1m",
          initial_capital: 10_000,
          fee_bps: 10,
          slippage_bps: 0,
        },
        pine_code_path: pinePath,
        ohlcv_csv_path: csvPath,
        output_dir: outDir,
        limits: { max_bars: 10 },
      }),
    });
    const payload = await response.json();
    assert.equal(response.status, 200);
    assert.equal(payload.status, "pass");
    assert.equal(payload.report.summary.bars_processed, 1);
    assert.equal(payload.artifact_manifest.report, join(outDir, "pineforge-report-full.json"));
    assert.equal(JSON.parse(await readFile(join(outDir, "trades.json"), "utf8")).length, 0);
  } finally {
    child.kill("SIGKILL");
  }
});

test("pineforge runner native mode reports unavailable when native command is missing", async () => {
  const port = 20_000 + Math.floor(Math.random() * 1000);
  const child = spawn(process.execPath, ["dist/server.js"], {
    env: {
      ...process.env,
      PINEFORGE_RUNNER_PORT: String(port),
      PINEFORGE_RUNNER_MODE: "native",
      PINEFORGE_NATIVE_COMMAND: "/does/not/exist/pineforge-native-runner.py",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  try {
    const response = await waitForReadyStatus(port, 503);
    const payload = await response.json();
    assert.equal(payload.status, "unavailable");
    assert.equal(payload.native_ready, false);
  } finally {
    child.kill("SIGKILL");
  }
});

test("pineforge runner native mode invokes native command and parses structured result", async () => {
  const port = 21_000 + Math.floor(Math.random() * 1000);
  const dir = await mkdtemp(join(tmpdir(), "pineforge-runner-native-test-"));
  const commandPath = join(dir, "native-command.mjs");
  await writeFile(
    commandPath,
    [
      "#!/usr/bin/env node",
      "let body = '';",
      "process.stdin.on('data', (chunk) => body += chunk);",
      "process.stdin.on('end', () => {",
      "  const input = JSON.parse(body);",
      "  const result = {",
      "    status: 'pass',",
      "    runner: 'pineforge-runner',",
      "    runner_version: 'test-native',",
      "    report: { summary: { bars_processed: 2 } },",
      "    trades: [],",
      "    equity_curve: [],",
      "    compile: { status: 'pass' },",
      "    artifact_manifest: { report: input.output_dir + '/pineforge-report-full.json' },",
      "    stats: { bars_processed: 2, compile_ms: 1, run_ms: 1, output_bytes: 0 }",
      "  };",
      "  console.log(JSON.stringify(result));",
      "});",
      "",
    ].join("\n"),
    "utf8",
  );
  await chmod(commandPath, 0o755);
  const child = spawn(process.execPath, ["dist/server.js"], {
    env: {
      ...process.env,
      PINEFORGE_RUNNER_PORT: String(port),
      PINEFORGE_RUNNER_MODE: "native",
      PINEFORGE_NATIVE_COMMAND: commandPath,
      PINEFORGE_ENGINE_VERSION: "test-engine",
      PINEFORGE_CODEGEN_VERSION: "test-codegen",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  try {
    const ready = await waitForReadyStatus(port, 200);
    const readyPayload = await ready.json();
    assert.equal(readyPayload.native_ready, true);
    assert.equal(readyPayload.codegen_version, "test-codegen");

    const pinePath = join(dir, "strategy.pine");
    const csvPath = join(dir, "ohlcv.csv");
    await writeFile(pinePath, '//@version=6\nstrategy("x")\nstrategy.entry("Long", strategy.long)\n', "utf8");
    await writeFile(csvPath, "timestamp,open,high,low,close,volume\n1,1,1,1,1,1\n2,1,1,1,1,1\n", "utf8");

    const response = await fetch(`http://127.0.0.1:${port}/v1/pineforge/backtests`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        job_id: "job_native",
        run_id: "run_native",
        config: {
          symbol: "BTCUSDT",
          timeframe: "1h",
          candle_timeframe: "1m",
          initial_capital: 10_000,
          fee_bps: 10,
          slippage_bps: 0,
        },
        pine_code_path: pinePath,
        ohlcv_csv_path: csvPath,
        output_dir: dir,
        limits: { max_bars: 10 },
      }),
    });
    const payload = await response.json();
    assert.equal(response.status, 200);
    assert.equal(payload.status, "pass");
    assert.equal(payload.runner_version, "test-native");
    assert.equal(payload.report.summary.bars_processed, 2);
  } finally {
    child.kill("SIGKILL");
  }
});

test("native wrapper rejects OHLCV CSV with missing required header", async () => {
  const dir = await mkdtemp(join(tmpdir(), "pineforge-native-csv-test-"));
  const payload = await runNativeWrapperWithCsv(dir, "time,open,high,low,close,volume\n1,1,1,1,1,1\n");

  assert.equal(payload.status, "fail");
  assert.equal(payload.error.code, "pineforge_native_runner_error");
  assert.match(payload.error.message, /Invalid OHLCV CSV header/);
});

test("native wrapper rejects OHLCV CSV with non-finite numeric rows", async () => {
  const dir = await mkdtemp(join(tmpdir(), "pineforge-native-csv-test-"));
  const payload = await runNativeWrapperWithCsv(dir, "timestamp,open,high,low,close,volume\n1,NaN,1,1,1,1\n");

  assert.equal(payload.status, "fail");
  assert.equal(payload.error.code, "pineforge_native_runner_error");
  assert.match(payload.error.message, /non-finite open/);
});

async function waitForReady(port: number) {
  await waitForReadyStatus(port, 200);
}

async function runNativeWrapperWithCsv(dir: string, csv: string) {
  const pinePath = join(dir, "strategy.pine");
  const csvPath = join(dir, "ohlcv.csv");
  const outDir = join(dir, "out");
  await writeFile(pinePath, '//@version=6\nstrategy("x")\nstrategy.entry("Long", strategy.long)\n', "utf8");
  await writeFile(csvPath, csv, "utf8");
  const child = spawn(process.env.PYTHON ?? "python3", [join(process.cwd(), "bin", "pineforge-native-runner.py")], {
    stdio: ["pipe", "pipe", "pipe"],
  });
  child.stdin.end(JSON.stringify({
    job_id: "job_csv",
    run_id: "run_csv",
    config: {
      symbol: "BTCUSDT",
      timeframe: "1m",
      candle_timeframe: "1m",
      initial_capital: 10_000,
      fee_bps: 10,
      slippage_bps: 0,
    },
    pine_code_path: pinePath,
    ohlcv_csv_path: csvPath,
    output_dir: outDir,
    limits: { max_bars: 10 },
  }));
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (chunk: Buffer) => stdout += chunk.toString("utf8"));
  child.stderr.on("data", (chunk: Buffer) => stderr += chunk.toString("utf8"));
  const code = await new Promise<number | null>((resolve) => child.on("close", resolve));
  assert.equal(code, 0, stderr);
  return JSON.parse(stdout.trim());
}

async function waitForReadyStatus(port: number, status: number): Promise<Response> {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/ready`);
      if (response.status === status) {
        return response;
      }
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  throw new Error(`runner did not reach ready status ${status}`);
}
