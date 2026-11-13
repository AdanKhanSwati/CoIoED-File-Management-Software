# Hearing Notice Enhancements — Integration Prototype

This module provides SQLite-backed prototype operations. **It is not integrated into the application's real schema or UI.**

## Implemented
1. Compute notice display name from its earliest/latest hearing day: `11th November 2026 to 13th November 2026`. Internal numeric notice identifiers remain unchanged.
2. Move a hearing **day** from one notice to another without retyping its cases. The operation uses a database transaction, logs actor and source/destination, rejects duplicate dates, and blocks approved days by default.
3. Generate sample HTML for printing one hearing day.

## Critical limitations
The included `install_schema()` is a standalone test schema, **not a migration** for the real application. Match the existing data model, administrative permissions, approval workflow, officer lists, and UI before using.

The sample individual-day printer contains only demo columns; **do not use as an official hearing schedule**. It must call the existing approved seven-column print engine. In the official consolidated notice, ANNEX-A/To:/dated appears only on the first physical page; each day header appears once, and continuation pages repeat only the table header. A single-day print should preserve the approved page formatting and not invent new stationery.

## Run tests
`python -m unittest discover -s tests -v`
