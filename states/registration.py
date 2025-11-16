from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    name = State()
    direction = State()
    graduation = State()
    confirm = State()
