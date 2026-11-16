from __future__ import annotations

import sqlite3
from typing import Any


def next_batch_no(conn: sqlite3.Connection) -> str:
    n = conn.execute("SELECT COUNT(*) c FROM apply_batches").fetchone()["c"] + 1
    return f"AB-{n:04d}"


def create_batch(
    conn: sqlite3.Connection,
    *,
    batch_no: str,
    clock_at: str,
    created_at: str,
) -> int:
    """Create the batch row up front in 'running' state; counts are finalized
    from the per-ticket outcomes once every ticket has reached its fate."""
    cur = conn.execute(
        '''
        INSERT INTO apply_batches(
          batch_no, clock_at, ticket_count, line_count,
          applied_ticket_count, failed_ticket_count,
          applied_line_count, failed_line_count,
          status, created_at
        )
        VALUES (?,?,0,0,0,0,0,0,'running',?)
        ''',
        (batch_no, clock_at, created_at),
    )
    return int(cur.lastrowid)


def finalize_batch(
    conn: sqlite3.Connection,
    batch_id: int,
    *,
    ticket_count: int,
    line_count: int,
    applied_ticket_count: int,
    failed_ticket_count: int,
    applied_line_count: int,
    failed_line_count: int,
    status: str,
) -> None:
    conn.execute(
        '''
        UPDATE apply_batches
        SET ticket_count=?, line_count=?,
            applied_ticket_count=?, failed_ticket_count=?,
            applied_line_count=?, failed_line_count=?,
            status=?
        WHERE id=?
        ''',
        (
            ticket_count,
            line_count,
            applied_ticket_count,
            failed_ticket_count,
            applied_line_count,
            failed_line_count,
            status,
            batch_id,
        ),
    )


def list_batches(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM apply_batches ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
