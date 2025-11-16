from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from loguru import logger

from bot_instance import bot
from constants import (
    BACK,
    BACHELOR_DIRECTIONS,
    CONFIRM_FINAL_BUTTON_TEXT,
    DIRECTION_OPTIONS,
    EDIT_FROM_CONFIRM_BUTTON_TEXT,
    GRADUATION_BACHELOR_OPTIONS,
    GRADUATION_MASTER_OPTIONS,
    HELP_MESSAGE,
    MASTER_DIRECTIONS,
    POSTGRADUATE_DIRECTION,
    REG_MESSAGES_NEW,
    REREGISTER_BUTTON_TEXT_PROFILE,
    START_MESSAGE,
)
from db.database import Database
from db.user import User
from states.registration import Registration
from utils.misc import update_commands_for_user
from utils.users import get_direction_track

router = Router()


def get_display_profile_text(data: dict, current_step_prompt: str = "") -> str:
    parts: list[str] = []
    name = data.get("name")
    if name:
        parts.append(f"👤 <b>ФИО:</b> {name}")

    direction = data.get("direction")
    if direction:
        parts.append(f"🎯 <b>Направление:</b> {direction}")

    track = data.get("direction_track") or get_direction_track(direction or "")
    graduation_value = data.get("magistracy_graduation_year")
    if graduation_value:
        if track == "postgraduate":
            parts.append(
                f"📅 <b>Год окончания магистратуры:</b> {graduation_value}"
            )
        else:
            parts.append(f"🎓 <b>Курс:</b> {graduation_value}")

    profile_str = "\n".join(parts) if parts else ""
    if not profile_str:
        return current_step_prompt
    if current_step_prompt:
        profile_str += f"\n\n{current_step_prompt}"
    return profile_str


def create_registration_keyboard(
    current_state_name: str | None,
    extra_rows: list[list[InlineKeyboardButton]] | None = None,
) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    if extra_rows:
        rows.extend(extra_rows)
    if current_state_name and current_state_name != Registration.name.state:
        rows.append([InlineKeyboardButton(text=BACK, callback_data="previous_step")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def build_option_rows(options: Iterable[str], prefix: str, per_row: int = 2) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    current_row: list[InlineKeyboardButton] = []
    for index, option in enumerate(options):
        current_row.append(
            InlineKeyboardButton(text=option, callback_data=f"{prefix}:{index}")
        )
        if len(current_row) >= per_row:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    return rows


def build_direction_keyboard() -> InlineKeyboardMarkup:
    rows = build_option_rows(DIRECTION_OPTIONS, "direction_select")
    return create_registration_keyboard(Registration.direction.state, extra_rows=rows)


async def show_direction_step(
    chat_id: int,
    message_id: int,
    state: FSMContext,
    data: dict,
    prompt_text: str,
) -> None:
    keyboard = build_direction_keyboard()
    await bot.edit_message_text(
        get_display_profile_text(data, prompt_text),
        chat_id=chat_id,
        message_id=message_id,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Registration.direction)


def course_prompt_for_track(track: str) -> str:
    if track == "bachelor":
        return REG_MESSAGES_NEW["graduation_bachelor"]
    if track == "master":
        return REG_MESSAGES_NEW["graduation_master"]
    return REG_MESSAGES_NEW["graduation_postgraduate"]


def course_options_for_track(track: str) -> list[str]:
    if track == "bachelor":
        return GRADUATION_BACHELOR_OPTIONS
    if track == "master":
        return GRADUATION_MASTER_OPTIONS
    return []


async def proceed_to_graduation_step(
    chat_id: int,
    message_id: int,
    state: FSMContext,
    *,
    direction: str,
) -> None:
    track = get_direction_track(direction)
    if not track:
        return

    await state.update_data(direction=direction, direction_track=track)
    updated_data = await state.get_data()
    prompt_text = course_prompt_for_track(track)
    options = course_options_for_track(track)
    extra_rows = build_option_rows(options, "graduation_select") if options else None
    keyboard = create_registration_keyboard(Registration.graduation.state, extra_rows=extra_rows)
    await bot.edit_message_text(
        get_display_profile_text(updated_data, prompt_text),
        chat_id=chat_id,
        message_id=message_id,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Registration.graduation)


async def show_confirmation(
    chat_id: int,
    message_id: int,
    state: FSMContext,
) -> None:
    updated_data = await state.get_data()
    confirm_text = get_display_profile_text(updated_data, REG_MESSAGES_NEW["confirm"])
    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=CONFIRM_FINAL_BUTTON_TEXT,
                    callback_data="confirm_registration_final",
                )
            ],
            [
                InlineKeyboardButton(
                    text=EDIT_FROM_CONFIRM_BUTTON_TEXT,
                    callback_data="edit_from_confirm",
                )
            ],
        ]
    )
    await bot.edit_message_text(
        confirm_text,
        chat_id=chat_id,
        message_id=message_id,
        reply_markup=confirm_keyboard,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Registration.confirm)


