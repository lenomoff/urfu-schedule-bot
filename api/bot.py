"""
Telegram-бот для расписания УрФУ (Modeus).
Уведомления о парах с советами по обучению.
"""

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8535325912:AAE4poM0RJUMOK8qUmjlDdW4TBiiexq82QY"
TOKEN_FILE = "modeus_token.json"
MODEUS_API_URL = "https://urfu.modeus.org/schedule-calendar-v2/api/calendar/events/search"
TIMEZONE = "Asia/Yekaterinburg"
NOTIFY_BEFORE_MINUTES = 15  # За 15 минут до пары

# ==================== СОВЕТЫ ПО ПРЕДМЕТАМ ====================
STUDY_TIPS = {
    "LAB": [
        "🔬 Перед лабораторной повторите теорию — так вы увереннее почувствуете себя у стенда.",
        "📝 Подготовьте бланк отчёта заранее, чтобы не тратить время на оформление.",
        "🧪 Проверьте список оборудования и расходников — возьмите с собой всё необходимое.",
        "💡 Если что-то не получается — не стесняйтесь спросить преподавателя, это нормально!",
        "📊 Ведите записи наблюдений сразу, пока свежи впечатления.",
    ],
    "LECT": [
        "📚 Перед лекцией просмотрите конспект прошлой — так материал усвоится лучше.",
        "✍️ Записывайте только ключевые мысли, а не всё подряд — это сэкономит время.",
        "❓ Если что-то непонятно — запишите вопрос, чтобы уточнить после пары.",
        "🎯 Попробуйте пересказать материал своими словами — лучший способ проверить понимание.",
        "📖 После лекции прочитайте рекомендованную литературу в тот же день.",
    ],
    "SEMI": [
        "💻 Перед практикой повторите синтаксис — так вы не будете постоянно подсматривать.",
        "🤝 Работайте в команде — обсуждение задач помогает найти лучшее решение.",
        "📋 Разбейте задачу на подзадачи — так проще отладить и проверить результат.",
        "🔍 Не забудьте про тестирование — проверьте крайние случаи.",
        "📝 Комментируйте код — через месяц вы скажете себе спасибо.",
    ],
    "default": [
        "📖 Повторите конспект прошлой пары — это займёт 5 минут, но очень поможет.",
        "🎯 Поставьте цель на пару: что именно вы хотите понять или научиться делать.",
        "☕ Выпейте воды и настройтесь на продуктивную работу.",
        "📝 Подготовьте вопросы — активное участие в паре улучшает запоминание.",
        "🧠 Попробуйте объяснить материал кому-нибудь — это лучший способ понять его самому.",
    ],
}

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

users = {}


# ==================== РАБОТА С ТОКЕНОМ ====================

def load_token() -> Optional[str]:
    """Загрузка токена из файла."""
    try:
        with open(TOKEN_FILE, "r") as f:
            data = json.load(f)
            return data.get("id_token") or data.get("access_token")
    except FileNotFoundError:
        logger.error(f"Файл {TOKEN_FILE} не найден!")
        return None
    except json.JSONDecodeError:
        logger.error(f"Ошибка чтения {TOKEN_FILE}!")
        return None


# ==================== API MODEUS ====================

async def get_schedule(date: Optional[datetime] = None) -> list[dict]:
    """Получение расписания из Modeus API."""
    if date is None:
        date = datetime.now()
    
    token = load_token()
    if not token:
        logger.error("Токен не найден!")
        return []
    
    start_time = date.strftime("%Y-%m-%d") + "T00:00:00Z"
    end_time = (date + timedelta(days=1)).strftime("%Y-%m-%d") + "T00:00:00Z"
    
    payload = {
        "size": 500,
        "timeMin": start_time,
        "timeMax": end_time,
        "attendeePersonId": ["330d3b77-3f42-457d-9642-cc95bd6c0b5f"]
    }
    
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Origin": "https://urfu.modeus.org",
        "Referer": "https://urfu.modeus.org/schedule-calendar/my",
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                MODEUS_API_URL,
                params={"tz": TIMEZONE},
                json=payload,
                headers=headers,
                ssl=False,
                timeout=30
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    events = data.get("_embedded", {}).get("events", [])
                    logger.info(f"Получено {len(events)} событий от API")
                    
                    lessons = []
                    for event in events:
                        event_id = event.get("id", "")
                        full_data = await get_event_details(session, token, event_id)
                        
                        if full_data:
                            lesson = parse_full_event(full_data)
                            if lesson:
                                lessons.append(lesson)
                    
                    logger.info(f"Обработано {len(lessons)} пар")
                    # Сортируем по времени
                    lessons.sort(key=lambda x: x.get("start", ""))
                    return lessons
                elif resp.status == 401:
                    logger.error("Токен истёк! Обновите через get_modeus_token.py")
                    return []
                else:
                    logger.error(f"Modeus API: {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"Ошибка запроса к Modeus: {e}")
            return []


async def get_event_details(session: aiohttp.ClientSession, token: str, event_id: str) -> dict:
    """Получение полных данных события."""
    try:
        url = f"https://urfu.modeus.org/schedule-calendar-v2/api/calendar/events/{event_id}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Bearer {token}",
        }
        async with session.get(url, headers=headers, ssl=False, timeout=10) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        logger.error(f"Ошибка получения деталей события: {e}")
    return {}


