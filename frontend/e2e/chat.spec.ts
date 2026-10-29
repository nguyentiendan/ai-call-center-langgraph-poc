import { expect, test } from "@playwright/test";

test("billing flow: create -> inquiry -> customer ID -> PIN -> grounded response", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "新しい問い合わせ" }).click();
  await expect(page.getByText("Sakura Mobile サポートです")).toBeVisible();

  const composer = page.getByPlaceholder("メッセージを入力（PINは書かないでください）");
  await composer.fill("今月の携帯料金が先月より高いのですが。");
  await page.getByRole("button", { name: "送信" }).click();
  await expect(page.getByText("デモ用のお客様IDを入力してください")).toBeVisible();

  await composer.fill("CUST-1001");
  await page.getByRole("button", { name: "送信" }).click();
  await expect(page.getByText("デモ用PINを専用入力欄")).toBeVisible();

  // PIN goes only into the dedicated password field, never the chat composer.
  await page.locator('input[type="password"]').fill("1234");
  await page.getByRole("button", { name: "確認する" }).click();

  await expect(page.getByText(/8,800円/)).toBeVisible();
  await expect(page.getByText(/6,600円/)).toBeVisible();
  await expect(page.getByText(/差額は2,200円/)).toBeVisible();
  // Chat history must not contain the PIN as a user message.
  await expect(page.locator(".bubble.user", { hasText: "1234" })).toHaveCount(0);
});
