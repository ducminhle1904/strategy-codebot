export type StrategyChatRuntimeName = "copilotkit";

export function configuredStrategyChatRuntime(): StrategyChatRuntimeName {
  return "copilotkit";
}

export function isCopilotKitRuntimeEnabled() {
  return true;
}

export function isAgUiDebugEnabled() {
  return process.env.NEXT_PUBLIC_DEBUG_AG_UI === "true";
}
