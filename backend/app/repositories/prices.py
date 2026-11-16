from __future__ import annotations

import sqlite3
from typing import Any


def list_book(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        '''
        SELECT b.*, s.title, s.category
        FROM price_book b
        JOIN skus s ON s.sku_code = b.sku_code
        ORDER BY b.sku_code
        '''
    ).fetchall()
    return [dict(r) for r in rows]


class PriceBookMissing(Exception):
    """应用改价时找不到对应商品的价签本。"""


def apply_book_line(
    conn: sqlite3.Connection,
    *,
    sku_code: str,
    price: float,
    ticket_no: str,
    effective_at: str,
    updated_at: str,
) -> None:
    """Write one ticket line into the price book and history.

    The price book row must already exist; a missing row is a line failure
    (找不到价签本) and raises PriceBookMissing so the caller can roll back the
    whole ticket. Any database error likewise propagates as a write failure.
    """
    row = conn.execute(
        "SELECT sku_code FROM price_book WHERE sku_code=?", (sku_code,)
    ).fetchone()
    if row is None:
        raise PriceBookMissing(f"商品 {sku_code} 找不到价签本")
    conn.execute(
        '''
        UPDATE price_book
        SET current_price=?, source_ticket_no=?, effective_at=?, updated_at=?
        WHERE sku_code=?
        ''',
        (price, ticket_no, effective_at, updated_at, sku_code),
    )
    conn.execute(
        '''
        INSERT INTO price_history(sku_code, price, source_ticket_no, effective_at, recorded_at)
        VALUES (?,?,?,?,?)
        ''',
        (sku_code, price, ticket_no, effective_at, updated_at),
    )


def list_history(conn: sqlite3.Connection, sku_code: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if sku_code:
        rows = conn.execute(
            "SELECT * FROM price_history WHERE sku_code=? ORDER BY id DESC LIMIT ?",
            (sku_code, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM price_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def price_at(conn: sqlite3.Connection, sku_code: str, at: str) -> float | None:
    row = conn.execute(
        '''
        SELECT price FROM price_history
        WHERE sku_code=? AND effective_at <= ?
        ORDER BY effective_at DESC, id DESC
        LIMIT 1
        ''',
        (sku_code, at),
    ).fetchone()
    return float(row["price"]) if row else None
