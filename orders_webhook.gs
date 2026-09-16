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
var STOCK_SHEET = "Залишки дропшиперів";

var ORDER_HEADERS = ["№", "Дата", "Джерело", "ID дропшипера", "Дропшипер",
  "Артикул", "Товар", "Розмір", "К-сть", "Дроп-ціна", "Оплата",
  "ПІБ отримувача", "Телефон", "Місто", "Відділення / адреса", "ТТН", "Статус",
  "Коментар", "Ціна продажу", "Передплата", "При отриманні", "Доставка",
  "На рахунок", "Чек",
  // нові колонки додаються ЛИШЕ в кінець, інакше поїдуть наявні дані
  "Статус Nova Poshta", "Оновлено"];

var ORDER_KEYS = ["order_no", "created_at", "source", "dropshipper_id", "dropshipper",
  "article", "product", "size", "qty", "price_drop", "payment",
  "recipient_fio", "recipient_phone", "city", "warehouse", "ttn", "status",
  "comment", "sale_price", "prepaid", "cod_amount", "delivery",
  "due_amount", "payment_proof",
  "np_status", "updated_at"];

var DROP_HEADERS = ["Telegram ID", "Ім'я", "Username", "Статус", "Дата", "Хто схвалив",
  "Тариф"];

// «Тариф» — необов'язкова колонка: drop1/drop2/drop3 (можна писати «Дроп 2»).
// Порожньо = загальний тариф бота (PRICE_TIER). Заповнюється руками в таблиці,
// бот її лише читає й ніколи не затирає.
var DROP_TIER_COL = 7;

// «Залишки дропшиперів» веде менеджер вручну; бот тільки читає.
// Колонка «Дроп-ціна» — довідкова: ціни бот бере з прайсу за тарифом.
var STOCK_COLS = { tg_id: 1, dropshipper: 2, article: 3, name: 4, price: 5,
  allocated: 6, reserved: 7, delivered: 8, available: 9, updated: 10 };

// Колонка I «Доступно» — формула =F-G-H. Скрипт її НІКОЛИ не пише: доступне
// рахуємо самі з F, G, H (свіжозаписане значення формули в тому ж виконанні
// ще не перерахувалося б). checkAvailableFormulas() перевіряє, що формули на місці.
// номери колонок рахуємо з ORDER_KEYS, щоб не з'їхали при зміні схеми
function orderCol_(key) {
  return ORDER_KEYS.indexOf(key) + 1;
}

var ORDER_TTN_COL = orderCol_("ttn");
var ORDER_NP_STATUS_COL = orderCol_("np_status");
var ORDER_UPDATED_COL = orderCol_("updated_at");

var LOCK_WAIT_MS = 20000;

// Кінцеві статуси НП: такі рядки бот більше не опитує (остаточну перевірку
// робить np_tracking.py — тут лише щоб не ганяти зайве).
// шукаємо ВСЕРЕДИНІ тексту: НП пише «Відправлення отримано», а не «Отримано»
// «Номер не знайдено» тут НЕМАЄ свідомо: свіжа ТТН може так відповідати, поки
// НП її не проіндексувала, тож рядок треба опитати ще раз (див. np_tracking.py).
var NP_FINAL = ["отримано", "одержано", "відмов", "поверн", "видалено",
  "припинено зберігання", "резерв знято"];

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

// Дописати відсутні підписи в шапку (для таблиць, створених до оновлення).
// Дивимося саме на перший рядок: getLastColumn() показує найширший рядок даних,
// тож у таблиці з уже заповненим «Тарифом» підпис інакше лишився б порожнім.
function ensureHeaders_(sh, headers) {
  if (sh.getLastRow() === 0) return;                 // порожній аркуш — шапку вже поставив sheet_
  var row = sh.getRange(1, 1, 1, headers.length).getValues()[0];
  for (var i = 0; i < headers.length; i++) {
    if (String(row[i] || "").trim() === "") {
      sh.getRange(1, i + 1).setValue(headers[i]).setFontWeight("bold");
    }
  }
}

