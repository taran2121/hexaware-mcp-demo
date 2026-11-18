"""Reset the demo to a known state, with dates relative to today.

Writes tracker/actions.json, tracker/clients.json, inbox/northwind-call.md and clears reports/.
Clients added during a demo (and their call notes) are removed.
Run before every rehearsal and every live session so the RAG statuses are
always: Northwind = Red, Contoso = Amber, Fabrikam = Green.

All names and clients are fictional training data.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
T = date.today()


def d(days: int) -> str:
    return (T + timedelta(days=days)).isoformat()


def friday_on_or_after(day: date) -> date:
    return day + timedelta(days=(4 - day.weekday()) % 7)


CLIENTS = [
    ("Northwind", "Priya", "Core-banking data migration, phase 2"),
    ("Contoso", "Rahul", "Retail analytics platform rollout"),
    ("Fabrikam", "Meera", "Supplier-portal integration"),
]

SEED = [
    ("Northwind", "Send revised SOW", "Priya", -3, "high", "open"),
    ("Northwind", "Share data migration plan", "Arjun", 4, "medium", "open"),
    ("Northwind", "Book steering committee slot", "Meera", -10, "low", "done"),
    ("Contoso", "Confirm UAT environment access", "Rahul", -2, "medium", "open"),
    ("Contoso", "Send training schedule", "Priya", 9, "low", "in_progress"),
    ("Fabrikam", "Review integration test results", "Arjun", 2, "high", "open"),
    ("Fabrikam", "Circulate release notes", "Meera", 12, "medium", "open"),
]


def main() -> None:
    created = datetime.now().isoformat(timespec="seconds")
    items = [
        {
            "id": f"ACT-{n:03}",
            "client": c,
            "title": t,
            "owner": o,
            "due": d(days),
            "priority": p,
            "status": s,
            "created": created,
            "notes": [],
        }
        for n, (c, t, o, days, p, s) in enumerate(SEED, start=1)
    ]
    (LAB / "tracker" / "actions.json").write_text(json.dumps(items, indent=2), encoding="utf-8")
    clients = [{"name": n, "account_lead": lead, "programme": prog, "created": created} for n, lead, prog in CLIENTS]
    (LAB / "tracker" / "clients.json").write_text(json.dumps(clients, indent=2), encoding="utf-8")
    keep = {f"{n.lower()}.md" for n, _, _ in CLIENTS}
    for f in (LAB / "tracker" / "notes").glob("*.md"):
        if f.name not in keep:
            f.unlink()  # notes of a client added during the demo

    fri = friday_on_or_after(T + timedelta(days=1))
    inbox = LAB / "inbox"
    inbox.mkdir(exist_ok=True)
    (inbox / "northwind-call.md").write_text(
        f"""# Northwind weekly call: {T.isoformat()}

Fictional training data. Attendees: Priya (delivery lead), Arjun (data), Meera (PMO),
Rahul (QA), and the Northwind programme sponsor.

## Decisions
- Cut-over moves to the second weekend of next month.
- Northwind will provide a sandbox for payment-gateway testing.

## Risks
- Northwind's legal review of the revised SOW is waiting on us; sponsor sounded frustrated.
- Payment-gateway sandbox may not arrive in time for system test.

## Action items
1. Priya to send the revised SOW to Northwind legal by {d(-3)} (already late, high priority).
2. Arjun to share the data reconciliation report by {d(3)}. This is urgent for the sponsor.
3. Meera to set up a weekly risk review with the Northwind PMO by {fri.isoformat()}.
4. Rahul to draft the payment-gateway test plan by {d(7)}. Important.
5. Priya to confirm cut-over weekend resourcing by {d(10)}.
6. Meera to circulate these minutes by {d(1)}, nice to have.
""",
        encoding="utf-8",
    )

    reports = LAB / "reports"
    reports.mkdir(exist_ok=True)
    for f in reports.iterdir():
        if f.is_file() and f.name != ".gitkeep":
            f.unlink()
    (inbox / "export.md").unlink(missing_ok=True)

    print(f"Reset done for {T.isoformat()}: {len(CLIENTS)} clients, {len(items)} actions, inbox/northwind-call.md, reports/ cleared.", file=sys.stderr)


if __name__ == "__main__":
    main()