def parse_full_event(data: dict) -> dict:
    """Парсинг полных данных события."""
    embedded = data.get("_embedded", {})
    
    # Название предмета
    course = embedded.get("course-unit-realization", {})
    subject = course.get("name", data.get("name", ""))
    
    # Преподаватель — ищем по person.href в event-attendees
    teacher = ""
    persons = embedded.get("persons", [])
    attendees = embedded.get("event-attendees", [])
    
    for attendee in attendees:
        if attendee.get("roleId") == "TEACH":
            # Получаем ID преподавателя из person.href
            person_href = attendee.get("_links", {}).get("person", {}).get("href", "")
            person_id = person_href.split("/")[-1] if person_href else ""
            
            # Ищем в persons
            for person in persons:
                if person.get("id") == person_id:
                    teacher = person.get("fullName", "")
                    break
            break
    
    # Аудитория (может быть ссылкой или номером)
    location = embedded.get("location", {})
    room = location.get("customLocation") or ""
    
    # Определяем тип локации
    if room.startswith("http"):
        room_display = f"🔗 {room}"
    elif room:
        room_display = f"🚪 {room}"
    else:
        room_display = "—"
    
    return {
        "name": subject,
        "start": data.get("start", ""),
        "end": data.get("end", ""),
        "type": data.get("typeId", ""),
        "teacher": teacher,
        "room": room,
        "room_display": room_display,
    }


def get_study_tip(lesson_type: str) -> str:
    """Получение случайного совета по обучению."""
    tips = STUDY_TIPS.get(lesson_type, STUDY_TIPS["default"])
    return random.choice(tips)


# ==================== УВЕДОМЛЕНИЯ ====================

async def send_notification(chat_id: int, lesson: dict):
    """Отправка уведомления о паре."""
    start_str = lesson['start'][11:16] if len(lesson['start']) > 11 else lesson['start']
    end_str = lesson['end'][11:16] if len(lesson['end']) > 11 else lesson['end']
    
    tip = get_study_tip(lesson.get("type", ""))
    
    # Определяем тип занятия
    type_names = {
        "LAB": "🔬 Лабораторная",
        "LECT": "📖 Лекция",
        "SEMI": "💻 Практика",
    }
    type_name = type_names.get(lesson.get("type", ""), "📚 Занятие")
    
    text = (
        f"⏰ <b>Через {NOTIFY_BEFORE_MINUTES} минут пара!</b>\n\n"
        f"{type_name}\n"
        f"📚 <b>{lesson['name']}</b>\n\n"
        f"🕐 Время: <b>{start_str} - {end_str}</b>\n"
        f"👨‍🏫 Преподаватель: {lesson.get('teacher', '—')}\n"
        f"📍 {lesson.get('room_display', '—')}\n\n"
        f"💡 <b>Совет:</b> {tip}"
    )
    await bot.send_message(chat_id, text, parse_mode="HTML")


async def check_upcoming_lessons():
    """Проверка предстоящих пар."""
    now = datetime.now()
    check_time = now + timedelta(minutes=NOTIFY_BEFORE_MINUTES)
    
    for chat_id in users.keys():
        lessons = await get_schedule(now)
        for lesson in lessons:
            try:
                start_str = lesson["start"]
                lesson_time = datetime.fromisoformat(start_str.replace("+05:00", ""))
                lesson_time = lesson_time.replace(tzinfo=None)
                
                if now <= lesson_time <= check_time:
                    await send_notification(chat_id, lesson)
            except (ValueError, KeyError):
                continue


# ==================== КОМАНДЫ БОТА ====================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Расписание на сегодня", callback_data="today")],
        [InlineKeyboardButton(text="📅 Расписание на завтра", callback_data="tomorrow")],
        [InlineKeyboardButton(text="📆 Расписание на неделю", callback_data="week")],
    ])
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        f"Я бот для расписания УрФУ (Modeus).\n"
        f"🔔 Уведомляю за {NOTIFY_BEFORE_MINUTES} минут до пары.\n"
        f"💡 Даю советы по обучению.\n\n"
        f"Нажмите кнопку ниже, чтобы увидеть расписание.",
        reply_markup=keyboard
    )


@dp.callback_query(F.data == "today")
async def callback_today(callback: CallbackQuery):
    await callback.message.answer("⏳ Загружаю расписание на сегодня...")
    lessons = await get_schedule()
    
    if not lessons:
        await callback.message.answer("❌ Не удалось получить расписание.")
        return
    
    text = f"📅 <b>Расписание на сегодня</b> ({datetime.now().strftime('%d.%m.%Y')}):\n\n"
    for lesson in lessons:
        start_str = lesson['start'][11:16] if len(lesson['start']) > 11 else lesson['start']
        end_str = lesson['end'][11:16] if len(lesson['end']) > 11 else lesson['end']
        text += (
            f"🕐 <b>{start_str} - {end_str}</b>\n"
            f"📚 {lesson['name']}\n"
            f"👨‍🏫 {lesson.get('teacher', '—')} | {lesson.get('room_display', '—')}\n\n"
        )
    
    await callback.message.answer(text, parse_mode="HTML")


