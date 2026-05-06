import sys
import os
import logging
import inngest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))

from event_booking import (
    fetch_availability,
    prompt_reservation,
    create_calendar_event,
    send_confirmation,
    TimeSlot,
    CalendarEvent,
    Confirmation,
)

inngest_client = inngest.Inngest(
    app_id="booking-agent",
    logger=logging.getLogger("uvicorn"),
    serializer=inngest.PydanticSerializer(),
)

@inngest_client.create_function(
    fn_id="agent-run",
    trigger=inngest.TriggerEvent(event="agent/run"),
)
async def run_agent(ctx: inngest.Context) -> None:
    available_slots = await ctx.step.run("fetch-availability", fetch_availability, output_type=list[TimeSlot])

    reservation = prompt_reservation(available_slots) # need ctx.step.wait_for_event?

    calendar_event = await ctx.step.run("create-calendar-event", lambda: create_calendar_event(reservation), output_type=CalendarEvent)

    await ctx.step.run("send-confirmation", lambda: send_confirmation(calendar_event), output_type=Confirmation)
