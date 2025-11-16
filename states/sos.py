from aiogram.fsm.state import State, StatesGroup


class Sos(StatesGroup):
    waiting_text = State()
    waiting_confirmation = State()
