import { SignedOutHome } from "@/components/auth/signed-out-home";
import { ArtifactsPage } from "@/components/strategy/artifacts-page";
import { auth } from "@clerk/nextjs/server";

export default async function ArtifactsRoute() {
  const { userId } = await auth();
  return userId ? <ArtifactsPage /> : <SignedOutHome />;
}