@dp.callback_query(F.data == "tomorrow")
async def callback_tomorrow(callback: CallbackQuery):
    tomorrow = datetime.now() + timedelta(days=1)
    await callback.message.answer("⏳ Загружаю расписание на завтра...")
    lessons = await get_schedule(tomorrow)
    
    if not lessons:
        await callback.message.answer("❌ Не удалось получить расписание.")
        return
    
    text = f"📅 <b>Расписание на завтра</b> ({tomorrow.strftime('%d.%m.%Y')}):\n\n"
    for lesson in lessons:
        start_str = lesson['start'][11:16] if len(lesson['start']) > 11 else lesson['start']
        end_str = lesson['end'][11:16] if len(lesson['end']) > 11 else lesson['end']
        text += (
            f"🕐 <b>{start_str} - {end_str}</b>\n"
            f"📚 {lesson['name']}\n"
            f"👨‍🏫 {lesson.get('teacher', '—')} | {lesson.get('room_display', '—')}\n\n"
        )
    
    await callback.message.answer(text, parse_mode="HTML")


@dp.callback_query(F.data == "week")
async def callback_week(callback: CallbackQuery):
    await callback.message.answer("⏳ Загружаю расписание на неделю...")
    
    today = datetime.now()
    week_lessons = []
    for i in range(7):
        date = today + timedelta(days=i)
        lessons = await get_schedule(date)
        week_lessons.extend(lessons)
    
    if not week_lessons:
        await callback.message.answer("❌ Не удалось получить расписание.")
        return
    
    text = f"📅 <b>Расписание на неделю</b>:\n\n"
    for lesson in week_lessons:
        date_str = lesson['start'][:10]
        start_str = lesson['start'][11:16] if len(lesson['start']) > 11 else lesson['start']
        end_str = lesson['end'][11:16] if len(lesson['end']) > 11 else lesson['end']
        text += (
            f"📆 {date_str}\n"
            f"🕐 <b>{start_str} - {end_str}</b>\n"
            f"📚 {lesson['name']}\n"
            f"👨‍🏫 {lesson.get('teacher', '—')} | {lesson.get('room_display', '—')}\n\n"
        )
    
    await callback.message.answer(text, parse_mode="HTML")


@dp.message(Command("today"))
async def cmd_today(message: Message):
    await message.answer("⏳ Загружаю расписание на сегодня...")
    lessons = await get_schedule()
    
    if not lessons:
        await message.answer("❌ Не удалось получить расписание.")
        return
    
    text = f"📅 <b>Расписание на сегодня</b> ({datetime.now().strftime('%d.%m.%Y')}):\n\n"
    for lesson in lessons:
        start_str = lesson['start'][11:16] if len(lesson['start']) > 11 else lesson['start']
        end_str = lesson['end'][11:16] if len(lesson['end']) > 11 else lesson['end']
        text += (
            f"🕐 <b>{start_str} - {end_str}</b>\n"
            f"📚 {lesson['name']}\n"
            f"👨‍🏫 {lesson.get('teacher', '—')} | {lesson.get('room_display', '—')}\n\n"
        )
    
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("week"))
async def cmd_week(message: Message):
    await message.answer("⏳ Загружаю расписание на неделю...")
    
    today = datetime.now()
    week_lessons = []
    for i in range(7):
        date = today + timedelta(days=i)
        lessons = await get_schedule(date)
        week_lessons.extend(lessons)
    
    if not week_lessons:
        await message.answer("❌ Не удалось получить расписание.")
        return
    
    text = f"📅 <b>Расписание на неделю</b>:\n\n"
    for lesson in week_lessons:
        date_str = lesson['start'][:10]
        start_str = lesson['start'][11:16] if len(lesson['start']) > 11 else lesson['start']
        end_str = lesson['end'][11:16] if len(lesson['end']) > 11 else lesson['end']
        text += (
            f"📆 {date_str}\n"
            f"🕐 <b>{start_str} - {end_str}</b>\n"
            f"📚 {lesson['name']}\n"
            f"👨‍🏫 {lesson.get('teacher', '—')} | {lesson.get('room_display', '—')}\n\n"
        )
    
    await message.answer(text, parse_mode="HTML")


# ==================== ЗАПУСК ====================

async def main():
    token = load_token()
    if not token:
        logger.error("Токен не найден!")
        return
    
    logger.info("Токен загружен")
    
    scheduler.add_job(
        check_upcoming_lessons,
        CronTrigger(minute="*/1"),
        id="check_lessons",
        replace_existing=True,
    )
    scheduler.start()
    
    logger.info("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
