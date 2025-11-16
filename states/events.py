from aiogram.fsm.state import State, StatesGroup


class EventCreation(StatesGroup):
    title = State()
    date = State()
    start_time = State()
    end_time = State()
    location = State()
    description = State()
    tags = State()
    confirm = State()


class EventEdit(StatesGroup):
    menu = State()
    title = State()
    date = State()
    start_time = State()
    end_time = State()
    location = State()
    description = State()
    tags = State()
    link = State()
