from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Iterable

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from constants import ADMIN_IDS, BACK, NEXT, PREVIOUS, RELOAD
from db.database import Database
from db.user import User
from handlers.events.common import PARTICIPANTS_PER_PAGE, number_to_emoji

router = Router()


def _format_contact_link(user: User) -> str:
    username = (user.get_username() or "").strip()
    tg_id = user.tg_id
    if username:
        handle = escape(username)
        return f'<a href="https://t.me/{handle}">@{handle}</a>'
    if tg_id:
        return f'<a href="tg://user?id={tg_id}">Открыть чат</a>'
    return "Контакт недоступен"


def _contact_url(user: User) -> str:
    username = (user.get_username() or "").strip()
    if username:
        return f"https://t.me/{username}"
    if user.tg_id:
        return f"tg://user?id={user.tg_id}"
    return ""


def _load_registered_users() -> list[User]:
    docs = Database.get().users.find({"registration_completed_at": {"$ne": None}})
    users = [User(doc) for doc in docs if doc]
    # Deduplicate by tg_id just in case old records remain.
    deduped: dict[int, User] = {}
    for user in users:
        if user.tg_id is None:
            continue
        deduped[user.tg_id] = user
    return sorted(
        deduped.values(),
        key=lambda item: (item.get_name() or "").lower() or str(item.tg_id or ""),
    )


def _paginate(items: Iterable[User], page: int) -> tuple[list[User], int, int]:
    all_items = list(items)
    total = len(all_items)
    total_pages = max(1, (total + PARTICIPANTS_PER_PAGE - 1) // PARTICIPANTS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * PARTICIPANTS_PER_PAGE
    return all_items[start : start + PARTICIPANTS_PER_PAGE], total_pages, page


def _render_list_text(users: list[User], page: int) -> str:
    subset, total_pages, normalized_page = _paginate(users, page)
    lines = [
        "📒 <b>Участники</b>",
        f"Страница {normalized_page + 1} из {total_pages}",
        "",
    ]
    if not subset:
        lines.append("Пока нет завершивших регистрацию.")
    else:
        start = normalized_page * PARTICIPANTS_PER_PAGE
        for offset, user in enumerate(subset):
            name = escape(user.get_name() or "Без имени")
            idx = start + offset + 1
            lines.append(f"{number_to_emoji(idx)} {name} — {_format_contact_link(user)}")
    return "\n".join(lines)


def _build_list_keyboard(users: list[User], page: int):
    subset, total_pages, normalized_page = _paginate(users, page)
    start = normalized_page * PARTICIPANTS_PER_PAGE

    # Navigation row (top)
    nav_row: list[InlineKeyboardButton] = [
        InlineKeyboardButton(
            text=RELOAD,
            callback_data=f"admin:users:list:{normalized_page}",
        )
    ]
    if normalized_page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text=PREVIOUS,
                callback_data=f"admin:users:list:{normalized_page - 1}",
            )
        )
    if normalized_page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(
                text=NEXT,
                callback_data=f"admin:users:list:{normalized_page + 1}",
            )
        )

    user_rows: list[list[InlineKeyboardButton]] = []
    for offset, user in enumerate(subset):
        idx = start + offset + 1
        user_button = InlineKeyboardButton(
            text=f"{number_to_emoji(idx)} {user.get_name() or 'Без имени'}",
            callback_data=f"admin:users:view:{user.tg_id}:{normalized_page}",
        )
        user_rows.append([user_button])

    # Group users in pairs
    grouped_user_rows: list[list[InlineKeyboardButton]] = []
    for idx in range(0, len(user_rows), 2):
        left = user_rows[idx][0]
        right = user_rows[idx + 1][0] if idx + 1 < len(user_rows) else None
        row = [left]
        if right:
            row.append(right)
        grouped_user_rows.append(row)

    rows: list[list[InlineKeyboardButton]] = []
    if nav_row:
        rows.append(nav_row)
    rows.extend(grouped_user_rows)

    return _render_list_text(users, normalized_page), InlineKeyboardMarkup(inline_keyboard=rows)


def _render_user_details(user: User) -> str:
    lines = [
        f"👤 <b>{escape(user.get_name() or 'Без имени')}</b>",
        f"🆔 <code>{user.tg_id}</code>",
    ]
    username = user.get_username()
    if username:
        lines.append(f"📨 @{escape(username)}")
    direction = user.get_direction()
    if direction:
        lines.append(f"🎯 Направление: {escape(direction)}")
    course = user.get_magistracy_graduation_year()
    if course:
        lines.append(f"🎓 Курс/год: {escape(str(course))}")
    registered_at = user.raw.get("registration_completed_at")
    if registered_at:
        try:
            dt = datetime.fromisoformat(str(registered_at))
            registered_at = dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            registered_at = str(registered_at)
        lines.append(f"✨ Регистрация завершена: {escape(registered_at)}")

    lines.extend(
        [
            "",
            _format_contact_link(user),
        ]
    )
    return "\n".join(lines)


@router.message(Command("participants"))
async def cmd_list_participants(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Недостаточно прав.")
        return

    users = _load_registered_users()
    text, keyboard = _build_list_keyboard(users, page=0)
    await message.answer(
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("admin:users:list:"))
async def cb_users_list(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    try:
        page = int(callback.data.split(":")[3])
    except (IndexError, ValueError):
        await callback.answer()
        return

    users = _load_registered_users()
    text, keyboard = _build_list_keyboard(users, page=page)
    try:
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:users:view:"))
async def cb_user_details(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    try:
        _, _, _, raw_user_id, raw_page = callback.data.split(":")
        user_id = int(raw_user_id)
        page = int(raw_page)
    except (ValueError, IndexError):
        await callback.answer()
        return

    users = _load_registered_users()
    user = next((item for item in users if item.tg_id == user_id), None)
    if not user:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    keyboard = InlineKeyboardBuilder()
    contact_url = _contact_url(user)
    if contact_url:
        keyboard.row(InlineKeyboardButton(text="Открыть чат", url=contact_url))
    keyboard.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=f"admin:users:list:{page}",
        )
    )

    try:
        await callback.message.edit_text(
            _render_user_details(user),
            reply_markup=keyboard.as_markup(),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        await callback.message.answer(
            _render_user_details(user),
            reply_markup=keyboard.as_markup(),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    await callback.answer()
