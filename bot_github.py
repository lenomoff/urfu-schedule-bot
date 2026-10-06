"""Бот расписания УрФУ для GitHub Actions: команды + уведомления за 30 и 15 минут."""

import asyncio
import json
import os
import random
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

BOT_TOKEN = os.environ["BOT_TOKEN"]
MODEUS_TOKEN = os.environ.get("MODEUS_TOKEN", "")
CHAT_ID = int(os.environ.get("CHAT_ID", "0") or 0)

API_URL = "https://urfu.modeus.org/schedule-calendar-v2/api/calendar/events/search"
PERSON_ID = "330d3b77-3f42-457d-9642-cc95bd6c0b5f"
TZ = timezone(timedelta(hours=5))
UTC = timezone.utc

REMINDERS = (30, 15)
WINDOW = timedelta(minutes=15)
STATE_FILE = "state.json"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://urfu.modeus.org",
    "Referer": "https://urfu.modeus.org/schedule-calendar/my",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
}

TIPS = {
    "LAB": [
        "Перечитай методичку до пары — сразу будет понятно, что и как делать.",
        "Собери список оборудования заранее, чтобы не терять 15 минут в начале.",
        "Веди записи прямо во время работы — потом по ним напишешь отчёт за минуту.",
        "Если результат не совпал с ожиданием — это не ошибка, а повод задать вопрос.",
    ],
    "LECT": [
        "Прочитай конспект прошлой пары — новое ляжет на уже знакомую структуру.",
        "Записывай только главное, своими словами. Пересказ — лучшая проверка понимания.",
        "Отметь непонятные места сразу: после пары вопросы кажутся глупыми.",
        "Пройдись по материалу в тот же день, пока свежо — это экономит час потом.",
    ],
    "SEMI": [
        "Повтори синтаксис перед практикой, чтобы не искать базу во время выполнения.",
        "Разбей задачу на шаги: так проще найти, где именно ошибка.",
        "Обсуди задачу с группой — часто всплывают идеи, которые в одиночку не видны.",
        "Не забудь про граничные случаи и тесты, их проверяют в первую очередь.",
    ],
    "default": [
        "Поставь цель на пару: что конкретно ты хочешь понять за эти 80 минут.",
        "Проверь наушники и зарядку — мелочь, которая стоит дороже всех остальных.",
        "Первые пять минут потрать на повторение прошлого, а не на листание телефона.",
        "После пары запиши одним предложением главное — так это не выветрится за день.",
    ],
}

TYPE_LABEL = {"LAB": "🔬 Лабораторная", "LECT": "📖 Лекция", "SEMI": "💻 Практика"}

# cmd -> (действие, сдвиг дней)
COMMANDS = {
    "/start": ("start", 0),
    "/help": ("start", 0),
    "/help": ("start", 0),
    "/сегодня": ("day", 0),
    "/today": ("day", 0),
    "/завтра": ("day", 1),
    "/tomorrow": ("day", 1),
    "/неделя": ("week", 0),
    "/week": ("week", 0),
}
CALLBACKS = {
    "today": ("day", 0),
    "tomorrow": ("day", 1),
    "week": ("week", 0),
}

KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📋 Сегодня", callback_data="today"),
     InlineKeyboardButton(text="📅 Завтра", callback_data="tomorrow")],
    [InlineKeyboardButton(text="🗓 Неделя", callback_data="week")],
])


def log(msg):
    print(msg, flush=True)


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("notified"), list):
                return data
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as e:
        log(f"state.json не читается: {e}")
    return {"notified": []}


def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log(f"state.json не сохранён: {e}")


async def get_schedule(day: datetime, session) -> list[dict]:
    if not MODEUS_TOKEN:
        log("MODEUS_TOKEN пуст — расписание пропущено")
        return []

    start_local = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    payload = {
        "size": 500,
        "timeMin": start_local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timeMax": end_local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "attendeePersonId": [PERSON_ID],
    }
    headers = {**HEADERS, "Authorization": f"Bearer {MODEUS_TOKEN}"}

    try:
        async with session.post(
            API_URL, params={"tz": "Asia/Yekaterinburg"},
            json=payload, headers=headers, ssl=False, timeout=30,
        ) as resp:
            if resp.status == 401:
                log("Токен УрФУ протух (401)")
                return []
            if resp.status != 200:
                log(f"Modeus API: HTTP {resp.status}")
                return []
            events = (await resp.json()).get("_embedded", {}).get("events", [])
    except Exception as e:
        log(f"Ошибка запроса расписания: {e}")
        return []

    lessons = []
    for event in events:
        detail = await fetch_detail(session, event.get("id", ""), headers)
        if detail:
            lessons.append(parse_lesson(detail))

    lessons.sort(key=lambda x: x["start"])
    return lessons


async def fetch_detail(session, event_id: str, headers: dict):
    if not event_id:
        return None
    url = f"https://urfu.modeus.org/schedule-calendar-v2/api/calendar/events/{event_id}"
    try:
        async with session.get(url, headers=headers, ssl=False, timeout=20) as resp:
            return await resp.json() if resp.status == 200 else None
    except Exception as e:
        log(f"Ошибка деталей {event_id}: {e}")
        return None


