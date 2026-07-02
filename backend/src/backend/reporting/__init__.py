"""Read-only reporting projections over the persistence tables.

Like ``persistence.leaderboard``, these derive team-facing views from the
``agents`` table on read — there is no separate metrics store. Every projection
uses an explicit column allowlist because ``Agent.id`` is the secret API key.
"""
