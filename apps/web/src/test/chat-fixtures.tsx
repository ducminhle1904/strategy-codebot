import { z } from "zod";

import { RunStatusSchema, type RunStatus } from "@/lib/backend-schemas";

export const ArtifactPreviewSchema = z.object({
  id: z.string().min(1),
  title: z.string().min(1),
  kind: z.enum(["pine", "mql5", "markdown", "json"]),
  content: z.string(),
});

export type ArtifactPreview = z.infer<typeof ArtifactPreviewSchema>;

export const ChatRequestSchema = z.object({
  prompt: z.string().trim().min(1),
  target: z.enum(["pine", "mql5"]),
  model: z.string().min(1).optional(),
});

export type ChatRequest = z.infer<typeof ChatRequestSchema>;

export const buildChatRequest = (input: ChatRequest) => {
  const parsed = ChatRequestSchema.parse(input);

  return {
    endpoint: "/api/chat",
    body: {
      ...parsed,
      stream: true,
    },
  };
};

export type SseMessage = {
  event?: string;
  id?: string;
  data: string;
};

export const parseSseMessages = (source: string): SseMessage[] => {
  const normalized = source.replace(/\r\n/g, "\n");

  return normalized
    .split(/\n\n+/)
    .map((chunk) => {
      const message: SseMessage = { data: "" };
      const data: string[] = [];

      for (const line of chunk.split("\n")) {
        if (!line || line.startsWith(":")) {
          continue;
        }

        const separatorIndex = line.indexOf(":");
        const field = separatorIndex === -1 ? line : line.slice(0, separatorIndex);
        const rawValue = separatorIndex === -1 ? "" : line.slice(separatorIndex + 1);
        const value = rawValue.startsWith(" ") ? rawValue.slice(1) : rawValue;

        if (field === "event") {
          message.event = value;
        } else if (field === "id") {
          message.id = value;
        } else if (field === "data") {
          data.push(value);
        }
      }

      message.data = data.join("\n");
      return message;
    })
    .filter((message) => message.event || message.id || message.data);
};

const statusLabels: Record<RunStatus, string> = {
  blocked: "Blocked",
  cancelled: "Cancelled",
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
};

export const RunStatusFixture = ({ status }: { status: RunStatus }) => {
  const parsed = RunStatusSchema.parse(status);

  return (
    <div aria-label="Run status" data-status={parsed} role="status">
      {statusLabels[parsed]}
    </div>
  );
};

export const ArtifactPreviewFixture = ({
  artifact,
}: {
  artifact?: ArtifactPreview;
}) => {
  if (!artifact) {
    return <p>No artifact selected</p>;
  }

  const parsed = ArtifactPreviewSchema.parse(artifact);

  return (
    <article aria-label="Artifact preview">
      <header>
        <p>{parsed.kind.toUpperCase()}</p>
        <h2>{parsed.title}</h2>
      </header>
      <pre>
        <code>{parsed.content}</code>
      </pre>
    </article>
  );
};
