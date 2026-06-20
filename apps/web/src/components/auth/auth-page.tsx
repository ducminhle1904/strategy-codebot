import { SignIn, SignUp } from "@clerk/nextjs";

export function AuthPage({ mode }: { mode: "sign-in" | "sign-up" }) {
  return (
    <main className="grid min-h-dvh place-items-center bg-background px-6">
      {mode === "sign-in" ? <SignIn /> : <SignUp />}
    </main>
  );
}
