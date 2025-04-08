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
    ReplyKeyboardRemove,
    InputMediaPhoto
)
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph
from io import BytesIO

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = "8125237649:AAHiHUVctjsSamLG7V_AH5TBbkofDLe8p3w"
ADMIN_CHAT_ID = -1002595180902
ADMIN_ID = 814124459
DATABASE_NAME = "requests.db"

# Инициализация бота
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# Клавиатуры
departments = ["Административно-управленческий", "Бухгалтерия", "Продажи", "Маркетинг", "Логистика/Снабжение", "Дизайн", "Веб-разработка", "Тех Контроль", "Инженерно-техническая служба", "Контроль качества", "Планово-экономический"]

request_types = ["🚨 Проблема (что-то сломалось)", "🛒 Закупка оборудования"]

def get_request_types_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=req_type)] for req_type in request_types
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_departments_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=dept)] for dept in departments
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_photo_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📷 Прикрепить фото/скрин")],
            [KeyboardButton(text="⏭ Пропустить")]
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
    request_type = State()
    department = State()
    full_name = State()
    problem = State()
    photo = State()

# Инициализация базы данных
async def init_db():
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute("DROP TABLE IF EXISTS requests")
        await db.execute("""
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT NOT NULL,
            department TEXT NOT NULL,
            request_type TEXT NOT NULL,
            problem TEXT NOT NULL,
            photo_id TEXT,
            status TEXT DEFAULT 'new',
            created_at TEXT NOT NULL,
            admin_message_id INTEGER
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
                """SELECT id, problem, status, created_at, request_type 
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
        status_icons = {"Новая": "🆕", "В работе": "🔄", "Решена": "✔️"}
        
        for req_id, problem, status, created_at, req_type in requests:
            date = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y")
            response.append(
                f"\n{status_icons.get(status, '❓')} {req_type} #{req_id}\n"
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
        "Выберите тип заявки:",
        reply_markup=get_request_types_keyboard()
    )
    await state.set_state(RequestForm.request_type)

@dp.message(Command("create_request"))
async def cmd_create_request(message: types.Message, state: FSMContext):
    await message.answer(
        "Выберите тип заявки:",
        reply_markup=get_request_types_keyboard()
    )
    await state.set_state(RequestForm.request_type)

# Обработчик команды генерации отчетов
@dp.message(Command("generate_reports"))
async def generate_reports(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Эта команда доступна только администратору")
        return

    try:
        # 1. Загружаем данные из базы
        async with aiosqlite.connect(DATABASE_NAME) as db:
            # Заявки на проблемы
            cursor = await db.execute(
                """SELECT id, created_at, full_name, problem, status 
                FROM requests 
                WHERE request_type = ? 
                ORDER BY created_at""",
                ("🚨 Проблема (что-то сломалось)",)
            )
            problems = await cursor.fetchall()

            # Заявки на закупки
            cursor = await db.execute(
                """SELECT id, created_at, full_name, problem, status 
                FROM requests 
                WHERE request_type = ? 
                ORDER BY created_at""",
                ("🛒 Закупка оборудования",)
            )
            purchases = await cursor.fetchall()

        # 2. Регистрируем шрифты (должны быть в одной папке со скриптом)
        try:
            pdfmetrics.registerFont(TTFont('DejaVu', 'DejaVuSans.ttf'))
            pdfmetrics.registerFont(TTFont('DejaVu-Bold', 'DejaVuSans-Bold.ttf'))
            main_font = 'DejaVu'
        except:
            # Fallback на стандартные шрифты (может не поддерживать кириллицу)
            main_font = 'Helvetica'
            await message.answer("⚠️ Используются стандартные шрифты (возможны проблемы с кириллицей)")

        # 3. Создаем отчет по проблемам
        problems_buffer = BytesIO()
        p = canvas.Canvas(problems_buffer, pagesize=A4)
        width, height = A4

        # Заголовок отчета
        p.setFont(f'{main_font}-Bold', 16)
        p.drawString(50, height - 50, "Отчет по проблемам")
        p.setFont(main_font, 12)
        p.drawString(50, height - 80, f"Дата формирования: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

        # Подготовка данных для таблицы
        data = [["№", "Дата", "Заявитель", "Проблема", "Статус"]]
        for row in problems:
            date = datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y")
            data.append([
                str(row[0]),
                date,
                row[2],
                row[3][:50] + ("..." if len(row[3]) > 50 else ""),
                row[4]
            ])

        # Создаем таблицу
        table = Table(data, colWidths=[30, 60, 120, 200, 60])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), f'{main_font}-Bold'),
            ('FONTNAME', (0, 1), (-1, -1), main_font),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))

        # Размещаем таблицу на странице
        table.wrapOn(p, width - 100, height)
        table.drawOn(p, 50, height - 120 - len(problems)*20)

        p.save()
        problems_buffer.seek(0)

        # 4. Создаем отчет по закупкам (аналогично)
        purchases_buffer = BytesIO()
        p = canvas.Canvas(purchases_buffer, pagesize=A4)

        # Заголовок отчета
        p.setFont(f'{main_font}-Bold', 16)
        p.drawString(50, height - 50, "Отчет по закупкам")
        p.setFont(main_font, 12)
        p.drawString(50, height - 80, f"Дата формирования: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

        # Подготовка данных для таблицы
        data = [["№", "Дата", "Заявитель", "Оборудование", "Статус"]]
        for row in purchases:
            date = datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y")
            data.append([
                str(row[0]),
                date,
                row[2],
                row[3][:50] + ("..." if len(row[3]) > 50 else ""),
                row[4]
            ])

        # Создаем таблицу
        table = Table(data, colWidths=[30, 60, 120, 200, 60])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), f'{main_font}-Bold'),
            ('FONTNAME', (0, 1), (-1, -1), main_font),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))

        # Размещаем таблицу на странице
        table.wrapOn(p, width - 100, height)
        table.drawOn(p, 50, height - 120 - len(purchases)*20)

        p.save()
        purchases_buffer.seek(0)

        # 5. Отправляем отчеты
        await message.answer("Отчеты сформированы:")

        await bot.send_document(
            chat_id=message.from_user.id,
            document=types.BufferedInputFile(
                problems_buffer.getvalue(),
                filename="problems_report.pdf"
            ),
            caption="Отчет по проблемам"
        )

        await bot.send_document(
            chat_id=message.from_user.id,
            document=types.BufferedInputFile(
                purchases_buffer.getvalue(),
                filename="purchases_report.pdf"
            ),
            caption="Отчет по закупкам"
        )

    except Exception as e:
        logger.error(f"Ошибка при формировании отчетов: {e}")
        await message.answer("⚠️ Произошла ошибка при формировании отчетов")

