/**
 * Дублювання замовлень у власну таблицю дропшипера.
 *
 * Куди писати — у Властивостях скрипту (⚙️ Налаштування проєкту):
 *   DROPSHIPPER_EXPORT_SHEETS_JSON =
 *     {"545995767":"1o0EHLxs0gsdGkMzOF9uWhnigHm3bUGdJRD8XkjrTtFw"}
 * Ключ — Telegram ID дропшипера, значення — ID його таблиці.
 * Доступ: обліковий запис, від імені якого працює скрипт, має бути
 * редактором тієї таблиці, інакше openById кине помилку (її ми ловимо).
 *
 * Правило: збій експорту НІКОЛИ не валить основне замовлення. Усі виклики
 * обгорнуті try/catch, помилка йде в Logger, бот отримує ok.
 *
 * Назва товару в експорті — «Персональна назва» з «Залишків дропшиперів»
 * (та сама, що бот показує в боті й пише в ТТН), далі «Назви товарів»,
 * далі заводська назва з прайсу.
 */

var EXPORT_PROP = "DROPSHIPPER_EXPORT_SHEETS_JSON";
var EXPORT_SHEET = "Замовлення";

var EXPORT_HEADERS = ["Номер замовлення", "Дата", "Назва товару", "Артикул",
  "Кількість", "Дроп-ціна", "Сума", "ПІБ отримувача", "Телефон отримувача",
  "Місто", "Відділення / адреса", "ТТН", "Статус замовлення",
  "Статус Nova Poshta", "Коментар"];

var EXPORT_ORDER_NO_COL = 1;
var EXPORT_TTN_COL = 12;
var EXPORT_NP_STATUS_COL = 14;

/** {tg_id: sheetId} із Властивостей скрипту; {} якщо не налаштовано. */
function exportTargets_() {
  var raw = PropertiesService.getScriptProperties().getProperty(EXPORT_PROP);
  if (!raw) return {};
  try {
    return JSON.parse(raw) || {};
  } catch (err) {
    Logger.log("DROPSHIPPER_EXPORT_SHEETS_JSON не читається як JSON: " + err);
    return {};
  }
}

/** Аркуш «Замовлення» в таблиці дропшипера (створимо, якщо його немає). */
function exportSheetFor_(tgId) {
  var id = exportTargets_()[String(tgId).trim()];
  if (!id) return null;
  var ss = SpreadsheetApp.openById(id);
  var sh = ss.getSheetByName(EXPORT_SHEET) || ss.insertSheet(EXPORT_SHEET);
  if (sh.getLastRow() === 0) {
    sh.appendRow(EXPORT_HEADERS);
    sh.getRange(1, 1, 1, EXPORT_HEADERS.length).setFontWeight("bold");
    sh.setFrozenRows(1);
  } else {
    // дописати лише відсутні підписи; ширину діапазону рахуємо по масиву,
    // інакше setValues падає на невідповідності розмірів
    var row = sh.getRange(1, 1, 1, EXPORT_HEADERS.length).getValues()[0];
    for (var i = 0; i < EXPORT_HEADERS.length; i++) {
      if (String(row[i] || "").trim() === "") {
        sh.getRange(1, i + 1).setValue(EXPORT_HEADERS[i]).setFontWeight("bold");
      }
    }
  }
  return sh;
}

/** Значення поля: спершу англійський ключ бота, потім підпис колонки. */
function exportField_(order, key, header) {
  if (order[key] !== undefined && order[key] !== null && order[key] !== "") {
    return order[key];
  }
  if (order[header] !== undefined && order[header] !== null) {
    return order[header];
  }
  return "";
}

/** Telegram ID автора замовлення — бот шле dropshipper_id. */
function exportTgId_(order) {
  return trim_(order.dropshipper_id || order["ID дропшипера"] ||
               order.telegram_id || order.tg_id || "");
}

/**
 * Назва товару очима дропшипера: «Залишки дропшиперів» → «Назви товарів» →
 * назва з прайсу, яку прислав бот.
 */
function dropshipperExportName_(tgId, article, fallback) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var want = canonArticle_(article);
  var tg = String(tgId).trim();

  var stock = ss.getSheetByName(STOCK_SHEET);
  if (stock && stock.getLastRow() > 1) {
    var sv = stock.getRange(2, 1, stock.getLastRow() - 1,
                            Math.max(stock.getLastColumn(), STOCK_COLS.name)).getValues();
    for (var i = 0; i < sv.length; i++) {
      if (trim_(sv[i][STOCK_COLS.tg_id - 1]) === tg &&
          canonArticle_(sv[i][STOCK_COLS.article - 1]) === want) {
        var own = trim_(sv[i][STOCK_COLS.name - 1]);
        if (own) return own;
        break;
      }
    }
  }

  var alias = ss.getSheetByName(ALIAS_SHEET);
  if (alias && alias.getLastRow() > 1) {
    var av = alias.getRange(2, 1, alias.getLastRow() - 1, 3).getValues();
    for (var a = 0; a < av.length; a++) {
      if (trim_(av[a][0]) === tg && canonArticle_(av[a][1]) === want) {
        var name = trim_(av[a][2]);
        if (name) return name;
      }
    }
  }
  return fallback || "";
}

