import { expect, test } from "@playwright/test";

test("visitor can explore evidence outcomes without executing a backend", async ({ page }) => {
  const errors: string[] = [];
  const unexpectedRequests: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
  page.on("request", request => { if (request.method() !== "GET" || !request.url().startsWith("http://127.0.0.1:4173")) unexpectedRequests.push(request.url()); });
  await page.goto("./");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const demo = page.getByRole("region", { name: "클릭형 실행 데모" });
  await expect(demo.getByText("브라우저 안에서만 동작하는 합성 데이터입니다.")).toBeVisible();
  await expect(demo.getByRole("button", { name: "이전 단계" })).toBeDisabled();
  for (let i = 0; i < 4; i++) await demo.getByRole("button", { name: "다음 단계" }).click();
  await expect(demo.getByTestId("technical-result")).toHaveText("PASS");
  await expect(demo.getByRole("button", { name: "다음 단계" })).toBeDisabled();
  await demo.getByRole("button", { name: "검사 실패", exact: true }).click();
  await expect(demo.getByTestId("technical-result")).toHaveCount(0);
  await expect(demo.getByRole("button", { name: "이전 단계" })).toBeDisabled();
  for (let i = 0; i < 4; i++) await demo.getByRole("button", { name: "다음 단계" }).click();
  await expect(demo.getByTestId("technical-result")).toHaveText("FAIL");
  await demo.getByRole("button", { name: "확인 불가", exact: true }).click();
  for (let i = 0; i < 4; i++) await demo.getByRole("button", { name: "다음 단계" }).click();
  await expect(demo.getByTestId("technical-result")).toHaveText("UNKNOWN");
  await demo.getByRole("button", { name: "이전 단계" }).click();
  await expect(demo.getByTestId("technical-result")).toHaveCount(0);
  await demo.getByRole("button", { name: "처음부터" }).click();
  await expect(demo.getByRole("button", { name: "이전 단계" })).toBeDisabled();
  expect(errors).toEqual([]);
  expect(unexpectedRequests).toEqual([]);
});

test("mobile layout and keyboard controls remain usable", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("./");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "본문으로 건너뛰기" })).toBeFocused();
  await page.keyboard.press("Enter");
  const demo = page.getByRole("region", { name: "클릭형 실행 데모" });
  const next = demo.getByRole("button", { name: "다음 단계" });
  await next.focus();
  await page.keyboard.press("Enter");
  await expect(demo.getByText("2 / 5", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: process.env.MERCURY_SCREENSHOT_DIR ? `${process.env.MERCURY_SCREENSHOT_DIR}/mobile.png` : "test-results/mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: process.env.MERCURY_SCREENSHOT_DIR ? `${process.env.MERCURY_SCREENSHOT_DIR}/desktop.png` : "test-results/desktop.png", fullPage: true });
});


test("exported assets load beneath the deployment mount", async ({ page, request, baseURL }) => {
  const root = new URL(baseURL!);
  const assets: { url: string; status: number }[] = [];
  page.on("response", response => {
    if (["script", "stylesheet"].includes(response.request().resourceType())) {
      assets.push({ url: response.url(), status: response.status() });
    }
  });
  const response = await page.goto("./");
  expect(response?.status()).toBe(200);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.getByRole("button", { name: "다음 단계" }).click();
  await expect(page.getByText("2 / 5", { exact: true })).toBeVisible();
  expect(assets.length).toBeGreaterThan(0);
  for (const asset of assets) {
    expect(asset.status).toBe(200);
    expect(new URL(asset.url).pathname.startsWith(`${root.pathname}_next/`)).toBe(true);
  }
  if (root.pathname !== "/") {
    expect((await request.get("http://127.0.0.1:4173/")).status()).toBe(404);
  }
});
