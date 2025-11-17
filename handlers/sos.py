from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User as AiogramUser,
)
from loguru import logger

from bot_instance import bot
from config import POMAGATOR_CHAT_ID, POMAGATOR_THREAD_ID
from states.sos import Sos

router = Router()


def _sos_keyboard(text_ready: bool) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    if text_ready:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="Отправить помогаторам",
                    callback_data="sos_send_request",
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton(text="Отменить", callback_data="sos_cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _sos_display_text(data: dict, status: str | None = None) -> str:
    lines: list[str] = ["🆘 <b>Напишите помогаторам</b>"]
    text = data.get("sos_text")
    if text:
        lines.extend(
            [
                "",
                escape(text),
                "",
                "Нажмите кнопку ниже, чтобы передать сообщение помогаторам.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "Опишите, что случилось, отдельным сообщением ниже.",
            ]
        )
    if status:
        lines.extend(["", status])
    return "\n".join(lines)


def _format_sos_message(sos_text: str, author: AiogramUser | None) -> str:
    escaped_text = escape(sos_text)
    if author:
        display_name = escape(author.full_name or "")
        tg_id = author.id
        username = author.username or ""
        mention = f'<a href="tg://user?id={tg_id}">{display_name}</a>' if display_name else ""
        if not mention:
            mention = f'<a href="tg://user?id={tg_id}">Пользователь</a>'
        author_line = mention
        if username:
            author_line += f" (@{escape(username)})"
        header = f"🆘 <b>Запрос помощи</b>\n👤 {author_line}"
    else:
        header = "🆘 <b>Запрос помощи</b>\n👤 Неизвестный пользователь"
    body = f"\n\n📝 {escaped_text}"
    return f"{header}{body}"


async def _deliver_sos(sos_text: str, author: AiogramUser | None) -> bool:
    if not POMAGATOR_CHAT_ID:
        logger.warning("POMAGATOR_CHAT_ID is not configured; SOS message was not delivered.")
        return False
    payload = _format_sos_message(sos_text, author)
    send_kwargs = dict(chat_id=POMAGATOR_CHAT_ID, text=payload, parse_mode=ParseMode.HTML)
    if POMAGATOR_THREAD_ID:
        send_kwargs["message_thread_id"] = POMAGATOR_THREAD_ID
    try:
        await bot.send_message(**send_kwargs)
        return True
    except Exception as exc:
        logger.error(f"Failed to deliver SOS message to {POMAGATOR_CHAT_ID}: {exc}")
        return False


@router.message(Command("sos"))
async def cmd_sos(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Sos.waiting_text)
    main_msg = await message.answer(
        _sos_display_text({}),
        parse_mode=ParseMode.HTML,
        reply_markup=_sos_keyboard(text_ready=False),
    )
    await state.update_data(
        main_message_id=main_msg.message_id,
        sos_text=None,
    )


@router.message(Sos.waiting_text)
async def sos_collect_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Сообщение пустое. Пожалуйста, опишите ситуацию.")
        return

    try:
        await message.delete()
    except TelegramBadRequest:
        pass

    data = await state.get_data()
    main_message_id = data.get("main_message_id")
    await state.update_data(sos_text=text)
    updated = await state.get_data()

    if main_message_id:
        try:
            await bot.edit_message_text(
                _sos_display_text(updated),
                chat_id=message.chat.id,
                message_id=main_message_id,
                reply_markup=_sos_keyboard(text_ready=True),
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest as exc:
            logger.warning(f"Failed to edit SOS message: {exc}")
            fallback = await message.answer(
                _sos_display_text(updated),
                reply_markup=_sos_keyboard(text_ready=True),
                parse_mode=ParseMode.HTML,
            )
            await state.update_data(main_message_id=fallback.message_id)
    else:
        fallback = await message.answer(
            _sos_display_text(updated),
            reply_markup=_sos_keyboard(text_ready=True),
            parse_mode=ParseMode.HTML,
        )
        await state.update_data(main_message_id=fallback.message_id)

    await state.set_state(Sos.waiting_confirmation)


@router.callback_query(F.data == "sos_send_request", Sos.waiting_confirmation)
async def cb_sos_send(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    sos_text = data.get("sos_text")
    main_message_id = data.get("main_message_id")

    if not sos_text:
        await callback.answer("Не найден текст сообщения. Начните заново командой /sos.", show_alert=True)
        await state.clear()
        return

    delivered = await _deliver_sos(sos_text, callback.from_user)
    status = (
        "✅ Сообщение отправлено помогаторам."
        if delivered
        else "⚠️ Не удалось доставить сообщение помогаторам. Попробуйте позже."
    )

    display_text = _sos_display_text({"sos_text": sos_text}, status=status)

    try:
        if main_message_id:
            await bot.edit_message_text(
                display_text,
                chat_id=callback.message.chat.id,
                message_id=main_message_id,
                reply_markup=None,
                parse_mode=ParseMode.HTML,
            )
        else:
            await callback.message.edit_text(
                display_text,
                reply_markup=None,
                parse_mode=ParseMode.HTML,
            )
    except Exception as exc:
        logger.warning(f"Could not edit SOS prompt message: {exc}")
        await callback.message.answer(status)

    await callback.answer("Отправлено!" if delivered else "Не удалось отправить.")
    await state.clear()


@router.callback_query(F.data == "sos_cancel")
async def cb_sos_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text("Запрос отменён.", reply_markup=None)
    except Exception:
        await callback.message.answer("Запрос отменён.")
    await callback.answer()


@router.message(Command("chatid"))
async def cmd_chat_id(message: Message):
    chat_id = message.chat.id
    logger.info(f"Chat ID requested in chat {chat_id}.")
    await message.answer(
        f"ID этого чата: <code>{chat_id}</code>\n\n"
        "Скопируйте значение (оно может быть отрицательным) и укажите его в constants.POMAGATOR_CHAT_ID.",
        parse_mode=ParseMode.HTML,
    )
