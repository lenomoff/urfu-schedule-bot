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
TZ = timezone(timedelta(hours=5))  # Asia/Yekaterinburg, без перехода на летнее время
UTC = timezone.utc

REMINDERS = (30, 15)
WINDOW = timedelta(minutes=15)  # допуск, если запуск workflow задержался
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


def save_state(state: dict) -> bool:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        return True
    except OSError as e:
        log(f"state.json не сохранён: {e}")
        return False


async def get_schedule(day: datetime, session) -> list[dict]:
    """Расписание на указанный локальный день, отсортированное по времени."""
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
                log("Токен УрФУ протух (401) — нужен новый refresh")
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


async def fetch_detail(session, event_id: str, headers: dict) -> dict | None:
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


def render(lesson: dict) -> str:
    tip = random.choice(TIPS.get(lesson["type"], TIPS["default"]))
    label = TYPE_LABEL.get(lesson["type"], "📚 Занятие")
    return (
        f"{label} · <b>{lesson['start']:%H:%M}–{lesson['end']:%H:%M}</b>\n"
        f"<b>{lesson['name']}</b>\n"
        f"👨‍🏫 {lesson['teacher']}  ·  {lesson['location']}\n\n"
        f"💡 {tip}"
    )


def render_day(lessons: list[dict], header: str) -> str:
    if not lessons:
        return f"{header}\n\nПар нет."
    lines = [header, ""]
    lines += [f"{render(lesson)}\n" for lesson in lessons]
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


KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📋 Сегодня", callback_data="today"),
     InlineKeyboardButton(text="📅 Завтра", callback_data="tomorrow")],
    [InlineKeyboardButton(text="🗓 Неделя", callback_data="week")],
])


async def handle_updates(bot: Bot, session) -> None:
    """Обрабатывает накопившиеся команды через getUpdates."""
    try:
        updates = await bot.get_updates(timeout=0, allowed_updates=["message", "callback_query"])
    except Exception as e:
        log(f"getUpdates не удался: {e}")
        return

    for update in updates:
        try:
            if update.callback_query:
                await bot.answer_callback_query(update.callback_query.id)
                when = {"today": 0, "tomorrow": 1, "week": None}.get(update.callback_query.data)
                if when is None:
                    continue
                chat_id, text = update.callback_query.message.chat.id, None
            elif update.message and update.message.text:
                cmd = update.message.text.split("@")[0].split()[0].lower()
                when = {"/start": None, "/today": 0, "/завтра": 1, "/tomorrow": 1, "/week": None}.get(cmd)
                if when is None:
                    continue
                chat_id = update.message.chat.id
            else:
                continue

            now = datetime.now(TZ)
            if when is None and update.callback_query is None:
                await bot.send_message(chat_id, "👋 Бот расписания УрФУ", reply_markup=KB)
                continue

            if when is None:  # неделя
                parts = []
                for i in range(7):
                    day = now + timedelta(days=i)
                    parts.append(render_day(await get_schedule(day, session),
                                           f"<b>{day:%d.%m.%Y %A}</b>"))
                text = "🗓 <b>Расписание на неделю</b>\n\n" + "\n\n".join(parts)
            else:
                day = now + timedelta(days=when)
                text = render_day(await get_schedule(day, session),
                                  f"📅 <b>{'Сегодня' if when == 0 else 'Завтра'}, {day:%d.%m.%Y}</b>")

            await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            log(f"Ошибка обработки апдейта: {e}")


async def notify(bot: Bot, session, state: dict) -> None:
    """Уведомления за 30 и 15 минут, без повторов."""
    if not CHAT_ID:
        log("CHAT_ID не задан — уведомления отключены")
        return

    now = datetime.now(TZ)
    lessons = await get_schedule(now, session)
    done = set(state["notified"])
    fresh: list[str] = []

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
                except Exception as e:
                    log(f"Не отправить уведомление: {e}")
                    continue
                done.add(key)
                fresh.append(key)

    state["notified"] = sorted(done)
    if fresh:
        save_state(state)


async def main() -> None:
    if not MODEUS_TOKEN:
        log("MODEUS_TOKEN пуст — сначала отработает refresh-token")
        return

    bot = Bot(token=BOT_TOKEN)
    # webhook конфликтует с getUpdates; pending-апдейты сохраняем
    await bot.delete_webhook(drop_pending_updates=False)

    import aiohttp
    state = load_state()
    async with aiohttp.ClientSession() as session:
        await handle_updates(bot, session)
        await notify(bot, session, state)

    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
