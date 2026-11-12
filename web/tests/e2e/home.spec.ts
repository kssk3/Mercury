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
    if (id === "scope_escape") await expect(demo.getByRole("cell", { name: /^NOT RUN\s*미실행$/ })).toHaveCount(4);
    if (id === "incomplete") await expect(demo.getByRole("cell", { name: /^NOT RUN\s*미실행$/ })).toHaveCount(2);
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

test("scenario conclusion, observed evidence and next decision precede details on desktop and mobile", async ({ page }) => {
  await page.goto("./");
  const demo = page.getByRole("region", { name: "클릭형 실행 데모" });
  const summary = demo.getByRole("region", { name: "선택한 시나리오의 판단" });
  const tabs = demo.getByRole("group", { name: "근거 보기" });
  const output = process.env.MERCURY_SCREENSHOT_DIR ?? "test-results";
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 375, height: 812 }]) {
    await page.setViewportSize(viewport);
    for (const [label, result, conclusion, evidence, next] of [
      ["정상 수정", "PASS", "선택한 검사가 수정 결과를 뒷받침합니다.", "최종 검사: 통과 2개", "사람이 검토"],
      ["회귀 발생", "FAIL", "수정 후 검사에서 회귀가 발견됐습니다.", "예상 40,000원 → 실제 43,000원", "배송비 정책"],
      ["범위 이탈", "OUT OF SCOPE", "승인한 변경 범위를 벗어났습니다.", "범위·보호 위반: shipping.py", "별도 승인"],
      ["검증 불완전", "UNKNOWN", "아직 완료로 판단할 수 없습니다.", "미실행 2개", "최종 검증"],
    ]) {
      await demo.getByRole("button", { name: new RegExp(`^${label}`) }).click();
      await expect(summary).toHaveCount(1);
      await expect(summary.getByTestId("evidence-outcome")).toHaveText(result);
      await expect(summary.getByRole("heading", { name: conclusion, exact: true })).toBeVisible();
      await expect(summary).toContainText(evidence);
      await expect(summary.getByTestId("next-decision")).toContainText(next);
      await expect(summary.getByText("핵심 근거", { exact: true })).toBeVisible();
      await expect(summary.getByText("다음 판단", { exact: true })).toBeVisible();
      expect(await summary.evaluate(node => Boolean(node.compareDocumentPosition(document.querySelector(".evidence-tabs")!) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
      const summaryBox = await summary.boundingBox(); const tabsBox = await tabs.boundingBox();
      expect(summaryBox!.y + summaryBox!.height).toBeLessThanOrEqual(tabsBox!.y);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    }
    await summary.getByText("기록된 실행 상태와 이유", { exact: true }).click();
    await expect(summary.getByTestId("recorded-status")).toBeVisible();
    await summary.getByText("기록된 실행 상태와 이유", { exact: true }).click();
    await demo.getByRole("button", { name: "다음 단계" }).click();
    await demo.getByRole("button", { name: "처음부터" }).click();
    await expect(summary.getByTestId("evidence-outcome")).toHaveText("UNKNOWN");
    await expect(summary).toContainText("미실행 2개");
    await summary.screenshot({ path: `${output}/${viewport.width === 375 ? "mobile" : "desktop"}-summary.png` });
  }
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


test("usage situations connect value explanations to the matching demo summary", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("./");
  const demo = page.getByRole("region", { name: "클릭형 실행 데모" });
  const summary = demo.getByRole("region", { name: "선택한 시나리오의 판단" });
  const usage = page.getByRole("region", { name: "AI 작업을 검토할 때, 이런 순간에 도움이 됩니다." });
  const tools = page.getByRole("region", { name: "익숙한 개발 도구와 함께 사용합니다." });
  await expect(page.getByRole("heading", { level: 1 })).toContainText("어디까지 확인됐는지");
  for (const name of ["Git diff", "테스트", "CI"]) await expect(tools.getByRole("heading", { name, exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "방금 본 장바구니 사례 재현하기" })).toHaveAttribute("href", /#recorded-cart-scenarios$/);
  for (const [name, hash, outcome] of [
    ["배송비 회귀 사례 보기", "#demo-regression", "FAIL"],
    ["승인 범위 이탈 사례 보기", "#demo-scope-escape", "OUT OF SCOPE"],
    ["미완료 검증 사례 보기", "#demo-incomplete", "UNKNOWN"],
  ]) {
    const link = usage.getByRole("link", { name });
    await link.focus(); await page.keyboard.press("Enter");
    await expect(page).toHaveURL(new RegExp(`${hash}$`));
    await expect(summary.getByTestId("evidence-outcome")).toHaveText(outcome);
    await expect(summary).toBeFocused();
    await expect(summary).toBeInViewport();
    await expect(demo.getByText("1 / 5", { exact: true })).toBeVisible();
  }
  await page.goBack();
  await expect(summary.getByTestId("evidence-outcome")).toHaveText("OUT OF SCOPE");
  await expect(summary).toBeFocused();
  await page.goForward();
  await expect(summary.getByTestId("evidence-outcome")).toHaveText("UNKNOWN");
  // Same URL fragment still activates the case after a manual selection.
  await demo.getByRole("button", { name: /^정상 수정/ }).click();
  await demo.getByRole("button", { name: "다음 단계" }).click();
  await usage.getByRole("link", { name: "미완료 검증 사례 보기" }).click();
  await expect(summary.getByTestId("evidence-outcome")).toHaveText("UNKNOWN");
  await expect(demo.getByText("1 / 5", { exact: true })).toBeVisible();
  await expect(summary).toBeFocused();
  // Ordinary section navigation must preserve the current selection and focus.
  await page.getByRole("link", { name: "작동 방식", exact: true }).click();
  await expect(page).toHaveURL(/#how-it-works$/);
  await expect(summary).not.toBeFocused();
  await expect(summary.getByTestId("evidence-outcome")).toHaveText("UNKNOWN");
  await page.getByRole("link", { name: "데모", exact: true }).click();
  await expect(page).toHaveURL(/#demo$/);
  await expect(summary.getByTestId("evidence-outcome")).toHaveText("UNKNOWN");
  const output = process.env.MERCURY_SCREENSHOT_DIR ?? "test-results";
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 375, 812]] as const) {
    await page.setViewportSize({ width, height });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await usage.screenshot({ path: `${output}/${name}-situations.png` });
    await tools.screenshot({ path: `${output}/${name}-tools.png` });
    await page.locator(".hero").screenshot({ path: `${output}/${name}-hero.png` });
    await usage.getByRole("link", { name: "배송비 회귀 사례 보기" }).click();
    await expect(summary).toBeFocused();
    await expect(summary).toBeInViewport();
  }
  expect(errors).toEqual([]);
});

test("shared scenario URLs initialize on direct entry and unknown fragments remain ordinary anchors", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const demo = page.getByRole("region", { name: "클릭형 실행 데모" });
  const summary = demo.getByRole("region", { name: "선택한 시나리오의 판단" });
  for (const [hash, outcome] of [["#demo-regression", "FAIL"], ["#demo-scope-escape", "OUT OF SCOPE"], ["#demo-incomplete", "UNKNOWN"], ["#demo-correct", "PASS"]]) {
    await page.goto(`./${hash}`);
    await page.reload();
    await expect(summary.getByTestId("evidence-outcome")).toHaveText(outcome);
    await expect(summary).toBeFocused();
    await expect(summary).toBeInViewport();
  }
  await page.goto("./#demo-unknown");
  await page.reload();
  await expect(summary.getByTestId("evidence-outcome")).toHaveText("PASS");
  await expect(summary).not.toBeFocused();
  await page.goto("./#demo");
  await page.reload();
  await expect(summary.getByTestId("evidence-outcome")).toHaveText("PASS");
  await expect(summary).not.toBeFocused();
});

test("result actions expose matching evidence with keyboard focus and honest check states", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("./");
  const demo = page.getByRole("region", { name: "클릭형 실행 데모" });
  const summary = demo.getByRole("region", { name: "선택한 시나리오의 판단" });
  const output = process.env.MERCURY_SCREENSHOT_DIR ?? "test-results";
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 375, height: 812 }]) {
    await page.setViewportSize(viewport);
    for (const [scenario, action, panel, evidence] of [
      ["정상 수정", "통과한 검사 보기", "Checks", "PASS"],
      ["회귀 발생", "실패한 검사 보기", "Checks", "FAIL"],
      ["범위 이탈", "승인 범위와 변경 파일 비교하기", "Scope", "shipping.py"],
      ["검증 불완전", "미실행·확인 불가 검사 보기", "Checks", "NOT RUN"],
    ]) {
      await demo.getByRole("button", { name: new RegExp(`^${scenario}`) }).click();
      const actionButton = summary.getByRole("button", { name: action });
      await actionButton.focus(); await page.keyboard.press("Enter");
      const target = demo.getByRole("region", { name: `${panel} 근거`, exact: true });
      await expect(target).toBeFocused();
      await expect(target).toBeInViewport();
      await expect(target).toContainText(evidence);
      await expect(demo.getByRole("button", { name: panel, exact: true })).toHaveAttribute("aria-pressed", "true");
      if (scenario === "검증 불완전") {
        await expect(summary).toContainText("최종 검사: 미실행 2개");
        await expect(summary).not.toContainText("0/2 통과");
        await expect(target.getByRole("row").filter({ hasText: "최종" })).toHaveCount(2);
        await expect(target.getByRole("row").filter({ hasText: "최종" }).filter({ hasText: "NOT RUN미실행" })).toHaveCount(2);
      }
      // A repeat action must restore the evidence panel after manual exploration.
      await demo.getByRole("button", { name: "Diff", exact: true }).click();
      await actionButton.click();
      await expect(target).toBeFocused();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    }
    await summary.screenshot({ path: `${output}/${viewport.width === 375 ? "mobile" : "desktop"}-evidence-action.png` });
    await demo.getByRole("region", { name: "Checks 근거", exact: true }).screenshot({ path: `${output}/${viewport.width === 375 ? "mobile" : "desktop"}-check-states.png` });
  }
});
