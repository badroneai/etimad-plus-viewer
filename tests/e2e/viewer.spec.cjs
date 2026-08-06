const { expect, test } = require("@playwright/test");

test("keyboard users can open, contain, and close tender details", async ({ page }) => {
  await page.goto("/#explorer");

  const explorerLink = page.locator('.nav-links-desktop [data-nav="explorer"]');
  await expect(explorerLink).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#tableWrap")).toHaveAttribute("aria-busy", "false");

  const tender = page.locator(".tender-link").first();
  await expect(tender).toBeVisible();
  await tender.focus();
  await tender.press("Enter");

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(page.locator(".app-bar")).toHaveJSProperty("inert", true);
  await expect(page.locator("#detailClose")).toBeFocused();

  await page.keyboard.press("Shift+Tab");
  await expect(page.locator("#copyRef")).toBeFocused();
  await page.keyboard.press("Escape");

  await expect(dialog).toBeHidden();
  await expect(page.locator(".app-bar")).toHaveJSProperty("inert", false);
  await expect(tender).toBeFocused();
});

test("mobile tender cards activate with the keyboard", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#explorer");

  const card = page.locator('.m-card[role="button"]').first();
  await expect(card).toBeVisible();
  await card.focus();
  await card.press("Enter");

  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.locator("#detailClose")).toBeFocused();
});

test("dataset failures remain visible and can be retried", async ({ page }) => {
  let failed = false;
  await page.route("**/data/open.json*", async (route) => {
    if (!failed) {
      failed = true;
      await route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
      return;
    }
    await route.continue();
  });

  await page.goto("/#explorer");
  const error = page.locator("#explorerError");
  await expect(error).toBeVisible();
  await expect(error).toContainText("تعذر تحميل مجموعة");

  await page.locator("#retryDataset").click();
  await expect(error).toBeHidden();
  await expect(page.locator(".tender-link").first()).toBeVisible();
});

test("bootstrap failures expose a working retry action", async ({ page }) => {
  let failed = false;
  await page.route("**/data/manifest.json*", async (route) => {
    if (!failed) {
      failed = true;
      await route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
      return;
    }
    await route.continue();
  });

  await page.goto("/");
  const error = page.locator("#appError");
  await expect(error).toBeVisible();
  await expect(error).toContainText("تعذر الإقلاع");

  await page.locator("#retryBoot").click();
  await expect(error).toBeHidden();
  await expect(page.locator("#catalog .catalog-card").first()).toBeAttached();
});

test("reduced-motion preference disables decorative motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");

  const duration = await page.locator(".hero-stage").evaluate(
    (node) => getComputedStyle(node).animationDuration
  );
  expect(Number.parseFloat(duration)).toBeLessThanOrEqual(0.00001);
});
