import { SignedOutHome } from "@/components/auth/signed-out-home";
import { PaperBotsPage } from "@/components/strategy/paper-bots-page";
import { auth } from "@clerk/nextjs/server";

export default async function PaperBotsRoute() {
  const { userId } = await auth();
  return userId ? <PaperBotsPage /> : <SignedOutHome />;
}
