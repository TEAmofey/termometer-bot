from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from bot_instance import bot
from constants import ADMIN_IDS, BACK
from db.base_event import EventRecord, STATUS_PENDING
from states.events import EventEdit

from .common import (
    TAG_ORDER,
    TAG_TITLE_BY_SLUG,
    can_manage_event,
    events_repo,
    normalize_tags,
)
from .details import (
    notify_admins,
    render_event_details_message,
    update_event_message,
    update_moderation_messages,
)
from .listing import edit_events_message

router = Router()

FIELD_PROMPTS = {
    "title": "Введите новое название события.",
    "date": "Введите новую дату в формате ДД.ММ.ГГГГ.",
    "start_time": "Введите новое время начала в формате ЧЧ:ММ.",
    "end_time": "Введите новое время окончания в формате ЧЧ:ММ.",
    "location": "Введите новую аудиторию или место.",
    "description": "Введите новое описание.",
}


def _build_event_edit_keyboard(event: EventRecord, page: int, show_past: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🏷 Название", callback_data=f"events:edit_field:title:{event.id}:{page}:{show_past}"
        ),
        InlineKeyboardButton(
            text="📅 Дата", callback_data=f"events:edit_field:date:{event.id}:{page}:{show_past}"
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🕒 Начало", callback_data=f"events:edit_field:start_time:{event.id}:{page}:{show_past}"
        ),
        InlineKeyboardButton(
            text="🕒 Конец", callback_data=f"events:edit_field:end_time:{event.id}:{page}:{show_past}"
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="📍 Аудитория", callback_data=f"events:edit_field:location:{event.id}:{page}:{show_past}"
        ),
        InlineKeyboardButton(
            text="📝 Описание", callback_data=f"events:edit_field:description:{event.id}:{page}:{show_past}"
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🎯 Теги", callback_data=f"events:edit_tags:{event.id}:{page}:{show_past}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔗 Ссылка", callback_data=f"events:setlink:{event.id}:{page}:{show_past}"
        ),
        InlineKeyboardButton(
            text="🗑 Удалить", callback_data=f"events:delete:{event.id}:{page}:{show_past}"
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="👥 Участники", callback_data=f"events:participants:{event.id}:{page}:{show_past}:0"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад", callback_data=f"events:edit_close:{event.id}:{page}:{show_past}"
        )
    )
    return builder.as_markup()


def _field_keyboard(event_id: int, page: int, show_past: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BACK,
                    callback_data=f"events:edit_menu:{event_id}:{page}:{show_past}",
                )
            ]
        ]
    )


