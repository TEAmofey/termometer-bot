from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from loguru import logger

from db.database import Database
from services.thermometer import (
    DEFAULT_THERMOMETER_SETTINGS,
    THERMOMETER_HELP_CALLBACK,
    THERMOMETER_HELP_SUFFIX,
    THERMOMETER_OK_CALLBACK,
    THERMOMETER_OK_SUFFIX,
    TIME_CHOICES,
    WEEKDAY_CHOICES,
    build_pomagator_payload,
    forward_to_pomagators,
    merge_thermometer_settings,
)

router = Router()

CB_PREFIX_TOGGLE = "thermo:toggle"
CB_PREFIX_DAY = "thermo:day:"
CB_PREFIX_TIME = "thermo:time:"


def _weekday_title(value: int) -> str:
    for weekday, title, _ in WEEKDAY_CHOICES:
        if weekday == value:
            return title
    return "Не задано"


def _load_settings(user_id: int) -> dict:
    users = Database.get().users
    doc = users.find_one({"tg_id": user_id}) or {"tg_id": user_id}
    raw_settings = doc.get("thermometer")
    settings = merge_thermometer_settings(raw_settings)
    if raw_settings is None:
        users.update_one(
            {"tg_id": user_id},
            {"$set": {"tg_id": user_id, "thermometer": settings}},
            upsert=True,
        )
    return settings


def _store_settings(user_id: int, settings: dict) -> None:
    Database.get().users.update_one(
        {"tg_id": user_id},
        {"$set": {"tg_id": user_id, "thermometer": settings}},
        upsert=True,
    )


