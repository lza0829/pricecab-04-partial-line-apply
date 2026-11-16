"""到期应用的单内失败命运测试。

选定策略：整单回滚（与成功行保留互斥，全批一致）——
任一明细行失败（找不到价签本 / 写入失败）时，本单已改价签与新增历史全部回滚，
单记 failed（非 applied、可再次到期应用），批次记整单失败，不得出现半单 applied。
"""

from conftest import sql

CLOCK = "2026-11-16T12:00:00Z"


def _create_ticket(client, title, effective_at, lines):
    r = client.post(
        "/api/tickets",
        json={"title": title, "effective_at": effective_at, "lines": lines},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _set_clock(client, clock=CLOCK):
    r = client.put("/api/apply/clock", json={"business_clock": clock})
    assert r.status_code == 200, r.text


def _book(client):
    return {r["sku_code"]: r for r in client.get("/api/prices/book").json()}


def _tickets(client):
    return {t["ticket_no"]: t for t in client.get("/api/tickets").json()}


def _batches(client):
    return client.get("/api/apply/batches").json()


def _history(client):
    return client.get("/api/prices/history").json()


def test_apply_all_success(client, db_path):
    """全成功：价签本、历史、单状态、批次计数同源一致。"""
    _set_clock(client)
    t1 = _create_ticket(
        client,
        "茶饮调价",
        "2026-11-16T10:00:00Z",
        [
            {"sku_code": "SKU-TEA-001", "new_price": 30.0},
            {"sku_code": "SKU-SNK-011", "new_price": 13.0},
        ],
    )
    t2 = _create_ticket(
        client,
        "日用调价",
        "2026-11-16T11:00:00Z",
        [{"sku_code": "SKU-DAY-021", "new_price": 40.0}],
    )

    res = client.post("/api/apply/due").json()
    assert res["applied"] is True
    assert res["batch_status"] == "done"
    assert res["applied_ticket_count"] == 2
    assert res["failed_ticket_count"] == 0
    assert res["applied_line_count"] == 3
    assert res["failed_line_count"] == 0

    book = _book(client)
    assert book["SKU-TEA-001"]["current_price"] == 30.0
    assert book["SKU-TEA-001"]["source_ticket_no"] == t1["ticket_no"]
    assert book["SKU-SNK-011"]["current_price"] == 13.0
    assert book["SKU-DAY-021"]["current_price"] == 40.0

    hist = _history(client)
    from_batch = [h for h in hist if h["source_ticket_no"] in (t1["ticket_no"], t2["ticket_no"])]
    assert len(from_batch) == 3

    tickets = _tickets(client)
    assert tickets[t1["ticket_no"]]["status"] == "applied"
    assert tickets[t2["ticket_no"]]["status"] == "applied"

    batches = _batches(client)
    assert len(batches) == 1
    b = batches[0]
    assert b["status"] == "done"
    assert (b["ticket_count"], b["applied_ticket_count"], b["failed_ticket_count"]) == (2, 2, 0)
    assert (b["line_count"], b["applied_line_count"], b["failed_line_count"]) == (3, 3, 0)

    # 同源校验：批次计数 == 本批 applied 单数 == 本批写入的历史行数
    batch_id = tickets[t1["ticket_no"]]["apply_batch_id"]
    assert tickets[t2["ticket_no"]]["apply_batch_id"] == batch_id
    n = sql(
        db_path,
        "SELECT COUNT(*) c FROM price_tickets WHERE apply_batch_id=? AND status='applied'",
        (batch_id,),
    )[0]["c"]
    assert n == b["applied_ticket_count"]
    n = sql(
        db_path,
        """SELECT COUNT(*) c FROM price_history
           WHERE source_ticket_no IN (
             SELECT ticket_no FROM price_tickets WHERE apply_batch_id=?)""",
        (batch_id,),
    )[0]["c"]
    assert n == b["applied_line_count"]


def test_partial_failure_rolls_back_whole_ticket(client, db_path):
    """部分失败触发整单回滚：本单已改价签与新增历史全部撤销，单记 failed，
    批次记整单失败；同批其他单正常 applied（批级一致，不混用命运）。"""
    _set_clock(client)
    t1 = _create_ticket(
        client,
        "会失败的单",
        "2026-11-16T10:00:00Z",
        [
            {"sku_code": "SKU-TEA-001", "new_price": 30.0},
            {"sku_code": "SKU-SNK-011", "new_price": 13.0},
        ],
    )
    t2 = _create_ticket(
        client,
        "会成功的单",
        "2026-11-16T10:30:00Z",
        [{"sku_code": "SKU-DAY-021", "new_price": 40.0}],
    )
    # 模拟 t1 第二行找不到价签本
    sql(db_path, "DELETE FROM price_book WHERE sku_code='SKU-SNK-011'")

    res = client.post("/api/apply/due").json()
    assert res["applied"] is True
    assert res["batch_status"] == "partial_failed"
    assert res["applied_ticket_count"] == 1
    assert res["failed_ticket_count"] == 1
    assert res["applied_line_count"] == 1
    assert res["failed_line_count"] == 2

    outcome = {o["ticket_no"]: o for o in res["tickets"]}
    assert outcome[t1["ticket_no"]]["status"] == "failed"
    assert "价签本" in outcome[t1["ticket_no"]]["error"]
    assert outcome[t2["ticket_no"]]["status"] == "applied"

    # 整单回滚：t1 第一行写入的新价不得残留，本单新增历史不得残留
    book = _book(client)
    assert book["SKU-TEA-001"]["current_price"] == 28.0  # 未变
    assert "SKU-SNK-011" not in book  # 仍无价签本
    assert book["SKU-DAY-021"]["current_price"] == 40.0  # 同批他单正常
    hist = _history(client)
    assert not [h for h in hist if h["source_ticket_no"] == t1["ticket_no"]]
    assert len([h for h in hist if h["source_ticket_no"] == t2["ticket_no"]]) == 1

    tickets = _tickets(client)
    assert tickets[t1["ticket_no"]]["status"] == "failed"
    assert "价签本" in tickets[t1["ticket_no"]]["apply_error"]
    assert tickets[t2["ticket_no"]]["status"] == "applied"

    b = _batches(client)[0]
    assert b["status"] == "partial_failed"
    assert (b["applied_ticket_count"], b["failed_ticket_count"]) == (1, 1)
    assert (b["applied_line_count"], b["failed_line_count"]) == (1, 2)

    # 失败单保持可再次到期应用：价签本仍缺失时再次应用仍整单失败
    res2 = client.post("/api/apply/due").json()
    assert res2["applied_ticket_count"] == 0
    assert res2["failed_ticket_count"] == 1
    assert res2["batch_status"] == "failed"
    assert _tickets(client)[t1["ticket_no"]]["status"] == "failed"

    # 补回价签本后再次到期应用：整单成功，错误留痕清除
    sql(
        db_path,
        """INSERT INTO price_book(sku_code, current_price, source_ticket_no, effective_at, updated_at)
           VALUES ('SKU-SNK-011', 12.5, NULL, '2026-11-16T09:00:00Z', '2026-11-16T09:00:00Z')""",
    )
    res3 = client.post("/api/apply/due").json()
    assert res3["batch_status"] == "done"
    assert res3["applied_ticket_count"] == 1
    assert res3["applied_line_count"] == 2
    book = _book(client)
    assert book["SKU-TEA-001"]["current_price"] == 30.0
    assert book["SKU-SNK-011"]["current_price"] == 13.0
    t1_after = _tickets(client)[t1["ticket_no"]]
    assert t1_after["status"] == "applied"
    assert t1_after["apply_error"] is None
    hist = _history(client)
    assert len([h for h in hist if h["source_ticket_no"] == t1["ticket_no"]]) == 2

    # 批次计数与库内实际同源
    b3 = _batches(client)[0]
    assert b3["status"] == "done"
    n = sql(
        db_path,
        "SELECT COUNT(*) c FROM price_history WHERE source_ticket_no=?",
        (t1["ticket_no"],),
    )[0]["c"]
    assert n == b3["applied_line_count"] == 2


def test_modify_lines_rejected_while_applying(client, db_path):
    """应用中并发改明细必须拒绝；scheduled 可追加，applied/failed 亦拒绝。"""
    t = _create_ticket(
        client,
        "并发守卫",
        "2026-11-16T10:00:00Z",
        [{"sku_code": "SKU-TEA-001", "new_price": 30.0}],
    )
    tid = t["id"]

    # scheduled：可追加明细
    r = client.post(f"/api/tickets/{tid}/lines", json={"sku_code": "SKU-SNK-011", "new_price": 13.0})
    assert r.status_code == 201, r.text
    assert len(r.json()["lines"]) == 2

    # 模拟到期应用进行中（单被认领为 applying）：追加明细被拒，明细不变
    sql(db_path, "UPDATE price_tickets SET status='applying' WHERE id=?", (tid,))
    r = client.post(f"/api/tickets/{tid}/lines", json={"sku_code": "SKU-DAY-021", "new_price": 40.0})
    assert r.status_code == 409
    assert "应用中" in r.json()["detail"]
    assert len(client.get(f"/api/tickets/{tid}").json()["lines"]) == 2

    # 回到 scheduled：可追加
    sql(db_path, "UPDATE price_tickets SET status='scheduled' WHERE id=?", (tid,))
    r = client.post(f"/api/tickets/{tid}/lines", json={"sku_code": "SKU-DAY-021", "new_price": 40.0})
    assert r.status_code == 201
    assert len(r.json()["lines"]) == 3

    # applied 后：拒绝
    _set_clock(client)
    client.post("/api/apply/due")
    assert _tickets(client)[t["ticket_no"]]["status"] == "applied"
    r = client.post(f"/api/tickets/{tid}/lines", json={"sku_code": "SKU-FRZ-031", "new_price": 25.0})
    assert r.status_code == 409

    # failed 单：同样拒绝（失败行走新单路径）
    t2 = _create_ticket(
        client,
        "会失败的单",
        "2026-11-16T10:00:00Z",
        [{"sku_code": "SKU-FRZ-031", "new_price": 25.0}],
    )
    sql(db_path, "DELETE FROM price_book WHERE sku_code='SKU-FRZ-031'")
    client.post("/api/apply/due")
    assert _tickets(client)[t2["ticket_no"]]["status"] == "failed"
    r = client.post(f"/api/tickets/{t2['id']}/lines", json={"sku_code": "SKU-FRZ-032", "new_price": 16.0})
    assert r.status_code == 409

    # 不存在的单：404
    r = client.post("/api/tickets/9999/lines", json={"sku_code": "SKU-FRZ-032", "new_price": 16.0})
    assert r.status_code == 404


def test_concurrent_apply_rejected(client, db_path):
    """同一时刻只允许一个到期应用批次，并发应用被拒。"""
    from app.services import apply_service

    assert apply_service._apply_lock.acquire(blocking=False)
    try:
        r = client.post("/api/apply/due")
        assert r.status_code == 409
        assert "正在进行" in r.json()["detail"]
    finally:
        apply_service._apply_lock.release()


def test_interrupted_apply_reclaimed_and_retried(client, db_path):
    """中断残留的 applying 单被回收为可重试状态，并在同次运行中整单应用。"""
    t = _create_ticket(
        client,
        "中断恢复",
        "2026-11-16T10:00:00Z",
        [{"sku_code": "SKU-TEA-001", "new_price": 31.0}],
    )
    # 模拟上一次应用中断：单停留在 applying，事务已回滚（价签未变）
    sql(db_path, "UPDATE price_tickets SET status='applying' WHERE id=?", (t["id"],))
    _set_clock(client)

    res = client.post("/api/apply/due").json()
    assert res["applied"] is True
    assert res["applied_ticket_count"] == 1
    assert res["failed_ticket_count"] == 0
    assert _tickets(client)[t["ticket_no"]]["status"] == "applied"
    assert _book(client)["SKU-TEA-001"]["current_price"] == 31.0


def test_no_due_tickets(client, db_path):
    _set_clock(client)
    res = client.post("/api/apply/due").json()
    assert res["applied"] is False
    assert _batches(client) == []


def test_migrate_adds_columns_to_old_schema(tmp_path):
    """旧库（无新列）启动时自动补齐 apply_error 与批次成功/失败计数列。"""
    import sqlite3

    from app.seed import init_schema

    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE price_tickets (
          id INTEGER PRIMARY KEY, ticket_no TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
          status TEXT NOT NULL, effective_at TEXT NOT NULL, note TEXT,
          created_at TEXT NOT NULL, applied_at TEXT, apply_batch_id INTEGER
        );
        CREATE TABLE apply_batches (
          id INTEGER PRIMARY KEY, batch_no TEXT NOT NULL UNIQUE, clock_at TEXT NOT NULL,
          ticket_count INTEGER NOT NULL, line_count INTEGER NOT NULL,
          status TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """
    )
    init_schema(conn)
    ticket_cols = {r["name"] for r in conn.execute("PRAGMA table_info(price_tickets)")}
    assert "apply_error" in ticket_cols
    batch_cols = {r["name"] for r in conn.execute("PRAGMA table_info(apply_batches)")}
    assert {
        "applied_ticket_count",
        "failed_ticket_count",
        "applied_line_count",
        "failed_line_count",
    } <= batch_cols
    conn.close()
