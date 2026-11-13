"""CoIoED hearing-notice operations for an SQLite-backed pilot.

Integration adapter: map actual application table/column names before using this
against existing production data. All writes use explicit SQLite transactions.
"""
from __future__ import annotations
from datetime import date
import sqlite3
from html import escape


def _ordinal(day: int) -> str:
    return f"{day}{'th' if 11 <= day % 100 <= 13 else {1:'st', 2:'nd', 3:'rd'}.get(day % 10, 'th')}"


def date_label(value: str) -> str:
    d = date.fromisoformat(value)
    return f"{_ordinal(d.day)} {d.strftime('%B %Y')}"


def install_schema(connection: sqlite3.Connection) -> None:
    """Standalone test schema; use migrations/adapters for an existing application."""
    connection.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS hearing_notices (
        id INTEGER PRIMARY KEY,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS hearing_days (
        id INTEGER PRIMARY KEY,
        notice_id INTEGER NOT NULL REFERENCES hearing_notices(id),
        hearing_date TEXT NOT NULL,
        hearing_time TEXT NOT NULL DEFAULT '10:30 AM',
        districts TEXT NOT NULL DEFAULT '',
        approved INTEGER NOT NULL DEFAULT 0 CHECK(approved IN (0,1)),
        UNIQUE (notice_id, hearing_date)
    );
    CREATE TABLE IF NOT EXISTS hearing_day_cases (
        id INTEGER PRIMARY KEY,
        day_id INTEGER NOT NULL REFERENCES hearing_days(id) ON DELETE CASCADE,
        case_code TEXT NOT NULL,
        sort_order INTEGER NOT NULL,
        details TEXT NOT NULL DEFAULT '',
        UNIQUE(day_id, sort_order)
    );
    CREATE TABLE IF NOT EXISTS hearing_day_moves (
        id INTEGER PRIMARY KEY,
        day_id INTEGER NOT NULL,
        source_notice_id INTEGER NOT NULL,
        destination_notice_id INTEGER NOT NULL,
        moved_by TEXT NOT NULL,
        moved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)


def notice_label(connection: sqlite3.Connection, notice_id: int) -> str:
    if not connection.execute('SELECT 1 FROM hearing_notices WHERE id=?', (notice_id,)).fetchone():
        raise ValueError('Notice not found')
    start, end = connection.execute('SELECT MIN(hearing_date), MAX(hearing_date) FROM hearing_days WHERE notice_id=?', (notice_id,)).fetchone()
    if start is None:
        return 'No hearing dates assigned'
    return date_label(start) if start == end else f'{date_label(start)} to {date_label(end)}'


def move_day(connection: sqlite3.Connection, day_id: int, destination_notice_id: int, *, actor: str, allow_approved: bool = False) -> None:
    """Move, don't copy: same day ID/case rows, changed notice FK, audit trail.

    Existing transactions are not supported: call this on a dedicated connection.
    Approved days are locked by default; approval change should follow institutional policy.
    """
    if not actor.strip():
        raise ValueError('Authenticated actor is required')
    if connection.in_transaction:
        raise RuntimeError('Complete existing transaction before moving a hearing day')
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('BEGIN IMMEDIATE')
    try:
        source = connection.execute('SELECT notice_id, hearing_date, approved FROM hearing_days WHERE id=?', (day_id,)).fetchone()
        if source is None:
            raise ValueError('Hearing day not found')
        source_id, hearing_date, approved = source
        if source_id == destination_notice_id:
            raise ValueError('Day already belongs to the destination notice')
        if approved and not allow_approved:
            raise PermissionError('Approved schedules cannot be moved without an authorized revision workflow')
        if not connection.execute('SELECT 1 FROM hearing_notices WHERE id=?', (destination_notice_id,)).fetchone():
            raise ValueError('Destination notice not found')
        if connection.execute('SELECT 1 FROM hearing_days WHERE notice_id=? AND hearing_date=?', (destination_notice_id, hearing_date)).fetchone():
            raise ValueError('Destination notice already has a schedule for this date; merge is not automatic')
        connection.execute('UPDATE hearing_days SET notice_id=? WHERE id=?', (destination_notice_id, day_id))
        connection.execute('INSERT INTO hearing_day_moves(day_id,source_notice_id,destination_notice_id,moved_by) VALUES(?,?,?,?)', (day_id, source_id, destination_notice_id, actor.strip()))
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def single_day_print_html(connection: sqlite3.Connection, day_id: int) -> str:
    """Printable *single day* HTML; existing official schedule renderer should replace body.

    No ANNEX-A/To block is emitted: the consolidated notice owns that first-page block.
    """
    day = connection.execute('SELECT hearing_date, hearing_time, districts FROM hearing_days WHERE id=?', (day_id,)).fetchone()
    if not day:
        raise ValueError('Hearing day not found')
    hearing_date, hearing_time, districts = day
    cases = connection.execute('SELECT case_code, details FROM hearing_day_cases WHERE day_id=? ORDER BY sort_order,id', (day_id,)).fetchall()
    rows = ''.join('<tr><td>'+str(i)+'</td><td>'+escape(code)+'</td><td>'+escape(details)+'</td></tr>' for i,(code,details) in enumerate(cases,1))
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Hearing Schedule - {escape(date_label(hearing_date))}</title>
<style>@page{{size:A4 landscape;margin:12mm}}body{{font:12px Arial,sans-serif;color:#111}}h2,p{{text-align:center;margin:8px}}table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid #333;padding:6px;text-align:left;vertical-align:top}}thead{{display:table-header-group}}button{{margin:12px}}@media print{{button{{display:none}}}}</style></head><body>
<button onclick="window.print()">Print this day</button><h2>SCHEDULE - INQUIRY HEARING</h2>
<p>{escape(hearing_time)} onwards on {escape(date_label(hearing_date))}</p><p>{escape(districts)}</p>
<table><thead><tr><th>Sr.</th><th>CoIoED No.</th><th>Details (demo adapter)</th></tr></thead><tbody>{rows}</tbody></table></body></html>'''
