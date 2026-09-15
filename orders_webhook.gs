/**
 * Google Apps Script для бота «Ялинки від Колі».
 *
 * Робить дві речі:
 *  1) приймає замовлення й дропшиперів від бота (doPost) і пише їх у таблицю;
 *  2) віддає боту дані назад (doGet) — щоб «Мої замовлення» і список схвалених
 *     дропшиперів не зникали при перезапуску хостингу.
 *
 * ЯК ОНОВИТИ (5 хвилин):
 *  1. Відкрийте таблицю замовлень → Розширення → Apps Script
 *  2. Замініть увесь код на цей, збережіть (💾)
 *  3. Деплой → Керувати розгортаннями → олівець ✏️ → Версія: Нова версія → Розгорнути
 *     (URL залишиться тим самим)
 *
 * СЕКРЕТ (обов'язково, інакше скрипт відхиляє всі запити):
 *  1. Придумайте довгий випадковий рядок (напр. `python -c "import secrets; print(secrets.token_urlsafe(32))"`)
 *  2. Apps Script → ⚙️ Налаштування проєкту → Властивості скрипту → Додати:
 *     SHEETS_API_SECRET = цей рядок
 *  3. У Railway → Variables додайте SHEETS_API_SECRET з тим самим значенням
 */

var ORDERS_SHEET = "Замовлення";
var DROPS_SHEET = "Дропшипери";
var ALIAS_SHEET = "Назви товарів";

var ORDER_HEADERS = ["№", "Дата", "Джерело", "ID дропшипера", "Дропшипер",
  "Артикул", "Товар", "Розмір", "К-сть", "Дроп-ціна", "Оплата",
  "ПІБ отримувача", "Телефон", "Місто", "Відділення / адреса", "ТТН", "Статус",
  "Коментар", "Ціна продажу", "Передплата", "При отриманні", "Доставка",
  "На рахунок", "Чек"];

var ORDER_KEYS = ["order_no", "created_at", "source", "dropshipper_id", "dropshipper",
  "article", "product", "size", "qty", "price_drop", "payment",
  "recipient_fio", "recipient_phone", "city", "warehouse", "ttn", "status",
  "comment", "sale_price", "prepaid", "cod_amount", "delivery",
  "due_amount", "payment_proof"];

var DROP_HEADERS = ["Telegram ID", "Ім'я", "Username", "Статус", "Дата", "Хто схвалив"];

// Власні назви товарів: дропшипер бачить свою назву, у ТТН лишається наша.
// Ключ — модель («Грандія», діє на всі розміри) або артикул («Cr6G-220»).
var ALIAS_HEADERS = ["Telegram ID", "Артикул або модель", "Своя назва"];

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// Секрет зберігається у Властивостях скрипту, а не в коді.
// Порівняння за сталий час, щоб не підбирали секрет по символу.
function authorized_(given) {
  var secret = PropertiesService.getScriptProperties().getProperty("SHEETS_API_SECRET");
  if (!secret || typeof given !== "string" || given.length !== secret.length) return false;
  var diff = 0;
  for (var i = 0; i < secret.length; i++) {
    diff |= secret.charCodeAt(i) ^ given.charCodeAt(i);
  }
  return diff === 0;
}

function unauthorized_() {
  return json_({ ok: false, error: "unauthorized" });
}

function sheet_(name, headers) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(name);
  if (!sh) {
    // перший аркуш без назви використовуємо під замовлення
    var first = ss.getSheets()[0];
    if (name === ORDERS_SHEET && first.getLastRow() > 0 &&
        ss.getSheetByName(DROPS_SHEET) === null && ss.getSheets().length === 1) {
      first.setName(ORDERS_SHEET);
      sh = first;
    } else {
      sh = ss.insertSheet(name);
    }
  }
  if (sh.getLastRow() === 0) {
    sh.appendRow(headers);
    sh.getRange(1, 1, 1, headers.length).setFontWeight("bold");
    sh.setFrozenRows(1);
  }
  return sh;
}

