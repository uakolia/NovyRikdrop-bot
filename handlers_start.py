"""Старт, доступ, допомога."""
from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

import config, keyboards as kb, storage

router = Router()

WELCOME = (
    "🎄 <b>Ялинкар — бот для дропшиперів</b>\n\n"
    "Тут ви оформлюєте замовлення на преміальні литі ялинки: "
    "бот сам створить ТТН Нової Пошти і передасть замовлення на відправку.\n"
)

HELP = (
    "ℹ️ <b>Як це працює</b>\n\n"
    "1. Натисніть «Нове замовлення»\n"
    "2. Оберіть категорію → модель → розмір (ціни — ваш дроп-тариф)\n"
    "3. Вкажіть тип оплати:\n"
    "   • <b>Післяплата</b> — клієнт платить при отриманні\n"
    "   • <b>Передплата</b> — ялинка вже оплачена\n"
    "4. Введіть ПІБ, телефон отримувача, місто і вантажне відділення НП\n"
    "5. Підтвердіть — бот створить ТТН і надішле вам номер\n\n"
    "❗️ Ялинки важкі, тому доступні лише <b>вантажні відділення</b> "
    "Нової Пошти (понад 30 кг).\n\n"
    "🆘 Щось не працює або є ідея? Натисніть <b>«Повідомити про проблему»</b> "
    "в головному меню — напишіть текстом (можна зі скріншотом), "
    "менеджер відповість у цьому ж чаті."
)


@router.message(CommandStart())
async def cmd_start(msg: Message):
    user = msg.from_user
    if storage.is_approved(user.id):
        await msg.answer(WELCOME, reply_markup=kb.main_menu())
        return
    storage.add_pending(user.id, user.full_name, user.username or "")
    await msg.answer(
        WELCOME + "\n⏳ Ваш запит на доступ надіслано менеджеру. "
        "Щойно вас схвалять — прийде повідомлення.")
    if config.ADMIN_CHAT_ID:
        uname = f"@{user.username}" if user.username else "без username"
        await msg.bot.send_message(
            config.ADMIN_CHAT_ID,
            f"👋 Новий дропшипер хоче доступ:\n"
            f"<b>{user.full_name}</b> ({uname}, id <code>{user.id}</code>)",
            reply_markup=kb.approve_kb(user.id))


@router.callback_query(F.data == "help")
async def cb_help(cb: CallbackQuery):
    await cb.message.answer(HELP, disable_web_page_preview=True)
    await cb.answer()