/** Рядок для таблиці дропшипера у порядку EXPORT_HEADERS. */
function dropshipperExportRow_(order) {
  var tgId = exportTgId_(order);
  var article = exportField_(order, "article", "Артикул");
  var qty = exportField_(order, "qty", "К-сть");
  var price = exportField_(order, "price_drop", "Дроп-ціна");
  var name = dropshipperExportName_(tgId, article,
                                    exportField_(order, "product", "Товар"));
  var sum = "";
  if (price !== "" && qty !== "") sum = num_(price) * num_(qty);

  return [
    exportField_(order, "order_no", "№"),
    exportField_(order, "created_at", "Дата"),
    name,
    article,
    qty,
    price,
    sum,
    exportField_(order, "recipient_fio", "ПІБ отримувача"),
    exportField_(order, "recipient_phone", "Телефон"),
    exportField_(order, "city", "Місто"),
    exportField_(order, "warehouse", "Відділення / адреса"),
    exportField_(order, "ttn", "ТТН"),
    exportField_(order, "status", "Статус"),
    exportField_(order, "np_status", "Статус Nova Poshta"),
    exportField_(order, "comment", "Коментар")
  ];
}

/** Номер рядка з таким номером замовлення або -1. */
function exportFindRow_(sh, orderNo) {
  var last = sh.getLastRow();
  if (last < 2 || orderNo === "" || orderNo === null) return -1;
  var want = String(orderNo).trim();
  var values = sh.getRange(2, EXPORT_ORDER_NO_COL, last - 1, 1).getValues();
  for (var i = 0; i < values.length; i++) {
    if (String(values[i][0] || "").trim() === want) return i + 2;
  }
  return -1;
}

/**
 * Додати замовлення в таблицю дропшипера. Повертає true, якщо записали.
 * Викликається з doPost; винятки назовні не випускає.
 */
function exportDropshipperOrder(order) {
  try {
    var tgId = exportTgId_(order);
    if (!tgId) return false;
    var sh = exportSheetFor_(tgId);
    if (!sh) return false;                       // для цього ID експорт не налаштовано
    var row = dropshipperExportRow_(order);
    var existing = exportFindRow_(sh, row[EXPORT_ORDER_NO_COL - 1]);
    if (existing > 0) {
      sh.getRange(existing, 1, 1, row.length).setValues([row]);
    } else {
      sh.appendRow(row);
    }
    return true;
  } catch (err) {
    Logger.log("Експорт замовлення не вдався: " + err);
    return false;
  }
}

/**
 * Оновити ТТН і статус НП у таблиці дропшипера.
 * info = {row: рядок головної таблиці, ttn, np_status}.
 */
function updateDropshipperOrderStatus_(mainSheet, info) {
  var vals = mainSheet.getRange(info.row, 1, 1, ORDER_HEADERS.length).getValues()[0];
  var tgId = trim_(vals[ORDER_KEYS.indexOf("dropshipper_id")]);
  if (!tgId) return false;
  var sh = exportSheetFor_(tgId);
  if (!sh) return false;
  var orderNo = vals[ORDER_KEYS.indexOf("order_no")];
  var row = exportFindRow_(sh, orderNo);
  if (row < 0) {
    // рядка ще немає (замовлення створене до вмикання експорту) — додамо цілком
    var order = {};
    for (var k = 0; k < ORDER_KEYS.length; k++) order[ORDER_KEYS[k]] = vals[k];
    return exportDropshipperOrder(order);
  }
  sh.getRange(row, EXPORT_TTN_COL).setValue(info.ttn || "");
  sh.getRange(row, EXPORT_NP_STATUS_COL).setValue(info.np_status || "");
  return true;
}

/**
 * Разова синхронізація: перенести в таблиці дропшиперів усе, що вже є в
 * головному аркуші «Замовлення». Запускати вручну з редактора Apps Script.
 */
function syncDropshipperOrdersFromAll() {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(ORDERS_SHEET);
  if (!sh || sh.getLastRow() < 2) return "немає замовлень";

  // рядок таблиці → об'єкт замовлення за шапкою, а не за порядком
  var width = Math.max(sh.getLastColumn(), ORDER_HEADERS.length);
  var header = sh.getRange(1, 1, 1, width).getValues()[0];
  var idx = {};
  for (var h = 0; h < header.length; h++) {
    var title = trim_(header[h]);
    if (title) idx[title] = h;
  }
  var values = sh.getRange(2, 1, sh.getLastRow() - 1, width).getValues();
  var done = 0, skipped = 0;
  for (var r = 0; r < values.length; r++) {
    var row = values[r];
    var order = {};
    for (var c = 0; c < ORDER_HEADERS.length; c++) {
      var col = idx[ORDER_HEADERS[c]];
      order[ORDER_KEYS[c]] = (col === undefined) ? "" : row[col];
    }
    if (!trim_(order.order_no)) { skipped++; continue; }
    if (exportDropshipperOrder(order)) { done++; } else { skipped++; }
  }
  var msg = "перенесено: " + done + ", пропущено: " + skipped;
  Logger.log(msg);
  return msg;
}
