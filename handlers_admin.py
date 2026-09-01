"""Адмін-команди: схвалення дропшиперів, налаштування НП, оновлення прайсу."""
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

import catalog, config, keyboards as kb, storage
import novaposhta as np
import np_store

router = Router()


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


# ---------- Налаштування Нової Пошти прямо в боті: /np_setup ----------

class NPSetup(StatesGroup):
    api_key = State()
    phone = State()
    city = State()
    city_pick = State()
    wh_pick = State()


@router.message(Command("np_setup"))
async def cmd_np_setup(msg: Message, state: FSMContext):
    if not _is_admin(msg.from_user.id):
        return
    await state.clear()
    await state.set_state(NPSetup.api_key)
    await msg.answer(
        "🔧 <b>Налаштування Нової Пошти</b>\n\n"
        "Надішліть ваш <b>API-ключ</b> одним повідомленням.\n"
        "Взяти його: new.novaposhta.ua → Налаштування → Безпека → API-ключі.\n\n"
        "Скасувати: /cancel")


@router.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext):
    if not _is_admin(msg.from_user.id):
        return
    await state.clear()
    await msg.answer("Скасовано.")


@router.message(NPSetup.api_key, F.text)
async def np_key(msg: Message, state: FSMContext):
    key = msg.text.strip()
    if len(key) < 20:
        await msg.answer("⚠️ Це не схоже на API-ключ. Спробуйте ще раз:")
        return
    await msg.answer("⏳ Перевіряю ключ…")
    try:
        info = await np.get_sender_info(key)
    except np.NPError as e:
        await msg.answer(f"⚠️ Нова Пошта відповіла: {e}\n"
                         "Перевірте ключ і надішліть ще раз:")
        return
    np_store.save(api_key=key, sender_ref=info["sender_ref"],
                  contact_ref=info["contact_ref"])
    await state.update_data(contact_name=info["contact_name"])
    text = (f"✅ Ключ працює!\n"
            f"Відправник: <b>{info['sender_name']}</b>\n"
            f"Контактна особа: {info['contact_name']}\n\n")
    if info["phone"]:
        np_store.save(phone="+" + re.sub(r"\D", "", info["phone"])
                      if not info["phone"].startswith("+") else info["phone"])
        await state.set_state(NPSetup.city)
        await msg.answer(text + "🏙 Тепер введіть <b>місто, з якого відправляєте</b>:")
    else:
        await state.set_state(NPSetup.phone)
        await msg.answer(text + "📞 Введіть <b>телефон відправника</b> "
                                "(наприклад 0671234567):")


@router.message(NPSetup.phone, F.text)
async def np_phone(msg: Message, state: FSMContext):
    digits = re.sub(r"\D", "", msg.text)
    if digits.startswith("380") and len(digits) == 12:
        phone = "+" + digits
    elif digits.startswith("0") and len(digits) == 10:
        phone = "+38" + digits
    else:
        await msg.answer("⚠️ Невірний формат. Приклад: 0671234567. Ще раз:")
        return
    np_store.save(phone=phone)
    await state.set_state(NPSetup.city)
    await msg.answer("🏙 Введіть <b>місто, з якого відправляєте</b>:")


@router.message(NPSetup.city, F.text)
async def np_city(msg: Message, state: FSMContext):
    try:
        cities = await np.search_cities(msg.text.strip())
    except np.NPError as e:
        await msg.answer(f"⚠️ Помилка НП: {e}\nСпробуйте ще раз:")
        return
    if not cities:
        await msg.answer("⚠️ Не знайдено. Введіть назву міста ще раз:")
        return
    await state.update_data(cities=cities)
    await state.set_state(NPSetup.city_pick)
    rows = [[InlineKeyboardButton(text=f"{c['name']} ({c['area']} обл.)",
                                  callback_data=f"npc:{i}")]
            for i, c in enumerate(cities)]
    await msg.answer("Оберіть місто:",
                     reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def _wh_kb(whs, page: int):
    per = 8
    chunk = whs[page * per:(page + 1) * per]
    rows = [[InlineKeyboardButton(
        text=(w["name"][:55] + "…") if len(w["name"]) > 55 else w["name"],
        callback_data=f"npw:{page * per + i}")] for i, w in enumerate(chunk)]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"npwp:{page - 1}"))
    if (page + 1) * per < len(whs):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"npwp:{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("npc:"), NPSetup.city_pick)
