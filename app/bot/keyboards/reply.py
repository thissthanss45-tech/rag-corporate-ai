from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_main_keyboard() -> ReplyKeyboardMarkup:
    # Создаем кнопки
    btn_chat = KeyboardButton(text="AI Chat 🧠✨")
    btn_upload = KeyboardButton(text="Загрузить документ 📤")

    # Собираем клавиатуру (макет)
    # resize_keyboard=True делает кнопки компактными, а не на пол-экрана
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [btn_chat],        # Первая строка (самая важная)
            [btn_upload]       # Вторая строка (админская)
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )
    return keyboard