// Дзеркало article_key.canon() з бота: у прайсі трапляються кириличні
// двійники латинських літер («Cr6сustom-220» з кириличною с). Ключ — лише
// для пошуку; у комірках лишається оригінальний рядок прайсу.
var HOMOGLYPHS = {
  "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
  "Р": "P", "С": "C", "Т": "T", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S",
  "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
  "і": "i", "ј": "j", "ѕ": "s"
};

function canonArticle_(v) {
  var s = String(v === null || v === undefined ? "" : v);
  if (s.normalize) s = s.normalize("NFKC");
  var out = "";
  for (var i = 0; i < s.length; i++) {
    var ch = s.charAt(i);
    out += HOMOGLYPHS[ch] || ch;
  }
  return out.toUpperCase().trim();
}

function num_(v) {
  var n = Number(String(v === null || v === undefined ? "" : v)
    .replace(/[\s\u00a0]/g, "").replace(",", "."));
  return isNaN(n) ? 0 : n;
}

function trim_(v) {
  return (v === null || v === undefined) ? "" : String(v).trim();
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
    ensureHeaders_(sh, DROP_HEADERS);
    var ids = sh.getRange(1, 1, Math.max(sh.getLastRow(), 1), 1).getValues();
    var row = -1;
    for (var i = 1; i < ids.length; i++) {
      if (String(ids[i][0]) === String(data.tg_id)) { row = i + 1; break; }
    }
    // тариф проставляє менеджер у таблиці — перезапис рядка його не чіпає
    var tier = row > 0 ? sh.getRange(row, DROP_TIER_COL).getValue() : "";
    var values = [String(data.tg_id), data.name || "", data.username || "",
      data.status || "схвалений", new Date(), data.approved_by || "", tier];
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

  if (type === "reserve" || type === "receive" || type === "release") {
    return stockOp_(type, data);
  }

  if (type === "ttn_status") {
    return ttnStatus_(data);
  }

  var osh = sheet_(ORDERS_SHEET, ORDER_HEADERS);
  ensureHeaders_(osh, ORDER_HEADERS);
  var orow = ORDER_KEYS.map(function (k) {
    return data[k] !== undefined ? data[k] : "";
  });
  osh.appendRow(orow);
  // дублюємо в таблицю дропшипера; збій експорту не має валити замовлення
  try {
    exportDropshipperOrder(data);
  } catch (err) {
    Logger.log("Експорт замовлення не вдався: " + err);
  }
  return json_({ ok: true });
}

/**
 * Резерв / видача / повернення залишку. Все — під замком скрипта: без нього
 * два одночасні замовлення могли б обидва пройти перевірку доступності.
 *
 *  reserve — G += qty (перевіряємо, що вистачає)
 *  receive — G -= qty, H += qty (клієнт забрав)
 *  release — G -= qty (замовлення скасоване/видалене)
 *
 * Колонку I не чіпаємо ніколи: там формула =F-G-H.
 */
function stockOp_(type, data) {
  var qty = num_(data.qty);
  if (!(qty > 0)) return json_({ ok: false, error: "qty must be > 0" });

  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(LOCK_WAIT_MS);
  } catch (err) {
    return json_({ ok: false, error: "sheet busy" });
  }
  try {
    var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(STOCK_SHEET);
    if (!sh || sh.getLastRow() < 2) {
      return json_({ ok: false, error: "no stock sheet" });
    }
    var width = Math.max(sh.getLastColumn(), STOCK_COLS.updated);
    var vals = sh.getRange(2, 1, sh.getLastRow() - 1, width).getValues();
    var want = canonArticle_(data.article);
    var tg = trim_(data.tg_id);
    var row = -1;
    for (var i = 0; i < vals.length; i++) {
      if (trim_(vals[i][STOCK_COLS.tg_id - 1]) === tg &&
          canonArticle_(vals[i][STOCK_COLS.article - 1]) === want) {
        row = i + 2; break;
      }
    }
    if (row < 0) return json_({ ok: false, error: "no stock row" });

    var v = vals[row - 2];
    var allocated = num_(v[STOCK_COLS.allocated - 1]);
    var reserved = num_(v[STOCK_COLS.reserved - 1]);
    var delivered = num_(v[STOCK_COLS.delivered - 1]);
    var available = allocated - reserved - delivered;

    if (type === "reserve") {
      if (qty > available) {
        return json_({ ok: false, error: "not enough", available: available,
                       requested: qty });
      }
      reserved += qty;
    } else if (type === "receive") {
      if (qty > reserved) {
        return json_({ ok: false, error: "not enough reserved",
                       reserved: reserved, requested: qty });
      }
      reserved -= qty;
      delivered += qty;
    } else {                                   // release
      if (qty > reserved) {
        return json_({ ok: false, error: "not enough reserved",
                       reserved: reserved, requested: qty });
      }
      reserved -= qty;
    }

    sh.getRange(row, STOCK_COLS.reserved).setValue(reserved);
    if (type === "receive") {
      sh.getRange(row, STOCK_COLS.delivered).setValue(delivered);
    }
    sh.getRange(row, STOCK_COLS.updated).setValue(new Date());
    SpreadsheetApp.flush();
    return json_({ ok: true, article: trim_(v[STOCK_COLS.article - 1]),
                   reserved: reserved, delivered: delivered,
                   available: allocated - reserved - delivered });
  } finally {
    lock.releaseLock();
  }
}

