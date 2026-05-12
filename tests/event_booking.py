from openai import OpenAI
from pydantic import BaseModel
from sdk import workflow, step, init

client = OpenAI()
MODEL = "gpt-5-mini"


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


@step()
def fetch_availability() -> list[TimeSlot]:
    print("\n  → fetch_availability...")
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


@step()
def create_calendar_event(reservation: TimeSlot) -> CalendarEvent:
    print("\n  → create_calendar_event...")
    response = client.responses.parse(
        model=MODEL,
        input=f"Create a short calendar event name for this appointment: {reservation.model_dump_json()}",
        text_format=CalendarEvent,
    )
    event = response.output_parsed
    print(f"   🗓️ SAVED TO CALENDAR: {event.name}")
    return event


@step()
def send_confirmation(calendar_event: CalendarEvent) -> Confirmation:
    print("\n  → send_confirmation...")
    response = client.responses.parse(
        model=MODEL,
        input=f"Write a one-sentence email confirmation for this appointment: {calendar_event.model_dump_json()}",
        text_format=Confirmation,
    )
    confirmation = response.output_parsed
    print(f"   📧 EMAIL SENT: {confirmation.message}")
    return confirmation


@workflow()
def book_event() -> Confirmation:
    available_slots = fetch_availability()
    reservation = prompt_reservation(available_slots)
    calendar_event = create_calendar_event(reservation)
    # raise Exception("💥 Process crashed!")  # toggle to test crash recovery
    confirmation = send_confirmation(calendar_event)
    return confirmation


if __name__ == "__main__":
    init(name="event-booking")
    book_event()
