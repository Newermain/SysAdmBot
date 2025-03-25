import logging
import re
import sqlite3
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InputFile,
    ReplyKeyboardRemove
)
from aiogram.utils.markdown import text
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Конфигурация ---
BOT_TOKEN = "7183399679:AAHKKtnKMFzuQX_R67_TzVkhwhrAobFiGDo"
ADMIN_CHAT_ID = -1002595180902  
DATABASE_NAME = "requests.db"

# --- Инициализация бота ---
storage = MemoryStorage()  # Хранилище состояний
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# --- Клавиатуры ---
departments = ["IT", "Бухгалтерия", "HR", "Маркетинг", "Продажи", "Другое"]

def get_departments_keyboard():
    # В aiogram 3.x кнопки передаются через keyboard
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=dept)] for dept in departments
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_admin_keyboard(request_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ В работе", callback_data=f"status_working_{request_id}")],
            [InlineKeyboardButton(text="✔️ Решено", callback_data=f"status_done_{request_id}")],
        ]
    )

# --- Состояния FSM ---
class RequestForm(StatesGroup):
    department = State()
    full_name = State()
    problem = State()
    photo = State()

# --- База данных ---
def init_db():
    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.execute("""
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

init_db()

# --- Обработчики ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я бот для подачи заявок на IT-проблемы.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="📝 Создать заявку", callback_data="create_request")
            ]]
        )
    )

@dp.callback_query(F.data == "create_request")
async def start_request(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Выберите ваш отдел:",
        reply_markup=get_departments_keyboard()
    )
    await state.set_state(RequestForm.department)

@dp.message(RequestForm.department)
async def process_department(message: types.Message, state: FSMContext):
    await state.update_data(department=message.text)
    await message.answer("📝 Введите ваше ФИО:", reply_markup=ReplyKeyboardRemove())
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
    await state.update_data(photo_id=message.photo[-1].file_id)
    await finish_request(message, state)

@dp.message(RequestForm.photo, Command("skip"))
async def skip_photo(message: types.Message, state: FSMContext):
    await state.update_data(photo_id=None)
    await finish_request(message, state)

async def finish_request(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO requests VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message.from_user.id,
                message.from_user.username,
                data["full_name"],
                data["department"],
                data["problem"],
                data.get("photo_id"),
                "new",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
        )
        request_id = cursor.lastrowid
    
    admin_text = (
        f"🚨 Новая заявка #{request_id}\n"
        f"👤 ФИО: {data['full_name']}\n"
        f"🏢 Отдел: {data['department']}\n"
        f"📝 Проблема: {data['problem']}\n"
        f"🕒 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    
    if data.get("photo_id"):
        await bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=data["photo_id"],
            caption=admin_text,
            reply_markup=get_admin_keyboard(request_id)
        )
    else:
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_text,
            reply_markup=get_admin_keyboard(request_id)
        )
    
    await message.answer(
        "✅ Спасибо! Заявка создана.\nМы скоро её обработаем!",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.clear()

@dp.callback_query(F.data.startswith("status_"))
async def update_status(callback: types.CallbackQuery):
    _, action, request_id = callback.data.split("_")
    
    with sqlite3.connect(DATABASE_NAME) as conn:
        status = "working" if action == "working" else "done"
        conn.execute(
            "UPDATE requests SET status = ? WHERE id = ?",
            (status, request_id)
        )
    
    status_text = "⏳ В работе" if action == "working" else "✅ Решено"
    
    if callback.message.caption:
        await callback.message.edit_caption(
            caption=f"{callback.message.caption}\n\nСтатус: {status_text}",
            reply_markup=get_admin_keyboard(request_id)
        )
    else:
        await callback.message.edit_text(
            text=f"{callback.message.text}\n\nСтатус: {status_text}",
            reply_markup=get_admin_keyboard(request_id)
        )
    
    await callback.answer(f"Статус заявки #{request_id} изменен!")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())