/**
 * Разова перевірка (запустити вручну в редакторі Apps Script): чи в колонці I
 * справді формула =F-G-H. Бот її не пише, але заповнити руками числом легко.
 */
function checkAvailableFormulas() {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(STOCK_SHEET);
  if (!sh || sh.getLastRow() < 2) return "немає аркуша «" + STOCK_SHEET + "»";
  var n = sh.getLastRow() - 1;
  var formulas = sh.getRange(2, STOCK_COLS.available, n, 1).getFormulas();
  var bad = [];
  for (var i = 0; i < n; i++) {
    var f = String(formulas[i][0] || "").replace(/\s/g, "").toUpperCase();
    if (!/^=[A-Z]*\d+-[A-Z]*\d+-[A-Z]*\d+$/.test(f)) bad.push(i + 2);
  }
  var msg = bad.length ? "рядки без формули =F-G-H: " + bad.join(", ")
                       : "усі " + n + " рядків мають формулу =F-G-H";
  Logger.log(msg);
  return msg;
}

/**
 * Пакетний запис статусів НП: updates = [{ttn, np_status}, ...].
 * Пишемо лише «Статус Nova Poshta» й «Оновлено» — решту колонок не чіпаємо.
 * Під замком, щоб не зіткнутися з доданням нового замовлення.
 */
function ttnStatus_(data) {
  var updates = data.updates || [];
  if (!updates.length) return json_({ ok: true, updated: 0 });

  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(LOCK_WAIT_MS);
  } catch (err) {
    return json_({ ok: false, error: "sheet busy" });
  }
  try {
    var sh = sheet_(ORDERS_SHEET, ORDER_HEADERS);
    ensureHeaders_(sh, ORDER_HEADERS);
    var last = sh.getLastRow();
    if (last < 2) return json_({ ok: true, updated: 0 });

    var ttns = sh.getRange(2, ORDER_TTN_COL, last - 1, 1).getValues();
    var byTtn = {};
    for (var i = 0; i < ttns.length; i++) {
      var t = trim_(ttns[i][0]);
      if (t) byTtn[t] = i + 2;
    }
    var now = new Date();
    var done = 0;
    var exported = [];
    for (var u = 0; u < updates.length; u++) {
      var row = byTtn[trim_(updates[u].ttn)];
      if (!row) continue;
      sh.getRange(row, ORDER_NP_STATUS_COL).setValue(updates[u].np_status || "");
      sh.getRange(row, ORDER_UPDATED_COL).setValue(now);
      done++;
      exported.push({ row: row, ttn: trim_(updates[u].ttn),
                      np_status: updates[u].np_status || "" });
    }
    SpreadsheetApp.flush();
    // у таблиці дропшипера оновлюємо ТТН і статус НП; збій експорту не має
    // зривати запис у головну таблицю
    for (var x = 0; x < exported.length; x++) {
      try {
        updateDropshipperOrderStatus_(sh, exported[x]);
      } catch (err) {
        Logger.log("export status failed for " + exported[x].ttn + ": " + err);
      }
    }
    return json_({ ok: true, updated: done });
  } finally {
    lock.releaseLock();
  }
}

