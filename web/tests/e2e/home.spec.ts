import { expect, test } from "@playwright/test";

test("visitor compares four recorded outcomes and matching downloadable evidence", async ({ page, request, baseURL }) => {
  const errors: string[] = [];
  const unexpectedRequests: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
  page.on("request", request => { if (request.method() !== "GET" || !request.url().startsWith("http://127.0.0.1:4173")) unexpectedRequests.push(request.url()); });
  await page.goto("./");
  const demo = page.getByRole("region", { name: "클릭형 실행 데모" });
  await expect(demo.getByText("Mercury 실제 실행 · 수정은 시뮬레이션", { exact: true })).toBeVisible();
  await expect(demo.getByRole("button", { name: "이전 단계" })).toBeDisabled();
  for (const [label, id, outcome, status] of [["정상 수정", "correct", "PASS", "pass"], ["회귀 발생", "regression", "FAIL", "human"], ["범위 이탈", "scope_escape", "OUT OF SCOPE", "human"], ["검증 불완전", "incomplete", "UNKNOWN", "unknown"]]) {
    await demo.getByRole("button", { name: new RegExp(`^${label}`) }).click();
    await expect(demo.getByTestId("evidence-outcome")).toHaveText(outcome);
    await expect(demo.getByTestId("recorded-status")).toHaveText(status);
    await demo.getByRole("button", { name: "Checks", exact: true }).click();
    if (id === "regression") await expect(demo.getByText("43,000원", { exact: true })).toBeVisible();
    if (id === "scope_escape") await expect(demo.getByRole("cell", { name: "NOT RUN", exact: true })).toHaveCount(4);
    if (id === "incomplete") await expect(demo.getByRole("cell", { name: "NOT RUN", exact: true })).toHaveCount(2);
    await demo.getByRole("button", { name: "Evidence", exact: true }).click();
    const link = demo.getByRole("link", { name: "공개 실행 근거 JSON 다운로드" });
    await expect(link).toHaveAttribute("href", `${new URL(baseURL!).pathname.replace(/\/$/, "")}/demo/cart/${id}.json`);
    const response = await request.get((await link.getAttribute("href"))!);
    expect(response.status()).toBe(200);
    const receipt = await response.json();
    expect(receipt.scenarioId).toBe(id);
    await expect(demo.getByTestId("run-id")).toHaveText(receipt.runId);
  }
  for (let i = 0; i < 4; i++) await demo.getByRole("button", { name: "다음 단계" }).click();
  await expect(demo.getByTestId("technical-result")).toHaveText("UNKNOWN");
  await expect(demo.getByRole("button", { name: "다음 단계" })).toBeDisabled();
  await demo.getByRole("button", { name: "처음부터" }).click();
  await expect(demo.getByTestId("technical-result")).toHaveCount(0);
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
  await next.focus(); await page.keyboard.press("Enter");
  await expect(demo.getByText("2 / 5", { exact: true })).toBeVisible();
  for (const panel of ["Diff", "Checks", "Scope", "Evidence"]) {
    const button = demo.getByRole("button", { name: panel, exact: true });
    await button.focus(); await page.keyboard.press("Enter");
    await expect(button).toHaveAttribute("aria-pressed", "true");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
  await demo.getByRole("button", { name: "Diff", exact: true }).click();
  const output = process.env.MERCURY_SCREENSHOT_DIR ?? "test-results";
  await page.screenshot({ path: `${output}/mobile.png`, fullPage: true });
  await demo.screenshot({ path: `${output}/mobile-demo.png` });
  await page.setViewportSize({ width: 1440, height: 1000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: `${output}/desktop.png`, fullPage: true });
  await demo.screenshot({ path: `${output}/desktop-demo.png` });
});

test("exported assets load beneath the deployment mount", async ({ page, request, baseURL }) => {
  const root = new URL(baseURL!);
  const assets: { url: string; status: number }[] = [];
  page.on("response", response => { if (["script", "stylesheet"].includes(response.request().resourceType())) assets.push({ url: response.url(), status: response.status() }); });
  const response = await page.goto("./");
  expect(response?.status()).toBe(200);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.getByRole("button", { name: "다음 단계" }).click();
  await expect(page.getByText("2 / 5", { exact: true })).toBeVisible();
  expect(assets.length).toBeGreaterThan(0);
  for (const asset of assets) { expect(asset.status).toBe(200); expect(new URL(asset.url).pathname.startsWith(`${root.pathname}_next/`)).toBe(true); }
  if (root.pathname !== "/") expect((await request.get("http://127.0.0.1:4173/")).status()).toBe(404);
});
