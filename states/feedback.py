from aiogram.fsm.state import State, StatesGroup


class Feedback(StatesGroup):
    waiting_text = State()
    waiting_choice = State()
