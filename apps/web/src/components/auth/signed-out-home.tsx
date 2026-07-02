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
    <main className="apple-page-shell relative grid min-h-dvh overflow-hidden text-foreground">
      <header className="apple-frosted absolute top-4 right-4 left-4 z-10 flex items-center justify-between rounded-full border px-4 py-2">
        <div className="flex items-center gap-2">
          <span className="flex size-8 items-center justify-center overflow-hidden rounded-full bg-white">
            <Image
              alt="Strategy Codebot"
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
        className="relative z-[1] min-h-dvh pt-24 pb-16"
        onSubmit={navigateToSignIn}
        placeholder={t.signedOutPlaceholder}
        submitLabel={t.logIn}
        suggestions={suggestions.map((label) => ({
          label,
          onSelect: navigateToSignIn,
        }))}
        title={t.signedOutTitle}
      />

      <p className="absolute right-4 bottom-3 left-4 z-[1] text-center text-muted-foreground text-xs">
        {t.signedOutDisclaimer}
      </p>
    </main>
  );
}
