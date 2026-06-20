import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

const clerkConfigured = Boolean(
  process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.CLERK_SECRET_KEY
);
const isProtectedApiRoute = clerkConfigured
  ? createRouteMatcher(["/api/backend(.*)", "/api/chat(.*)"])
  : null;

const middleware = clerkConfigured
  ? clerkMiddleware(async (auth, request) => {
    if (isProtectedApiRoute?.(request)) {
      const session = await auth();
      if (!session.userId) {
        return NextResponse.json(
          { error: { code: "unauthorized", message: "Sign in to use Strategy Codebot." } },
          { status: 401 }
        );
      }
    }
    return NextResponse.next();
  })
  : function authConfigurationMiddleware(request: NextRequest) {
    const message =
      "Clerk authentication is required. Set NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY.";
    if (request.nextUrl.pathname.startsWith("/api/")) {
      return NextResponse.json(
        {
          error: {
            code: "auth_not_configured",
            message,
          },
        },
        { status: 503 }
      );
    }
    return new NextResponse(message, { status: 503 });
  };

export default middleware;

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api)(.*)",
    "/__clerk/:path*",
  ],
};
