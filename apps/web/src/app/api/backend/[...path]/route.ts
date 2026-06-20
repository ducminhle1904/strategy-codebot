import { buildBackendHeaders, DEFAULT_API_BASE_URL } from "@/lib/backend-client";
import { resolveBackendTenant } from "@/lib/server-auth";

export const runtime = "nodejs";

type RouteContext = {
  params: Promise<{
    path?: string[];
  }>;
};

const FORWARDED_HEADERS = [
  "accept",
  "content-type",
  "idempotency-key",
  "last-event-id",
  "x-request-id",
];
const MAX_PROXY_BODY_BYTES = 1024 * 1024;

export async function GET(request: Request, context: RouteContext) {
  return proxyBackendRequest(request, context);
}

export async function POST(request: Request, context: RouteContext) {
  return proxyBackendRequest(request, context);
}

export async function PATCH(request: Request, context: RouteContext) {
  return proxyBackendRequest(request, context);
}

export async function DELETE(request: Request, context: RouteContext) {
  return proxyBackendRequest(request, context);
}

export async function OPTIONS() {
  return new Response(null, { status: 204 });
}

async function proxyBackendRequest(request: Request, context: RouteContext) {
  const { path = [] } = await context.params;
  const upstreamUrl = buildUpstreamUrl(path, request.url);
  const startedAt = Date.now();
  const tenant = await resolveTenantOrResponse();
  if (tenant instanceof Response) {
    return tenant;
  }
  const body = await requestBodyOrResponse(request);
  if (body instanceof Response) {
    return body;
  }

  debugLog("request", {
    method: request.method,
    path: `/${path.join("/")}`,
    upstream: redactUrl(upstreamUrl),
  });

  let response: Response;
  try {
    response = await fetch(upstreamUrl, {
      body,
      headers: forwardedHeaders(request.headers, tenant),
      method: request.method,
      signal: request.signal,
    });
  } catch (error) {
    debugLog("error", {
      error: error instanceof Error ? error.message : String(error),
      method: request.method,
      upstream: redactUrl(upstreamUrl),
    });
    return Response.json(
      {
        error: {
          code: "backend_proxy_failed",
          message: error instanceof Error ? error.message : "Backend proxy request failed.",
        },
      },
      { status: 502 }
    );
  }

  debugLog("response", {
    durationMs: Date.now() - startedAt,
    method: request.method,
    status: response.status,
    upstream: redactUrl(upstreamUrl),
  });

  return new Response(response.body, {
    headers: responseHeaders(response.headers),
    status: response.status,
    statusText: response.statusText,
  });
}

async function resolveTenantOrResponse() {
  try {
    return await resolveBackendTenant();
  } catch (error) {
    if (error instanceof Response) {
      const isAuthConfigError = error.status === 503;
      return Response.json(
        {
          error: {
            code: isAuthConfigError ? "auth_not_configured" : "unauthorized",
            message: isAuthConfigError
              ? "Authentication is not configured."
              : "Sign in to use Strategy Codebot.",
          },
        },
        { status: error.status }
      );
    }
    throw error;
  }
}

function debugLog(event: string, payload: Record<string, unknown>) {
  if (process.env.STRATEGY_CODEBOT_WEB_DEBUG !== "1") {
    return;
  }
  console.info(`[strategy-web-api-proxy] ${event}`, JSON.stringify(payload));
}

function redactUrl(url: URL) {
  const clone = new URL(url);
  clone.username = "";
  clone.password = "";
  return clone.toString();
}

function buildUpstreamUrl(path: string[], requestUrl: string) {
  const url = new URL(requestUrl);
  const upstream = new URL(
    `/${path.map((segment) => encodeURIComponent(segment)).join("/")}`,
    DEFAULT_API_BASE_URL
  );
  upstream.search = url.search;
  return upstream;
}

function forwardedHeaders(
  headers: Headers,
  tenant: Awaited<ReturnType<typeof resolveBackendTenant>>
) {
  const forwarded = buildBackendHeaders({
    internalAuthSecret: tenant.internalAuthSecret,
    userId: tenant.userId,
    userTier: tenant.userTier,
    workspaceId: tenant.workspaceId,
    workspaceRole: tenant.workspaceRole,
  });
  for (const header of FORWARDED_HEADERS) {
    const value = headers.get(header);
    if (value) {
      forwarded.set(header, value);
    }
  }
  return forwarded;
}

async function requestBodyOrResponse(request: Request) {
  if (request.method === "GET" || request.method === "HEAD") {
    return undefined;
  }
  const length = request.headers.get("content-length");
  if (length && Number(length) > MAX_PROXY_BODY_BYTES) {
    return proxyBodyTooLarge();
  }
  const body = await request.arrayBuffer();
  if (body.byteLength > MAX_PROXY_BODY_BYTES) {
    return proxyBodyTooLarge();
  }
  return body;
}

function proxyBodyTooLarge() {
  return Response.json(
    {
      error: {
        code: "request_body_too_large",
        message: "Request body is too large for the web proxy.",
      },
    },
    { status: 413 }
  );
}

function responseHeaders(headers: Headers) {
  const response = new Headers();
  for (const header of ["cache-control", "content-type", "last-event-id"]) {
    const value = headers.get(header);
    if (value) {
      response.set(header, value);
    }
  }
  return response;
}
