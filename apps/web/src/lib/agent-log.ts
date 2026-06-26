type AgentLogLevel = "error" | "info" | "warn";

const SENSITIVE_KEY_PATTERN = /(authorization|cookie|password|secret|token|api[_-]?key)/i;
const CONTENT_KEY_PATTERN = /(content|message_content|prompt|raw_body|tool_output)/i;
const MAX_VALUE_LENGTH = 240;

export function agentLog(
  level: AgentLogLevel,
  event: string,
  fields: Record<string, unknown> = {}
) {
  const component = stringValue(fields.component) ?? "app";
  const line = formatLogfmt({
    ts: new Date().toISOString(),
    lvl: level,
    svc: "web",
    component,
    event,
    ...withoutReservedFields(fields),
  });
  console[level](line);
}

export function formatLogfmt(fields: Record<string, unknown>) {
  return Object.entries(fields)
    .filter(([, value]) => value !== undefined)
    .map(([key, value]) => `${normalizeKey(key)}=${formatValue(key, value)}`)
    .join(" ");
}

function normalizeKey(key: string) {
  return key.replace(/[^A-Za-z0-9_]/g, "_").toLowerCase();
}

function withoutReservedFields(fields: Record<string, unknown>) {
  const safeFields: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(fields)) {
    if (["component", "event", "lvl", "svc", "ts"].includes(key)) {
      continue;
    }
    safeFields[key] = value;
  }
  return safeFields;
}

function formatValue(key: string, value: unknown) {
  const sanitized = sanitizeValue(key, value);
  const raw = singleLine(String(sanitized));
  const escaped = raw.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  return /\s|=|"/.test(escaped) ? `"${escaped}"` : escaped;
}

function sanitizeValue(key: string, value: unknown): string | number | boolean | null {
  if (value === null) {
    return "null";
  }
  if (SENSITIVE_KEY_PATTERN.test(key)) {
    return "[redacted]";
  }
  if (CONTENT_KEY_PATTERN.test(key)) {
    return typeof value === "string" ? `[redacted len=${value.length}]` : "[redacted]";
  }
  if (typeof value === "string") {
    return truncate(value);
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  if (Array.isArray(value)) {
    return truncate(value.map((item) => String(item)).join(","));
  }
  if (value && typeof value === "object") {
    return `keys:${Object.keys(value).sort().join(",")}`;
  }
  return truncate(String(value));
}

function truncate(value: string) {
  return value.length > MAX_VALUE_LENGTH ? `${value.slice(0, MAX_VALUE_LENGTH)}...` : value;
}

function singleLine(value: string) {
  return value.replace(/[\r\n\t]+/g, " ");
}

function stringValue(value: unknown) {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}
