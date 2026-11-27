import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "ga_don.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, col_name: str, col_def: str) -> None:
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col_name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")


def init_db() -> None:
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                balance REAL NOT NULL DEFAULT 0.0,
                debt REAL NOT NULL DEFAULT 0.0,
                debt_limit REAL NOT NULL DEFAULT 0.0,
                pnl_net REAL NOT NULL DEFAULT 0.0,
                last_payout_info TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS dos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                xanh_win REAL NOT NULL DEFAULT 10.0,
                xanh_lose REAL NOT NULL DEFAULT 10.0,
                do_win REAL NOT NULL DEFAULT 10.0,
                do_lose REAL NOT NULL DEFAULT 10.0,
                status TEXT NOT NULL DEFAULT 'OPEN',
                bet_seq INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                do_name TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                side TEXT NOT NULL,
                amount REAL NOT NULL,
                win_net REAL NOT NULL,
                lose_amount REAL NOT NULL,
                price_str TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL DEFAULT '',
                staff_id INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                payout_text TEXT NOT NULL DEFAULT '',
                qr_file_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'PENDING',
                assigned_staff_id INTEGER NOT NULL DEFAULT 0,
                approved_by INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS wallet_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_type TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                balance_before REAL NOT NULL,
                balance_after REAL NOT NULL,
                staff_id INTEGER NOT NULL DEFAULT 0,
                ref_code TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                day_key TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS settled_dos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                do_name TEXT NOT NULL,
                result TEXT NOT NULL,
                summary_text TEXT NOT NULL,
                day_key TEXT NOT NULL,
                settled_at TEXT NOT NULL,
                total_fee_5pct REAL NOT NULL DEFAULT 0.0,
                house_net REAL NOT NULL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        # Migration an toàn cho DB cũ nếu đã tồn tại
        _ensure_column(conn, "bets", "created_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "bets", "staff_id", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "withdrawals", "approved_by", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "settled_dos", "total_fee_5pct", "REAL NOT NULL DEFAULT 0.0")
        _ensure_column(conn, "settled_dos", "house_net", "REAL NOT NULL DEFAULT 0.0")

        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('realized_profit', '0.0')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('assigned_approver_id', '0')")

        cur = conn.execute("SELECT COUNT(*) AS c FROM dos")
        if cur.fetchone()["c"] == 0:
            conn.execute(
                """
                INSERT INTO dos (name, xanh_win, xanh_lose, do_win, do_lose, status, bet_seq)
                VALUES ('Độ 1', 10.0, 10.0, 10.0, 10.0, 'OPEN', 1)
                """
            )
        conn.commit()


# ==================== SETTINGS ====================
def get_setting(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        conn.commit()


def get_realized_profit() -> float:
    return float(get_setting("realized_profit", "0.0"))


def add_realized_profit(delta: float) -> float:
    new_val = get_realized_profit() + delta
    set_setting("realized_profit", str(new_val))
    return new_val


# ==================== USERS ====================
def get_user(user_id: int, name: str = "", username: str = "") -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            display = name.strip() or f"Khách {user_id}"
            conn.execute(
                "INSERT INTO users (user_id, name, username) VALUES (?, ?, ?)",
                (user_id, display, username or ""),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        else:
            if name and name != row["name"]:
                conn.execute("UPDATE users SET name = ? WHERE user_id = ?", (name, user_id))
            if username is not None and username != row["username"]:
                conn.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row)


def get_all_users() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY user_id ASC").fetchall()
        return [dict(r) for r in rows]


def update_user_fields(user_id: int, **fields) -> None:
    if not fields:
        return
    if "balance" in fields and float(fields["balance"]) < 0.0:
        fields["balance"] = 0.0
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [user_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE users SET {cols} WHERE user_id = ?", vals)
        conn.commit()


# ==================== WALLET TRANSACTIONS ====================
def log_wallet_transaction(
    tx_type: str,
    user_id: int,
    amount: float,
    balance_before: float,
    balance_after: float,
    staff_id: int = 0,
    ref_code: str = "",
    note: str = "",
    day_key: str = "",
    created_at: str = "",
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO wallet_transactions
            (tx_type, user_id, amount, balance_before, balance_after, staff_id, ref_code, note, day_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tx_type,
                user_id,
                amount,
                balance_before,
                balance_after,
                staff_id,
                ref_code,
                note,
                day_key,
                created_at,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_wallet_transactions_by_day(day_key: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM wallet_transactions WHERE day_key = ? ORDER BY id ASC",
            (day_key,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_user_wallet_transactions(user_id: int, limit: int = 15) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM wallet_transactions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ==================== DOS (ĐỘ ĐANG MỞ / TẠM DỪNG / CHỜ KQ) ====================
def get_open_dos() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM dos WHERE status = 'OPEN' ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]


def get_unsettled_dos() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM dos WHERE status IN ('OPEN', 'HOLD', 'WAITING') ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_dos() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM dos ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]


def get_do_by_id(do_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM dos WHERE id = ?", (do_id,)).fetchone()
        return dict(row) if row else None


def get_do_by_name(name: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM dos WHERE name = ? ORDER BY id DESC LIMIT 1",
            (name,),
        ).fetchone()
        return dict(row) if row else None


def create_do(
    name: str,
    xanh_win: float = 10.0,
    xanh_lose: float = 10.0,
    do_win: float = 10.0,
    do_lose: float = 10.0,
    status: str = "OPEN",
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO dos (name, xanh_win, xanh_lose, do_win, do_lose, status, bet_seq)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (name, xanh_win, xanh_lose, do_win, do_lose, status),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_do(do_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [do_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE dos SET {cols} WHERE id = ?", vals)
        conn.commit()


# ==================== BETS ====================
def add_bet_and_advance_seq(
    do_id: int,
    expected_seq: int,
    code: str,
    do_name: str,
    user_id: int,
    side: str,
    amount: float,
    win_net: float,
    lose_amount: float,
    price_str: str,
    created_at: str = "",
    staff_id: int = 0,
) -> int:
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE dos SET bet_seq = bet_seq + 1 WHERE id = ? AND status = 'OPEN' AND bet_seq = ?",
            (do_id, expected_seq),
        )
        if cur.rowcount != 1:
            conn.rollback()
            raise RuntimeError("Trạng thái độ hoặc số thứ tự mã đã thay đổi, vui lòng thử lại.")

        cur_bet = conn.execute(
            """
            INSERT INTO bets (code, do_name, user_id, side, amount, win_net, lose_amount, price_str, status, created_at, staff_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
            """,
            (code, do_name, user_id, side, amount, win_net, lose_amount, price_str, created_at, staff_id),
        )
        conn.commit()
        return int(cur_bet.lastrowid)


def get_active_bets(do_name: str | None = None, user_id: int | None = None) -> list[dict]:
    query = "SELECT * FROM bets WHERE status = 'ACTIVE'"
    params: list = []
    if do_name:
        query += " AND do_name = ?"
        params.append(do_name)
    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)
    query += " ORDER BY id ASC"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def update_bet_status(bet_id: int, status: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE bets SET status = ? WHERE id = ?", (status, bet_id))
        conn.commit()


def update_bets_status_by_do(do_name: str, status: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE bets SET status = ? WHERE do_name = ? AND status = 'ACTIVE'", (status, do_name))
        conn.commit()


# ==================== WITHDRAWALS ====================
def create_withdrawal(
    user_id: int,
    amount: float,
    payout_text: str,
    qr_file_id: str,
    assigned_staff_id: int,
    created_at: str,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO withdrawals (user_id, amount, payout_text, qr_file_id, status, assigned_staff_id, created_at)
            VALUES (?, ?, ?, ?, 'PENDING', ?, ?)
            """,
            (user_id, amount, payout_text, qr_file_id, assigned_staff_id, created_at),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_withdrawal(w_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (w_id,)).fetchone()
        return dict(row) if row else None


def get_pending_withdrawals_total() -> float:
    with get_conn() as conn:
        row = conn.execute("SELECT COALESCE(SUM(amount), 0.0) AS tot FROM withdrawals WHERE status = 'PENDING'").fetchone()
        return float(row["tot"]) if row else 0.0


def update_withdrawal(w_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [w_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE withdrawals SET {cols} WHERE id = ?", vals)
        conn.commit()


# ==================== SETTLED DOS LOG ====================
def insert_settled_do(
    do_name: str,
    result: str,
    summary_text: str,
    day_key: str,
    settled_at: str,
    total_fee_5pct: float = 0.0,
    house_net: float = 0.0,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO settled_dos (do_name, result, summary_text, day_key, settled_at, total_fee_5pct, house_net)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (do_name, result, summary_text, day_key, settled_at, total_fee_5pct, house_net),
        )
        conn.commit()


def get_settled_dos_by_day(day_key: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM settled_dos WHERE day_key = ? ORDER BY id ASC", (day_key,)).fetchall()
        return [dict(r) for r in rows]


def clear_settled_dos_by_day(day_key: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM settled_dos WHERE day_key = ?", (day_key,))
        conn.commit()
