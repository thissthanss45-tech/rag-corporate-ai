from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🧠 Задать вопрос"), KeyboardButton(text="🔁 Сменить модель")],
            [KeyboardButton(text="📄 Загрузить документ")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери действие",
    )


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="↩️ Отмена")]],
        resize_keyboard=True,
        input_field_placeholder="Можно отменить в любой момент",
    )


def question_model_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🦙 Llama"), KeyboardButton(text="🐋 DeepSeek")],
            [KeyboardButton(text="↩️ Отмена")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери ИИ-модель для ответа",
    )


def upload_status_inline(task_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Проверить статус", callback_data=f"status:{task_id}")
    builder.button(text="↩️ Назад", callback_data="back:main")
    builder.adjust(1)
    return builder.as_markup()


def back_inline() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="↩️ Назад", callback_data="back:main")
    return builder.as_markup()
