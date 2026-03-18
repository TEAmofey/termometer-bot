from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.filters import StateFilter
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
from utils.telegram_text import (
    TELEGRAM_TEXT_LIMIT,
    fits_telegram_text,
    shorten_text_for_html_preview,
    split_text_for_html,
)

router = Router()
PREVIEW_SUFFIX = "\n\n[Показан фрагмент. Полный текст будет отправлен помогаторам целиком.]"


def _append_text(existing_text: str | None, new_text: str) -> str:
    if not existing_text:
        return new_text
    return f"{existing_text}\n\n{new_text}"


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
        text = shorten_text_for_html_preview(text, 2500, PREVIEW_SUFFIX)
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
        header = f"🆘 <b>Запрос помощи</b>\n👤 {author_line}\n\n📝 "
        continuation_template = f"🆘 <b>Запрос помощи</b> (часть {{index}})\n👤 {author_line}\n\n📝 "
    else:
        header = "🆘 <b>Запрос помощи</b>\n👤 Неизвестный пользователь\n\n📝 "
        continuation_template = "🆘 <b>Запрос помощи</b> (часть {index})\n👤 Неизвестный пользователь\n\n📝 "

    try:
        max_header_len = max(len(header), len(continuation_template.format(index=999)))
        chunks = split_text_for_html(sos_text, TELEGRAM_TEXT_LIMIT - max_header_len)
        for index, chunk in enumerate(chunks, start=1):
            chunk_header = header if index == 1 else continuation_template.format(index=index)
            await bot.send_message(
                chat_id=POMAGATOR_CHAT_ID,
                text=f"{chunk_header}{escape(chunk)}",
                message_thread_id=POMAGATOR_THREAD_ID,
                parse_mode=ParseMode.HTML,
            )
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


@router.message(StateFilter(Sos.waiting_text, Sos.waiting_confirmation))
async def sos_collect_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Сообщение пустое. Пожалуйста, опишите ситуацию.")
        return

    data = await state.get_data()
    main_message_id = data.get("main_message_id")
    combined_text = _append_text(data.get("sos_text"), text)
    await state.update_data(sos_text=combined_text)
    updated = await state.get_data()

    display_text = _sos_display_text(updated)
    if not fits_telegram_text(display_text):
        await message.answer(
            "Текст слишком длинный даже для предпросмотра в боте. Сократите его или разбейте на несколько сообщений."
        )
        return

    if main_message_id:
        try:
            await bot.edit_message_text(
                display_text,
                chat_id=message.chat.id,
                message_id=main_message_id,
                reply_markup=_sos_keyboard(text_ready=True),
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest as exc:
            logger.warning(f"Failed to edit SOS message: {exc}")
            fallback = await message.answer(
                display_text,
                reply_markup=_sos_keyboard(text_ready=True),
                parse_mode=ParseMode.HTML,
            )
            await state.update_data(main_message_id=fallback.message_id)
    else:
        fallback = await message.answer(
            display_text,
            reply_markup=_sos_keyboard(text_ready=True),
            parse_mode=ParseMode.HTML,
        )
        await state.update_data(main_message_id=fallback.message_id)

    try:
        await message.delete()
    except TelegramBadRequest:
        pass

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


@router.message(Command("threadid"))
async def cmd_thread_id(message: Message):
    thread_id = message.message_thread_id
    chat_id = message.chat.id
    logger.info(
        "Thread ID requested in chat %s (thread %s).",
        chat_id,
        thread_id,
    )

    if thread_id is None:
        await message.answer(
            "Это сообщение не из топика (message_thread_id отсутствует).\n\n"
            f"ID чата: <code>{chat_id}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    await message.answer(
        f"ID этого топика: <code>{thread_id}</code>\n\n"
        f"ID чата: <code>{chat_id}</code>",
        parse_mode=ParseMode.HTML,
    )
