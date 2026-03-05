from aiogram.fsm.state import State, StatesGroup

class ChatStates(StatesGroup):
    # Состояние, когда мы ждем вопрос от пользователя
    waiting_for_question = State()
    waiting_for_upload = State()