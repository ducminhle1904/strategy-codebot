import { BackendClient } from "./backend-client";

export type BackendTenant = {
  userId: string;
  workspaceId: string;
  userTier: "free" | "paid_low" | "paid_medium" | "paid_high";
  workspaceRole: "owner" | "member";
  internalAuthSecret?: string;
};

const TIER_VALUES = new Set(["free", "paid_low", "paid_medium", "paid_high"]);

export async function resolveBackendTenant(): Promise<BackendTenant> {
  if (!isClerkConfigured()) {
    throw new Response("Authentication is not configured.", { status: 503 });
  }

  const { auth } = await import("@clerk/nextjs/server");
  const session = await auth();
  if (!session.userId) {
    throw new Response("Unauthorized", { status: 401 });
  }
  const orgId = session.orgId ?? null;
  const role = roleFromClerk(session.orgRole);
  const metadata = (session.sessionClaims?.metadata ?? {}) as Record<string, unknown>;
  const publicMetadata = (session.sessionClaims?.public_metadata ?? {}) as Record<string, unknown>;
  return {
    userId: session.userId,
    workspaceId: orgId ?? `personal_${session.userId}`,
    userTier: tierFromMetadata(metadata.user_tier ?? publicMetadata.user_tier),
    workspaceRole: role,
    internalAuthSecret: process.env.STRATEGY_CODEBOT_INTERNAL_AUTH_SECRET,
  };
}

export async function createServerBackendClient(baseUrl?: string) {
  const tenant = await resolveBackendTenant();
  return new BackendClient({
    baseUrl,
    internalAuthSecret: tenant.internalAuthSecret,
    userId: tenant.userId,
    userTier: tenant.userTier,
    workspaceId: tenant.workspaceId,
    workspaceRole: tenant.workspaceRole,
  });
}

export function isClerkConfigured() {
  return Boolean(
    process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.CLERK_SECRET_KEY
  );
}

function tierFromMetadata(value: unknown): BackendTenant["userTier"] {
  return typeof value === "string" && TIER_VALUES.has(value)
    ? (value as BackendTenant["userTier"])
    : "paid_low";
}

function roleFromClerk(value: string | null | undefined): BackendTenant["workspaceRole"] {
  return value?.includes("admin") || value?.includes("owner") ? "owner" : "member";
}
