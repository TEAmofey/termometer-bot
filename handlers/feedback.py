from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User as AiogramUser,
)
from aiogram.exceptions import TelegramBadRequest
from loguru import logger

from bot_instance import bot
from constants import ADMIN_IDS
from states.feedback import Feedback

router = Router()


def _feedback_keyboard(text_ready: bool) -> InlineKeyboardMarkup:
    buttons = []
    if text_ready:
        buttons.extend(
            [
                [
                    InlineKeyboardButton(
                        text="Отправить анонимно", callback_data="feedback_send_anonymous"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Отправить с именем", callback_data="feedback_send_named"
                    )
                ],
            ]
        )
    buttons.append([InlineKeyboardButton(text="Отменить", callback_data="feedback_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _feedback_mode_label(mode: str | None) -> str | None:
    if mode == "anonymous":
        return "Анонимно"
    if mode == "named":
        return "С именем"
    return None


def _feedback_display_text(data: dict, status: str | None = None) -> str:
    lines: list[str] = ["📝 <b>Отзыв</b>"]
    text = data.get("feedback_text")
    if text:
        lines.append("")
        lines.append(escape(text))
    else:
        lines.append("")
        lines.append("Текст ещё не введён. Отправьте отзыв отдельным сообщением ниже.")

    mode_label = _feedback_mode_label(data.get("feedback_mode"))
    if mode_label:
        lines.append("")
        lines.append(f"📨 Способ отправки: <b>{mode_label}</b>")
    else:
        lines.append("")
        lines.append("После ввода текста выберите способ отправки кнопками ниже.")

    if status:
        lines.append("")
        lines.append(status)

    return "\n".join(lines)


def _format_feedback_message(
    feedback_text: str,
    *,
    is_anonymous: bool,
    author: AiogramUser | None,
) -> str:
    escaped_text = escape(feedback_text)
    if is_anonymous:
        header = "📣 <b>Получен анонимный отзыв</b>"
        author_line = ""
    else:
        display_name = escape(author.full_name or "") if author else "Неизвестный пользователь"
        username = author.username if author else ""
        tg_id = author.id if author else 0
        mention = f'<a href="tg://user?id={tg_id}">{display_name}</a>' if tg_id else display_name
        if username:
            author_line = f"{mention} (@{escape(username)})"
        else:
            author_line = mention
        header = "📣 <b>Получен отзыв</b>"

        author_line = f"\n👤 {author_line}"

    body = f"\n\n📝 {escaped_text}"
    return f"{header}{author_line}{body}"


async def _deliver_feedback(
    feedback_text: str,
    *,
    is_anonymous: bool,
    author: AiogramUser | None,
) -> bool:
    if not ADMIN_IDS:
        logger.warning("No admin IDs configured; feedback message dropped.")
        return False

    payload = _format_feedback_message(
        feedback_text,
        is_anonymous=is_anonymous,
        author=author,
    )
    delivered_any = False
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, payload, parse_mode=ParseMode.HTML)
            delivered_any = True
        except Exception as exc:
            logger.error(f"Failed to deliver feedback to admin {admin_id}: {exc}")
    return delivered_any


@router.message(Command("feedback"))
async def cmd_feedback(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Feedback.waiting_text)
    main_msg = await message.answer(
        _feedback_display_text({}),
        parse_mode=ParseMode.HTML,
        reply_markup=_feedback_keyboard(text_ready=False),
    )
    await state.update_data(
        feedback_text=None,
        feedback_mode=None,
        main_message_id=main_msg.message_id,
    )


@router.message(Feedback.waiting_text)
async def feedback_collect_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Кажется, сообщение пустое. Пожалуйста, отправьте текст отзыва.")
        return

    try:
        await message.delete()
    except TelegramBadRequest:
        pass

    data = await state.get_data()
    main_message_id = data.get("main_message_id")
    await state.update_data(feedback_text=text, feedback_mode=None)
    updated = await state.get_data()

    if main_message_id:
        try:
            await bot.edit_message_text(
                _feedback_display_text(updated),
                chat_id=message.chat.id,
                message_id=main_message_id,
                reply_markup=_feedback_keyboard(text_ready=True),
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest as exc:
            logger.warning(f"Failed to edit feedback message: {exc}")
            fallback = await message.answer(
                _feedback_display_text(updated),
                reply_markup=_feedback_keyboard(text_ready=True),
                parse_mode=ParseMode.HTML,
            )
            await state.update_data(main_message_id=fallback.message_id)
    else:
        fallback = await message.answer(
            _feedback_display_text(updated),
            reply_markup=_feedback_keyboard(text_ready=True),
            parse_mode=ParseMode.HTML,
        )
        await state.update_data(main_message_id=fallback.message_id)

    await state.set_state(Feedback.waiting_choice)


async def _finalize_feedback(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    is_anonymous: bool,
) -> None:
    data = await state.get_data()
    main_message_id = data.get("main_message_id")
    feedback_text = data.get("feedback_text")
    if not feedback_text:
        await callback.answer("Не найден текст отзыва, начните заново.", show_alert=True)
        await state.clear()
        return

    mode_key = "anonymous" if is_anonymous else "named"
    await state.update_data(feedback_mode=mode_key)

    delivered = await _deliver_feedback(
        feedback_text,
        is_anonymous=is_anonymous,
        author=callback.from_user,
    )

    status = (
        "✅ Отзыв отправлен администраторам."
        if delivered
        else "⚠️ Отзыв сохранён, но не удалось уведомить администраторов."
    )

    updated = await state.get_data()
    try:
        if main_message_id:
            await bot.edit_message_text(
                _feedback_display_text(updated, status=status),
                chat_id=callback.message.chat.id,
                message_id=main_message_id,
                reply_markup=None,
                parse_mode=ParseMode.HTML,
            )
        else:
            await callback.message.edit_text(
                _feedback_display_text(updated, status=status),
                reply_markup=None,
                parse_mode=ParseMode.HTML,
            )
    except Exception as exc:
        logger.warning(f"Could not edit feedback prompt message: {exc}")
        await callback.message.answer(
            status,
            parse_mode=ParseMode.HTML,
        )

    await callback.answer("Готово!")
    await state.clear()


@router.callback_query(F.data == "feedback_send_anonymous", Feedback.waiting_choice)
async def cb_feedback_send_anonymous(callback: CallbackQuery, state: FSMContext):
    await _finalize_feedback(callback, state, is_anonymous=True)


@router.callback_query(F.data == "feedback_send_named", Feedback.waiting_choice)
async def cb_feedback_send_named(callback: CallbackQuery, state: FSMContext):
    await _finalize_feedback(callback, state, is_anonymous=False)


@router.callback_query(F.data == "feedback_cancel")
async def cb_feedback_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text("Отзыв отменён.", reply_markup=None)
    except Exception:
        await callback.message.answer("Отзыв отменён.")
    await callback.answer()