# Обработка заявки
@dp.message(RequestForm.request_type)
async def process_request_type(message: types.Message, state: FSMContext):
    if message.text not in request_types:
        await message.answer("Пожалуйста, выберите тип заявки из предложенных вариантов.")
        return
    
    await state.update_data(request_type=message.text)
    await message.answer(
        "Выберите ваш отдел:",
        reply_markup=get_departments_keyboard()
    )
    await state.set_state(RequestForm.department)

@dp.message(RequestForm.department)
async def process_department(message: types.Message, state: FSMContext):
    if message.text not in departments:
        await message.answer("Пожалуйста, выберите отдел из предложенных вариантов.")
        return
        
    await state.update_data(department=message.text)
    await message.answer(
        "📝 Введите ваше ФИО и логин Telegram в формате:\n"
        "<b>Фамилия Имя Отчество @username</b>\n\n"
        "Пример: <i>Иванов Иван Иванович @ivanov</i>",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.HTML
    )
    await state.set_state(RequestForm.full_name)

@dp.message(RequestForm.full_name)
async def process_full_name(message: types.Message, state: FSMContext):
    # Разбиваем сообщение на части
    parts = message.text.split()
    
    # Проверяем минимальное количество частей (ФИО + логин)
    if len(parts) < 4:
        await message.answer(
            "❌ Неверный формат. Пожалуйста, укажите:\n"
            "<b>Фамилия Имя Отчество @username</b>\n\n"
            "Пример: <i>Иванов Иван Иванович @ivanov</i>",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Извлекаем логин (последняя часть)
    username = parts[-1]
    
    # Проверяем формат логина
    if not username.startswith('@') or len(username) < 2:
        await message.answer(
            "❌ Неверный формат логина. Логин должен начинаться с @ и содержать имя пользователя.\n"
            "Пример: <i>@ivanov</i>",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Проверяем ФИО (все части кроме последней)
    fio = ' '.join(parts[:-1])
    if not re.match(r"^[А-ЯЁ][а-яё]+\s[А-ЯЁ][а-яё]+\s[А-ЯЁ][а-яё]+$", fio):
        await message.answer(
            "❌ Неверный формат ФИО. Пожалуйста, укажите:\n"
            "<b>Фамилия Имя Отчество @username</b>\n\n"
            "Пример: <i>Иванов Иван Иванович @ivanov</i>",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Сохраняем данные
    await state.update_data({
        'full_name': fio, 
        'telegram_username': username[1:]
    })
    
    data = await state.get_data()
    if "🚨 Проблема" in data["request_type"]:
        prompt = "🔧 Опишите проблему:"
    else:
        prompt = "🛒 Опишите, какое оборудование необходимо закупить:"
    
    await message.answer(prompt)
    await state.set_state(RequestForm.problem)
    
@dp.message(RequestForm.problem)
async def process_problem(message: types.Message, state: FSMContext):
    await state.update_data(problem=message.text)
    await message.answer(
        "Хотите прикрепить фото/скрин к заявке?",
        reply_markup=get_photo_keyboard()
    )
    await state.set_state(RequestForm.photo)

@dp.message(RequestForm.photo, F.text == "⏭ Пропустить")
async def skip_photo(message: types.Message, state: FSMContext):
    await state.update_data(photo=None)
    await finish_request(message, state)

@dp.message(RequestForm.photo, F.text == "📷 Прикрепить фото/скрин")
async def request_photo(message: types.Message, state: FSMContext):
    await message.answer("Отправьте фото или скрин проблемы:", reply_markup=ReplyKeyboardRemove())

@dp.message(RequestForm.photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id  # Берем самое высокое качество
    await state.update_data(photo=photo_id)
    await finish_request(message, state)

@dp.message(RequestForm.photo)
async def wrong_photo_input(message: types.Message):
    await message.answer("Пожалуйста, отправьте фото или выберите действие из меню.")

async def finish_request(message: types.Message, state: FSMContext):
    data = await state.get_data()
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        async with aiosqlite.connect(DATABASE_NAME) as db:
            # Сохраняем заявку в БД
            cursor = await db.execute(
                """INSERT INTO requests 
                (user_id, username, full_name, department, request_type, problem, photo_id, status, created_at) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message.from_user.id,
                    message.from_user.username,
                    data["full_name"],
                    data["department"],
                    data["request_type"],
                    data["problem"],
                    data.get("photo"),
                    "new",
                    created_at
                )
            )
            request_id = cursor.lastrowid
            await db.commit()

        # Формируем текст заявки
        request_text = (
            f"{data['request_type']} #{request_id}\n"
            f"👤 ФИО: {data['full_name']}\n"
            f"🔗 Логин: @{message.from_user.username}\n"
            f"🏢 Отдел: {data['department']}\n"
            f"📝 Описание: {data['problem']}\n"
            f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"🆕 Статус: Новая"
        )
        
        # Отправляем заявку админу
        if data.get("photo"):
            # Если есть фото, отправляем с фото
            media = InputMediaPhoto(
                media=data["photo"],
                caption=request_text
            )
            sent_message = await bot.send_media_group(
                chat_id=ADMIN_CHAT_ID,
                media=[media]
            )
            # Отправляем клавиатуру отдельным сообщением
            admin_message = await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"Действия по заявке #{request_id}:",
                reply_markup=get_admin_keyboard(request_id)
            )
            # Сохраняем ID последнего сообщения (с кнопками)
            admin_message_id = admin_message.message_id
        else:
            # Если фото нет, просто отправляем текст
            sent_message = await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=request_text,
                reply_markup=get_admin_keyboard(request_id)
            )
            admin_message_id = sent_message.message_id

        # Сохраняем ID сообщения в базе данных
        async with aiosqlite.connect(DATABASE_NAME) as db:
            await db.execute(
                "UPDATE requests SET admin_message_id = ? WHERE id = ?",
                (admin_message_id, request_id)
            )
            await db.commit()

        # Отправляем подтверждение пользователю
        user_message = (
            f"✅ Заявка #{request_id} создана!\n\n"
            f"🏷️ Тип: {data['request_type']}\n"
            f"🏢 Отдел: {data['department']}\n"
            f"📝 Описание: {data['problem']}\n"
        )
        
        if data.get("photo"):
            await bot.send_photo(
                chat_id=message.from_user.id,
                photo=data["photo"],
                caption=user_message + "\n📷 Фото прикреплено"
            )
        else:
            await message.answer(user_message)
        
        await message.answer(
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
            # Получаем данные заявки
            cursor = await db.execute(
                """SELECT user_id, full_name, department, problem, request_type, photo_id, admin_message_id 
                FROM requests WHERE id = ?""",
                (request_id,)
            )
            request_data = await cursor.fetchone()
            
            if not request_data:
                await callback.answer("Заявка не найдена", show_alert=True)
                return

            user_id, full_name, department, problem, req_type, photo_id, admin_message_id = request_data

            # Обновляем статус в базе данных
            await db.execute(
                "UPDATE requests SET status = ? WHERE id = ?",
                (new_status, request_id)
            )
            await db.commit()

        # 1. Обновляем сообщение администратору
        admin_message = (
            f"{req_type} #{request_id}\n"
            f"👤 ФИО: {full_name}\n"
            f"🔗 Логин: @{callback.from_user.username}\n"  # Добавляем эту строку
            f"🏢 Отдел: {department}\n"
            f"📝 Описание: {problem}\n"
            f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"🕒 Статус: {status_text}"
        )
        
        try:
            if photo_id:
                # Если была фотография, редактируем подпись к медиагруппе невозможно,
                # поэтому отправляем новое текстовое сообщение с кнопками
                await bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=admin_message,
                    reply_markup=get_admin_keyboard(request_id)
                )
            else:
                # Если фото не было, просто редактируем существующее сообщение
                await bot.edit_message_text(
                    chat_id=ADMIN_CHAT_ID,
                    message_id=admin_message_id,
                    text=admin_message,
                    reply_markup=get_admin_keyboard(request_id)
                )
        except Exception as e:
            logger.error(f"Ошибка при редактировании сообщения: {e}")

        # 2. Отправляем уведомление пользователю
        user_notification = (
            f"🔔 Статус вашей заявки #{request_id} обновлён:\n"
            f"🏷️ Тип: {req_type}\n"
            f"🔄 Новый статус: {status_text}\n"
            f"📝 Описание: {problem[:100]}{'...' if len(problem) > 100 else ''}"
        )
        
        try:
            if photo_id:
                await bot.send_photo(
                    chat_id=user_id,
                    photo=photo_id,
                    caption=user_notification
                )
            else:
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