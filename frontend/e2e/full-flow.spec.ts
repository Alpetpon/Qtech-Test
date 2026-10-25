import { expect, type Page, test } from "@playwright/test";

import { fresh } from "./stack";

// Запуск занимает чат c1 на 14 дней, а «Применить» обновляет прайс: каждый прогон — на чистой базе
test.beforeEach(fresh);

function nordpack(page: Page) {
  return page.getByRole("article").filter({ has: page.getByRole("heading", { name: "NordPack", exact: true }) });
}

test("рассылка от запуска до «Применить» на перемотанных часах", async ({ page, context }) => {
  await test.step("«Новые без движения»: сообщение в Kestrel Group уходит сразу", async () => {
    await page.goto("/");
    await page.getByLabel("Новые без движения").check();
    await page.getByRole("button", { name: "Показать превью" }).click();
    await expect(page.getByText("Чат «Kestrel Group»")).toBeVisible();

    await page.getByRole("button", { name: "Запустить" }).click();
    await expect(page).toHaveURL(/\/campaigns\/\d+$/);
    await expect(page.getByText("Статус: отправка завершена")).toBeVisible();
  });

  await test.step("превью «Активные, прайс устарел» и запуск", async () => {
    await page.goto("/");
    await page.getByLabel("Активные, прайс устарел").check();
    await page.getByRole("button", { name: "Показать превью" }).click();

    await expect(page.getByText("Чатов в рассылке: 1,")).toBeVisible();
    await expect(page.getByText("Чат «NordPack x Us»")).toBeVisible();
    const excluded = page.getByRole("listitem");
    await expect(excluded.filter({ hasText: "Orbis Trade" })).toContainText("писали 7 дней назад");
    await expect(excluded.filter({ hasText: "Velta Textile" })).toContainText("чат выключен");

    await page.getByRole("button", { name: "Запустить" }).click();
    await expect(page).toHaveURL(/\/campaigns\/\d+$/);
  });

  await test.step("сообщение NordPack ждёт паузу 1–3 минуты, «+1 день» — ушло, и ответ уже пришёл", async () => {
    await expect(page.getByText("Статус: идёт отправка")).toBeVisible();
    await expect(page.getByText("Отправлено за последние сутки: 1 из 100")).toBeVisible();

    await page.getByRole("button", { name: "+1 день" }).click();
    await expect(page.getByText("Статус: отправка завершена")).toBeVisible();
    // Очередь догоняет промотанные сутки: сообщение ушло через 1–3 минуты по часам после Kestrel Group,
    // а не в момент перемотки, поэтому ответ NordPack через 2 часа уже разобран
    await expect(page.getByRole("row").filter({ hasText: "NordPack" })).toContainText("предложение");
  });

  await test.step("отчёт: одно предложение из двух сообщений NordPack", async () => {
    await page.getByRole("link", { name: "Отчёт →" }).click();
    const offer = nordpack(page);
    await expect(offer.getByLabel("Товар")).toHaveValue("Коробки 40x30x30");
    await expect(offer.getByLabel("Цена")).toHaveValue("0.42");
    await expect(offer.getByLabel("Валюта")).toHaveValue("EUR");
    await expect(offer.getByLabel("Мин. партия")).toHaveValue("5000");
    await expect(offer.getByLabel("Срок поставки, дней")).toHaveValue("10");
    await expect(offer.getByLabel("Регионы")).toHaveValue("EU");
    await expect(offer).toContainText("прайс от 10.07.2026");
  });

  await test.step("правка цены и «Применить»; во второй вкладке — «уже применено»", async () => {
    // Вкладка открыта до применения: кнопка «Применить» в ней ещё есть
    const stale = await context.newPage();
    await stale.goto(page.url());
    await expect(nordpack(stale).getByLabel("Цена")).toHaveValue("0.42");

    const offer = nordpack(page);
    await offer.getByLabel("Цена").fill("0.40");
    await offer.getByRole("button", { name: "Применить" }).click();
    await expect(offer.getByRole("status")).toHaveText(
      "Применено: прайс поставщика обновлён — Коробки 40x30x30, 0.40 EUR.",
    );
    await expect(offer.getByRole("button", { name: "Применить" })).toHaveCount(0);
    await expect(offer.getByText("0.40 EUR", { exact: true })).toBeVisible();
    await expect(offer).toContainText("прайс от 02.11.2026");

    await nordpack(stale).getByRole("button", { name: "Применить" }).click();
    await expect(nordpack(stale).getByRole("status")).toHaveText(
      "Уже применено: это предложение уже в прайсе поставщика, второй раз оно не применяется.",
    );
  });
});

test("пустой менеджер вписывают в превью, и рассылка запускается", async ({ page }) => {
  // Через 7 дней в чат Orbis Trade снова можно писать, а менеджера у него в данных нет
  await expect(await page.request.post("/api/clock/advance", { data: { by: "P7D" } })).toBeOK();
  await page.goto("/");
  await page.getByLabel("Активные, прайс устарел").check();
  await page.getByRole("button", { name: "Показать превью" }).click();
  await expect(page.getByText("У Orbis Trade пустой менеджер")).toBeVisible();
  await expect(page.getByRole("button", { name: "Запустить" })).toBeDisabled();

  await page.getByLabel("Менеджер Orbis Trade").fill("Игорь");
  await page.getByRole("button", { name: "Сохранить" }).click();
  await expect(page.getByText("Запуск заблокирован:")).toHaveCount(0);
  await expect(page.getByRole("listitem").filter({ hasText: "Чат «Orbis / Us»" })).toContainText("С уважением, Игорь");

  await page.getByRole("button", { name: "Запустить" }).click();
  await expect(page).toHaveURL(/\/campaigns\/\d+$/);
});