function doGet(e) {
  if (!authorized_(e && e.parameter && e.parameter.secret)) return unauthorized_();
  var what = (e && e.parameter && e.parameter.what) || "";

  if (what === "dropshippers") {
    var sh = sheet_(DROPS_SHEET, DROP_HEADERS);
    ensureHeaders_(sh, DROP_HEADERS);
    var last = sh.getLastRow();
    var rows = [];
    if (last > 1) {
      var vals = sh.getRange(2, 1, last - 1, DROP_HEADERS.length).getValues();
      for (var i = 0; i < vals.length; i++) {
        if (!vals[i][0]) continue;
        rows.push({
          tg_id: trim_(vals[i][0]), name: vals[i][1], username: vals[i][2],
          status: vals[i][3] || "схвалений",
          tier: trim_(vals[i][DROP_TIER_COL - 1])
        });
      }
    }
    return json_({ rows: rows });
  }

  if (what === "stock") {
    // аркуш ведуть руками; якщо його ще немає — просто порожній список
    var ssh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(STOCK_SHEET);
    var srows = [];
    if (ssh && ssh.getLastRow() > 1) {
      var width = Math.max(ssh.getLastColumn(), STOCK_COLS.available);
      var sv = ssh.getRange(2, 1, ssh.getLastRow() - 1, width).getValues();
      for (var s = 0; s < sv.length; s++) {
        var art = trim_(sv[s][STOCK_COLS.article - 1]);
        if (!art) continue;
        srows.push({
          tg_id: trim_(sv[s][STOCK_COLS.tg_id - 1]),
          dropshipper: trim_(sv[s][STOCK_COLS.dropshipper - 1]),
          article: art,
          // назву обрізаємо: вона потрапляє в опис ТТН на паперовій накладній
          name: trim_(sv[s][STOCK_COLS.name - 1]),
          allocated: sv[s][STOCK_COLS.allocated - 1],
          reserved: sv[s][STOCK_COLS.reserved - 1],
          delivered: sv[s][STOCK_COLS.delivered - 1],
          available: sv[s][STOCK_COLS.available - 1]
        });
      }
    }
    return json_({ rows: srows });
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

  if (what === "ttns") {
    // замовлення з ТТН, статус яких ще не кінцевий; телефон потрібен НП для
    // повного статусу — у логах бота він не з'являється
    var tsh = sheet_(ORDERS_SHEET, ORDER_HEADERS);
    ensureHeaders_(tsh, ORDER_HEADERS);
    var tlast = tsh.getLastRow();
    var trows = [];
    if (tlast > 1) {
      var tv = tsh.getRange(2, 1, tlast - 1, ORDER_HEADERS.length).getValues();
      for (var t = 0; t < tv.length; t++) {
        var ttn = trim_(tv[t][ORDER_TTN_COL - 1]);
        if (!ttn) continue;
        var st = trim_(tv[t][ORDER_NP_STATUS_COL - 1]).toLowerCase();
        var final = false;
        for (var f = 0; f < NP_FINAL.length; f++) {
          if (st.indexOf(NP_FINAL[f]) >= 0) { final = true; break; }
        }
        if (final) continue;
        var made = tv[t][orderCol_("created_at") - 1];
        trows.push({
          order_no: tv[t][orderCol_("order_no") - 1],
          created_at: (made instanceof Date) ? made.toISOString() : trim_(made),
          ttn: ttn,
          tg_id: trim_(tv[t][orderCol_("dropshipper_id") - 1]),
          article: trim_(tv[t][orderCol_("article") - 1]),
          qty: tv[t][orderCol_("qty") - 1],
          phone: trim_(tv[t][orderCol_("recipient_phone") - 1]),
          np_status: trim_(tv[t][ORDER_NP_STATUS_COL - 1])
        });
      }
    }
    return json_({ rows: trows });
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

  return json_({ ok: true,
    hint: "what=dropshippers|orders|aliases|maxorder|stock|ttns" });
}
