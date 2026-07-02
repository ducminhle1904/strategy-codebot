import { COPILOT_STRATEGY_AGENT_ID } from "@/lib/copilot-constants";

import { GET, POST } from "./route-handler";

export const STRATEGY_COPILOT_AGENT_ADAPTER = {
  id: COPILOT_STRATEGY_AGENT_ID,
  transport: "ag-ui-sse",
} as const;

export function runStrategyCopilotAgent(request: Request) {
  return POST(request);
}

export function getStrategyCopilotRuntimeInfo() {
  return GET();
}
