"""
Telegram-бот для расписания УрФУ (Modeus) — GitHub Actions
Отправляет уведомления о предстоящих парах.
"""

import os
import asyncio
from datetime import datetime, timedelta

import aiohttp
from aiogram import Bot

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MODEUS_TOKEN = os.environ.get("MODEUS_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
MODEUS_API_URL = "https://urfu.modeus.org/schedule-calendar-v2/api/calendar/events/search"
TIMEZONE = "Asia/Yekaterinburg"
NOTIFY_BEFORE_MINUTES = 15

bot = Bot(token=BOT_TOKEN)


async def get_schedule(date=None):
    if date is None:
        date = datetime.now()
    
    if not MODEUS_TOKEN:
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
        "Authorization": f"Bearer {MODEUS_TOKEN}",
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
                    
                    lessons = []
                    for event in events:
                        event_id = event.get("id", "")
                        full_data = await get_event_details(session, event_id)
                        if full_data:
                            lesson = parse_full_event(full_data)
                            if lesson:
                                lessons.append(lesson)
                    
                    lessons.sort(key=lambda x: x.get("start", ""))
                    return lessons
                return []
        except:
            return []


async def get_event_details(session, event_id):
    try:
        url = f"https://urfu.modeus.org/schedule-calendar-v2/api/calendar/events/{event_id}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Bearer {MODEUS_TOKEN}",
        }
        async with session.get(url, headers=headers, ssl=False, timeout=10) as resp:
            if resp.status == 200:
                return await resp.json()
    except:
        pass
    return {}


def parse_full_event(data):
    embedded = data.get("_embedded", {})
    course = embedded.get("course-unit-realization", {})
    subject = course.get("name", data.get("name", ""))
    
    teacher = ""
    persons = embedded.get("persons", [])
    attendees = embedded.get("event-attendees", [])
    
    for attendee in attendees:
        if attendee.get("roleId") == "TEACH":
            person_href = attendee.get("_links", {}).get("person", {}).get("href", "")
            person_id = person_href.split("/")[-1] if person_href else ""
            for person in persons:
                if person.get("id") == person_id:
                    teacher = person.get("fullName", "")
                    break
            break
    
    location = embedded.get("location", {})
    room = location.get("customLocation") or ""
    
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


async def send_notification(chat_id, lesson):
    start_str = lesson['start'][11:16] if len(lesson['start']) > 11 else lesson['start']
    end_str = lesson['end'][11:16] if len(lesson['end']) > 11 else lesson['end']
    
    text = (
        f"⏰ <b>Через {NOTIFY_BEFORE_MINUTES} минут пара!</b>\n\n"
        f"📚 <b>{lesson['name']}</b>\n"
        f"🕐 Время: <b>{start_str} - {end_str}</b>\n"
        f"👨‍🏫 Преподаватель: {lesson.get('teacher', '—')}\n"
        f"📍 {lesson.get('room_display', '—')}"
    )
    await bot.send_message(chat_id, text, parse_mode="HTML")


async def main():
    if not CHAT_ID:
        print("CHAT_ID не задан")
        return
    
    now = datetime.now()
    check_time = now + timedelta(minutes=NOTIFY_BEFORE_MINUTES)
    
    lessons = await get_schedule(now)
    for lesson in lessons:
        try:
            start_str = lesson["start"]
            lesson_time = datetime.fromisoformat(start_str.replace("+05:00", ""))
            lesson_time = lesson_time.replace(tzinfo=None)
            
            if now <= lesson_time <= check_time:
                await send_notification(CHAT_ID, lesson)
                print(f"Отправлено уведомление: {lesson['name']}")
        except:
            continue


if __name__ == "__main__":
    asyncio.run(main())