def _tags_keyboard(tags: list[str], event_id: int, page: int, show_past: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    selected = set(tags)
    for slug in TAG_ORDER:
        title = TAG_TITLE_BY_SLUG.get(slug, slug)
        prefix = "✅" if slug in selected else "⬜️"
        builder.row(
            InlineKeyboardButton(
                text=f"{prefix} {title}",
                callback_data=f"events:edit_tags_toggle:{slug}:{event_id}:{page}:{show_past}",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="Готово",
            callback_data=f"events:edit_tags_done:{event_id}:{page}:{show_past}",
        ),
        InlineKeyboardButton(
            text=BACK,
            callback_data=f"events:edit_menu:{event_id}:{page}:{show_past}",
        ),
    )
    return builder.as_markup()


def _tags_extra_lines(tags: list[str]) -> list[str]:
    titles = [TAG_TITLE_BY_SLUG.get(tag, tag) for tag in tags]
    pretty = ", ".join(titles) if titles else "–"
    return [
        "🎯 Используйте кнопки, чтобы отметить подходящие группы.",
        f"🔽 Текущий выбор: {pretty}",
    ]


def _link_extra_lines(event: EventRecord, error: Optional[str] = None) -> list[str]:
    current = event.registration_link.strip() if event.registration_link else ""
    lines = [
        "🔗 Отправьте ссылку сообщением в этот чат.",
        "➖ Отправьте '-' чтобы убрать ссылку.",
    ]
    if current:
        lines.append(f"📎 Текущая ссылка: {escape(current)}")
    else:
        lines.append("📎 Текущая ссылка: не указана.")
    if error:
        lines.append(f"⚠️ {escape(error)}")
    return lines


async def _get_context(state: FSMContext) -> Optional[tuple[EventRecord, int, int, int, int, int]]:
    data = await state.get_data()
    event_id = data.get("edit_event_id")
    chat_id = data.get("edit_chat_id")
    message_id = data.get("edit_message_id")
    user_id = data.get("edit_user_id")
    page = int(data.get("edit_page", 0) or 0)
    show_past = int(data.get("edit_show_past", 0) or 0)
    if event_id is None or chat_id is None or message_id is None or user_id is None:
        return None
    event = events_repo().get(int(event_id))
    if not event:
        return None
    return event, chat_id, message_id, user_id, page, show_past


async def _render_menu(
    state: FSMContext,
    event: EventRecord,
    *,
    extra_lines: Optional[list[str]] = None,
) -> None:
    context = await _get_context(state)
    if not context:
        return
    _, chat_id, message_id, user_id, page, show_past = context
    await update_event_message(
        chat_id=chat_id,
        message_id=message_id,
        user_id=user_id,
        event=event,
        page=page,
        show_past=show_past,
        keyboard_override=_build_event_edit_keyboard(event, page, show_past),
        extra_lines=extra_lines,
    )


async def _show_field_prompt(
    state: FSMContext,
    event: EventRecord,
    field_key: str,
    error: Optional[str] = None,
) -> None:
    context = await _get_context(state)
    if not context:
        return
    _, chat_id, message_id, user_id, page, show_past = context
    prompt = FIELD_PROMPTS.get(field_key, "")
    extra = [f"✏️ {escape(prompt)}"] if prompt else []
    if error:
        extra.append(f"⚠️ {escape(error)}")
    await update_event_message(
        chat_id=chat_id,
        message_id=message_id,
        user_id=user_id,
        event=event,
        page=page,
        show_past=show_past,
        extra_lines=extra or None,
        keyboard_override=_field_keyboard(event.id, page, show_past),
    )


async def _show_link_prompt(
    state: FSMContext,
    event: EventRecord,
    *,
    error: Optional[str] = None,
) -> None:
    context = await _get_context(state)
    if not context:
        return
    _, chat_id, message_id, user_id, page, show_past = context
    await update_event_message(
        chat_id=chat_id,
        message_id=message_id,
        user_id=user_id,
        event=event,
        page=page,
        show_past=show_past,
        extra_lines=_link_extra_lines(event, error),
        keyboard_override=_field_keyboard(event.id, page, show_past),
    )


async def _process_field_input(message: Message, state: FSMContext, field_key: str) -> None:
    context = await _get_context(state)
    if not context:
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        return
    event, _, _, _, page, show_past = context

    value = (message.text or "").strip()
    try:
        await message.delete()
    except TelegramBadRequest:
        pass

    if not value:
        await _show_field_prompt(state, event, field_key, error="Сообщение пустое.")
        return

    repo = events_repo()
    updates: dict[str, str] = {}
    prompt = FIELD_PROMPTS.get(field_key, "")

    if field_key == "title":
        updates["title"] = value
    elif field_key == "location":
        updates["location"] = value
    elif field_key == "description":
        updates["short_description"] = value
    elif field_key == "date":
        try:
            parsed_date = datetime.strptime(value, "%d.%m.%Y").date()
        except ValueError:
            await _show_field_prompt(state, event, field_key, error="Используйте формат ДД.ММ.ГГГГ.")
            return
        start_dt = event.scheduled_datetime()
        end_dt = event.end_datetime()
        if start_dt:
            new_start = start_dt.replace(year=parsed_date.year, month=parsed_date.month, day=parsed_date.day)
        else:
            new_start = datetime.combine(parsed_date, datetime.now().time())
        if end_dt:
            new_end = end_dt.replace(year=parsed_date.year, month=parsed_date.month, day=parsed_date.day)
            if new_end <= new_start:
                await _show_field_prompt(state, event, field_key, error="Дата делает время окончания раньше начала.")
                return
            updates["ends_at"] = new_end.isoformat()
        updates["starts_at"] = new_start.isoformat()
    elif field_key in {"start_time", "end_time"}:
        try:
            parsed_time = datetime.strptime(value, "%H:%M").time()
        except ValueError:
            await _show_field_prompt(state, event, field_key, error="Используйте формат ЧЧ:ММ.")
            return
        base_date = (event.scheduled_datetime() or datetime.now()).date()
        if field_key == "start_time":
            new_start = datetime.combine(base_date, parsed_time)
            end_dt = event.end_datetime()
            if end_dt and new_start >= end_dt:
                await _show_field_prompt(state, event, field_key, error="Время начала не может быть позже окончания.")
                return
            updates["starts_at"] = new_start.isoformat()
        else:
            new_end = datetime.combine(base_date, parsed_time)
            start_dt = event.scheduled_datetime()
            if start_dt and new_end <= start_dt:
                await _show_field_prompt(state, event, field_key, error="Время окончания должно быть позже начала.")
                return
            updates["ends_at"] = new_end.isoformat()
    else:
        return

    updated = repo.update(event.id, updates)
    if not updated:
        await _show_field_prompt(state, event, field_key, error="Не удалось обновить событие.")
        return

    await state.set_state(EventEdit.menu)
    await state.update_data(edit_field=None)
    await _render_menu(state, updated)


@router.callback_query(F.data.startswith("events:edit:"))
async def cb_events_edit(callback: CallbackQuery, state: FSMContext) -> None:
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
    if not can_manage_event(callback.from_user.id, event):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    await state.clear()
    await state.set_state(EventEdit.menu)
    await state.update_data(
        edit_event_id=event_id,
        edit_page=page,
        edit_show_past=show_past,
        edit_chat_id=callback.message.chat.id,
        edit_message_id=callback.message.message_id,
        edit_user_id=callback.from_user.id,
        edit_field=None,
        edit_tags=None,
    )
    await _render_menu(state, event)
    await callback.answer("Режим редактирования")


@router.callback_query(F.data.startswith("events:edit_close:"))
async def cb_events_edit_close(callback: CallbackQuery, state: FSMContext) -> None:
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
    if not can_manage_event(callback.from_user.id, event):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    await state.clear()
    await render_event_details_message(callback, event, page, show_past)
    await callback.answer("Просмотр события")


@router.callback_query(F.data.startswith("events:edit_menu:"))
async def cb_events_edit_menu(callback: CallbackQuery, state: FSMContext) -> None:
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
    if not can_manage_event(callback.from_user.id, event):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    await state.set_state(EventEdit.menu)
    await state.update_data(
        edit_event_id=event_id,
        edit_page=page,
        edit_show_past=show_past,
        edit_chat_id=callback.message.chat.id,
        edit_message_id=callback.message.message_id,
        edit_user_id=callback.from_user.id,
        edit_field=None,
        edit_tags=None,
    )
    await _render_menu(state, event)
    await callback.answer("Редактирование")


@router.callback_query(F.data.startswith("events:edit_field:"))
async def cb_events_edit_field(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 6:
        await callback.answer()
        return
    field_key = parts[2]
    try:
        event_id = int(parts[3])
        page = int(parts[4])
        show_past = int(parts[5])
    except ValueError:
        await callback.answer()
        return

    if field_key not in FIELD_PROMPTS:
        await callback.answer()
        return

    event = events_repo().get(event_id)
    if not event:
        await callback.answer("Событие не найдено.", show_alert=True)
        return
    if not can_manage_event(callback.from_user.id, event):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    await state.set_state(getattr(EventEdit, field_key))
    await state.update_data(
        edit_event_id=event_id,
        edit_page=page,
        edit_show_past=show_past,
        edit_chat_id=callback.message.chat.id,
        edit_message_id=callback.message.message_id,
        edit_user_id=callback.from_user.id,
        edit_field=field_key,
    )
    await _show_field_prompt(state, event, field_key)
    await callback.answer()


@router.message(EventEdit.title)
async def edit_title(message: Message, state: FSMContext) -> None:
    await _process_field_input(message, state, "title")


@router.message(EventEdit.date)
async def edit_date(message: Message, state: FSMContext) -> None:
    await _process_field_input(message, state, "date")


@router.message(EventEdit.start_time)
async def edit_start_time(message: Message, state: FSMContext) -> None:
    await _process_field_input(message, state, "start_time")


@router.message(EventEdit.end_time)
async def edit_end_time(message: Message, state: FSMContext) -> None:
    await _process_field_input(message, state, "end_time")


@router.message(EventEdit.location)
async def edit_location(message: Message, state: FSMContext) -> None:
    await _process_field_input(message, state, "location")


@router.message(EventEdit.description)
async def edit_description(message: Message, state: FSMContext) -> None:
    await _process_field_input(message, state, "description")


@router.callback_query(F.data.startswith("events:edit_tags:"))
async def cb_edit_tags(callback: CallbackQuery, state: FSMContext) -> None:
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
    if not can_manage_event(callback.from_user.id, event):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    tags = normalize_tags(event.tags or TAG_ORDER)
    await state.set_state(EventEdit.tags)
    await state.update_data(
        edit_event_id=event_id,
        edit_page=page,
        edit_show_past=show_past,
        edit_chat_id=callback.message.chat.id,
        edit_message_id=callback.message.message_id,
        edit_user_id=callback.from_user.id,
        edit_tags=tags,
    )
    await update_event_message(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        user_id=callback.from_user.id,
        event=event,
        page=page,
        show_past=show_past,
        extra_lines=_tags_extra_lines(tags),
        keyboard_override=_tags_keyboard(tags, event_id, page, show_past),
    )
    await callback.answer()


@router.callback_query(EventEdit.tags, F.data.startswith("events:edit_tags_toggle:"))
async def cb_edit_tags_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 6:
        await callback.answer()
        return
    slug = parts[2]
    try:
        event_id = int(parts[3])
        page = int(parts[4])
        show_past = int(parts[5])
    except ValueError:
        await callback.answer()
        return

    if slug not in TAG_TITLE_BY_SLUG:
        await callback.answer()
        return

    data = await state.get_data()
    selected = set(data.get("edit_tags") or [])
    if slug in selected:
        if len(selected) == 1:
            await callback.answer("Нужно оставить хотя бы одну группу.", show_alert=True)
            return
        selected.remove(slug)
    else:
        selected.add(slug)
    tags = normalize_tags(selected)
    await state.update_data(edit_tags=tags)

    event = events_repo().get(event_id)
    if not event:
        await callback.answer("Событие не найдено.", show_alert=True)
        return

    event.tags = tags
    await update_event_message(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        user_id=callback.from_user.id,
        event=event,
        page=page,
        show_past=show_past,
        extra_lines=_tags_extra_lines(tags),
        keyboard_override=_tags_keyboard(tags, event_id, page, show_past),
    )
    await callback.answer()


@router.callback_query(EventEdit.tags, F.data.startswith("events:edit_tags_done:"))
async def cb_edit_tags_done(callback: CallbackQuery, state: FSMContext) -> None:
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

    tags = normalize_tags((await state.get_data()).get("edit_tags") or [])
    if not tags:
        await callback.answer("Нужно выбрать хотя бы одну группу.", show_alert=True)
        return

    updated = events_repo().update(event_id, {"tags": tags})
    if not updated:
        await callback.answer("Не удалось обновить событие.", show_alert=True)
        return

    await state.set_state(EventEdit.menu)
    await state.update_data(edit_tags=None)
    await _render_menu(state, updated)
    await callback.answer("Теги обновлены")


@router.callback_query(F.data.startswith("events:setlink:"))
async def cb_set_link(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer()
        return
    try:
        field_event_id = int(parts[2])
        page = int(parts[3])
        show_past = int(parts[4])
    except ValueError:
        await callback.answer()
        return

    event = events_repo().get(field_event_id)
    if not event:
        await callback.answer("Событие не найдено.", show_alert=True)
        return
    if not can_manage_event(callback.from_user.id, event):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    await state.set_state(EventEdit.link)
    await state.update_data(
        edit_event_id=field_event_id,
        edit_page=page,
        edit_show_past=show_past,
        edit_chat_id=callback.message.chat.id,
        edit_message_id=callback.message.message_id,
        edit_user_id=callback.from_user.id,
        edit_field="link",
    )
    await _show_link_prompt(state, event)
    await callback.answer("Отправьте ссылку сообщением")


@router.message(EventEdit.link)
async def edit_link(message: Message, state: FSMContext) -> None:
    context = await _get_context(state)
    try:
        await message.delete()
    except TelegramBadRequest:
        pass

    if not context:
        await state.clear()
        return

    event, _, _, _, _, _ = context
    text = (message.text or "").strip()

    if not text:
        await _show_link_prompt(state, event, error="Сообщение пустое. Попробуйте ещё раз.")
        return

    updates = {"registration_link": "" if text == "-" else text}
    feedback = "Ссылка удалена." if text == "-" else "Ссылка обновлена."

    updated = events_repo().update(event.id, updates)
    if not updated:
        await _show_link_prompt(state, event, error="Не удалось обновить событие.")
        return

    await state.set_state(EventEdit.menu)
    await state.update_data(edit_field=None)
    await _render_menu(state, updated, extra_lines=[feedback])


@router.callback_query(F.data.startswith("events:delete:"))
async def cb_events_delete(callback: CallbackQuery, state: FSMContext) -> None:
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
    if not can_manage_event(callback.from_user.id, event):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"events:delete_confirm:{event_id}:{page}:{show_past}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=f"events:edit_menu:{event_id}:{page}:{show_past}",
                )
            ],
        ]
    )
    try:
        await callback.message.edit_text(
            "Отправить событие обратно на модерацию?",
            reply_markup=keyboard,
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("events:delete_confirm:"))
async def cb_events_delete_confirm(callback: CallbackQuery, state: FSMContext) -> None:
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

    repo = events_repo()
    event = repo.get(event_id)
    if not event:
        await callback.answer("Событие не найдено.", show_alert=True)
        return
    if not can_manage_event(callback.from_user.id, event):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    updated = repo.update(
        event_id,
        {
            "status": STATUS_PENDING,
            "approved_by": None,
            "approved_at": None,
            "moderator_note": "Событие отправлено на повторную модерацию.",
            "attendees": [],
            "moderation_messages": [],
        },
    )
    if not updated:
        await callback.answer("Не удалось обновить событие.", show_alert=True)
        return

    await notify_admins(updated)
    await state.clear()
    await edit_events_message(callback, page, bool(show_past))
    await callback.answer("Событие отправлено на модерацию")
