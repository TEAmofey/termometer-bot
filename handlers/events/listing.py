from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Optional, Sequence

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from constants import EVENTS_PAGE_SIZE, NEXT, PREVIOUS, RELOAD

from .common import (
    events_repo,
    event_visible_for_user,
    format_time_range,
    load_user,
    number_to_emoji,
    sort_events,
)

router = Router()


def _split_events_by_time(events: Sequence) -> tuple[list, list]:
    today = datetime.now().date()
    upcoming: list = []
    past: list = []
    for event in events:
        start = event.scheduled_datetime()
        if not start or start.date() >= today:
            upcoming.append(event)
        else:
            past.append(event)
    return sort_events(upcoming), sorted(
        past,
        key=lambda item: item.scheduled_datetime() or datetime.min,
        reverse=True,
    )


def _format_event_list_entry(index: int, event) -> list[str]:
    emoji_index = number_to_emoji(index)
    lines = [f"{emoji_index} <b>{escape(event.title)}</b>"]
    lines.append(f"🕒 {format_time_range(event)}")
    if event.location:
        lines.append(f"📍 {escape(event.location)}")
    return lines


def _build_events_message(
    events_page: list,
    start_index: int,
    upcoming_count: int,
    show_past: bool,
) -> str:
    if not events_page:
        if show_past:
            return "История событий пока пустая."
        return "🔜 Сейчас нет актуальных событий. Предложите своё мероприятие через кнопку ниже."

    lines: list[str] = []
    current_section: Optional[str] = None

    for offset, event in enumerate(events_page):
        global_index = start_index + offset
        is_upcoming = global_index < upcoming_count
        section_key = "upcoming" if is_upcoming else "past"

        if section_key != current_section:
            if lines and lines[-1] != "":
                lines.append("")
            header = (
                "<b>Актуальные события ⬇️</b>"
                if section_key == "upcoming"
                else "<b>Прошедшие события ⬇️</b>"
            )
            lines.append(header)
            lines.append("")
        current_section = section_key

        lines.extend(_format_event_list_entry(global_index + 1, event))
        lines.append("")

    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _build_events_keyboard(
    events_page: list,
    page: int,
    show_past: bool,
    total_pages: int,
    start_index: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle_text = "🕰 Прошедшие" if not show_past else "🆕 Актуальные"

    nav_buttons: list[InlineKeyboardButton] = [
        InlineKeyboardButton(
            text=toggle_text, callback_data=f"events:toggle:{page}:{int(show_past)}"
        ),
        InlineKeyboardButton(
            text=RELOAD, callback_data=f"events:refresh:{page}:{int(show_past)}"
        ),
    ]
    if total_pages > 1 and page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                text=PREVIOUS,
                callback_data=f"events:list:{page - 1}:{int(show_past)}",
            )
        )
    if total_pages > 1 and page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(
                text=NEXT,
                callback_data=f"events:list:{page + 1}:{int(show_past)}",
            )
        )
    if nav_buttons:
        builder.row(*nav_buttons)

    event_buttons: list[InlineKeyboardButton] = []
    for offset, event in enumerate(events_page):
        index = start_index + offset + 1
        event_buttons.append(
            InlineKeyboardButton(
                text=number_to_emoji(index),
                callback_data=f"events:details:{event.id}:{page}:{int(show_past)}",
            )
        )
    for idx in range(0, len(event_buttons), 2):
        builder.row(*event_buttons[idx : idx + 2])

    builder.row(
        InlineKeyboardButton(
            text="➕ Добавить событие",
            callback_data=f"events:add:{page}:{int(show_past)}",
        )
    )
    return builder.as_markup()


def render_events_view(
    user_id: int, page: int, show_past: bool
) -> tuple[str, InlineKeyboardMarkup, int, int]:
    user = load_user(user_id)
    events = [event for event in events_repo().list_all() if event_visible_for_user(event, user)]
    upcoming, past = _split_events_by_time(events)

    combined = upcoming + (past if show_past else [])
    total_count = len(combined)

    if total_count == 0:
        text = (
            "История событий пока пустая."
            if show_past
            else "🔜 Сейчас нет актуальных событий. Предложите своё мероприятие через кнопку ниже."
        )
        keyboard = _build_events_keyboard([], 0, show_past, 1, 0)
        return text, keyboard, 1, 0

    total_pages = max(1, (total_count + EVENTS_PAGE_SIZE - 1) // EVENTS_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start_index = page * EVENTS_PAGE_SIZE
    events_page = combined[start_index : start_index + EVENTS_PAGE_SIZE]

    text = _build_events_message(events_page, start_index, len(upcoming), show_past)
    keyboard = _build_events_keyboard(
        events_page, page, show_past, total_pages, start_index
    )
    return text, keyboard, total_pages, page


async def edit_events_message(callback: CallbackQuery, page: int, show_past: bool) -> None:
    text, keyboard, _, _ = render_events_view(callback.from_user.id, page, show_past)
    try:
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to edit events list message: {exc}")


@router.message(Command("events"))
async def cmd_events(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, keyboard, _, _ = render_events_view(message.from_user.id, 0, False)
    await message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)


@router.callback_query(F.data.startswith("events:list:"))
async def cb_events_list(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    try:
        page = int(parts[2])
        show_past = bool(int(parts[3]))
    except ValueError:
        await callback.answer()
        return
    await edit_events_message(callback, page, show_past)
    await callback.answer()


@router.callback_query(F.data.startswith("events:refresh:"))
async def cb_events_refresh(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    try:
        page = int(parts[2])
        show_past = bool(int(parts[3]))
    except ValueError:
        await callback.answer()
        return
    await edit_events_message(callback, page, show_past)
    await callback.answer()


@router.callback_query(F.data.startswith("events:toggle:"))
async def cb_events_toggle(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    try:
        page = int(parts[2])
        show_past = bool(int(parts[3]))
    except ValueError:
        await callback.answer()
        return
    await edit_events_message(callback, 0, not show_past)
    await callback.answer()
