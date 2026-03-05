from aiogram.fsm.state import State, StatesGroup


class BotStates(StatesGroup):
    waiting_for_document = State()
    waiting_for_question_model = State()
    waiting_for_question = State()
