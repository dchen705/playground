import time
import functools
from openai import OpenAI
from pydantic import BaseModel

client = OpenAI()
MODEL = "gpt-4o-mini"

def llm_call(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        print(f"\n  → {fn.__name__}...")
        result = fn(*args, **kwargs)
        return result
    return wrapper


class TimeSlot(BaseModel):
    day: str
    date: str
    time: str

class AvailableSlots(BaseModel):
    slots: list[TimeSlot]

class CalendarEvent(BaseModel):
    name: str
    time: TimeSlot

class Confirmation(BaseModel):
    message: str


@llm_call
def fetch_availability() -> list[TimeSlot]:
    response = client.responses.parse(
        model=MODEL,
        input="Generate 3 realistic appointment time slots for next week. Use specific days and times.",
        text_format=AvailableSlots,
    )
    return response.output_parsed.slots


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
def create_calendar_event(reservation: TimeSlot) -> CalendarEvent:
    response = client.responses.parse(
        model=MODEL,
        input=f"Create a short calendar event name for this appointment: {reservation.model_dump_json()}",
        text_format=CalendarEvent,
    )
    event = response.output_parsed
    print(f"   🗓️ SAVED TO CALENDAR: {event.name}")
    return event


@llm_call
def send_confirmation(calendar_event: CalendarEvent) -> Confirmation:
    response = client.responses.parse(
        model=MODEL,
        input=f"Write a one-sentence email confirmation for this appointment: {calendar_event.model_dump_json()}",
        text_format=Confirmation,
    )
    confirmation = response.output_parsed
    print(f"   📧 EMAIL SENT: {confirmation.message}")
    return confirmation


def run():
    available_slots = fetch_availability()
    reservation = prompt_reservation(available_slots)
    calendar_event = create_calendar_event(reservation)
    # raise Exception("💥 Process crashed!")  # toggle for crash demo
    send_confirmation(calendar_event)


if __name__ == "__main__":
    run()
