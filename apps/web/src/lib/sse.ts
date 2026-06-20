export type SseMessage = {
  id?: string;
  event: string;
  data: unknown;
};

export function parseSseMessages(text: string): SseMessage[] {
  return splitSseFrames(text).flatMap(parseSseFrame);
}

export function parseSseJsonPayloads(text: string): unknown[] {
  return parseSseMessages(text).map((message) => message.data);
}

export function splitCompleteSseFrames(text: string): {
  frames: string[];
  remaining: string;
} {
  const parts = normalizeSseLineEndings(text).split(/\n\n+/);
  return {
    frames: parts.slice(0, -1),
    remaining: parts.at(-1) ?? "",
  };
}

function splitSseFrames(text: string): string[] {
  return normalizeSseLineEndings(text).split(/\n\n+/);
}

function normalizeSseLineEndings(text: string): string {
  return text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
}

function parseSseFrame(frame: string): SseMessage[] {
  if (!frame.trim()) {
    return [];
  }

  let id: string | undefined;
  let event = "message";
  const dataLines: string[] = [];

  for (const line of frame.split("\n")) {
    if (line.startsWith("id:")) {
      id = line.slice(3).trim();
    } else if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).replace(/^ /, ""));
    }
  }

  if (dataLines.length === 0) {
    return [];
  }

  const rawData = dataLines.join("\n");
  try {
    return [{ data: JSON.parse(rawData), event, id }];
  } catch {
    return [{ data: { raw: rawData }, event, id }];
  }
}
