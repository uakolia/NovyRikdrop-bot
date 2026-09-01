/**
 * Google Apps Script: приймає замовлення від бота і додає рядок у таблицю.
 *
 * Як встановити (5 хвилин):
 * 1. Створіть нову Google Таблицю "Замовлення Ялинкар"
 * 2. Розширення → Apps Script, вставте цей код
 * 3. Деплой → Новий деплой → тип "Веб-додаток"
 *    - Виконувати від імені: мене
 *    - Доступ: усі (Anyone)
 * 4. Скопіюйте URL веб-додатка у .env бота: SHEET_WEBHOOK_URL=...
 */
var HEADERS = ["№", "Дата", "Джерело", "ID дропшипера", "Дропшипер",
  "Артикул", "Товар", "Розмір", "К-сть", "Дроп-ціна", "Оплата",
  "ПІБ отримувача", "Телефон", "Місто", "Відділення", "ТТН", "Статус", "Коментар",
  "Ціна продажу", "Передплата", "При отриманні"];

var KEYS = ["order_no", "created_at", "source", "dropshipper_id", "dropshipper",
  "article", "product", "size", "qty", "price_drop", "payment",
  "recipient_fio", "recipient_phone", "city", "warehouse", "ttn", "status", "comment",
  "sale_price", "prepaid", "cod_amount"];

function doPost(e) {
  var data = JSON.parse(e.postData.contents);
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(HEADERS);
    sheet.getRange(1, 1, 1, HEADERS.length).setFontWeight("bold");
  }
  var row = KEYS.map(function (k) { return data[k] !== undefined ? data[k] : ""; });
  sheet.appendRow(row);
  return ContentService.createTextOutput(JSON.stringify({ ok: true }))
    .setMimeType(ContentService.MimeType.JSON);
}
