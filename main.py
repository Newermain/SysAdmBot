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

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = "token_bot"
ADMIN_CHAT_ID = ID chat
DATABASE_NAME = "requests.db"

# Инициализация бота
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# Клавиатуры
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

# Состояния FSM
class RequestForm(StatesGroup):
    department = State()
    full_name = State()
    problem = State()

# Инициализация базы данных
async def init_db():
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT NOT NULL,
            department TEXT NOT NULL,
            problem TEXT NOT NULL,
            status TEXT DEFAULT 'new',
            created_at TEXT NOT NULL
        )
        """)
        await db.commit()
        logger.info("База данных инициализирована")

# Команда для просмотра заявок
@dp.message(Command("my_requests"))
async def show_my_requests(message: types.Message):
    try:
        async with aiosqlite.connect(DATABASE_NAME) as db:
            cursor = await db.execute(
                """SELECT id, problem, status, created_at 
                FROM requests 
                WHERE user_id = ? 
                ORDER BY created_at DESC""",
                (message.from_user.id,)
            )
            requests = await cursor.fetchall()

        if not requests:
            await message.answer("📭 У вас нет активных заявок")
            return

        response = ["📋 Ваши заявки:"]
        status_icons = {"new": "🆕", "working": "🔄", "done": "✔️"}
        
        for req_id, problem, status, created_at in requests:
            date = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y")
            response.append(
                f"\n{status_icons.get(status, '❓')} Заявка #{req_id}\n"
                f"📅 {date} | Статус: {status.capitalize()}\n"
                f"📝 {problem[:50]}{'...' if len(problem) > 50 else ''}"
            )

        await message.answer("\n".join(response))
    except Exception as e:
        logger.error(f"Ошибка при получении заявок: {e}")
        await message.answer("⚠️ Произошла ошибка при получении списка заявок")

# Обработчики команд
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я бот для подачи заявок на IT-проблемы.\n\n"
        "📌 Доступные команды:\n"
        "/create_request - создать новую заявку\n"
        "/my_requests - просмотреть ваши заявки",
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

# Обработка заявки
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
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        async with aiosqlite.connect(DATABASE_NAME) as db:
            # Сохраняем заявку в БД
            cursor = await db.execute(
                """INSERT INTO requests 
                (user_id, username, full_name, department, problem, status, created_at) 
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    message.from_user.id,
                    message.from_user.username,
                    data["full_name"],
                    data["department"],
                    data["problem"],
                    "new",
                    created_at
                )
            )
            request_id = cursor.lastrowid
            await db.commit()

        # Формируем текст заявки
        request_text = (
            f"🚨 Новая заявка #{request_id}\n"
            f"👤 ФИО: {data['full_name']}\n"
            f"🏢 Отдел: {data['department']}\n"
            f"📝 Проблема: {data['problem']}\n"
            f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"🆕 Статус: Новая"
        )
        
        # Отправляем заявку админу
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=request_text,
            reply_markup=get_admin_keyboard(request_id)
        )

        # Отправляем подтверждение пользователю
        await message.answer(
            f"✅ Заявка #{request_id} создана!\n\n"
            f"🏢 Отдел: {data['department']}\n"
            f"📝 Проблема: {data['problem']}\n"
            f"🕒 Статус: Новая\n\n"
            f"Вы можете отслеживать статус через /my_requests",
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
        request_id = int(request_id)
        new_status = "working" if action == "working" else "done"
        status_text = "🔄 В работе" if action == "working" else "✅ Решено"

        async with aiosqlite.connect(DATABASE_NAME) as db:
            # Обновляем статус в базе данных
            await db.execute(
                "UPDATE requests SET status = ? WHERE id = ?",
                (new_status, request_id)
            )
            
            # Получаем данные заявки
            cursor = await db.execute(
                """SELECT user_id, full_name, department, problem 
                FROM requests WHERE id = ?""",
                (request_id,)
            )
            request_data = await cursor.fetchone()
            await db.commit()

        if not request_data:
            await callback.answer("Заявка не найдена", show_alert=True)
            return

        user_id, full_name, department, problem = request_data

        # 1. Отправляем новое сообщение администратору
        admin_message = (
            f"🚨 Заявка #{request_id} (обновление)\n"
            f"👤 ФИО: {full_name}\n"
            f"🏢 Отдел: {department}\n"
            f"📝 Проблема: {problem}\n"
            f"🕒 Статус: {status_text}"
        )
        
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_message,
            reply_markup=get_admin_keyboard(request_id)
        )

        # 2. Отправляем уведомление пользователю
        user_notification = (
            f"🔔 Статус вашей заявки #{request_id} обновлён:\n"
            f"🏷️ Новый статус: {status_text}\n"
            f"📝 Проблема: {problem[:100]}{'...' if len(problem) > 100 else ''}"
        )
        
        try:
            await bot.send_message(
                chat_id=user_id,
                text=user_notification
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление пользователю: {e}")

        await callback.answer(f"Статус заявки #{request_id} изменён на: {status_text}")

    except ValueError:
        await callback.answer("Ошибка в формате запроса", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка при обновлении статуса: {e}")
        await callback.answer("⚠️ Произошла ошибка при обновлении статуса", show_alert=True)

# Запуск бота
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
