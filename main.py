import logging
import re
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InputFile,
)
from aiogram.utils.markdown import text
from aiogram.enums import ParseMode 
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Конфигурация ---
BOT_TOKEN = "7183399679:AAHKKtnKMFzuQX_R67_TzVkhwhrAobFiGDo"  # Токен бота
ADMIN_CHAT_ID = " -1002595180902"  # Токен чата
DATABASE_NAME = "requests.db"  # Название файла БД

# --- Инициализация бота ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Клавиатуры ---
departments = ["IT", "Бухгалтерия", "HR", "Маркетинг", "Продажи", "Другое"]

def get_departments_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    for dept in departments:
        keyboard.add(KeyboardButton(text=dept))
    return keyboard

def get_admin_keyboard(request_id):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ В работе", callback_data=f"status_working_{request_id}")],
        [InlineKeyboardButton(text="✔️ Решено", callback_data=f"status_done_{request_id}")],
    ])
    return keyboard

# --- Состояния FSM (Finite State Machine) ---
class RequestForm(StatesGroup):
    department = State()
    full_name = State()
    problem = State()
    photo = State()

# --- База данных SQLite ---
def init_db():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        full_name TEXT,
        department TEXT,
        problem TEXT,
        photo_id TEXT,
        status TEXT DEFAULT 'new',
        created_at TEXT
    )
    """)
    conn.commit()
    conn.close()

init_db()  # Создаем таблицу при запуске

# --- Обработчики команд ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Создать заявку", callback_data="create_request")]
    ])
    await message.answer(
        "👋 Привет! Я бот для подачи заявок на IT-проблемы.\n"
        "Нажми кнопку ниже, чтобы создать заявку.",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data == "create_request")
async def start_request(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Выберите ваш отдел:",
        reply_markup=get_departments_keyboard()
    )
    await state.set_state(RequestForm.department)

# --- Обработка заявки (FSM) ---
@dp.message(RequestForm.department)
async def process_department(message: types.Message, state: FSMContext):
    await state.update_data(department=message.text)
    await message.answer("📝 Введите ваше ФИО:")
    await state.set_state(RequestForm.full_name)

@dp.message(RequestForm.full_name)
async def process_full_name(message: types.Message, state: FSMContext):
    if not re.match(r"^[А-ЯЁ][а-яё]+\s[А-ЯЁ][а-яё]+\s[А-ЯЁ][а-яё]+$", message.text):
        await message.answer("❌ Неверный формат ФИО. Пример: Иванов Иван Иванович")
        return
    await state.update_data(full_name=message.text)
    await message.answer("🔧 Опишите проблему:")
    await state.set_state(RequestForm.problem)

@dp.message(RequestForm.problem)
async def process_problem(message: types.Message, state: FSMContext):
    await state.update_data(problem=message.text)
    await message.answer("📸 Пришлите скриншот (если нужно). Или нажмите /skip.")
    await state.set_state(RequestForm.photo)

@dp.message(RequestForm.photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)
    await finish_request(message, state)

@dp.message(RequestForm.photo, Command("skip"))
async def skip_photo(message: types.Message, state: FSMContext):
    await state.update_data(photo_id=None)
    await finish_request(message, state)

async def finish_request(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    # Сохраняем заявку в БД
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO requests (user_id, username, full_name, department, problem, photo_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            message.from_user.id,
            message.from_user.username,
            data["full_name"],
            data["department"],
            data["problem"],
            data.get("photo_id"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
    )
    request_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Формируем сообщение для админа
    admin_text = (
        f"🚨 **Новая заявка #{request_id}**\n"
        f"👤 **ФИО:** {data['full_name']}\n"
        f"🏢 **Отдел:** {data['department']}\n"
        f"📝 **Проблема:** {data['problem']}\n"
        f"🕒 **Дата:** {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    
    # Отправляем админу
    if data.get("photo_id"):
        await bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=data["photo_id"],
            caption=admin_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_admin_keyboard(request_id)
        )
    else:
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_admin_keyboard(request_id)
        )

    # Подтверждение пользователю
    await message.answer(
        "✅ **Спасибо! Заявка создана.**\n"
        "Мы скоро её обработаем!",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.clear()

# --- Обработка статусов (админ) ---
@dp.callback_query(lambda c: c.data.startswith("status_"))
async def update_status(callback: types.CallbackQuery):
    _, action, request_id = callback.data.split("_")
    
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    if action == "working":
        new_status = "working"
        status_text = "⏳ В работе"
    elif action == "done":
        new_status = "done"
        status_text = "✅ Решено"
    
    cursor.execute(
        "UPDATE requests SET status = ? WHERE id = ?",
        (new_status, request_id)
    )
    conn.commit()
    
    # Обновляем сообщение
    await callback.message.edit_caption(
        caption=f"{callback.message.caption}\n\n**Статус:** {status_text}",
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer(f"Статус заявки #{request_id} изменен!")

# --- Запуск бота ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())