function doPost(e) {
  var data;
  try {
    data = JSON.parse(e.postData.contents);
  } catch (err) {
    return unauthorized_();
  }
  if (!data || !authorized_(data.secret)) return unauthorized_();
  delete data.secret;
  var type = data.type || "order";

  if (type === "dropshipper") {
    var sh = sheet_(DROPS_SHEET, DROP_HEADERS);
    var ids = sh.getRange(1, 1, Math.max(sh.getLastRow(), 1), 1).getValues();
    var row = -1;
    for (var i = 1; i < ids.length; i++) {
      if (String(ids[i][0]) === String(data.tg_id)) { row = i + 1; break; }
    }
    var values = [String(data.tg_id), data.name || "", data.username || "",
      data.status || "схвалений", new Date(), data.approved_by || ""];
    if (row > 0) {
      sh.getRange(row, 1, 1, values.length).setValues([values]);
    } else {
      sh.appendRow(values);
    }
    return json_({ ok: true });
  }

  if (type === "alias") {
    var ash = sheet_(ALIAS_SHEET, ALIAS_HEADERS);
    var last = ash.getLastRow();
    var found = -1;
    if (last > 1) {
      var vals = ash.getRange(2, 1, last - 1, 2).getValues();
      for (var a = 0; a < vals.length; a++) {
        if (String(vals[a][0]) === String(data.tg_id) &&
            String(vals[a][1]).trim().toLowerCase() ===
            String(data.key).trim().toLowerCase()) {
          found = a + 2; break;
        }
      }
    }
    if (!data.name) {                       // порожня назва = прибрати
      if (found > 0) ash.deleteRow(found);
      return json_({ ok: true, removed: found > 0 });
    }
    var arow = [String(data.tg_id), data.key, data.name];
    if (found > 0) {
      ash.getRange(found, 1, 1, arow.length).setValues([arow]);
    } else {
      ash.appendRow(arow);
    }
    return json_({ ok: true });
  }

  var osh = sheet_(ORDERS_SHEET, ORDER_HEADERS);
  var orow = ORDER_KEYS.map(function (k) {
    return data[k] !== undefined ? data[k] : "";
  });
  osh.appendRow(orow);
  return json_({ ok: true });
}

function doGet(e) {
  if (!authorized_(e && e.parameter && e.parameter.secret)) return unauthorized_();
  var what = (e && e.parameter && e.parameter.what) || "";

  if (what === "dropshippers") {
    var sh = sheet_(DROPS_SHEET, DROP_HEADERS);
    var last = sh.getLastRow();
    var rows = [];
    if (last > 1) {
      var vals = sh.getRange(2, 1, last - 1, DROP_HEADERS.length).getValues();
      for (var i = 0; i < vals.length; i++) {
        if (!vals[i][0]) continue;
        rows.push({
          tg_id: String(vals[i][0]), name: vals[i][1], username: vals[i][2],
          status: vals[i][3] || "схвалений"
        });
      }
    }
    return json_({ rows: rows });
  }

  if (what === "orders") {
    var id = String((e.parameter && e.parameter.id) || "");
    var limit = parseInt((e.parameter && e.parameter.limit) || "10", 10) || 10;
    var osh = sheet_(ORDERS_SHEET, ORDER_HEADERS);
    var lastRow = osh.getLastRow();
    var out = [];
    if (lastRow > 1) {
      var data = osh.getRange(2, 1, lastRow - 1, ORDER_HEADERS.length).getValues();
      for (var j = data.length - 1; j >= 0 && out.length < limit; j--) {
        if (id && String(data[j][3]) !== id) continue;
        var o = {};
        for (var k = 0; k < ORDER_KEYS.length; k++) {
          var v = data[j][k];
          o[ORDER_KEYS[k]] = (v instanceof Date)
            ? Utilities.formatDate(v, Session.getScriptTimeZone(), "dd.MM.yyyy HH:mm")
            : v;
        }
        out.push(o);
      }
    }
    return json_({ rows: out });
  }

  if (what === "aliases") {
    var ash = sheet_(ALIAS_SHEET, ALIAS_HEADERS);
    var lastA = ash.getLastRow();
    var arows = [];
    if (lastA > 1) {
      var av = ash.getRange(2, 1, lastA - 1, 3).getValues();
      for (var q = 0; q < av.length; q++) {
        if (!av[q][0] || !av[q][1] || !av[q][2]) continue;
        arows.push({ tg_id: String(av[q][0]), key: String(av[q][1]),
                     name: String(av[q][2]) });
      }
    }
    return json_({ rows: arows });
  }

  if (what === "maxorder") {
    var s = sheet_(ORDERS_SHEET, ORDER_HEADERS);
    var n = s.getLastRow();
    var max = 0;
    if (n > 1) {
      var col = s.getRange(2, 1, n - 1, 1).getValues();
      for (var m = 0; m < col.length; m++) {
        var num = parseInt(col[m][0], 10);
        if (!isNaN(num) && num > max) max = num;
      }
    }
    return json_({ max: max });
  }

  return json_({ ok: true, hint: "what=dropshippers|orders|aliases|maxorder" });
}
