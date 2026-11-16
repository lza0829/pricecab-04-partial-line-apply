from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import tickets as ticket_repo
from app.repositories import prices as price_repo
from app.repositories import batches as batch_repo


# Per-ticket failure fate (mutually exclusive strategies — exactly one is
# implemented here, and every ticket in a batch shares it):
#
#   整单回滚 (whole-ticket rollback): each ticket is applied in its own
#   transaction. If any line fails (找不到价签本 / 写入失败), the transaction is
#   rolled back, so every price-book change and every history row this ticket
#   already wrote is undone — no half-applied ticket, no residual new price.
#   The ticket is marked 'failed' (non-applied, retriable by the next
#   apply-due run) and the batch records a whole-ticket failure.
#
# The alternative fate (keep successful lines / partial success) is NOT
# implemented and must not be mixed in.
TICKET_FAILURE_FATE = "whole_ticket_rollback"

# In-process mutual exclusion for apply runs: a second concurrent apply is
# rejected instead of interleaving with the running batch.
_apply_lock = threading.Lock()


class LineApplyError(Exception):
    """A ticket line cannot be applied (e.g. the ticket has no lines)."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_clock() -> dict[str, Any]:
    conn = connect()
    try:
        return {
            "business_clock": settings_repo.get(conn, "business_clock"),
            "clock_label": settings_repo.get(conn, "clock_label"),
        }
    finally:
        conn.close()


def set_clock(clock_at: str) -> dict[str, Any]:
    clock_at = (clock_at or "").strip()
    if not clock_at:
        raise HTTPException(400, "业务钟不能为空")
    conn = connect(immediate=True)
    try:
        settings_repo.set_value(conn, "business_clock", clock_at)
        conn.commit()
        return get_clock()
    finally:
        conn.close()


def list_batches() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return batch_repo.list_batches(conn)
    finally:
        conn.close()


def apply_due() -> dict[str, Any]:
    if not _apply_lock.acquire(blocking=False):
        raise HTTPException(409, "已有到期应用批次正在进行，请稍后重试")
    try:
        return _apply_due_locked()
    finally:
        _apply_lock.release()


def _apply_due_locked() -> dict[str, Any]:
    """Apply all due tickets (scheduled, plus failed ones being retried).

    Batch-level commit protocol, consistent with the per-ticket fate:
      1. Reclaim interrupted tickets, claim every due ticket as 'applying'
         (line modifications are rejected while claimed), create the batch row.
      2. Apply each ticket in its own transaction — whole-ticket rollback on
         any line failure.
      3. Finalize the batch from the same per-ticket outcomes, so batch
         counts, ticket statuses, price book and history stay same-source.
    """
    # Phase 1: claim + create batch, committed so other connections see the
    # 'applying' guard and the running batch.
    conn = connect(immediate=True)
    try:
        clock = settings_repo.get(conn, "business_clock")
        if not clock:
            raise HTTPException(400, "业务钟未设置")
        ticket_repo.reclaim_interrupted(conn)
        claimed = ticket_repo.claim_due(conn, clock)
        if not claimed:
            conn.commit()
            return {
                "applied": False,
                "message": "当前业务钟下没有到期改价单",
                "business_clock": clock,
                "ticket_count": 0,
                "line_count": 0,
            }
        batch_no = batch_repo.next_batch_no(conn)
        created = _now()
        batch_id = batch_repo.create_batch(conn, batch_no=batch_no, clock_at=clock, created_at=created)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Phase 2: one transaction per ticket — the whole-ticket-rollback fate.
    outcomes = [_apply_one_ticket(t, batch_id, created) for t in claimed]

    # Phase 3: finalize the batch from the same outcomes that drove the
    # per-ticket commits (成功/失败行数、单状态、价签本、历史、批次计数同源).
    applied = [o for o in outcomes if o["status"] == "applied"]
    failed = [o for o in outcomes if o["status"] == "failed"]
    applied_lines = sum(o["line_count"] for o in applied)
    failed_lines = sum(o["line_count"] for o in failed)
    if not failed:
        batch_status = "done"
    elif not applied:
        batch_status = "failed"
    else:
        batch_status = "partial_failed"

    conn = connect(immediate=True)
    try:
        batch_repo.finalize_batch(
            conn,
            batch_id,
            ticket_count=len(outcomes),
            line_count=applied_lines + failed_lines,
            applied_ticket_count=len(applied),
            failed_ticket_count=len(failed),
            applied_line_count=applied_lines,
            failed_line_count=failed_lines,
            status=batch_status,
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "applied": True,
        "batch_no": batch_no,
        "batch_status": batch_status,
        "fate": TICKET_FAILURE_FATE,
        "business_clock": clock,
        "ticket_count": len(outcomes),
        "applied_ticket_count": len(applied),
        "failed_ticket_count": len(failed),
        "line_count": applied_lines + failed_lines,
        "applied_line_count": applied_lines,
        "failed_line_count": failed_lines,
        "tickets": outcomes,
    }


def _apply_one_ticket(ticket: dict[str, Any], batch_id: int, applied_at: str) -> dict[str, Any]:
    """Apply one claimed ticket atomically.

    On any line failure the ticket transaction is rolled back: every price
    tag this ticket changed and every history row it added is undone, and the
    ticket is marked 'failed' (non-applied, due again on the next run).
    """
    conn = connect(immediate=True)
    lines: list[dict[str, Any]] = []
    try:
        lines = ticket_repo.list_lines(conn, ticket["id"])
        if not lines:
            raise LineApplyError("改价单没有明细行")
        for line in lines:
            price_repo.apply_book_line(
                conn,
                sku_code=line["sku_code"],
                price=float(line["new_price"]),
                ticket_no=ticket["ticket_no"],
                effective_at=ticket["effective_at"],
                updated_at=applied_at,
            )
        ticket_repo.mark_applied(conn, ticket["id"], applied_at=applied_at, batch_id=batch_id)
        conn.commit()
        return {
            "ticket_no": ticket["ticket_no"],
            "status": "applied",
            "line_count": len(lines),
            "error": None,
        }
    except Exception as exc:
        # 整单回滚：撤销本单已写入的全部价签与历史，不留半单 applied。
        conn.rollback()
        error = str(exc)
        try:
            ticket_repo.mark_failed(conn, ticket["id"], error=error)
            conn.commit()
        except Exception:
            conn.rollback()
        return {
            "ticket_no": ticket["ticket_no"],
            "status": "failed",
            "line_count": len(lines),
            "error": error,
        }
    finally:
        conn.close()
