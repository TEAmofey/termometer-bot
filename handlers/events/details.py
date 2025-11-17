from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from bot_instance import bot
from constants import ADMIN_IDS, BACK
from db.base_event import EventRecord, STATUS_APPROVED, STATUS_REJECTED

from .common import (
    PARTICIPANTS_PER_PAGE,
    can_manage_event,
    events_repo,
    format_tags,
    format_time_range,
    is_user_registered,
    load_event_attendees,
    number_to_emoji,
)

router = Router()


def format_event_details(event: EventRecord, extra_lines: Optional[list[str]] = None) -> str:
    lines = [f"📌 <b>{escape(event.title)}</b>"]
    start_dt = event.scheduled_datetime()
    end_dt = event.end_datetime()
    if start_dt:
        when = start_dt.strftime("%d.%m.%Y %H:%M")
        if end_dt:
            when = f"{when} – {end_dt.strftime('%H:%M')}"
        lines.append(f"🗓 {when}")
    if event.location:
        lines.append(f"📍 {escape(event.location)}")
    if event.short_description:
        lines.append("")
        lines.append(f"📝 {escape(event.short_description)}")
    if event.registration_link:
        lines.append("")
        lines.append(f'<a href="{escape(event.registration_link)}">🔗 Ссылка на видеозапись</a>')
    if event.contact_name and event.contact_url:
        lines.append("")
        lines.append(f'<a href="{escape(event.contact_url)}">☎️ {escape(event.contact_name)}</a>')
    elif event.contact:
        lines.append("")
        lines.append(f"☎️ {escape(event.contact)}")
    lines.append("")
    lines.append(f"👥 Зарегистрировано: {len(event.attendees)}")
    if event.tags:
        lines.append("")
        lines.append(f"🏷 {escape(format_tags(event.tags))}")
    if extra_lines:
        lines.append("")
        lines.extend(extra_lines)
    return "\n".join(lines)


def build_event_keyboard(
    event: EventRecord,
    viewer_id: int,
    page: int,
    show_past: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    is_registered = is_user_registered(event, viewer_id)

    if event.status == STATUS_APPROVED:
        if is_registered:
            builder.row(
                InlineKeyboardButton(
                    text="❌ Отменить запись",
                    callback_data=f"events:signoff:{event.id}:{page}:{show_past}",
                )
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text="✅ Зарегистрироваться",
                    callback_data=f"events:signup:{event.id}:{page}:{show_past}",
                )
            )
    elif is_registered:
        builder.row(
            InlineKeyboardButton(
                text="❌ Отменить запись",
                callback_data=f"events:signoff:{event.id}:{page}:{show_past}",
            )
        )

    if event.registration_link:
        builder.row(InlineKeyboardButton(text="Запись", url=event.registration_link))

    if can_manage_event(viewer_id, event):
        builder.row(
            InlineKeyboardButton(
                text="✏️ Изменить",
                callback_data=f"events:edit:{event.id}:{page}:{show_past}",
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="👥 Участники",
                callback_data=f"events:participants:{event.id}:{page}:{show_past}:0",
            )
        )

    builder.row(
        InlineKeyboardButton(
            text=BACK, callback_data=f"events:list:{page}:{show_past}"
        )
    )
    return builder.as_markup()


