import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import { shadcn } from "@clerk/ui/themes";
import { Geist, Geist_Mono } from "next/font/google";
import Script from "next/script";
import "./globals.css";
import { AppProviders } from "./providers";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const themeBootScript = `
(function () {
  try {
    var stored = window.localStorage.getItem("strategy-codebot-theme");
    var theme = stored === "light" || stored === "dark" || stored === "system" ? stored : "system";
    var resolved = theme === "system"
      ? (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
      : theme;
    document.documentElement.classList.toggle("dark", resolved === "dark");
    document.documentElement.style.colorScheme = resolved;
  } catch (_) {}
})();
`;

export const metadata: Metadata = {
  title: "Strategy Codebot",
  description: "Chat workspace for reviewable strategy code artifacts.",
  icons: {
    icon: [
      {
        rel: "icon",
        sizes: "32x32",
        type: "image/png",
        url: "/brand/strategy-codebot-favicon-32.png",
      },
      {
        rel: "icon",
        sizes: "192x192",
        type: "image/png",
        url: "/brand/strategy-codebot-icon-192.png",
      },
    ],
    apple: [
      {
        sizes: "192x192",
        type: "image/png",
        url: "/brand/strategy-codebot-icon-192.png",
      },
    ],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const clerkPublishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  if (!clerkPublishableKey) {
    throw new Error("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is required.");
  }
  const content = <AppProviders>{children}</AppProviders>;
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        <Script
          id="theme-boot"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{ __html: themeBootScript }}
        />
      </head>
      <body className="min-h-full flex flex-col">
        <ClerkProvider
          appearance={{ theme: shadcn }}
          publishableKey={clerkPublishableKey}
        >
          {content}
        </ClerkProvider>
      </body>
    </html>
  );
}
