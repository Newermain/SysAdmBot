import logging
import re
import aiosqlite
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove
)
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
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# --- Клавиатуры ---
departments = ["IT", "Бухгалтерия", "HR", "Маркетинг", "Продажи", "Другое"]

def get_departments_keyboard():
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
            [InlineKeyboardButton(text="🔄 В работе", callback_data=f"status_working_{request_id}")],
            [InlineKeyboardButton(text="✔️ Решено", callback_data=f"status_done_{request_id}")]
        ]
    )

# --- Состояния FSM ---
class RequestForm(StatesGroup):
    department = State()
    full_name = State()
    problem = State()

# --- Инициализация базы данных ---
async def init_db():
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute("DROP TABLE IF EXISTS requests")  # Удаляем старую таблицу
        await db.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            department TEXT,
            problem TEXT,
            status TEXT DEFAULT 'new',
            created_at TEXT,
            admin_message_id INTEGER
        )
        """)
        await db.commit()
        logger.info("База данных инициализирована")

# --- Обработчики команд ---
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

# --- Обработка заявки ---
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
    await finish_request(message, state)

async def finish_request(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    try:
        async with aiosqlite.connect(DATABASE_NAME) as db:
            # Вставляем основную информацию о заявке
            cursor = await db.execute(
                "INSERT INTO requests (user_id, username, full_name, department, problem, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    message.from_user.id,
                    message.from_user.username,
                    data["full_name"],
                    data["department"],
                    data["problem"],
                    "new",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
            )
            request_id = cursor.lastrowid
            await db.commit()

        # Формируем текст заявки
        request_text = (
            f"🚨 Заявка #{request_id}\n"
            f"👤 ФИО: {data['full_name']}\n"
            f"🏢 Отдел: {data['department']}\n"
            f"📝 Проблема: {data['problem']}\n"
            f"🕒 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"🆕 Статус: Новая"
        )
        
        # Отправляем заявку админу
        admin_msg = await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=request_text,
            reply_markup=get_admin_keyboard(request_id)
        )
        
        # Обновляем запись с ID сообщения админа
        async with aiosqlite.connect(DATABASE_NAME) as db:
            await db.execute(
                "UPDATE requests SET admin_message_id = ? WHERE id = ?",
                (admin_msg.message_id, request_id)
            )
            await db.commit()

        await message.answer(
            "✅ Спасибо! Заявка создана.\nМы скоро её обработаем!",
            reply_markup=ReplyKeyboardRemove()
        )
        
    except Exception as e:
        logger.error(f"Ошибка при создании заявки: {e}")
        await message.answer(
            "⚠️ Произошла ошибка при создании заявки. Пожалуйста, попробуйте позже.",
            reply_markup=ReplyKeyboardRemove()
        )
    finally:
        await state.clear()

@dp.callback_query(F.data.startswith("status_"))
async def update_status(callback: types.CallbackQuery):
    try:
        _, action, request_id = callback.data.split("_")
        new_status = "working" if action == "working" else "done"
        status_text = "🔄 В работе" if action == "working" else "✅ Решено"

        async with aiosqlite.connect(DATABASE_NAME) as db:
            # Обновляем статус в базе
            await db.execute(
                "UPDATE requests SET status = ? WHERE id = ?",
                (new_status, request_id)
            )
            
            # Получаем данные заявки
            cursor = await db.execute(
                "SELECT full_name, department, problem, admin_message_id FROM requests WHERE id = ?",
                (request_id,)
            )
            full_name, department, problem, admin_message_id = await cursor.fetchone()
            await db.commit()

        # Формируем обновленный текст
        updated_text = (
            f"🚨 Заявка #{request_id}\n"
            f"👤 ФИО: {full_name}\n"
            f"🏢 Отдел: {department}\n"
            f"📝 Проблема: {problem}\n"
            f"🕒 Статус: {status_text}"
        )

        # Обновляем сообщение
        await bot.edit_message_text(
            chat_id=ADMIN_CHAT_ID,
            message_id=admin_message_id,
            text=updated_text,
            reply_markup=get_admin_keyboard(request_id)
        )

        await callback.answer(f"Статус заявки #{request_id} изменен на: {status_text}")
    except Exception as e:
        logger.error(f"Ошибка при обновлении статуса: {e}")
        await callback.answer("⚠️ Произошла ошибка при обновлении статуса", show_alert=True)

# --- Запуск бота ---
async def main():
    await init_db()  # Инициализация БД перед запуском
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())