async def update_event_message(
    *,
    chat_id: int,
    message_id: int,
    user_id: int,
    event: EventRecord,
    page: int,
    show_past: int,
    extra_lines: Optional[list[str]] = None,
    keyboard_override: Optional[InlineKeyboardMarkup] = None,
) -> None:
    keyboard = keyboard_override or build_event_keyboard(event, user_id, page, show_past)
    text = format_event_details(event, extra_lines)
    try:
        await bot.edit_message_text(
            text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as exc:
        logger.warning(f"Failed to render event details: {exc}")


async def render_event_details_message(
    callback: CallbackQuery, event: EventRecord, page: int, show_past: int
) -> None:
    await update_event_message(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        user_id=callback.from_user.id,
        event=event,
        page=page,
        show_past=show_past,
    )


async def notify_admins(event: EventRecord) -> None:
    if not ADMIN_IDS:
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить", callback_data=f"events:approve:{event.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚫 Отклонить", callback_data=f"events:reject:{event.id}"
                )
            ],
        ]
    )
    messages = []
    for admin_id in ADMIN_IDS:
        try:
            msg = await bot.send_message(
                admin_id,
                format_event_details(event),
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            messages.append({"chat_id": admin_id, "message_id": msg.message_id})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to notify admin {admin_id}: {exc}")
    if messages:
        events_repo().update(event.id or 0, {"moderation_messages": messages})


async def update_moderation_messages(event: EventRecord) -> None:
    if not event.moderation_messages:
        return
    for entry in event.moderation_messages:
        chat_id = entry.get("chat_id")
        message_id = entry.get("message_id")
        if not (chat_id and message_id):
            continue
        try:
            await bot.edit_message_text(
                format_event_details(event),
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=None,
                disable_web_page_preview=True,
            )
        except TelegramBadRequest as exc:
            logger.warning(f"Failed to update moderation message: {exc}")


async def notify_creator(event: EventRecord, text: str) -> None:
    if not event.created_by:
        return
    try:
        await bot.send_message(event.created_by, text, disable_web_page_preview=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to notify creator {event.created_by}: {exc}")


@router.callback_query(F.data.startswith("events:details:"))
async def cb_event_details(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer()
        return
    try:
        event_id = int(parts[2])
        page = int(parts[3])
        show_past = int(parts[4])
    except ValueError:
        await callback.answer()
        return

    event = events_repo().get(event_id)
    if not event:
        await callback.answer("Событие не найдено или ещё не опубликовано.", show_alert=True)
        return
    if event.status != STATUS_APPROVED and not can_manage_event(callback.from_user.id, event):
        await callback.answer("Событие не найдено или ещё не опубликовано.", show_alert=True)
        return

    await render_event_details_message(callback, event, page, show_past)
    await callback.answer()


@router.callback_query(F.data.startswith("events:signup:"))
async def cb_events_signup(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer()
        return
    try:
        event_id = int(parts[2])
        page = int(parts[3])
        show_past = int(parts[4])
    except ValueError:
        await callback.answer()
        return

    event = events_repo().get(event_id)
    if not event:
        await callback.answer("Событие не найдено.", show_alert=True)
        return
    if event.status != STATUS_APPROVED:
        await callback.answer("Регистрация пока недоступна.", show_alert=True)
        return
    if is_user_registered(event, callback.from_user.id):
        await callback.answer("Вы уже записаны на событие.")
        return

    attendees = list(event.attendees)
    attendees.append(callback.from_user.id)
    updated = events_repo().update(event_id, {"attendees": attendees})
    if not updated:
        await callback.answer("Не удалось обновить событие.", show_alert=True)
        return

    await render_event_details_message(callback, updated, page, show_past)
    await callback.answer("Вы записались на событие.")


@router.callback_query(F.data.startswith("events:signoff:"))
async def cb_events_signoff(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer()
        return
    try:
        event_id = int(parts[2])
        page = int(parts[3])
        show_past = int(parts[4])
    except ValueError:
        await callback.answer()
        return

    event = events_repo().get(event_id)
    if not event:
        await callback.answer("Событие не найдено.", show_alert=True)
        return
    if callback.from_user.id not in event.attendees:
        await callback.answer("Вы не записаны на это событие.")
        return

    attendees = [uid for uid in event.attendees if uid != callback.from_user.id]
    updated = events_repo().update(event_id, {"attendees": attendees})
    if not updated:
        await callback.answer("Не удалось обновить событие.", show_alert=True)
        return

    await render_event_details_message(callback, updated, page, show_past)
    await callback.answer("Запись отменена.")


@router.callback_query(F.data.startswith("events:participants:"))
async def cb_events_participants(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 6:
        await callback.answer()
        return
    try:
        event_id = int(parts[2])
        event_page = int(parts[3])
        show_past = int(parts[4])
        users_page = int(parts[5])
    except ValueError:
        await callback.answer()
        return

    event = events_repo().get(event_id)
    if not event:
        await callback.answer("Событие не найдено.", show_alert=True)
        return
    if not can_manage_event(callback.from_user.id, event):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    attendees = load_event_attendees(event)
    total = len(attendees)
    total_pages = max(1, (total + PARTICIPANTS_PER_PAGE - 1) // PARTICIPANTS_PER_PAGE)
    users_page = max(0, min(users_page, total_pages - 1))
    start = users_page * PARTICIPANTS_PER_PAGE
    subset = attendees[start : start + PARTICIPANTS_PER_PAGE]

    lines = [f"👥 <b>Участники события:</b>", f"<i>{escape(event.title)}</i>", ""]
    if not subset:
        lines.append("Пока никто не зарегистрировался.")
    else:
        for offset, user in enumerate(subset):
            idx = start + offset + 1
            emoji_idx = number_to_emoji(idx)
            name = escape(user.get_name() or "Без имени")
            username = user.get_username()
            if username:
                contact = f"@{username}"
            else:
                contact = f"<code>{user.tg_id}</code>"
            lines.append(f"{emoji_idx} {name} — {contact}")

    builder = InlineKeyboardBuilder()
    nav_buttons: list[InlineKeyboardButton] = []
    if users_page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"events:participants:{event_id}:{event_page}:{show_past}:{users_page - 1}",
            )
        )
    if users_page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"events:participants:{event_id}:{event_page}:{show_past}:{users_page + 1}",
            )
        )
    nav_buttons.append(
        InlineKeyboardButton(
            text=BACK,
            callback_data=f"events:details:{event_id}:{event_page}:{show_past}",
        )
    )
    builder.row(*nav_buttons)

    try:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=builder.as_markup(),
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as exc:
        logger.warning(f"Failed to show participants: {exc}")
    await callback.answer()


@router.callback_query(F.data.startswith("events:approve:"))
async def cb_events_approve(callback: CallbackQuery) -> None:
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    try:
        event_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return

    event = events_repo().get(event_id)
    if not event:
        await callback.answer("Событие не найдено.", show_alert=True)
        return
    if event.status == STATUS_APPROVED:
        await callback.answer("Уже одобрено.")
        return

    updated = events_repo().update(
        event_id,
        {
            "status": STATUS_APPROVED,
            "approved_by": callback.from_user.id,
            "approved_at": datetime.now().isoformat(),
            "moderator_note": None,
        },
    )
    if not updated:
        await callback.answer("Не удалось обновить событие.", show_alert=True)
        return

    await update_moderation_messages(updated)
    await notify_creator(
        updated,
        f"Ваше мероприятие «{updated.title}» одобрено и доступно в /events.",
    )
    await callback.answer("Событие одобрено.")


@router.callback_query(F.data.startswith("events:reject:"))
async def cb_events_reject(callback: CallbackQuery) -> None:
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    try:
        event_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return

    event = events_repo().get(event_id)
    if not event:
        await callback.answer("Событие не найдено.", show_alert=True)
        return
    if event.status == STATUS_REJECTED:
        await callback.answer("Событие уже отклонено.")
        return

    updated = events_repo().update(
        event_id,
        {
            "status": STATUS_REJECTED,
            "approved_by": callback.from_user.id,
            "approved_at": datetime.now().isoformat(),
            "moderator_note": "Событие отклонено администратором.",
        },
    )
    if not updated:
        await callback.answer("Не удалось обновить событие.", show_alert=True)
        return

    await update_moderation_messages(updated)
    await notify_creator(
        updated,
        f"Ваше мероприятие «{updated.title}» отклонено. Вы можете обновить данные и отправить на модерацию повторно.",
    )
    await callback.answer("Событие отклонено.")
