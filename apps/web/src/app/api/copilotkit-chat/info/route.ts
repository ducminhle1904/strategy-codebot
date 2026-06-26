import { copilotRuntimeInfo } from "../runtime-info";

export const runtime = "nodejs";

export async function GET() {
  return Response.json(copilotRuntimeInfo());
}
