"use client";

import { StrategyStartPrompt } from "@/components/strategy/start-prompt";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/language";
import { SignInButton, SignUpButton } from "@clerk/nextjs";
import Image from "next/image";
import { useRouter } from "next/navigation";

export function SignedOutHome() {
  const router = useRouter();
  const { t } = useI18n();
  const suggestions = [
    t.signedOutSuggestionSpec,
    t.signedOutSuggestionPine,
    t.signedOutSuggestionRisk,
  ];

  const navigateToSignIn = () => {
    router.push("/sign-in");
  };

  return (
    <main className="relative grid min-h-dvh overflow-hidden bg-background text-foreground">
      <header className="absolute top-0 right-0 left-0 z-10 flex items-center justify-between px-5 py-4">
        <div className="flex items-center gap-2">
          <span className="flex size-8 items-center justify-center overflow-hidden rounded-[10px] bg-white">
            <Image
              alt=""
              className="size-full object-cover"
              height={32}
              src="/brand/strategy-codebot-icon-192.png"
              width={32}
            />
          </span>
          <span className="font-medium text-sm">Strategy Codebot</span>
        </div>
        <div className="flex items-center gap-2">
          <SignInButton mode="redirect">
            <Button size="sm" type="button" variant="outline">
              {t.logIn}
            </Button>
          </SignInButton>
          <SignUpButton mode="redirect">
            <Button size="sm" type="button">
              {t.signUp}
            </Button>
          </SignUpButton>
        </div>
      </header>

      <StrategyStartPrompt
        className="min-h-dvh py-24"
        onSubmit={navigateToSignIn}
        placeholder={t.signedOutPlaceholder}
        submitLabel={t.logIn}
        suggestions={suggestions.map((label) => ({
          label,
          onSelect: navigateToSignIn,
        }))}
        title={t.signedOutTitle}
      />

      <p className="absolute right-4 bottom-3 left-4 text-center text-muted-foreground text-xs">
        {t.signedOutDisclaimer}
      </p>
    </main>
  );
}
