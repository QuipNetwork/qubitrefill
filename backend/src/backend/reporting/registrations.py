"""Registration digest projection — the registered-contact list for the team.

Modeled on ``persistence.leaderboard``: a typed, read-only projection of the
``agents`` table. The agent id is the secret API key, so — exactly as in the
leaderboard — it is **never** selected or emitted here; the query names its
columns explicitly rather than loading whole ``Agent`` rows.

Pure functions (no I/O except the passed session) so the scheduler and the CLI
share one code path and the whole thing is unit-testable without a mail server.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Agent

# The exact, ordered set of columns that leave the DB. Adding a column to Agent
# does not silently widen the export — it must be added here on purpose. Note the
# absence of ``id`` (the API key).
CSV_COLUMNS: tuple[str, ...] = (
    "name",
    "handle",
    "email",
    "updates_opt_in",
    "reach_out",
    "created_at",
    "jobs_solved",
)

# Leading characters a spreadsheet may interpret as a formula. User-controlled
# fields (name, reach_out) are prefixed with a quote so Excel/Sheets treat the
# cell as text — CSV/formula-injection defense.
_FORMULA_TRIGGERS: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class RegistrationRow:
    """One registrant, allowlisted to non-secret fields."""

    name: str
    handle: str | None
    email: str
    updates_opt_in: bool | None
    reach_out: list[str] | None
    created_at: str
    jobs_solved: int


@dataclass(frozen=True)
class RegistrationStats:
    total: int
    opted_in: int
    opt_in_rate: float  # 0..1
    new_since: int


async def fetch_rows(session: AsyncSession) -> list[RegistrationRow]:
    """Load all registrants as allowlisted rows, oldest first.

    Selects named columns — never ``Agent.id`` — so the secret API key cannot
    reach a report even by accident.
    """
    result = await session.execute(
        select(
            Agent.name,
            Agent.handle,
            Agent.email,
            Agent.updates_opt_in,
            Agent.reach_out,
            Agent.created_at,
            Agent.jobs_solved,
        ).order_by(Agent.created_at)
    )
    return [RegistrationRow(*row) for row in result.all()]


def compute_stats(rows: list[RegistrationRow], since: str | None) -> RegistrationStats:
    """Aggregate counts. ``since`` is the ISO-8601 UTC timestamp of the last send.

    ``new_since`` counts registrants created after ``since`` (all of them when
    ``since`` is None — the first digest). Comparing full timestamps (not just the
    date prefix) means someone who registers later on the send's own day is still
    counted; ISO-8601 with a fixed offset sorts lexically, so a string compare is
    correct.
    """
    total = len(rows)
    opted_in = sum(1 for r in rows if r.updates_opt_in)
    new_since = total if since is None else sum(1 for r in rows if r.created_at > since)
    rate = opted_in / total if total else 0.0
    return RegistrationStats(total=total, opted_in=opted_in, opt_in_rate=rate, new_since=new_since)


def _sanitize_cell(value: object) -> str:
    """Render a cell as text, neutralizing spreadsheet formula injection."""
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "yes" if value else "no"
    elif isinstance(value, (list, tuple)):
        text = "; ".join(str(v) for v in value)
    else:
        text = str(value)
    if text.startswith(_FORMULA_TRIGGERS):
        return "'" + text
    return text


def rows_to_csv(rows: list[RegistrationRow]) -> str:
    """Serialize rows to CSV text with the fixed header and injection guard."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        writer.writerow([_sanitize_cell(getattr(r, col)) for col in CSV_COLUMNS])
    return buffer.getvalue()


def render(stats: RegistrationStats, today: str) -> tuple[str, str, str]:
    """Build (subject, text, html) for the digest email. The CSV is attached."""
    subject = f"Qubitrefill registrations — {today} ({stats.total} total)"
    pct = f"{stats.opt_in_rate * 100:.0f}%"
    lines = (
        f"Registration digest for {today}",
        "",
        f"  Total registered:   {stats.total}",
        f"  Opted in to updates: {stats.opted_in} ({pct})",
        f"  New since last digest: {stats.new_since}",
        "",
        "The full contact list is attached as a CSV. The 'updates_opt_in' column "
        "shows who consented to updates — check it before contacting anyone.",
    )
    text = "\n".join(lines)
    html = (
        f"<p><strong>Registration digest for {today}</strong></p>"
        "<ul>"
        f"<li>Total registered: <strong>{stats.total}</strong></li>"
        f"<li>Opted in to updates: <strong>{stats.opted_in}</strong> ({pct})</li>"
        f"<li>New since last digest: <strong>{stats.new_since}</strong></li>"
        "</ul>"
        "<p>The full contact list is attached as a CSV. The <code>updates_opt_in</code> "
        "column shows who consented to updates — check it before contacting anyone.</p>"
    )
    return subject, text, html
