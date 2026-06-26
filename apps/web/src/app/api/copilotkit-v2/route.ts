import {
  getStrategyCopilotRuntimeInfo,
  runStrategyCopilotAgent,
  STRATEGY_COPILOT_AGENT_ADAPTER,
} from "../copilotkit-chat/strategy-copilot-agent-adapter";

export const runtime = "nodejs";

const COPILOTKIT_V2_ADAPTER = {
  adapter: STRATEGY_COPILOT_AGENT_ADAPTER.transport,
  agent: STRATEGY_COPILOT_AGENT_ADAPTER.id,
  status: "adapter_probe",
  mode: "single-route",
};

export async function POST(request: Request) {
  return runStrategyCopilotAgent(request);
}

export async function GET() {
  const response = await getStrategyCopilotRuntimeInfo();
  return withAdapterMetadata(response);
}

async function withAdapterMetadata(response: Response) {
  const info = await response.json();
  const headers = new Headers(response.headers);
  headers.set("Content-Type", "application/json");
  return new Response(
    JSON.stringify({
      ...info,
      copilotkit_v2_adapter: COPILOTKIT_V2_ADAPTER,
    }),
    {
      headers,
      status: response.status,
    },
  );
}