@router.message(Command("help"))
async def send_help(message: Message):
    user_id = message.from_user.id
    await update_commands_for_user(user_id)
    await message.reply(text=HELP_MESSAGE, parse_mode=ParseMode.HTML)


async def start_new_registration_flow(
    message_or_callback: Message | CallbackQuery,
    state: FSMContext,
    existing_data: dict | None = None,
) -> None:
    await state.clear()
    if existing_data:
        await state.update_data(**existing_data)

    await state.set_state(Registration.name)
    current_data = await state.get_data()
    text = get_display_profile_text(current_data, REG_MESSAGES_NEW["name"])
    keyboard = create_registration_keyboard(Registration.name.state)

    if isinstance(message_or_callback, Message):
        main_msg = await message_or_callback.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    else:
        try:
            await message_or_callback.message.delete()
        except TelegramBadRequest:
            logger.warning("Could not delete message before starting registration flow.")
        main_msg = await message_or_callback.message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        await message_or_callback.answer()

    await state.update_data(main_message_id=main_msg.message_id)


@router.message(Command("start", "profile"))
async def send_welcome(message: Message, command: CommandObject, state: FSMContext):
    tg_id = message.from_user.id
    if command.command == "start":
        await message.answer(START_MESSAGE, parse_mode=ParseMode.HTML)
    await update_commands_for_user(tg_id)
    username = message.from_user.username
    logger.info(
        f"User {tg_id} (@{username or 'NoUsername'}) started interaction with command: {message.text}"
    )

    command_args: str = command.args or ""
    users_db = Database.get().users
    user_data_db = users_db.find_one({"tg_id": tg_id})
    user = User(user_data_db) if user_data_db else None
    if user and not user.is_registration_complete():
        user = None

    if command_args.startswith("register_"):
        event_id = command_args.split("_", 1)[1]
        if user:
            msg_text = register(event_id, tg_id)
            await message.reply(msg_text, parse_mode=ParseMode.HTML)
            logger.info(f"User {tg_id} auto-registered for event {event_id} (already in system).")
        else:
            logger.info(f"User {tg_id} needs to register before event {event_id}. Starting flow.")
            await start_new_registration_flow(message, state, existing_data={"event_id": event_id})
        return

    if user:
        profile_text = get_display_profile_text(user.raw)
        profile_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=REREGISTER_BUTTON_TEXT_PROFILE,
                        callback_data="initiate_reregistration_flow",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="👍 Все хорошо, ничего не менять",
                        callback_data="profile_confirmed_show_help",
                    )
                ],
            ]
        )
        await message.reply(profile_text, reply_markup=profile_keyboard, parse_mode=ParseMode.HTML)
        logger.info(f"User {tg_id} viewed existing profile.")
    else:
        logger.info(f"User {tg_id} is not registered. Starting registration flow.")
        await start_new_registration_flow(message, state)


@router.callback_query(F.data == "initiate_reregistration_flow")
async def cb_initiate_reregistration_flow(callback: CallbackQuery, state: FSMContext):
    logger.info(f"User {callback.from_user.id} initiated re-registration.")
    await start_new_registration_flow(callback, state)


