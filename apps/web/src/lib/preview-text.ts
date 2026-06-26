export function userFacingPreviewText(value: string): string {
  return value
    .replace(/\blocal preview engine\b/gi, "local preview")
    .replace(/\blocal preview runner\b/gi, "local preview")
    .replace(/pineforge[-_\s]*(?:runner|engine)?/gi, "local preview")
    .replace(/\bpineforge\b/gi, "local preview")
    .replace(/\brunner\b/gi, "preview runtime")
    .replace(/\bengine\b/gi, "preview runtime")
    .replace(/\bcompile(?:d|r)?\b/gi, "compatibility")
    .replace(/\btranspile(?:d|r)?\b/gi, "compatibility");
}