def parse_lesson(detail: dict) -> dict:
    embedded = detail.get("_embedded", {})

    subject = embedded.get("course-unit-realization", {}).get("name") or detail.get("name", "")

    teacher = ""
    for attendee in embedded.get("event-attendees", []):
        if attendee.get("roleId") == "TEACH":
            href = attendee.get("_links", {}).get("person", {}).get("href", "")
            wanted = href.rsplit("/", 1)[-1]
            for person in embedded.get("persons", []):
                if person.get("id") == wanted:
                    teacher = person.get("fullName", "")
                    break
            break

    room = embedded.get("location", {}).get("customLocation") or ""
    location = f"🔗 {room}" if room.startswith("http") else (f"🚪 {room}" if room else "—")

    return {
        "id": detail.get("id", ""),
        "name": subject,
        "teacher": teacher or "—",
        "location": location,
        "type": detail.get("typeId", ""),
        "start": datetime.fromisoformat(detail["start"]),
        "end": datetime.fromisoformat(detail["end"]),
    }


def render_day(lessons: list[dict], header: str) -> str:
    if not lessons:
        return f"{header}\n\nПар нет."
    lines = [header, ""]
    for lesson in lessons:
        tip = random.choice(TIPS.get(lesson["type"], TIPS["default"]))
        label = TYPE_LABEL.get(lesson["type"], "📚 Занятие")
        lines.append(
            f"{label} · <b>{lesson['start']:%H:%M}–{lesson['end']:%H:%M}</b>\n"
            f"<b>{lesson['name']}</b>\n"
            f"👨‍🏫 {lesson['teacher']}  ·  {lesson['location']}\n\n"
            f"💡 {tip}\n"
        )
    return "\n".join(lines).strip()


def render_reminder(lesson: dict, minutes: int) -> str:
    tip = random.choice(TIPS.get(lesson["type"], TIPS["default"]))
    label = TYPE_LABEL.get(lesson["type"], "📚 Занятие")
    return (
        f"⏰ <b>Через {minutes} минут пара</b>\n\n"
        f"{label} · <b>{lesson['start']:%H:%M}–{lesson['end']:%H:%M}</b>\n"
        f"<b>{lesson['name']}</b>\n"
        f"👨‍🏫 {lesson['teacher']}  ·  {lesson['location']}\n\n"
        f"💡 {tip}"
    )


async def handle_updates(bot: Bot, session) -> None:
    try:
        updates = await bot.get_updates(timeout=0, allowed_updates=["message", "callback_query"])
    except Exception as e:
        log(f"getUpdates не удался: {e}")
        return

    log(f"Апдейтов получено: {len(updates)}")
    if not updates:
        log("Новых сообщений нет")

    for update in updates:
        chat_id = None
        action = None        offset = 0

        try:
            if update.callback_query:
                await bot.answer_callback_query(update.callback_query.id)
                chat_id = update.callback_query.message.chat.id
                action, offset = CALLBACKS.get(update.callback_query.data, (None, 0))
            elif update.message and update.message.text:
                chat_id = update.message.chat.id
                cmd = update.message.text.split("@")[0].split()[0].lower()
                action, offset = COMMANDS.get(cmd, (None, 0))
            else:
                continue

            if chat_id is None or action is None:
                continue

            log(f"Обрабатываю {action} (сдвиг {offset}) для {chat_id}")

            if action == "start":
                await bot.send_message(
                    chat_id,
                    "👋 Бот расписания УрФУ.\n\n"
                    f"📋 Сегодня · 📅 Завтра · 🗓 Неделя\n"
                    f"🔔 Уведомляю за 30 и 15 минут до пары.",
                    reply_markup=KB,
                )
                continue

            day = datetime.now(TZ) + timedelta(days=offset)

            if action == "day":
                text = render_day(
                    await get_schedule(day, session),
                    f"📅 <b>{'Сегодня' if offset == 0 else 'Завтра'}, {day:%d.%m.%Y}</b>",
                )
            else:
                parts = []
                for i in range(7):
                    d = day + timedelta(days=i)
                    parts.append(render_day(await get_schedule(d, session),
 f"<b>{d:%d.%m.%Y}</b>"))
                text = "🗓 <b>Расписание на неделю</b>\n\n" + "\n\n".join(parts)

            await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            log(f"Ошибка обработки апдейта: {e}")


async def notify(bot: Bot, session, state: dict) -> None:
    if not CHAT_ID:
        log("CHAT_ID не задан — уведомления отключены")
        return

    now = datetime.now(TZ)
    lessons = await get_schedule(now, session)
    log(f"Пар сегодня: {len(lessons)}")
    done = set(state["notified"])

    for lesson in lessons:
        for minutes in REMINDERS:
            key = f"{lesson['id']}_{minutes}"
            if key in done:
                continue
            fire_at = lesson["start"] - timedelta(minutes=minutes)
            if fire_at <= now < fire_at + WINDOW:
                try:
                    await bot.send_message(CHAT_ID, render_reminder(lesson, minutes),
                                           parse_mode=ParseMode.HTML)
                    log(f"Уведомление за {minutes} мин: {lesson['name']}")
                    done.add(key)
                except Exception as e:
                    log(f"Не отправить уведомление: {e}")

    state["notified"] = sorted(done)
    save_state(state)


async def main() -> None:
    log(f"CHAT_ID={CHAT_ID}, токен УрФУ: {'есть' if MODEUS_TOKEN else 'НЕТ'}")

    if not MODEUS_TOKEN:
        log("Без токена УрФУ работают только команды")
    bot = Bot(token=BOT_TOKEN)
    await bot.delete_webhook(drop_pending_updates=False)

    import aiohttp
    state = load_state()
    async with aiohttp.ClientSession() as session:
        await handle_updates(bot, session)
        await notify(bot, session, state)

    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