@router.message(Registration.name)
async def process_name(message: Message, state: FSMContext):
    if message.text and message.text.startswith("/"):
        await message.delete()
        user_data = await state.get_data()
        main_message_id = user_data.get("main_message_id")
        if main_message_id:
            error_text = (
                "⚠️ Сначала завершите регистрацию, затем используйте команды.\n\n"
                + REG_MESSAGES_NEW["name"]
            )
            keyboard = create_registration_keyboard(Registration.name.state)
            try:
                await bot.edit_message_text(
                    error_text,
                    chat_id=message.chat.id,
                    message_id=main_message_id,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                )
            except TelegramBadRequest:
                logger.warning("Failed to edit message in process_name.")
        return

    await message.delete()
    user_data = await state.get_data()
    main_message_id = user_data.get("main_message_id")
    if not main_message_id:
        logger.error("main_message_id missing during name processing.")
        await start_new_registration_flow(message, state, existing_data=user_data)
        return

    await state.update_data(name=message.text.strip())
    updated_data = await state.get_data()
    prompt_text = REG_MESSAGES_NEW["direction"]
    keyboard = build_direction_keyboard()
    await bot.edit_message_text(
        get_display_profile_text(updated_data, prompt_text),
        chat_id=message.chat.id,
        message_id=main_message_id,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Registration.direction)


@router.message(Registration.direction)
async def process_direction(message: Message, state: FSMContext):
    if message.text and message.text.startswith("/"):
        await message.delete()
        user_data = await state.get_data()
        main_message_id = user_data.get("main_message_id")
        if main_message_id:
            error_text = (
                "⚠️ Сначала завершите регистрацию, затем используйте команды.\n\n"
                + REG_MESSAGES_NEW["direction"]
            )
            keyboard = build_direction_keyboard()
            try:
                await bot.edit_message_text(
                    error_text,
                    chat_id=message.chat.id,
                    message_id=main_message_id,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                )
            except TelegramBadRequest:
                logger.warning("Failed to edit message in process_direction for slash command.")
        return

    await message.delete()
    user_data = await state.get_data()
    main_message_id = user_data.get("main_message_id")
    if not main_message_id:
        return

    direction = (message.text or "").strip()
    track = get_direction_track(direction)
    if not track:
        await show_direction_step(
            message.chat.id,
            main_message_id,
            state,
            user_data,
            "⚠️ Не удалось распознать направление. Попробуйте выбрать из списка или ввести своё.",
        )
        return

    await proceed_to_graduation_step(
        message.chat.id,
        main_message_id,
        state,
        direction=direction,
    )


