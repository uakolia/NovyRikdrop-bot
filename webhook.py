"""HTTP-вебхук для замовлень із сайту (Weblium через Zapier/Make/webhook форми).

POST /weblium?secret=...   JSON-тіло, поля (будь-які з них):
  product | назва | name      — назва товару або артикул
  article                      — артикул (точніший за назву)
  qty | quantity               — кількість (типово 1)
  payment | оплата             — "післяплата" або "передплата"
  fio | name_recipient | піб   — ПІБ отримувача
  phone | телефон              — телефон отримувача
  city | місто                 — місто
  warehouse | відділення       — відділення НП (текстом)

Бот запише замовлення в журнал і повідомить менеджера. Якщо вдасться однозначно
знайти місто і вантажне відділення в НП — створить і ТТН.
"""
from aiohttp import web

import catalog, config, novaposhta as np, orders, storage


def _field(data, *keys, default=""):
    for k in keys:
        for kk in (k, k.lower(), k.upper(), k.capitalize()):
            if kk in data and str(data[kk]).strip():
                return str(data[kk]).strip()
    return default


async def handle_weblium(request: web.Request):
    if not config.WEBLIUM_SECRET:
        return web.json_response({"error": "вебхук вимкнено"}, status=403)
    if request.query.get("secret") != config.WEBLIUM_SECRET:
        return web.json_response({"error": "невірний secret"}, status=403)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = dict(await request.post())

    article = _field(data, "article", "артикул")
    product = _field(data, "product", "назва", "name", "товар")
    item = catalog.by_article(article) if article else None
    if not item and product:
        low = product.lower()
        matches = [i for i in catalog.items()
                   if i["article"].lower() in low or i["model_ua"].lower() in low]
        item = matches[0] if len(matches) == 1 else None

    qty = _field(data, "qty", "quantity", "кількість", default="1")
    qty = int(qty) if qty.isdigit() and 0 < int(qty) < 100 else 1
    payment = _field(data, "payment", "оплата", default="післяплата")
    payment = "передплата" if "перед" in payment.lower() else "післяплата"

    order = orders.new_order(
        order_no=storage.next_order_no(),
        source="weblium",
        dropshipper=_field(data, "shop", "магазин", default="сайт Weblium"),
        article=item["article"] if item else article,
        product=item["model_ua"] if item else product,
        size=catalog.size_label(item) if item else "",
        qty=qty,
        price_drop=int(catalog.drop_price(item) * qty) if item else "",
        payment=payment,
        recipient_fio=_field(data, "fio", "піб", "name_recipient", "recipient"),
        recipient_phone=_field(data, "phone", "телефон"),
        city=_field(data, "city", "місто"),
        warehouse=_field(data, "warehouse", "відділення", "branch"),
    )

    # спроба авто-ТТН: місто й вантажне відділення мають знайтись однозначно
    if config.NP_AUTO_TTN and item and order["recipient_fio"] and order["recipient_phone"]:
        try:
            cities = await np.search_cities(order["city"], limit=1)
            if cities:
                whs = await np.cargo_warehouses(cities[0]["ref"])
                import re
                want = re.sub(r"\D", "", order["warehouse"])
                match = [w for w in whs if want and str(w["number"]) == want]
                if len(match) == 1:
                    res = await np.create_ttn(
                        recipient_city_ref=cities[0]["ref"],
                        recipient_warehouse_ref=match[0]["ref"],
                        fio=order["recipient_fio"], phone=order["recipient_phone"],
                        description=catalog.ttn_description(item),
                        cost=catalog.drop_price(item) * qty,
                        weight=(item["weight_kg"] or 5) * qty,
                        volume=(item.get("volume_m3") or 0) * qty or None,
                        seats=qty)
                    order["ttn"] = res["ttn"]
                    order["status"] = "ТТН створено"
                    order["warehouse"] = match[0]["name"]
                    order["city"] = cities[0]["name"]
        except Exception as e:  # noqa: BLE001
            order["comment"] = f"Авто-ТТН не вдалась: {e}"

    orders.save_csv(order)
    orders.save_local(order)
    await orders.send_to_sheet(order)
    bot = request.app["bot"]
    if config.ADMIN_CHAT_ID:
        try:
            await bot.send_message(config.ADMIN_CHAT_ID, orders.admin_text(order))
        except Exception:  # noqa: BLE001
            pass
    return web.json_response({"ok": True, "order_no": order["order_no"],
                              "ttn": order.get("ttn") or None})


async def handle_health(_request):
    return web.json_response({"ok": True})


def make_app(bot):
    app = web.Application()
    app["bot"] = bot
    app.router.add_post("/weblium", handle_weblium)
    app.router.add_get("/", handle_health)
    return app
