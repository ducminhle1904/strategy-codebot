import { SignedOutHome } from "@/components/auth/signed-out-home";
import { StrategyWorkspace } from "@/components/strategy/workspace";
import { auth } from "@clerk/nextjs/server";

export default async function Home() {
  const { userId } = await auth();
  return userId ? <StrategyWorkspace /> : <SignedOutHome />;
}
