import random
import time
import functools
from pydantic import BaseModel

def llm_call(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        print(f"\n  → {fn.__name__}...")
        time.sleep(random.uniform(1, 3))
        result = fn(*args, **kwargs)
        return result
    return wrapper

class TimeSlot(BaseModel):
    day: str
    date: str
    time: str

class CalenderEvent(BaseModel):
    name: str
    time: TimeSlot

@llm_call
def fetch_availability() -> list[TimeSlot]:
    TIMES = [
        {"day": "Monday", "date": "May 11", "time": "9:00 AM"},
        {"day": "Monday", "date": "May 11", "time": "2:00 PM"},
        {"day": "Tuesday", "date": "May 12", "time": "10:00 AM"},
        {"day": "Wednesday", "date": "May 13", "time": "11:00 AM"},
        {"day": "Thursday", "date": "May 14", "time": "9:00 AM"},
        {"day": "Friday", "date": "May 15", "time": "1:00 PM"},
        {"day": "Friday", "date": "May 15", "time": "4:00 PM"},
    ]

    available_times = random.sample(TIMES, 3)

    return [TimeSlot(**t) for t in available_times]

def prompt_reservation(available_slots: list[TimeSlot]) -> TimeSlot:
    print("\n  Available Slots")
    print("  " + "─" * 28)
    for i, slot in enumerate(available_slots, 1):
        print(f"  [{i}]  {slot.day}, {slot.date}  ·  {slot.time}")
    print("  " + "─" * 28)

    while True:
        choice = input("\n  Select a slot (1-3): ").strip()
        if choice in ("1", "2", "3"):
            return available_slots[int(choice) - 1]
        print("  Please enter 1, 2, or 3.")

@llm_call
def create_calendar_event(reservation: TimeSlot) -> CalenderEvent:
    # insert external API call
    print("   🗓️ SAVED TO CALENDER")
    return CalenderEvent(
        name="My Event",
        time=reservation
    )

@llm_call
def send_confirmation(calender_event):
    # insert external API call
    print("   📧 EMAIL SENT")

def run():
    available_slots = fetch_availability()
    reservation = prompt_reservation(available_slots)
    calender_event = create_calendar_event(reservation)
    # raise Exception("💥 Process crashed!") # toggle for crash
    send_confirmation(calender_event)


run()
