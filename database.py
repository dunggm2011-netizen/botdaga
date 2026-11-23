import sqlite3
from config import DB_PATH


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_bets_table(conn: sqlite3.Connection) -> None:
    """
    Migration: nếu bảng bets cũ có cột win_net → đổi sang win_gross.
    Chạy an toàn nhiều lần.
    """
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(bets)")
    cols = {row[1] for row in cur.fetchall()}
    if "win_net" in cols and "win_gross" not in cols:
        # SQLite >= 3.25 hỗ trợ RENAME COLUMN
        try:
            cur.execute("ALTER TABLE bets RENAME COLUMN win_net TO win_gross")
            conn.commit()
        except sqlite3.OperationalError:
            # Fallback: tạo bảng mới, copy dữ liệu
            cur.execute("""
                CREATE TABLE bets_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    do_name TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    amount REAL NOT NULL,
                    win_gross REAL NOT NULL,
                    lose_amount REAL NOT NULL,
                    price_str TEXT NOT NULL,
                    status TEXT DEFAULT 'ACTIVE'
                )
            """)
            cur.execute("""
                INSERT INTO bets_new (id, code, do_name, user_id, side, amount, win_gross, lose_amount, price_str, status)
                SELECT id, code, do_name, user_id, side, amount, win_net, lose_amount, price_str, status
                FROM bets
            """)
            cur.execute("DROP TABLE bets")
            cur.execute("ALTER TABLE bets_new RENAME TO bets")
            conn.commit()


def init_db() -> None:
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                username TEXT DEFAULT '',
                balance REAL DEFAULT 0.0,
                debt REAL DEFAULT 0.0,
                debt_limit REAL DEFAULT 0.0,
                pnl_net REAL DEFAULT 0.0,
                last_payout_info TEXT DEFAULT ''
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS dos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'OPEN',
                xanh_win REAL DEFAULT 10.0,
                xanh_lose REAL DEFAULT 10.0,
                do_win REAL DEFAULT 10.0,
                do_lose REAL DEFAULT 10.0,
                bet_seq INTEGER DEFAULT 1
            )
        """)

        # Bảng bets dùng win_gross (chưa phế)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                do_name TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                side TEXT NOT NULL,
                amount REAL NOT NULL,
                win_gross REAL NOT NULL,
                lose_amount REAL NOT NULL,
                price_str TEXT NOT NULL,
                status TEXT DEFAULT 'ACTIVE'
            )
        """)

        # Migration cho DB cũ (nếu có win_net)
        _migrate_bets_table(conn)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                payout_text TEXT DEFAULT '',
                qr_file_id TEXT DEFAULT '',
                assigned_staff_id INTEGER DEFAULT 0,
                status TEXT DEFAULT 'PENDING',
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS settled_dos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                do_name TEXT NOT NULL,
                result TEXT NOT NULL,
                summary_text TEXT NOT NULL,
                day_key TEXT NOT NULL,
                settled_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                val_text TEXT DEFAULT ''
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS system_meta (
                key TEXT PRIMARY KEY,
                val_real REAL DEFAULT 0.0
            )
        """)

        cur.execute("""
            INSERT OR IGNORE INTO dos (name, status, xanh_win, xanh_lose, do_win, do_lose, bet_seq)
            VALUES ('Độ 1', 'OPEN', 10.0, 10.0, 10.0, 10.0, 1)
        """)

        cur.execute("""
            INSERT OR IGNORE INTO settings (key, val_text)
            VALUES ('assigned_approver_id', '0')
        """)

        cur.execute("""
            INSERT OR IGNORE INTO system_meta (key, val_real)
            VALUES ('realized_profit', 0.0)
        """)

        conn.commit()


def set_setting(key: str, val_text: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, val_text) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET val_text = excluded.val_text
            """,
            (key, val_text),
        )
        conn.commit()


def get_setting(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT val_text FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["val_text"]) if row and row["val_text"] is not None else default


def get_user(user_id: int, first_name: str = "Khách", username: str = "") -> dict:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()

        if not row:
            safe_name = first_name or f"User_{user_id}"
            cur.execute(
                """
                INSERT INTO users (user_id, name, username, balance, debt, debt_limit, pnl_net, last_payout_info)
                VALUES (?, ?, ?, 0.0, 0.0, 0.0, 0.0, '')
                """,
                (user_id, safe_name, username or ""),
            )
            conn.commit()
            cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
        else:
            new_name = first_name if first_name else row["name"]
            new_user = username if username else row["username"]
            if new_name != row["name"] or new_user != row["username"]:
                cur.execute(
                    "UPDATE users SET name = ?, username = ? WHERE user_id = ?",
                    (new_name, new_user, user_id),
                )
                conn.commit()
                cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                row = cur.fetchone()

        return dict(row)


def get_all_users() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY user_id ASC").fetchall()
        return [dict(r) for r in rows]


