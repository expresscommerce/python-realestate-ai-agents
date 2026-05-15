"""Visit scheduling tools (not operational - users book via Redfin link)."""

from __future__ import annotations

from datetime import date, timedelta

_LEADS: list[dict] = []


def schedule_visit(
    property_id: str,
    client_name: str,
    client_phone: str,
    preferred_date: str,
    preferred_time: str,
) -> str:
    missing = []
    if not property_id:
        missing.append("property address or ID")
    if not client_name:
        missing.append("your full name")
    if not client_phone:
        missing.append("your phone number")
    if not preferred_date:
        missing.append("preferred date")
    if not preferred_time:
        missing.append("preferred time")
    if missing:
        return f"I still need: {', '.join(missing)}."

    lead = {
        "id": len(_LEADS) + 1,
        "property_id": property_id,
        "client_name": client_name,
        "client_phone": client_phone,
        "preferred_date": preferred_date,
        "preferred_time": preferred_time,
        "status": "scheduled",
    }
    _LEADS.append(lead)

    return (
        f"Visit BOOKED!\n"
        f"Property: {property_id}\n"
        f"Name: {client_name}\n"
        f"Phone: {client_phone}\n"
        f"Date: {preferred_date}\n"
        f"Time: {preferred_time}\n"
        "Our team will reach out to confirm."
    )


def check_availability(property_id: str) -> str:
    today = date.today()
    days = [today + timedelta(days=i) for i in range(1, 8)]
    slots = ["10:00 AM", "12:00 PM", "2:00 PM", "4:00 PM", "6:00 PM"]
    first = days[0].strftime("%A, %b %d")
    last = days[-1].strftime("%A, %b %d")
    return (
        f"Available slots for property {property_id}:\n"
        f"Any day from {first} to {last}.\n"
        f"Time slots: {', '.join(slots)}.\n"
        "Let me know your preferred date and time."
    )
