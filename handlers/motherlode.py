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
from config import MOTHERLODE_CHAT_ID
from db.user import User
from utils.users import education_lines
from states.motherlode import Motherlode
from utils.telegram_text import (
    TELEGRAM_TEXT_LIMIT,
    fits_telegram_text,
    shorten_text_for_html_preview,
    split_text_for_html,
)

router = Router()
PREVIEW_SUFFIX = "\n\n[Показан фрагмент. Полный текст будет отправлен ботмейстерам целиком.]"


def _append_text(existing_text: str | None, new_text: str) -> str:
    if not existing_text:
        return new_text
    return f"{existing_text}\n\n{new_text}"


def _motherlode_keyboard(text_ready: bool) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    if text_ready:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="Отправить ботмейстерам",
                    callback_data="motherlode_send_request",
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton(text="Отменить", callback_data="motherlode_cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _motherlode_display_text(data: dict, status: str | None = None) -> str:
    lines: list[str] = ["📚 <b>Напишите ботмейстерам</b>"]
    text = data.get("motherlode_text")
    if text:
        text = shorten_text_for_html_preview(text, 2500, PREVIEW_SUFFIX)
        lines.extend(
            [
                "",
                escape(text),
                "",
                "Нажмите кнопку ниже, чтобы передать запрос ботмейстерам.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "Опишите свой запрос по учёбе. "
                "Постарайтесь сформулировать его максимально точно (укажите предмет или конкретную проблематику), "
                "чтобы наши ботмейстеры смогли оперативнее понять, чем вам помочь.",
            ]
        )
    if status:
        lines.extend(["", status])
    return "\n".join(lines)


def _motherlode_header(author: AiogramUser | None) -> str:
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
        header = f"📚 <b>Учебный запрос</b>\n👤 {author_line}"
    else:
        header = "📚 <b>Учебный запрос</b>\n👤 Неизвестный пользователь"
    user = User.get_by_tg_id(author.id) if author else None
    if not user or not user.is_registration_complete():
        raise ValueError("A complete profile is required for a study request")
    header += "\n" + "\n".join(education_lines(user.raw if user else {}))
    return header


def _format_motherlode_message(text: str, author: AiogramUser | None) -> str:
    return f"{_motherlode_header(author)}\n\n📝 {escape(text)}"


async def _deliver_motherlode(text: str, author: AiogramUser | None) -> bool:
    if not MOTHERLODE_CHAT_ID:
        logger.warning("MOTHERLODE_CHAT_ID is not configured; message was not delivered.")
        return False
    try:
        header = f"{_motherlode_header(author)}\n\n📝 "
        continuation_header = header.replace(
            "<b>Учебный запрос</b>", "<b>Учебный запрос</b> (часть {index})", 1
        )
        max_header_len = max(len(header), len(continuation_header.replace("{index}", "999", 1)))
        chunks = split_text_for_html(text, TELEGRAM_TEXT_LIMIT - max_header_len)
        for index, chunk in enumerate(chunks, start=1):
            chunk_header = header if index == 1 else continuation_header.replace("{index}", str(index), 1)
            await bot.send_message(
                chat_id=MOTHERLODE_CHAT_ID,
                text=f"{chunk_header}{escape(chunk)}",
                parse_mode=ParseMode.HTML,
            )
        return True
    except Exception as exc:
        logger.error(f"Failed to deliver motherlode message to {MOTHERLODE_CHAT_ID}: {exc}")
        return False


async def _require_registration(event: Message | CallbackQuery, state: FSMContext, author: AiogramUser | None) -> bool:
    user = User.get_by_tg_id(author.id) if author else None
    if user and user.is_registration_complete():
        return False
    from handlers.registration import start_new_registration_flow

    data = await state.get_data()
    message = event if isinstance(event, Message) else event.message
    await message.answer(
        "Перед отправкой учебного запроса нужно заполнить анкету: имя, направление и курс "
        "(для выпускников — статус, для аспирантов — год окончания магистратуры). "
        "После регистрации вернёмся к вашему запросу."
    )
    await start_new_registration_flow(event, state, existing_data={
        "resume_motherlode": True,
        "motherlode_text": data.get("motherlode_text"),
    })
    return True


async def open_motherlode(message: Message, state: FSMContext, text: str | None = None):
    await state.clear()
    await state.set_state(Motherlode.waiting_confirmation if text else Motherlode.waiting_text)
    main_msg = await message.answer(
        _motherlode_display_text({"motherlode_text": text}),
        parse_mode=ParseMode.HTML,
        reply_markup=_motherlode_keyboard(text_ready=bool(text)),
    )
    await state.update_data(main_message_id=main_msg.message_id, motherlode_text=text)


@router.message(Command("motherlode"))
async def cmd_motherlode(message: Message, state: FSMContext):
    if await _require_registration(message, state, message.from_user):
        return
    await open_motherlode(message, state)


@router.message(StateFilter(Motherlode.waiting_text, Motherlode.waiting_confirmation))
async def motherlode_collect_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Сообщение пустое. Пожалуйста, опишите ситуацию.")
        return

    data = await state.get_data()
    main_message_id = data.get("main_message_id")
    combined_text = _append_text(data.get("motherlode_text"), text)
    await state.update_data(motherlode_text=combined_text)
    updated = await state.get_data()

    display_text = _motherlode_display_text(updated)
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
                reply_markup=_motherlode_keyboard(text_ready=True),
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest as exc:
            logger.warning(f"Failed to edit motherlode message: {exc}")
            fallback = await message.answer(
                display_text,
                reply_markup=_motherlode_keyboard(text_ready=True),
                parse_mode=ParseMode.HTML,
            )
            await state.update_data(main_message_id=fallback.message_id)
    else:
        fallback = await message.answer(
            display_text,
            reply_markup=_motherlode_keyboard(text_ready=True),
            parse_mode=ParseMode.HTML,
        )
        await state.update_data(main_message_id=fallback.message_id)

    try:
        await message.delete()
    except TelegramBadRequest:
        pass

    await state.set_state(Motherlode.waiting_confirmation)


@router.callback_query(F.data == "motherlode_send_request", Motherlode.waiting_confirmation)
async def cb_motherlode_send(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    motherlode_text = data.get("motherlode_text")
    main_message_id = data.get("main_message_id")

    if not motherlode_text:
        await callback.answer("Не найден текст сообщения. Начните заново командой /motherlode.", show_alert=True)
        await state.clear()
        return

    if await _require_registration(callback, state, callback.from_user):
        return

    delivered = await _deliver_motherlode(motherlode_text, callback.from_user)
    status = (
        "✅ Сообщение отправлено ботмейстерам."
        if delivered
        else "⚠️ Не удалось доставить сообщение ботмейстерам. Попробуйте позже."
    )

    display_text = _motherlode_display_text({"motherlode_text": motherlode_text}, status=status)

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
        logger.warning(f"Could not edit motherlode prompt message: {exc}")
        await callback.message.answer(status)

    await callback.answer("Отправлено!" if delivered else "Не удалось отправить.")
    await state.clear()


@router.callback_query(F.data == "motherlode_cancel")
async def cb_motherlode_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text("Запрос отменён.", reply_markup=None)
    except Exception:
        await callback.message.answer("Запрос отменён.")
    await callback.answer()
