export const runtime = "nodejs";

export async function GET() {
  return Response.json({
    nextCursor: null,
    threads: [],
  });
}
