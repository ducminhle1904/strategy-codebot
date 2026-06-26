import { SignedOutHome } from "@/components/auth/signed-out-home";
import { StrategyWorkspace } from "@/components/strategy/workspace";
import { auth } from "@clerk/nextjs/server";

export default async function ConversationPage({
  params,
}: {
  params: Promise<{ conversationId: string }>;
}) {
  const { userId } = await auth();
  const { conversationId } = await params;
  return userId ? (
    <StrategyWorkspace
      initialConversationId={conversationId}
      key={conversationId}
    />
  ) : (
    <SignedOutHome />
  );
}
