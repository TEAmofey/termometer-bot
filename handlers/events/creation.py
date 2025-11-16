from __future__ import annotations

from datetime import datetime, time as dt_time
from html import escape
from typing import Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from bot_instance import bot
from constants import BACK
from db.base_event import STATUS_PENDING
from states.events import EventCreation

from .common import TAG_ORDER, TAG_TITLE_BY_SLUG, build_contact_info, events_repo, normalize_tags
from .details import notify_admins
from .listing import edit_events_message

router = Router()

CB_CREATE_BACK = "events:create:back"
CB_CREATE_TAG_DONE = "events:create:tags_done"
CB_CREATE_SUBMIT = "events:create:submit"
CB_CREATE_TAG_PREFIX = "events:create:tag:"

CREATION_SEQUENCE = [
    EventCreation.title,
    EventCreation.date,
    EventCreation.start_time,
    EventCreation.end_time,
    EventCreation.location,
    EventCreation.description,
    EventCreation.tags,
    EventCreation.confirm,
]

CREATION_PROMPTS = {
    EventCreation.title.state: "Введите название мероприятия.",
    EventCreation.date.state: "Укажите дату в формате ДД.ММ.ГГГГ.",
    EventCreation.start_time.state: "Введите время начала в формате ЧЧ:ММ.",
    EventCreation.end_time.state: "Введите время окончания в формате ЧЧ:ММ.",
    EventCreation.location.state: "Укажите аудиторию или место проведения.",
    EventCreation.description.state: "Добавьте краткое описание мероприятия.",
    EventCreation.tags.state: "Выберите теги, кому подойдёт мероприятие.",
    EventCreation.confirm.state: "Проверьте данные и отправьте на модерацию.",
}


def _state_index(state_name: Optional[str]) -> Optional[int]:
    if not state_name:
        return None
    for index, state in enumerate(CREATION_SEQUENCE):
        if state.state == state_name:
            return index
    return None


def _prompt_for_state(state_name: Optional[str]) -> str:
    return CREATION_PROMPTS.get(state_name or "", "")


