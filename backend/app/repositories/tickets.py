from __future__ import annotations

import sqlite3
from typing import Any


def next_ticket_no(conn: sqlite3.Connection) -> str:
    n = conn.execute("SELECT COUNT(*) c FROM price_tickets").fetchone()["c"] + 1
    return f"PT-{n:04d}"


def create_ticket(
    conn: sqlite3.Connection,
    *,
    ticket_no: str,
    title: str,
    effective_at: str,
    note: str | None,
    created_at: str,
) -> int:
    cur = conn.execute(
        '''
        INSERT INTO price_tickets(ticket_no, title, status, effective_at, note, created_at)
        VALUES (?,?,?,?,?,?)
        ''',
        (ticket_no, title, "scheduled", effective_at, note, created_at),
    )
    return int(cur.lastrowid)


def add_line(
    conn: sqlite3.Connection,
    *,
    ticket_id: int,
    sku_code: str,
    old_price: float,
    new_price: float,
) -> int:
    cur = conn.execute(
        '''
        INSERT INTO price_ticket_lines(ticket_id, sku_code, old_price, new_price)
        VALUES (?,?,?,?)
        ''',
        (ticket_id, sku_code, old_price, new_price),
    )
    return int(cur.lastrowid)


def list_tickets(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        '''
        SELECT t.*,
               (SELECT COUNT(*) FROM price_ticket_lines l WHERE l.ticket_id=t.id) AS line_count
        FROM price_tickets t
        ORDER BY t.id DESC
        LIMIT ?
        ''',
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_ticket(conn: sqlite3.Connection, ticket_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM price_tickets WHERE id=?", (ticket_id,)).fetchone()
    return dict(row) if row else None


def list_lines(conn: sqlite3.Connection, ticket_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM price_ticket_lines WHERE ticket_id=? ORDER BY id",
        (ticket_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def due_tickets(conn: sqlite3.Connection, clock_at: str) -> list[dict[str, Any]]:
    """Tickets due at the business clock. Failed tickets stay retriable: a failed
    whole-ticket rollback leaves the ticket non-applied, so the next apply run
    picks it up again once its effective_at is due."""
    rows = conn.execute(
        '''
        SELECT * FROM price_tickets
        WHERE status IN ('scheduled','failed') AND effective_at <= ?
        ORDER BY effective_at, id
        ''',
        (clock_at,),
    ).fetchall()
    return [dict(r) for r in rows]


def reclaim_interrupted(conn: sqlite3.Connection) -> int:
    """Tickets left in 'applying' belong to an interrupted apply run (the apply
    lock guarantees no live run while we hold it). Their ticket transaction was
    rolled back, so mark them failed and let them be retried."""
    cur = conn.execute(
        '''
        UPDATE price_tickets
        SET status='failed', apply_error='上一次应用中断，本单已整单回滚，可再次到期应用'
        WHERE status='applying'
        ''',
    )
    return cur.rowcount


def claim_due(conn: sqlite3.Connection, clock_at: str) -> list[dict[str, Any]]:
    """Move every due ticket to 'applying' in one atomic step and return the
    claimed tickets. While a ticket is 'applying', line modifications are
    rejected with 409 by the ticket endpoints."""
    conn.execute(
        '''
        UPDATE price_tickets SET status='applying'
        WHERE status IN ('scheduled','failed') AND effective_at <= ?
        ''',
        (clock_at,),
    )
    rows = conn.execute(
        "SELECT * FROM price_tickets WHERE status='applying' ORDER BY effective_at, id"
    ).fetchall()
    return [dict(r) for r in rows]


def mark_applied(
    conn: sqlite3.Connection,
    ticket_id: int,
    *,
    applied_at: str,
    batch_id: int,
) -> None:
    conn.execute(
        '''
        UPDATE price_tickets
        SET status='applied', applied_at=?, apply_batch_id=?, apply_error=NULL
        WHERE id=?
        ''',
        (applied_at, batch_id, ticket_id),
    )


def mark_failed(conn: sqlite3.Connection, ticket_id: int, *, error: str) -> None:
    conn.execute(
        '''
        UPDATE price_tickets
        SET status='failed', apply_error=?
        WHERE id=?
        ''',
        (error, ticket_id),
    )