@router.callback_query(F.data.startswith("direction_select"), Registration.direction)
async def cb_direction_select(callback: CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    main_message_id = user_data.get("main_message_id")
    if main_message_id is None:
        await callback.answer("Сообщение не найдено, начните регистрацию заново.", show_alert=True)
        return

    try:
        _, index_str = callback.data.split(":", 1)
        index = int(index_str)
        direction = DIRECTION_OPTIONS[index]
    except (ValueError, IndexError):
        await callback.answer("Не удалось определить направление.", show_alert=True)
        return

    await proceed_to_graduation_step(
        callback.message.chat.id,
        main_message_id,
        state,
        direction=direction,
    )
    await callback.answer(f"Выбрано направление: {direction}")


def extract_course_number(text: str, valid_options: Iterable[int]) -> str | None:
    match = re.search(r"\d{1,2}", text)
    if not match:
        return None
    value = match.group()
    if int(value) in valid_options:
        return value
    return None


def extract_graduation_year(text: str) -> str | None:
    match = re.search(r"(20\d{2})", text)
    if not match:
        return None
    year = int(match.group())
    if 2000 <= year <= 2100:
        return str(year)
    return None


@router.callback_query(F.data == "edit_from_confirm")
async def cb_edit_from_confirm(callback: CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    main_message_id = user_data.get("main_message_id")
    if not main_message_id:
        await callback.answer("Произошла ошибка. Начните регистрацию заново.", show_alert=True)
        return

    track = user_data.get("direction_track") or get_direction_track(user_data.get("direction", ""))
    if not track:
        await callback.answer("Не удалось определить направление.", show_alert=True)
        return

    prompt_text = course_prompt_for_track(track)
    options = course_options_for_track(track)
    extra_rows = build_option_rows(options, "graduation_select") if options else None
    keyboard = create_registration_keyboard(Registration.graduation.state, extra_rows=extra_rows)
    await bot.edit_message_text(
        get_display_profile_text(user_data, prompt_text),
        chat_id=callback.message.chat.id,
        message_id=main_message_id,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Registration.graduation)
    await callback.answer("Измените информацию о курсе или годе окончания.")


@router.callback_query(F.data == "confirm_registration_final", Registration.confirm)
async def cb_confirm_registration_final(callback: CallbackQuery, state: FSMContext):
    user_data_from_state = await state.get_data()

    main_message_id = user_data_from_state.pop("main_message_id", None)
    direction_track = user_data_from_state.pop("direction_track", None)

    graduation_value = user_data_from_state.get("magistracy_graduation_year")
    if direction_track != "postgraduate" and graduation_value:
        user_data_from_state["magistracy_graduation_year"] = str(graduation_value)

    user = User(user_data_from_state)
    user.tg_id = callback.from_user.id
    user.raw["username"] = callback.from_user.username or ""

    existing_doc = Database.get().users.find_one({"tg_id": user.tg_id}) or {}
    merged_payload = dict(existing_doc)
    merged_payload.update(user.raw)

    timestamp = datetime.now(timezone.utc).isoformat()
    if not merged_payload.get("registration_completed_at"):
        merged_payload["registration_completed_at"] = timestamp

    thermometer_settings = merged_payload.get("thermometer")
    if isinstance(thermometer_settings, dict):
        thermometer_settings = dict(thermometer_settings)
    else:
        thermometer_settings = {}
    if not thermometer_settings.get("last_sent_at"):
        thermometer_settings["last_sent_at"] = timestamp
    merged_payload["thermometer"] = thermometer_settings

    user.raw = merged_payload

    logger.info(f"User {user.tg_id} confirmed registration. Data: {user.raw}")
    user.save_to_db()

    await callback.answer("Ваши данные сохранены!", show_alert=True)

    help_text = HELP_MESSAGE
    help_message_sent = False
    if main_message_id:
        try:
            await bot.edit_message_text(
                help_text,
                chat_id=callback.message.chat.id,
                message_id=main_message_id,
                reply_markup=None,
                parse_mode=ParseMode.HTML,
            )
            help_message_sent = True
        except TelegramBadRequest:
            logger.warning("Could not edit final registration message.")

    event_id = user_data_from_state.get("event_id")
    if event_id:
        logger.info(f"User {user.tg_id} registering for event {event_id} after onboarding.")
        registration_response = register(event_id, callback.from_user.id)
        await callback.message.answer(
            f"{registration_response}\nТеперь ваша регистрация в системе завершена!",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.HTML,
        )

    if not help_message_sent:
        await callback.message.answer(help_text, parse_mode=ParseMode.HTML)

    await state.clear()
    logger.info(f"User {user.tg_id} registration process finished. State cleared.")


@router.callback_query(F.data == "profile_confirmed_show_help")
async def cb_profile_confirmed_show_help(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    logger.info(f"User {callback.from_user.id} confirmed profile, showing help.")

    try:
        await callback.message.edit_text(
            HELP_MESSAGE,
            reply_markup=None,
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest:
        logger.warning("Failed to edit message to HELP_MESSAGE, sending new message.")
        await callback.message.answer(
            HELP_MESSAGE,
            reply_markup=None,
            parse_mode=ParseMode.HTML,
        )

    await callback.answer()


@router.message(Registration.graduation)
async def process_graduation(message: Message, state: FSMContext):
    if message.text and message.text.startswith("/"):
        await message.delete()
        user_data = await state.get_data()
        main_message_id = user_data.get("main_message_id")
        track = user_data.get("direction_track", "")
        if main_message_id:
            prompt_text = (
                "⚠️ Сначала завершите регистрацию, затем используйте команды.\n\n"
                + course_prompt_for_track(track or "bachelor")
            )
            options = course_options_for_track(track or "")
            extra_rows = build_option_rows(options, "graduation_select") if options else None
            keyboard = create_registration_keyboard(Registration.graduation.state, extra_rows=extra_rows)
            try:
                await bot.edit_message_text(
                    get_display_profile_text(user_data, prompt_text),
                    chat_id=message.chat.id,
                    message_id=main_message_id,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                )
            except TelegramBadRequest:
                logger.warning("Failed to edit message in process_graduation for slash command.")
        return

    await message.delete()
    user_data = await state.get_data()
    main_message_id = user_data.get("main_message_id")
    if main_message_id is None:
        return

    track = user_data.get("direction_track")
    if not track:
        logger.error("direction_track missing before graduation step.")
        await start_new_registration_flow(message, state)
        return

    raw_value = (message.text or "").strip()
    stored_value: str | None = None
    if track == "postgraduate":
        stored_value = extract_graduation_year(raw_value)
    elif track == "bachelor":
        stored_value = extract_course_number(raw_value, range(1, 5))
    elif track == "master":
        stored_value = extract_course_number(raw_value, range(1, 3))

    if not stored_value:
        prompt_text = (
            "⚠️ Не удалось распознать значение. Попробуйте снова.\n\n"
            + course_prompt_for_track(track)
        )
        options = course_options_for_track(track)
        extra_rows = build_option_rows(options, "graduation_select") if options else None
        keyboard = create_registration_keyboard(Registration.graduation.state, extra_rows=extra_rows)
        await bot.edit_message_text(
            get_display_profile_text(user_data, prompt_text),
            chat_id=message.chat.id,
            message_id=main_message_id,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return

    await state.update_data(magistracy_graduation_year=stored_value)
    await show_confirmation(message.chat.id, main_message_id, state)


@router.callback_query(F.data.startswith("graduation_select"), Registration.graduation)
async def cb_graduation_select(callback: CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    main_message_id = user_data.get("main_message_id")
    if main_message_id is None:
        await callback.answer("Не удалось обновить шаг, начните регистрацию заново.", show_alert=True)
        return

    track = user_data.get("direction_track")
    if not track:
        await callback.answer("Направление не найдено, начните заново.", show_alert=True)
        return

    options = course_options_for_track(track)
    try:
        _, index_str = callback.data.split(":", 1)
        index = int(index_str)
        selected = options[index]
    except (ValueError, IndexError):
        await callback.answer("Не удалось распознать выбор.", show_alert=True)
        return

    if track == "bachelor":
        stored_value = extract_course_number(selected, range(1, 5))
    elif track == "master":
        stored_value = extract_course_number(selected, range(1, 3))
    else:
        stored_value = None
    if stored_value is None:
        await callback.answer("Неверный выбор, попробуйте снова.", show_alert=True)
        return

    await state.update_data(magistracy_graduation_year=stored_value)
    await show_confirmation(callback.message.chat.id, main_message_id, state)
    await callback.answer(f"Выбран {selected}")


@router.callback_query(F.data == "previous_step")
async def cb_previous_step(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    user_data = await state.get_data()
    main_message_id = user_data.get("main_message_id")
    chat_id = callback.message.chat.id

    if not main_message_id:
        await callback.answer("Произошла ошибка, попробуйте начать заново.", show_alert=True)
        return

    new_state = None
    prompt_text = ""
    keyboard: InlineKeyboardMarkup | None = None

    if current_state == Registration.direction.state:
        user_data.pop("name", None)
        user_data.pop("direction", None)
        user_data.pop("direction_track", None)
        prompt_text = REG_MESSAGES_NEW["name"]
        new_state = Registration.name
        keyboard = create_registration_keyboard(Registration.name.state)
    elif current_state == Registration.graduation.state:
        user_data.pop("direction", None)
        user_data.pop("direction_track", None)
        user_data.pop("magistracy_graduation_year", None)
        prompt_text = REG_MESSAGES_NEW["direction"]
        keyboard = build_direction_keyboard()
        new_state = Registration.direction
    elif current_state == Registration.confirm.state:
        user_data.pop("magistracy_graduation_year", None)
        track = user_data.get("direction_track")
        if not track:
            await callback.answer("Не удалось определить предыдущий шаг.", show_alert=True)
            return
        prompt_text = course_prompt_for_track(track)
        options = course_options_for_track(track)
        extra_rows = build_option_rows(options, "graduation_select") if options else None
        keyboard = create_registration_keyboard(Registration.graduation.state, extra_rows=extra_rows)
        new_state = Registration.graduation
    else:
        await callback.answer("Невозможно вернуться назад с этого шага.", show_alert=True)
        return

    await state.set_data(user_data)
    await bot.edit_message_text(
        get_display_profile_text(user_data, prompt_text),
        chat_id=chat_id,
        message_id=main_message_id,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
    if new_state:
        await state.set_state(new_state)
    await callback.answer()