def _build_summary(data: dict, prompt: str) -> str:
    lines = ["📋 <b>Новое мероприятие</b>"]

    title = data.get("title")
    if title:
        lines.append(f"🏷 <b>Название:</b> {escape(title)}")

    event_date = data.get("event_date")
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    if event_date:
        date_display = datetime.strptime(event_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        if start_time and end_time:
            lines.append(f"🕒 <b>Дата и время:</b> {date_display} {start_time} – {end_time}")
        elif start_time:
            lines.append(f"🕒 <b>Дата и время:</b> {date_display} {start_time}")
        else:
            lines.append(f"🗓 <b>Дата:</b> {date_display}")

    location = data.get("location")
    if location:
        lines.append(f"📍 <b>Аудитория:</b> {escape(location)}")

    description = data.get("short_description")
    if description:
        lines.append(f"📝 <b>Описание:</b> {escape(description)}")

    contact_name = data.get("contact_name")
    contact_url = data.get("contact_url")
    if contact_name and contact_url:
        lines.append(f"☎️ <b>Контакт:</b> {escape(contact_name)} ({contact_url})")

    tags = data.get("tags") or []
    if tags:
        pretty_tags = ", ".join(TAG_TITLE_BY_SLUG.get(tag, tag) for tag in tags)
        lines.append(f"🎯 <b>Теги:</b> {escape(pretty_tags)}")

    registration_link = data.get("registration_link")
    if registration_link:
        lines.append(f"🔗 <b>Ссылка:</b> {registration_link}")

    if prompt:
        lines.append(f"ℹ️ {prompt}")

    error = data.get("error")
    if error:
        lines.append(f"⚠️ {escape(error)}")

    return "\n".join(lines)


def _build_keyboard(state_name: Optional[str], data: dict) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    index = _state_index(state_name)

    if state_name == EventCreation.tags.state:
        selected = set(data.get("tags", []))
        for slug, title in TAG_TITLE_BY_SLUG.items():
            prefix = "✅" if slug in selected else "⬜️"
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{prefix} {title}",
                        callback_data=f"{CB_CREATE_TAG_PREFIX}{slug}",
                    )
                ]
            )
        rows.append([InlineKeyboardButton(text="Готово", callback_data=CB_CREATE_TAG_DONE)])
    elif state_name == EventCreation.confirm.state:
        rows.append([
            InlineKeyboardButton(text="Отправить на модерацию", callback_data=CB_CREATE_SUBMIT)
        ])

    if index is not None and index >= 0:
        rows.append([InlineKeyboardButton(text=BACK, callback_data=CB_CREATE_BACK)])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _parse_date(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(value.strip(), "%d.%m.%Y")
    except ValueError:
        return None


def _parse_time(value: str) -> Optional[dt_time]:
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        return None


async def _refresh_message(state: FSMContext) -> None:
    data = await state.get_data()
    chat_id = data.get("chat_id")
    message_id = data.get("main_message_id")
    current_state = await state.get_state()
    if not chat_id or not message_id or not current_state:
        return
    summary = _build_summary(data, _prompt_for_state(current_state))
    keyboard = _build_keyboard(current_state, data)
    try:
        await bot.edit_message_text(
            summary,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as exc:
        logger.warning(f"Failed to edit event creation message: {exc}")


async def _set_error(state: FSMContext, error: str) -> None:
    await state.update_data(error=error)
    await _refresh_message(state)


def _next_state(state_name: Optional[str]) -> Optional[str]:
    index = _state_index(state_name)
    if index is None or index + 1 >= len(CREATION_SEQUENCE):
        return None
    return CREATION_SEQUENCE[index + 1].state


def _prev_state(state_name: Optional[str]) -> Optional[str]:
    index = _state_index(state_name)
    if index is None or index == 0:
        return None
    return CREATION_SEQUENCE[index - 1].state


async def _set_defaults(state: FSMContext, user) -> None:
    name, url = build_contact_info(user)
    await state.update_data(
        contact_name=name,
        contact_url=url,
        registration_link="",
        tags=list(TAG_ORDER),
        created_by=user.id,
    )


@router.callback_query(F.data.startswith("events:add"))
async def start_creation(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(EventCreation.title)
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 0
    show_past = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 0
    await state.update_data(
        chat_id=callback.message.chat.id,
        main_message_id=callback.message.message_id,
        origin_page=page,
        origin_show_past=show_past,
        error=None,
    )
    await _set_defaults(state, callback.from_user)
    await _refresh_message(state)
    await callback.answer()


async def _handle_input(message: Message, state: FSMContext, value: str) -> None:
    current_state = await state.get_state()
    await state.update_data(error=None)

    if current_state == EventCreation.title.state:
        await state.update_data(title=value)
    elif current_state == EventCreation.date.state:
        parsed = _parse_date(value)
        if not parsed:
            await _set_error(state, "Не удалось распознать дату. Используйте формат ДД.ММ.ГГГГ.")
            return
        await state.update_data(event_date=parsed.date().isoformat())
    elif current_state == EventCreation.start_time.state:
        parsed = _parse_time(value)
        if not parsed:
            await _set_error(state, "Не удалось распознать время. Используйте формат ЧЧ:ММ.")
            return
        await state.update_data(start_time=parsed.strftime("%H:%M"))
    elif current_state == EventCreation.end_time.state:
        parsed = _parse_time(value)
        if not parsed:
            await _set_error(state, "Не удалось распознать время. Используйте формат ЧЧ:ММ.")
            return
        data = await state.get_data()
        start_str = data.get("start_time")
        if start_str:
            start_dt = datetime.strptime(start_str, "%H:%M")
            end_dt = datetime.strptime(parsed.strftime("%H:%M"), "%H:%M")
            if end_dt <= start_dt:
                await _set_error(state, "Время окончания должно быть позже времени начала.")
                return
        await state.update_data(end_time=parsed.strftime("%H:%M"))
    elif current_state == EventCreation.location.state:
        await state.update_data(location=value)
    elif current_state == EventCreation.description.state:
        await state.update_data(short_description=value)
    else:
        return

    next_state = _next_state(current_state)
    if next_state:
        await state.set_state(next_state)
    await _refresh_message(state)


@router.message(
    F.text,
    StateFilter(
        EventCreation.title,
        EventCreation.date,
        EventCreation.start_time,
        EventCreation.end_time,
        EventCreation.location,
        EventCreation.description,
    ),
)
async def handle_creation_text(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    if not value:
        await _set_error(state, "Сообщение пустое. Попробуйте ещё раз.")
        return
    await _handle_input(message, state, value)


@router.message(EventCreation.tags)
async def handle_tags_text(message: Message, state: FSMContext) -> None:
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    await _set_error(state, "Используйте кнопки ниже, чтобы выбрать теги.")


@router.callback_query(EventCreation.tags, F.data.startswith(CB_CREATE_TAG_PREFIX))
async def cb_toggle_tag(callback: CallbackQuery, state: FSMContext) -> None:
    slug = callback.data.replace(CB_CREATE_TAG_PREFIX, "", 1)
    if slug not in TAG_TITLE_BY_SLUG:
        await callback.answer()
        return
    data = await state.get_data()
    selected = set(data.get("tags", []))
    if slug in selected:
        if len(selected) == 1:
            await callback.answer("Нужно оставить хотя бы одну группу.", show_alert=True)
            return
        selected.remove(slug)
    else:
        selected.add(slug)
    await state.update_data(tags=normalize_tags(selected), error=None)
    await _refresh_message(state)
    await callback.answer()


@router.callback_query(EventCreation.tags, F.data == CB_CREATE_TAG_DONE)
async def cb_tags_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = data.get("tags") or []
    if not selected:
        await callback.answer("Нужно выбрать хотя бы одну группу.", show_alert=True)
        return
    await state.set_state(EventCreation.confirm)
    await state.update_data(error=None)
    await _refresh_message(state)
    await callback.answer()


@router.callback_query(F.data == CB_CREATE_BACK)
async def cb_creation_back(callback: CallbackQuery, state: FSMContext) -> None:
    current_state = await state.get_state()
    previous = _prev_state(current_state)
    if not previous:
        data = await state.get_data()
        page = int(data.get("origin_page", 0))
        show_past = bool(int(data.get("origin_show_past", 0)))
        await state.clear()
        await edit_events_message(callback, page, show_past)
        await callback.answer("Создание отменено")
        return

    await state.set_state(previous)
    await state.update_data(error=None)
    await _refresh_message(state)
    await callback.answer()


@router.callback_query(EventCreation.confirm, F.data == CB_CREATE_SUBMIT)
async def cb_creation_submit(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    event_date = data.get("event_date")
    start = data.get("start_time")
    end = data.get("end_time")
    if not (event_date and start and end):
        await callback.answer("Не удалось собрать данные события.", show_alert=True)
        return

    starts_at = datetime.strptime(f"{event_date} {start}", "%Y-%m-%d %H:%M")
    ends_at = datetime.strptime(f"{event_date} {end}", "%Y-%m-%d %H:%M")
    payload = {
        "title": data.get("title", ""),
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "location": data.get("location", ""),
        "short_description": data.get("short_description", ""),
        "tags": data.get("tags", []),
        "status": STATUS_PENDING,
        "created_by": callback.from_user.id,
        "creator_name": callback.from_user.full_name or "",
        "creator_username": callback.from_user.username or "",
        "contact_name": data.get("contact_name", ""),
        "contact_url": data.get("contact_url", ""),
        "registration_link": data.get("registration_link", ""),
    }

    repo = events_repo()
    event = repo.insert(payload)

    page = int(data.get("origin_page", 0))
    show = bool(int(data.get("origin_show_past", 0)))

    await state.clear()
    await edit_events_message(callback, page, show)
    await callback.answer("Отправлено на модерацию")
    await notify_admins(event)
