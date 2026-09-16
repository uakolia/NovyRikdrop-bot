"""Звернення дропшиперів: «Повідомити про проблему» + відповідь адміна."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

import config, keyboards as kb, storage

router = Router()


class Support(StatesGroup):
    text = State()


@router.callback_query(F.data == "support:new")
async def support_start(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(Support.text)
    await cb.message.edit_text(
        "✍️ <b>Повідомити про проблему</b>\n\n"
        "Опишіть, що не працює або чого не хватає — одним повідомленням. "
        "Можна прикріпити фото чи скріншот.\n\n"
        "Наприклад: <i>«не знаходить відділення в Умані»</i> або "
        "<i>«ціна на Грейс 2.2 не збігається з прайсом»</i>.\n\n"
        "Скасувати: /cancel",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="↩️ Назад", callback_data="support:back")]]))
    await cb.answer()


@router.callback_query(F.data == "support:back")
async def support_back(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Головне меню:", reply_markup=kb.main_menu())
    await cb.answer()


@router.message(Support.text, F.text | F.photo | F.document)
async def support_text(msg: Message, state: FSMContext):
    if msg.text and msg.text.strip().startswith("/"):
        await state.clear()
        await msg.answer("Скасовано.", reply_markup=kb.main_menu())
        return
    await state.clear()
    user = msg.from_user
    uname = f"@{user.username}" if user.username else user.full_name
    header = (f"🆘 <b>Звернення від дропшипера</b>\n"
              f"{uname} (id <code>{user.id}</code>)\n"
              f"🕒 {config.now().strftime('%d.%m.%Y %H:%M')}\n")
    body = msg.text or msg.caption or "(без тексту)"
    reply_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✍️ Відповісти",
                             callback_data=f"sup_reply:{user.id}")]])

    if config.ADMIN_CHAT_ID:
        try:
            if msg.photo:
                await msg.bot.send_photo(
                    config.ADMIN_CHAT_ID, msg.photo[-1].file_id,
                    caption=f"{header}\n💬 {body}", reply_markup=reply_kb)
            elif msg.document:
                await msg.bot.send_document(
                    config.ADMIN_CHAT_ID, msg.document.file_id,
                    caption=f"{header}\n💬 {body}", reply_markup=reply_kb)
            else:
                await msg.bot.send_message(config.ADMIN_CHAT_ID,
                                           f"{header}\n💬 {body}",
                                           reply_markup=reply_kb)
        except Exception:  # noqa: BLE001
            pass
    await msg.answer(
        "✅ Дякуємо! Повідомлення надіслано менеджеру — відповімо найближчим часом.",
        reply_markup=kb.main_menu())


# ---------- відповідь адміна ----------

class AdminReply(StatesGroup):
    text = State()


@router.callback_query(F.data.startswith("sup_reply:"))
async def admin_reply_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in config.ADMIN_IDS:
        await cb.answer("Лише для адміністратора", show_alert=True)
        return
    user_id = int(cb.data.split(":")[1])
    await state.clear()
    await state.set_state(AdminReply.text)
    await state.update_data(reply_to=user_id)
    await cb.message.answer(f"✍️ Напишіть відповідь для дропшипера "
                            f"(id {user_id}). Скасувати: /cancel")
    await cb.answer()


@router.message(AdminReply.text, F.text)
async def admin_reply_send(msg: Message, state: FSMContext):
    if msg.text.strip().startswith("/"):
        await state.clear()
        await msg.answer("Скасовано.")
        return
    data = await state.get_data()
    await state.clear()
    try:
        await msg.bot.send_message(
            data["reply_to"],
            f"💬 <b>Відповідь менеджера:</b>\n\n{msg.text}",
            reply_markup=kb.main_menu())
        await msg.answer("✅ Відповідь надіслано.")
    except Exception as e:  # noqa: BLE001
        await msg.answer(f"⚠️ Не вдалося надіслати: {e}")


@router.message(Command("say"))
async def cmd_say(msg: Message):
    """/say ID текст — написати дропшиперу напряму."""
    if msg.from_user.id not in config.ADMIN_IDS:
        return
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await msg.answer("Використання: <code>/say ID текст повідомлення</code>")
        return
    try:
        await msg.bot.send_message(int(parts[1]),
                                   f"💬 <b>Повідомлення від менеджера:</b>\n\n{parts[2]}")
        await msg.answer("✅ Надіслано.")
    except Exception as e:  # noqa: BLE001
        await msg.answer(f"⚠️ Не вдалося: {e}")
