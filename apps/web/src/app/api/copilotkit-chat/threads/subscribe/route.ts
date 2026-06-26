export const runtime = "nodejs";

export async function POST() {
  return Response.json(
    {
      error: "CopilotKit thread metadata subscriptions are not enabled.",
    },
    { status: 501 }
  );
}