def _build_settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    enabled = bool(settings.get("enabled", DEFAULT_THERMOMETER_SETTINGS["enabled"]))
    toggle_prefix = "🔔" if enabled else "🔕"
    toggle_text = "Отключить термометр" if enabled else "Включить термометр"
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"{toggle_prefix} {toggle_text}", callback_data=CB_PREFIX_TOGGLE)]
    ]

    day_row: list[InlineKeyboardButton] = []
    for weekday, _, short in WEEKDAY_CHOICES:
        is_selected = weekday == settings.get("weekday", DEFAULT_THERMOMETER_SETTINGS["weekday"])
        prefix = "✅" if is_selected else "⬜️"
        day_row.append(
            InlineKeyboardButton(
                text=f"{prefix} {short}",
                callback_data=f"{CB_PREFIX_DAY}{weekday}",
            )
        )
        if len(day_row) == 3:
            rows.append(day_row)
            day_row = []
    if day_row:
        rows.append(day_row)

    time_row: list[InlineKeyboardButton] = []
    for time_option in TIME_CHOICES:
        is_selected = time_option == settings.get("time", DEFAULT_THERMOMETER_SETTINGS["time"])
        prefix = "✅" if is_selected else "⬜️"
        time_row.append(
            InlineKeyboardButton(
                text=f"{prefix} {time_option}",
                callback_data=f"{CB_PREFIX_TIME}{time_option.replace(':', '')}",
            )
        )
        if len(time_row) == 2:
            rows.append(time_row)
            time_row = []
    if time_row:
        rows.append(time_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _render_settings_text(settings: dict) -> str:
    enabled = bool(settings.get("enabled", DEFAULT_THERMOMETER_SETTINGS["enabled"]))
    status_text = "включен" if enabled else "выключен"
    weekday_title = _weekday_title(settings.get("weekday", DEFAULT_THERMOMETER_SETTINGS["weekday"]))
    time_display = settings.get("time", DEFAULT_THERMOMETER_SETTINGS["time"])

    lines = [
        "🌡 <b>Термометр</b>",
        "",
        (
            "Это еженедельное сообщение с проверкой самочувствия, которое будет приходить автоматически."
            "Настройте, когда именно бот должен спрашивать у вас, всё ли в порядке."
        ),
        "",
        f"<b>Статус:</b> {status_text}",
        f"<b>Рассылка:</b> {weekday_title} в {time_display} (MSK)",
        "",
        "Используйте кнопки ниже, чтобы изменить день недели и время прихода сообщения или отключить термометр.",
    ]
    if not enabled:
        lines.append("Термометр выключен — включите его, чтобы снова получать сообщения.")
    return "\n".join(lines)


async def _append_result_note(message: Message, suffix: str) -> None:
    base_text = message.html_text or message.text or ""
    if not base_text:
        return
    normalized_suffix = suffix.strip()
    if normalized_suffix and normalized_suffix in base_text:
        new_text = base_text
    else:
        new_text = f"{base_text}{suffix}"
    try:
        await message.edit_text(new_text, reply_markup=None)
    except TelegramBadRequest as exc:
        logger.debug("Thermometer: failed to edit text for result note: {}", exc)
        try:
            await message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass


async def _refresh_settings_message(message: Message, settings: dict) -> None:
    keyboard = _build_settings_keyboard(settings)
    text = _render_settings_text(settings)
    try:
        await message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest as exc:
        logger.warning("Thermometer: failed to edit settings message: {}", exc)
        await message.answer(text, reply_markup=keyboard)


@router.message(Command("thermometer"))
async def cmd_thermometer(message: Message) -> None:
    settings = _load_settings(message.from_user.id)
    keyboard = _build_settings_keyboard(settings)
    await message.answer(
        _render_settings_text(settings),
        reply_markup=keyboard,
    )


@router.callback_query(F.data == CB_PREFIX_TOGGLE)
async def cb_toggle(callback: CallbackQuery) -> None:
    settings = _load_settings(callback.from_user.id)
    settings["enabled"] = not settings.get("enabled", DEFAULT_THERMOMETER_SETTINGS["enabled"])
    _store_settings(callback.from_user.id, settings)
    await _refresh_settings_message(callback.message, settings)
    await callback.answer("Настройки обновлены.")


@router.callback_query(F.data.startswith(CB_PREFIX_DAY))
async def cb_select_day(callback: CallbackQuery) -> None:
    try:
        weekday = int(callback.data.replace(CB_PREFIX_DAY, "", 1))
    except ValueError:
        await callback.answer("Не удалось обновить день.", show_alert=True)
        return
    if weekday < 0 or weekday > 6:
        await callback.answer("Неизвестный день недели.", show_alert=True)
        return
    settings = _load_settings(callback.from_user.id)
    if settings.get("weekday") == weekday:
        await callback.answer(f"{_weekday_title(weekday)} уже выбран.")
        return
    settings["weekday"] = weekday
    _store_settings(callback.from_user.id, settings)
    await _refresh_settings_message(callback.message, settings)
    await callback.answer(f"Термометр перенесён на {_weekday_title(weekday)}.")


@router.callback_query(F.data.startswith(CB_PREFIX_TIME))
async def cb_select_time(callback: CallbackQuery) -> None:
    raw_value = callback.data.replace(CB_PREFIX_TIME, "", 1)
    if len(raw_value) not in (3, 4):
        await callback.answer("Не удалось обновить время.", show_alert=True)
        return
    padded = raw_value.zfill(4)
    value = f"{padded[:2]}:{padded[2:]}"
    if value not in TIME_CHOICES:
        await callback.answer("Это время недоступно.", show_alert=True)
        return
    settings = _load_settings(callback.from_user.id)
    if settings.get("time") == value:
        await callback.answer("Это время уже выбрано.")
        return
    settings["time"] = value
    _store_settings(callback.from_user.id, settings)
    await _refresh_settings_message(callback.message, settings)
    await callback.answer(f"Время напоминания: {value}.")


@router.callback_query(F.data == THERMOMETER_OK_CALLBACK)
async def cb_thermo_ok(callback: CallbackQuery) -> None:
    await _append_result_note(callback.message, THERMOMETER_OK_SUFFIX)
    await callback.answer("Рады, что всё хорошо! ❤️")


@router.callback_query(F.data == THERMOMETER_HELP_CALLBACK)
async def cb_thermo_help(callback: CallbackQuery) -> None:
    payload = build_pomagator_payload(
        callback.from_user.id,
        callback.from_user.full_name,
        callback.from_user.username,
    )
    delivered = await forward_to_pomagators(payload)
    await _append_result_note(callback.message, THERMOMETER_HELP_SUFFIX)
    if delivered:
        await callback.message.answer(
            "Передали помогаторам. Мы скоро свяжемся!"
        )
        await callback.answer("Запрос отправлен.")
    else:
        await callback.message.answer(
            "Не удалось передать запрос помогаторам. Попробуйте ещё раз позже."
        )
        await callback.answer("Ошибка отправки", show_alert=True)
