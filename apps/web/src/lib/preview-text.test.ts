import { describe, expect, it } from "vitest";

import { userFacingPreviewText } from "./preview-text";

describe("userFacingPreviewText", () => {
  it("sanitizes internal preview runtime terms", () => {
    const text = userFacingPreviewText("PineForge runner compile failed in the local preview engine");

    expect(text).toBe("local preview compatibility failed in the local preview");
    expect(text).not.toMatch(/pineforge|runner|engine|compile/i);
  });
});
