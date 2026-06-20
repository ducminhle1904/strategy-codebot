import { expect, test } from "@playwright/test";

const webBaseUrl = process.env.STRATEGY_CODEBOT_E2E_WEB_BASE_URL ?? "http://127.0.0.1:3000";

test("web workspace loads against real docker backend", async ({ page }) => {
  await page.goto(webBaseUrl);
  await expect(page.locator("body")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Application error");

  const ready = await page.request.get(`${webBaseUrl}/api/backend/ready`);
  expect(ready.ok()).toBe(true);
  const payload = await ready.json();
  expect(payload.status).toBe("ok");
});