async def np_city_pick(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    city = data["cities"][int(cb.data.split(":")[1])]
    try:
        whs = await np.all_warehouses(city["ref"])
    except np.NPError as e:
        await cb.message.edit_text(f"⚠️ Помилка НП: {e}")
        await cb.answer()
        return
    np_store.save(city_ref=city["ref"], city_name=city["name"])
    await state.update_data(whs=whs)
    await state.set_state(NPSetup.wh_pick)
    await cb.message.edit_text(
        f"📍 {city['name']} — оберіть <b>відділення, з якого відправляєте</b>:",
        reply_markup=_wh_kb(whs, 0))
    await cb.answer()


@router.callback_query(F.data.startswith("npwp:"), NPSetup.wh_pick)
async def np_wh_page(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await cb.message.edit_reply_markup(
        reply_markup=_wh_kb(data["whs"], int(cb.data.split(":")[1])))
    await cb.answer()


@router.callback_query(F.data.startswith("npw:"), NPSetup.wh_pick)
async def np_wh_pick(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    wh = data["whs"][int(cb.data.split(":")[1])]
    np_store.save(warehouse_ref=wh["ref"], warehouse_name=wh["name"])
    await state.clear()
    await cb.message.edit_text(
        "🎉 <b>Нову Пошту налаштовано!</b>\n\n"
        f"Відправка: {np_store.get('city_name')}, {wh['name']}\n"
        f"Телефон: {np_store.get('phone')}\n\n"
        "Бот уже може створювати ТТН. ❗️Щоб налаштування пережили "
        "перезапуск хостингу, додайте в Railway → Variables ці рядки "
        "(надішлю наступним повідомленням).")
    await cb.message.answer(f"<code>{np_store.env_lines()}</code>")
    await cb.answer()


@router.message(Command("np_check"))
async def cmd_np_check(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    if np_store.is_ready():
        await msg.answer("✅ НП налаштовано.\n"
                         f"Відправка: {np_store.get('city_name') or '—'}, "
                         f"{np_store.get('warehouse_name') or '—'}\n"
                         f"Телефон: {np_store.get('phone')}")
    else:
        missing = [k for k, v in np_store.all_values().items() if not v]
        await msg.answer("⚠️ НП не налаштовано повністю. Бракує: "
                         + ", ".join(missing) + "\nЗапустіть /np_setup")


@router.callback_query(F.data.startswith("approve:"))
async def cb_approve(cb: CallbackQuery):
    if not _is_admin(cb.from_user.id):
        await cb.answer("Лише для адміністратора", show_alert=True)
        return
    user_id = int(cb.data.split(":")[1])
    who = f"@{cb.from_user.username}" if cb.from_user.username else str(cb.from_user.id)
    info, err = await storage.approve(user_id, who)
    note = f"\n\n✅ Схвалено ({info.get('name', '')})"
    if err:
        note += (f"\n⚠️ У таблицю не записалось: {err}\n"
                 "Доступ діятиме до перезапуску — перевірте SHEET_WEBHOOK_URL")
    await cb.message.edit_text(cb.message.html_text + note)
    try:
        await cb.bot.send_message(
            user_id, "✅ Вам відкрито доступ! Можете оформлювати замовлення.",
            reply_markup=kb.main_menu())
    except Exception:  # noqa: BLE001
        pass
    await cb.answer("Схвалено")


@router.callback_query(F.data.startswith("deny:"))
async def cb_deny(cb: CallbackQuery):
    if not _is_admin(cb.from_user.id):
        await cb.answer("Лише для адміністратора", show_alert=True)
        return
    user_id = int(cb.data.split(":")[1])
    who = f"@{cb.from_user.username}" if cb.from_user.username else str(cb.from_user.id)
    await storage.deny(user_id, who)
    await cb.message.edit_text(cb.message.html_text + "\n\n🚫 Відхилено")
    await cb.answer("Відхилено")


@router.message(Command("users"))
async def cmd_users(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    users = storage.approved_users()
    if not users:
        await msg.answer("Схвалених дропшиперів поки немає.")
        return
    lines = [f"• {u.get('name', '?')} (@{u.get('username') or '—'}, id {uid})"
             for uid, u in users.items()]
    await msg.answer("👥 <b>Схвалені дропшипери:</b>\n" + "\n".join(lines))


@router.message(Command("block"))
async def cmd_block(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await msg.answer("Використання: <code>/block ID_користувача</code>")
        return
    who = f"@{msg.from_user.username}" if msg.from_user.username else str(msg.from_user.id)
    await storage.deny(int(parts[1]), who)
    await msg.answer("🚫 Доступ закрито.")


@router.message(Command("reload"))
async def cmd_reload(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    before = len(catalog.items())
    await msg.answer("⏳ Оновлюю каталог із Google Таблиці (усі вкладки)…")
    n, err = await catalog.reload_from_google()
    if err:
        await msg.answer(f"⚠️ Не вдалося: {err}")
    else:
        cats = ", ".join(f"{c} — {sum(1 for i in catalog.items() if catalog.category_of(i) == c)}"
                         for c in catalog.categories())
        await msg.answer(f"✅ Каталог оновлено: <b>{n}</b> товарів "
                         f"(було {before}).\n\n{cats}")


@router.message(Command("pending"))
async def cmd_pending(msg: Message):
    """Хто чекає на схвалення."""
    if not _is_admin(msg.from_user.id):
        return
    p = storage.pending_users()
    if not p:
        await msg.answer("Заявок на доступ немає.")
        return
    for uid, u in list(p.items())[:20]:
        uname = f"@{u.get('username')}" if u.get("username") else "без username"
        await msg.answer(f"⏳ <b>{u.get('name', '?')}</b> ({uname}, "
                         f"id <code>{uid}</code>)",
                         reply_markup=kb.approve_kb(int(uid)))


@router.message(Command("sync"))
async def cmd_sync(msg: Message):
    """Перечитати список дропшиперів із Google Таблиці."""
    if not _is_admin(msg.from_user.id):
        return
    n, err = await storage.sync_from_sheet(force=True)
    if err:
        await msg.answer(f"⚠️ Не вдалося: {err}")
        return
    users = storage.approved_users()
    lines = [f"• {u.get('name') or '?'} (@{u.get('username') or '—'}, id {uid})"
             for uid, u in users.items()]
    await msg.answer(f"✅ Синхронізовано: {n} схвалених\n" + "\n".join(lines[:30]))


@router.message(Command("catalog"))
async def cmd_catalog(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    items = catalog.items()
    lines = [f"📦 <b>Каталог: {len(items)} товарів</b>", ""]
    for c in catalog.categories():
        n = sum(1 for i in items if catalog.category_of(i) == c)
        lines.append(f"• {c} — {n}")
    est = [i["article"] for i in items if i.get("weight_estimated")]
    if est:
        lines += ["", f"⚠️ Вага оцінена (немає в прайсі) у {len(est)}: "
                      + ", ".join(est[:12])]
    await msg.answer("\n".join(lines))


@router.message(Command("id"))
async def cmd_id(msg: Message):
    await msg.answer(f"ID цього чату: <code>{msg.chat.id}</code>\n"
                     f"Ваш ID: <code>{msg.from_user.id}</code>")