def update_user_fields(user_id: int, **kwargs) -> None:
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs.keys())
    vals = list(kwargs.values()) + [user_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE users SET {cols} WHERE user_id = ?", vals)
        conn.commit()


def get_next_do_name() -> str:
    with get_conn() as conn:
        rows = conn.execute("SELECT name FROM dos").fetchall()
    used_nums: set[int] = set()
    for r in rows:
        parts = str(r["name"]).strip().split()
        if parts and parts[-1].isdigit():
            used_nums.add(int(parts[-1]))

    next_num = 1
    while next_num in used_nums:
        next_num += 1
    return f"Độ {next_num}"


def get_do_by_id(do_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM dos WHERE id = ?", (do_id,)).fetchone()
        return dict(row) if row else None


def get_do_by_name(name: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM dos WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None


def get_open_dos() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM dos WHERE status = 'OPEN' ORDER BY id ASC").fetchall()
        if not rows:
            next_name = get_next_do_name()
            conn.execute(
                """
                INSERT INTO dos (name, status, xanh_win, xanh_lose, do_win, do_lose, bet_seq)
                VALUES (?, 'OPEN', 10.0, 10.0, 10.0, 10.0, 1)
                """,
                (next_name,),
            )
            conn.commit()
            rows = conn.execute("SELECT * FROM dos WHERE status = 'OPEN' ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]


def create_new_do(
    name: str | None = None,
    xanh_win: float = 10.0,
    xanh_lose: float = 10.0,
    do_win: float = 10.0,
    do_lose: float = 10.0,
) -> dict:
    do_name = name or get_next_do_name()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO dos (name, status, xanh_win, xanh_lose, do_win, do_lose, bet_seq)
            VALUES (?, 'OPEN', ?, ?, ?, ?, 1)
            ON CONFLICT(name) DO UPDATE SET
                status = 'OPEN',
                xanh_win = excluded.xanh_win,
                xanh_lose = excluded.xanh_lose,
                do_win = excluded.do_win,
                do_lose = excluded.do_lose,
                bet_seq = 1
            """,
            (do_name, xanh_win, xanh_lose, do_win, do_lose),
        )
        conn.commit()
    return get_do_by_name(do_name)  # type: ignore


def update_do(do_id: int, **kwargs) -> None:
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs.keys())
    vals = list(kwargs.values()) + [do_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE dos SET {cols} WHERE id = ?", vals)
        conn.commit()


def add_bet_record(
    code: str,
    do_name: str,
    user_id: int,
    side: str,
    amount: float,
    win_gross: float,     # ĐỔI: win_net → win_gross
    lose_amount: float,
    price_str: str,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bets (code, do_name, user_id, side, amount, win_gross, lose_amount, price_str, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
            """,
            (code, do_name, user_id, side, amount, win_gross, lose_amount, price_str),
        )
        conn.commit()


def get_active_bets(do_name: str | None = None, user_id: int | None = None) -> list[dict]:
    query = "SELECT * FROM bets WHERE status = 'ACTIVE'"
    params: list = []
    if do_name:
        query += " AND do_name = ?"
        params.append(do_name)
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(user_id)
    query += " ORDER BY id ASC"

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def update_bets_status_by_do(do_name: str, status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE bets SET status = ? WHERE do_name = ? AND status = 'ACTIVE'",
            (status, do_name),
        )
        conn.commit()


def update_bet_status(bet_id: int, status: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE bets SET status = ? WHERE id = ?", (status, bet_id))
        conn.commit()


def create_withdrawal(
    user_id: int,
    amount: float,
    payout_text: str,
    qr_file_id: str,
    assigned_staff_id: int,
    created_at: str,
) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO withdrawals (user_id, amount, payout_text, qr_file_id, assigned_staff_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'PENDING', ?)
            """,
            (user_id, amount, payout_text, qr_file_id, assigned_staff_id, created_at),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_withdrawal(w_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (w_id,)).fetchone()
        return dict(row) if row else None


def update_withdrawal(w_id: int, **kwargs) -> None:
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs.keys())
    vals = list(kwargs.values()) + [w_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE withdrawals SET {cols} WHERE id = ?", vals)
        conn.commit()


def insert_settled_do(do_name: str, result: str, summary_text: str, day_key: str, settled_at: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO settled_dos (do_name, result, summary_text, day_key, settled_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (do_name, result, summary_text, day_key, settled_at),
        )
        conn.commit()


def get_settled_dos_by_day(day_key: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM settled_dos WHERE day_key = ? ORDER BY id ASC",
            (day_key,),
        ).fetchall()
        return [dict(r) for r in rows]


def clear_settled_dos_by_day(day_key: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM settled_dos WHERE day_key = ?", (day_key,))
        conn.commit()


def get_realized_profit() -> float:
    with get_conn() as conn:
        row = conn.execute("SELECT val_real FROM system_meta WHERE key = 'realized_profit'").fetchone()
        return float(row["val_real"]) if row else 0.0


def add_realized_profit(delta: float) -> float:
    current = get_realized_profit() + delta
    with get_conn() as conn:
        conn.execute("UPDATE system_meta SET val_real = ? WHERE key = 'realized_profit'", (current,))
        conn.commit()
    return current
