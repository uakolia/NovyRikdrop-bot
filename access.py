"""Глобальний фільтр доступу: усе, крім /start і звернень, лише для схвалених."""
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

import keyboards as kb
import storage

# що дозволено НЕсхваленому користувачу
ALLOWED_COMMANDS = {"/start", "/help", "/id"}
ALLOWED_CALLBACKS = {"help", "support:new", "support:back"}

DENIED = ("⛔️ Доступ до бота ще не відкрито.\n\n"
          "Ваш запит у менеджера — щойно вас схвалять, прийде повідомлення. "
          "Якщо чекаєте довго, напишіть менеджеру напряму.")


class ApprovalMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        if user is None or storage.is_approved(user.id):
            return await handler(event, data)

        # Не знайшли в списку — можливо, список просто не підтягнувся при
        # старті (після редеплою локального файлу немає). Перечитуємо аркуш,
        # перш ніж відмовляти; частота обмежена в storage.
        if await storage.ensure_synced(user.id):
            return await handler(event, data)

        # НЕсхвалений: пропускаємо лише /start, допомогу та звернення
        if isinstance(event, Message):
            text = (event.text or "").strip().split()[0].lower() if event.text else ""
            state = data.get("state")
            cur = await state.get_state() if state else None
            if text in ALLOWED_COMMANDS or (cur or "").startswith("Support"):
                return await handler(event, data)
            await event.answer(DENIED)
            return None

        if isinstance(event, CallbackQuery):
            if event.data in ALLOWED_CALLBACKS:
                return await handler(event, data)
            await event.answer("Доступ ще не схвалено менеджером", show_alert=True)
            return None

        return None


def guest_menu():
    return kb.support_only_menu()
