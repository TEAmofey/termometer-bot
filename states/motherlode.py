from aiogram.fsm.state import State, StatesGroup


class Motherlode(StatesGroup):
    waiting_text = State()
    waiting_confirmation = State()
