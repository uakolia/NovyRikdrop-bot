"""Адмін-команди: схвалення дропшиперів, оновлення прайсу, службові."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import catalog, config, keyboards as kb, storage

router = Router()


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


@router.callback_query(F.data.startswith("approve:"))
async def cb_approve(cb: CallbackQuery):
    if not _is_admin(cb.from_user.id):
        await cb.answer("Лише для адміністратора", show_alert=True)
        return
    user_id = int(cb.data.split(":")[1])
    info = storage.approve(user_id)
    await cb.message.edit_text(cb.message.html_text +
                               f"\n\n✅ Схвалено ({info.get('name', '')})")
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
    storage.deny(user_id)
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
    storage.deny(int(parts[1]))
    await msg.answer("🚫 Доступ закрито.")


@router.message(Command("reload"))
async def cmd_reload(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    await msg.answer("⏳ Оновлюю каталог із Google Таблиці…")
    n, err = await catalog.reload_from_google()
    if err:
        await msg.answer(f"⚠️ Не вдалося: {err}\nКаталог залишився без змін.")
    else:
        await msg.answer(f"✅ Каталог оновлено: {n} товарів.")


@router.message(Command("id"))
async def cmd_id(msg: Message):
    await msg.answer(f"ID цього чату: <code>{msg.chat.id}</code>\n"
                     f"Ваш ID: <code>{msg.from_user.id}</code>")
