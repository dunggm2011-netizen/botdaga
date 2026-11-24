from datetime import datetime, timezone, timedelta
from config import FEE_RATE, MASTER_IDS, MIN_WITHDRAW_AMOUNT
import database as db

VN_TZ = timezone(timedelta(hours=7))


def fmt_num(val: float) -> str:
    return f"{val:,.0f}".replace(",", ".")


def fmt_signed(val: float) -> str:
    if val > 0:
        return f"\U0001f7e2 <b>+{fmt_num(val)}</b>"
    if val < 0:
        return f"\U0001f534 <b>-{fmt_num(abs(val))}</b>"
    return "\u26aa <b>0</b>"


def calc_fee_and_net_profit(gross_profit: float) -> dict:
    """
    Nguy\xean t\u1eafc kh\u1ea5u tr\u1eeb 5%:
    - Ch\u1ec9 t\xednh tr\xean l\u1ee3i nhu\u1eadn tr\u01b0\u1edbc kh\u1ea5u tr\u1eeb.
    - Kh\u1ea5u tr\u1eeb = l\u1ee3i nhu\u1eadn * 5%, th\u1ef1c nh\u1eadn = l\u1ee3i nhu\u1eadn * 95%.
    """
    profit = max(0.0, float(gross_profit))
    fee_amount = profit * FEE_RATE
    net_profit = profit - fee_amount
    return {
        "gross_profit": profit,
        "fee_amount": fee_amount,
        "net_profit": net_profit,
    }


def get_assigned_approver_id() -> int:
    raw = db.get_setting("assigned_approver_id", "0")
    return int(raw) if raw.isdigit() else 0


def set_assigned_approver_id(staff_id: int) -> None:
    db.set_setting("assigned_approver_id", str(staff_id))


def can_approve_item(actor_id: int, specific_assigned_id: int | None = None) -> tuple[bool, str]:
    if actor_id in MASTER_IDS:
        return True, ""

    target_id = (
        specific_assigned_id
        if (specific_assigned_id and specific_assigned_id > 0)
        else get_assigned_approver_id()
    )
    if target_id > 0 and actor_id != target_id:
        return False, f"Y\xeau c\u1ea7u n\xe0y \u0111ang ch\u1ec9 \u0111\u1ecbnh cho nh\xe2n vi\xean ID <code>{target_id}</code> duy\u1ec7t."
    return True, ""


def parse_price_input(raw_text: str) -> dict:
    updates: dict[str, float] = {}
    parts = [p.strip() for p in raw_text.split("|") if p.strip()]
    if not parts:
        raise ValueError("Empty price input")

    for part in parts:
        tokens = part.upper().split()
        if len(tokens) < 2 or "/" not in tokens[1]:
            raise ValueError("Invalid price format")
        side = tokens[0]
        win_s, lose_s = tokens[1].split("/", 1)
        win_v, lose_v = float(win_s), float(lose_s)
        if win_v <= 0 or lose_v <= 0:
            raise ValueError("Price values must be positive")

        if side == "XANH":
            updates["xanh_win"] = win_v
            updates["xanh_lose"] = lose_v
        elif side in ("DO", "\u0110\u1ece"):
            updates["do_win"] = win_v
            updates["do_lose"] = lose_v
        else:
            raise ValueError("Unknown side")

    if "xanh_win" in updates and "do_win" not in updates:
        updates["do_win"] = updates["xanh_lose"]
        updates["do_lose"] = updates["xanh_win"]
    elif "do_win" in updates and "xanh_win" not in updates:
        updates["xanh_win"] = updates["do_lose"]
        updates["xanh_lose"] = updates["do_win"]

    return updates


def parse_single_side_ratio(raw_text: str, chosen_side: str) -> dict:
    ratio_token = raw_text.strip().split()[-1]
    w_s, l_s = ratio_token.split("/", 1)
    w_v, l_v = float(w_s), float(l_s)
    if w_v <= 0 or l_v <= 0:
        raise ValueError("Ratio must be positive")

    if chosen_side.upper() == "XANH":
        return {"xanh_win": w_v, "xanh_lose": l_v, "do_win": l_v, "do_lose": w_v}
    return {"do_win": w_v, "do_lose": l_v, "xanh_win": l_v, "xanh_lose": w_v}


def get_side_price(do: dict, side: str) -> tuple[float, float]:
    if side == "XANH":
        return float(do["xanh_win"]), float(do["xanh_lose"])
    return float(do["do_win"]), float(do["do_lose"])


def calc_bet_numbers(amount: float, win_u: float, lose_u: float) -> dict:
    base_u = win_u if win_u > 0 else 10.0
    pay_u = lose_u if lose_u > 0 else 10.0
    lose_amount = amount
    win_gross = amount * (pay_u / base_u)
    fee_info = calc_fee_and_net_profit(win_gross)
    return {
        "amount": amount,
        "win_gross": win_gross,
        "fee_amount": fee_info["fee_amount"],
        "win_net": fee_info["net_profit"],
        "lose_amount": lose_amount,
    }


def max_worst_case_loss(user_id: int, do_name: str, extra_bet: dict | None = None) -> float:
    bets = db.get_active_bets(do_name=do_name, user_id=user_id)

    xanh_win_net = sum(float(b["win_net"]) for b in bets if b["side"] == "XANH")
    xanh_lose = sum(float(b["lose_amount"]) for b in bets if b["side"] == "XANH")

    do_win_net = sum(float(b["win_net"]) for b in bets if b["side"] == "DO")
    do_lose = sum(float(b["lose_amount"]) for b in bets if b["side"] == "DO")

    if extra_bet:
        if extra_bet["side"] == "XANH":
            xanh_win_net += float(extra_bet["win_net"])
            xanh_lose += float(extra_bet["lose_amount"])
        else:
            do_win_net += float(extra_bet["win_net"])
            do_lose += float(extra_bet["lose_amount"])

    pnl_if_xanh = xanh_win_net - do_lose
    pnl_if_do = do_win_net - xanh_lose

    loss_if_xanh = max(0.0, -pnl_if_xanh)
    loss_if_do = max(0.0, -pnl_if_do)
    return max(loss_if_xanh, loss_if_do)


def _calc_other_dos_edge_bonus(user_id: int, current_do_name: str, open_dos: list[dict]) -> float:
    bonus = 0.0
    for d in open_dos:
        if d["name"] == current_do_name:
            continue
        bets = db.get_active_bets(do_name=d["name"], user_id=user_id)
        if not bets:
            continue
        xanh_win_net = sum(float(b["win_net"]) for b in bets if b["side"] == "XANH")
        xanh_lose = sum(float(b["lose_amount"]) for b in bets if b["side"] == "XANH")
        do_win_net = sum(float(b["win_net"]) for b in bets if b["side"] == "DO")
        do_lose = sum(float(b["lose_amount"]) for b in bets if b["side"] == "DO")

        pnl_if_xanh = xanh_win_net - do_lose
        pnl_if_do = do_win_net - xanh_lose

        if pnl_if_xanh > 0 and pnl_if_do > 0:
            bonus += min(pnl_if_xanh, pnl_if_do)
    return max(0.0, bonus)


def get_user_betting_limit_info(user_id: int, do_id: int | None = None) -> dict:
    """Tr\u1ea7n t\u1ed5ng m\u1ed7i c\u1eeda = v\u1ed1n kh\u1ea3 d\u1ee5ng - g\u1ed1c c\u1eeda \u0111\u1ed1i di\u1ec7n + l\u1eddi r\xf2ng \u0111\u1ed1i di\u1ec7n.

    M\u1ed7i m\xe3 m\u1edbi ph\u1ea3i tr\u1eeb T\u1ea4T C\u1ea2 g\u1ed1c \u0111\xe3 \u0111\u1eb7t \u1edf c\u1eeda \u0111\xf3. \u0110\u1ed3ng th\u1eddi ki\u1ec3m tra
    r\u1ee7i ro x\u1ea5u nh\u1ea5t c\u1ee7a m\u1ecdi \u0111\u1ed9 OPEN/HOLD, kh\xf4ng c\u1ed9ng l\xe3i ch\u01b0a quy\u1ebft to\xe1n.
    """
    u = db.get_user(user_id)
    open_dos = [d for d in db.get_unsettled_dos()]
    if do_id is not None:
        do = db.get_do_by_id(do_id)
    else:
        active = [d for d in open_dos if d.get("status") == "OPEN"]
        do = active[-1] if active else None
    bal = float(u["balance"])
    debt = float(u["debt"])
    debt_limit = float(u["debt_limit"])
    max_capacity = max(0.0, bal + max(0.0, debt_limit - debt))
    all_open_bets = []
    for d in open_dos:
        all_open_bets.extend(db.get_active_bets(do_name=d["name"], user_id=user_id))
    existing_bets = [b for b in all_open_bets if do and b["do_name"] == do["name"]]
    other_bets = [b for b in all_open_bets if not do or b["do_name"] != do["name"]]
    xanh_win_net_sum = sum(float(b["win_net"]) for b in existing_bets if b["side"] == "XANH")
    do_win_net_sum = sum(float(b["win_net"]) for b in existing_bets if b["side"] == "DO")
    xanh_lose_sum = sum(float(b["lose_amount"]) for b in existing_bets if b["side"] == "XANH")
    do_lose_sum = sum(float(b["lose_amount"]) for b in existing_bets if b["side"] == "DO")
    cur_do_locked_capital = max(0.0, do_lose_sum - xanh_win_net_sum, xanh_lose_sum - do_win_net_sum)
    other_risk = sum(max_worst_case_loss(user_id, d["name"]) for d in open_dos if not do or d["id"] != do["id"])
    # V\u1ed1n g\u1ed1c d\xe0nh cho \u0111\u1ed9 n\xe0y sau khi gi\u1eef ch\u1ed7 cho c\xe1c \u0111\u1ed9 ch\u01b0a ch\u1ed1t.
    capacity_here = max(0.0, max_capacity - other_risk)
    # Tr\u1ea7n t\u1ed5ng: ph\u1ea7n v\u1ed1n ch\u01b0a d\xf9ng b\xean \u0111\u1ed1i di\u1ec7n + l\u1eddi r\xf2ng b\xean \u0111\u1ed1i di\u1ec7n.
    xanh_total_cap = max(0.0, capacity_here - do_lose_sum + do_win_net_sum)
    do_total_cap = max(0.0, capacity_here - xanh_lose_sum + xanh_win_net_sum)
    max_xanh = max(0.0, xanh_total_cap - xanh_lose_sum)
    max_do = max(0.0, do_total_cap - do_lose_sum)
    return {
        "user": u, "do": do, "open_dos": open_dos,
        "all_open_bets": all_open_bets,
        "max_capacity": max_capacity,
        "total_open_principal": sum(float(b["amount"]) for b in all_open_bets),
        "locked_capital": other_risk + cur_do_locked_capital,
        "base_unused": max(0.0, capacity_here - cur_do_locked_capital),
        "edge_bonus": 0.0,
        "unused_capital": max(0.0, capacity_here - cur_do_locked_capital),
        "xanh_opp_win": do_win_net_sum, "do_opp_win": xanh_win_net_sum,
        "max_xanh": max_xanh, "max_do": max_do,
        "xanh_total_cap": xanh_total_cap, "do_total_cap": do_total_cap,
        "xanh_placed": xanh_lose_sum, "do_placed": do_lose_sum,
    }


def can_open_do_by_time(now_dt: datetime | None = None) -> tuple[bool, str]:
    current = now_dt.astimezone(VN_TZ) if now_dt else datetime.now(VN_TZ)
    if current.hour < 7:
        return (
            False,
            f"\u23f0 Ch\u01b0a t\u1edbi gi\u1edd m\u1edf k\u1ef3/\u0111\u1ed9! H\u1ec7 th\u1ed1ng ch\u1ec9 m\u1edf t\u1eeb <b>07:00</b> s\xe1ng "
            f"(Hi\u1ec7n t\u1ea1i: <b>{current.strftime('%H:%M - %d/%m/%Y')}</b>).",
        )
    return True, ""


def normalize_do_name(raw_do: str | int) -> str:
    s = str(raw_do).strip()
    if s.isdigit():
        return f"\u0110\u1ed9 {int(s)}"
    low = s.lower()
    if low.startswith("\u0111\u1ed9"):
        tail = s[2:].strip()
        if tail.isdigit():
            return f"\u0110\u1ed9 {int(tail)}"
        return f"\u0110\u1ed9 {tail}".strip()
    return s


def _get_or_create_do_record(
    do_name: str,
    xanh_win: float,
    xanh_lose: float,
    do_win: float,
    do_lose: float,
) -> dict | None:
    existing = None
    if hasattr(db, "get_do_by_name"):
        existing = db.get_do_by_name(do_name)
    else:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM dos WHERE name = ? ORDER BY id DESC LIMIT 1",
                (do_name,),
            ).fetchone()
            if row:
                existing = dict(row)

    if existing and existing["status"] == "CLOSED":
        return None  # Kh\xf4ng m\u1edf l\u1ea1i \u0111\u1ed9 \u0111\xe3 ch\u1ed1t
    if existing:
        db.update_do(
            int(existing["id"]),
            status="OPEN",
            xanh_win=xanh_win,
            xanh_lose=xanh_lose,
            do_win=do_win,
            do_lose=do_lose,
        )
        return db.get_do_by_id(int(existing["id"]))

    if hasattr(db, "create_do"):
        new_id = db.create_do(
            name=do_name,
            xanh_win=xanh_win,
            xanh_lose=xanh_lose,
            do_win=do_win,
            do_lose=do_lose,
            status="OPEN",
        )
        return db.get_do_by_id(int(new_id))

    with db.get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO dos (name, xanh_win, xanh_lose, do_win, do_lose, status, bet_seq)
            VALUES (?, ?, ?, ?, ?, 'OPEN', 1)
            """,
            (do_name, xanh_win, xanh_lose, do_win, do_lose),
        )
        conn.commit()
        new_id = cur.lastrowid
    return db.get_do_by_id(int(new_id))


def open_specific_do_logic(
    raw_do: str | int,
    xanh_win: float = 10.0,
    xanh_lose: float = 10.0,
    do_win: float = 10.0,
    do_lose: float = 10.0,
    exclusive_active: bool = True,
) -> tuple[bool, str, dict | None]:
    ok_time, time_msg = can_open_do_by_time()
    if not ok_time:
        return False, time_msg, None

    do_name = normalize_do_name(raw_do)
    if not do_name or do_name == "\u0110\u1ed9":
        return False, "\u274c S\u1ed1 ho\u1eb7c t\xean \u0111\u1ed9 kh\xf4ng h\u1ee3p l\u1ec7 (v\xed d\u1ee5 nh\u1eadp <code>3</code> ho\u1eb7c <code>\u0110\u1ed9 3</code>).", None

    held_names: list[str] = []
    if exclusive_active:
        for d in db.get_open_dos():
            if d.get("status") == "OPEN" and d.get("name") != do_name:
                db.update_do(int(d["id"]), status="HOLD")
                held_names.append(str(d["name"]))

    do_rec = _get_or_create_do_record(
        do_name=do_name,
        xanh_win=xanh_win,
        xanh_lose=xanh_lose,
        do_win=do_win,
        do_lose=do_lose,
    )
    if not do_rec:
        return False, f"\u274c Kh\xf4ng th\u1ec3 m\u1edf <b>{do_name}</b>. \u0110\u1ed9 \u0111\xe3 ch\u1ed1t kh\xf4ng th\u1ec3 m\u1edf l\u1ea1i; h\xe3y d\xf9ng t\xean \u0111\u1ed9 m\u1edbi.", None

    msg = (
        f"\u2705 <b>\u0110\xc3 M\u1ede {do_name.upper()}</b> "
        f"(XANH <code>{xanh_win:g}/{xanh_lose:g}</code> | \u0110\u1ece <code>{do_win:g}/{do_lose:g}</code>).\n"
        f"\u2022 Hi\u1ec7n ch\u1ec9 nh\u1eadn c\u01b0\u1ee3c cho <b>{do_name}</b> \u0111\u1ec3 tr\xe1nh \u0111\xe1nh nh\u1ea7m \u0111\u1ed9."
    )
    if held_names:
        msg += f"\n\u2022 \u0110\xe3 chuy\u1ec3n sang tr\u1ea1ng th\xe1i ch\u1edd/ho\xe3n: <b>{', '.join(held_names)}</b>."

    return True, msg, do_rec


def place_bet_logic(user_id: int, amount: float, side: str, do_id: int | None = None) -> tuple[bool, str]:
    side_norm = side.upper()
    if side_norm not in ("XANH", "DO"):
        return False, "C\u1eeda \u0111\u1eb7t kh\xf4ng h\u1ee3p l\u1ec7."

    u = db.get_user(user_id)
    open_dos = db.get_unsettled_dos()
    if do_id:
        do = db.get_do_by_id(do_id)
    else:
        do = open_dos[-1] if open_dos else None

    if not do or do.get("status") != "OPEN":
        return False, "\u0110\u1ed9 n\xe0y hi\u1ec7n \u0111ang t\u1ea1m ho\xe3n, kh\xf3a ho\u1eb7c \u0111\xe3 ch\u1ed1t."
    import math
    if not math.isfinite(amount) or amount <= 0:
        return False, "S\u1ed1 ti\u1ec1n \u0111\u1eb7t ph\u1ea3i h\u1eefu h\u1ea1n v\xe0 l\u1edbn h\u01a1n 0."

    bal = float(u["balance"])
    debt = float(u["debt"])
    debt_limit = float(u["debt_limit"])

    lim = get_user_betting_limit_info(user_id, do_id=int(do["id"]))
    max_capacity = lim["max_capacity"]
    edge_bonus = lim["edge_bonus"]
    unused_capital = lim["unused_capital"]

    existing_bets = db.get_active_bets(do_name=do["name"], user_id=user_id)
    opp_side = "XANH" if side_norm == "DO" else "DO"

    opp_win_net_sum = sum(float(b["win_net"]) for b in existing_bets if b["side"] == opp_side)
    win_u, lose_u = get_side_price(do, side_norm)
    calc = calc_bet_numbers(amount, win_u, lose_u)

    # H\u1ea1n m\u1ee9c c\xf2n l\u1ea1i sau khi tr\u1eeb T\u1ea4T C\u1ea2 m\xe3 \u0111\xe3 \u0111\u1eb7t \u1edf c\xf9ng c\u1eeda.
    allowed_budget = lim["max_xanh"] if side_norm == "XANH" else lim["max_do"]
    if amount > allowed_budget + 1e-6:
        return (
            False,
            f"\u274c V\u01b0\u1ee3t h\u1ea1n m\u1ee9c t\u1ed5ng c\u01b0\u1ee3c c\u1eeda {side_norm} ({do['name']})!\n"
            f"\u2022 L\u1eddi r\xf2ng c\u1eeda {opp_side} (sau 5% ph\u1ebf): <b>{fmt_num(opp_win_net_sum)}</b>\n"
            f"\u2022 H\u1ea1n m\u1ee9c t\u1ed1i \u0111a cho m\xe3 n\xe0y: <b>{fmt_num(allowed_budget)}</b>\n"
            f"\u2022 \u0110\xe3 tr\u1eeb t\u1ea5t c\u1ea3 m\xe3 \u0111\xe3 c\u01b0\u1ee3c tr\u01b0\u1edbc \u0111\xf3 \u1edf c\xf9ng c\u1eeda.",
        )

    other_dos_worst_loss = sum(
        max_worst_case_loss(user_id, d["name"])
        for d in open_dos
        if d["id"] != do["id"]
    )
    current_do_worst_loss = max_worst_case_loss(user_id, do["name"], extra_bet={"side": side_norm, **calc})
    total_worst_loss = other_dos_worst_loss + current_do_worst_loss

    hard_risk_cap = max_capacity
    if total_worst_loss > hard_risk_cap + 1e-6:
        remain_risk = max(0.0, hard_risk_cap - other_dos_worst_loss)
        return (
            False,
            f"\u274c V\u01b0\u1ee3t tr\u1ea7n r\u1ee7i ro t\u1ed1i \u0111a c\u1ee7a t\xe0i kho\u1ea3n!\n"
            f"\u2022 R\u1ee7i ro sau m\xe3 n\xe0y: <b>{fmt_num(total_worst_loss)}</b> / Tr\u1ea7n t\u1ed1i \u0111a: <b>{fmt_num(hard_risk_cap)}</b>\n"
            f"\u2022 Kh\u1ea3 n\u0103ng ch\u1ecbu r\u1ee7i ro c\xf2n l\u1ea1i: <b>{fmt_num(remain_risk)}</b>",
        )

    seq = int(do["bet_seq"])
    code = f"#M{seq:02d}"
    price_str = f"{win_u:g}/{lose_u:g}"

    db.add_bet_and_advance_seq(
        do_id=int(do["id"]), expected_seq=seq,
        code=code,
        do_name=do["name"],
        user_id=user_id,
        side=side_norm,
        amount=amount,
        win_net=calc["win_net"],
        lose_amount=calc["lose_amount"],
        price_str=price_str,
    )

    side_label = "\U0001f7e2 XANH" if side_norm == "XANH" else "\U0001f534 \u0110\u1ece"
    msg = (
        f"\u2705 <b>\u0110\xc3 NH\u1eacN K\xc8O \u2014 {do['name']} | {side_label}</b>\n"
        f"\u2022 M\xe3: <b>{code}</b> | Gi\xe1 l\xfac \u0111\u1eb7t: <b>{price_str}</b>\n"
        f"\u2022 M\u1ee9c \u0111\xe1nh: <b>{fmt_num(amount)}</b> (Thua m\xe3 n\xe0y: <b>{fmt_num(calc['lose_amount'])}</b>)\n"
        f"\u2022 N\u1ebfu {side_label} th\u1eafng: l\u1eddi <b>{fmt_num(calc['win_net'])}</b> <i>(\u0111\xe3 tr\u1eeb 5% ph\u1ebf)</i>\n"
        f"\u2022 V\xed: <b>{fmt_num(bal)}</b> | N\u1ee3: <b>{fmt_num(debt)}/{fmt_num(debt_limit)}</b>"
    )
    return True, msg


def apply_deposit_logic(target_uid: int, amount: float) -> dict:
    u = db.get_user(target_uid)
    new_balance = float(u["balance"]) + amount
    db.update_user_fields(target_uid, balance=new_balance)
    new_profit = db.get_realized_profit()  # N\u1ea1p v\u1ed1n kh\xf4ng ph\u1ea3i l\u1ee3i nhu\u1eadn
    return {
        "user": db.get_user(target_uid),
        "new_balance": new_balance,
        "realized_profit": new_profit,
    }


def create_withdrawal_logic(
    user_id: int, amount: float, payout_text: str = "", qr_file_id: str = ""
) -> tuple[bool, str, dict | None]:
    u = db.get_user(user_id)
    bal = float(u["balance"])

    if amount < MIN_WITHDRAW_AMOUNT:
        return (
            False,
            f"\u274c S\u1ed1 ti\u1ec1n r\xfat t\u1ed1i thi\u1ec3u l\xe0 <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b>.",
            None,
        )
    if amount > bal + 1e-6:
        return (
            False,
            f"\u274c S\u1ed1 ti\u1ec1n r\xfat v\u01b0\u1ee3t qu\xe1 s\u1ed1 d\u01b0 v\xed (T\u1ed1i \u0111a c\xf3 th\u1ec3 r\xfat: <b>{fmt_num(bal)}</b>).",
            None,
        )

    new_bal = bal - amount
    info_save = payout_text if payout_text else "[\u1ea2nh m\xe3 QR]"
    db.update_user_fields(user_id, balance=new_bal, last_payout_info=info_save)

    assigned_id = get_assigned_approver_id()
    w_id = db.create_withdrawal(
        user_id=user_id,
        amount=amount,
        payout_text=payout_text,
        qr_file_id=qr_file_id,
        assigned_staff_id=assigned_id,
        created_at=datetime.now(VN_TZ).strftime("%H:%M - %d/%m/%Y"),
    )
    w_rec = db.get_withdrawal(w_id)
    return (
        True,
        f"\u2705 \u0110\xe3 t\u1ea1o l\u1ec7nh r\xfat <b>{fmt_num(amount)}</b> (M\xe3 r\xfat: <code>#R{w_id:03d}</code>), \u0111ang ch\u1edd duy\u1ec7t.",
        w_rec,
    )


def confirm_withdrawal_logic(w_id: int) -> tuple[bool, str, dict | None]:
    w = db.get_withdrawal(w_id)
    if not w:
        return False, "Kh\xf4ng t\xecm th\u1ea5y l\u1ec7nh r\xfat.", None
    if w["status"] != "PENDING":
        return False, f"L\u1ec7nh r\xfat <code>#R{w_id:03d}</code> \u0111\xe3 \u0111\u01b0\u1ee3c x\u1eed l\xfd tr\u01b0\u1edbc \u0111\xf3 ({w['status']}).", None

    amt = float(w["amount"])
    db.update_withdrawal(w_id, status="APPROVED")
    db.add_realized_profit(-amt)
    u = db.get_user(int(w["user_id"]))
    return True, f"\u2705 \u0110\xe3 x\xe1c nh\u1eadn chuy\u1ec3n ti\u1ec1n l\u1ec7nh <code>#R{w_id:03d}</code> (<b>{fmt_num(amt)}</b>).", {"withdrawal": w, "user": u}


def reject_withdrawal_logic(w_id: int) -> tuple[bool, str, dict | None]:
    w = db.get_withdrawal(w_id)
    if not w:
        return False, "Kh\xf4ng t\xecm th\u1ea5y l\u1ec7nh r\xfat.", None
    if w["status"] != "PENDING":
        return False, f"L\u1ec7nh r\xfat <code>#R{w_id:03d}</code> \u0111\xe3 x\u1eed l\xfd tr\u01b0\u1edbc \u0111\xf3.", None

    amt = float(w["amount"])
    uid = int(w["user_id"])
    u = db.get_user(uid)
    refunded_bal = float(u["balance"]) + amt

    db.update_withdrawal(w_id, status="REJECTED")
    db.update_user_fields(uid, balance=refunded_bal)
    return True, f"\u274c \u0110\xe3 t\u1eeb ch\u1ed1i v\xe0 ho\xe0n l\u1ea1i <b>{fmt_num(amt)}</b> v\xe0o v\xed ID <code>{uid}</code>.", {"withdrawal": w, "balance": refunded_bal}


def settle_single_do_no_reopen(do: dict, result: str) -> dict[int, float]:
    do_name = do["name"]
    bets = db.get_active_bets(do_name=do_name)

    if result in ("CANCEL", "DRAW"):
        status_val = "CANCELLED" if result == "CANCEL" else "DRAW"
        db.update_bets_status_by_do(do_name, status_val)
        db.update_do(int(do["id"]), status="CLOSED")
        return {}

    per_user_net: dict[int, float] = {}
    for b in bets:
        uid = int(b["user_id"])
        per_user_net.setdefault(uid, 0.0)
        if b["side"] == result:
            db.update_bet_status(int(b["id"]), "WON")
            per_user_net[uid] += float(b["win_net"])
        else:
            db.update_bet_status(int(b["id"]), "LOST")
            per_user_net[uid] -= float(b["lose_amount"])

    db.update_do(int(do["id"]), status="CLOSED")
    return per_user_net


def settle_multiple_dos_logic(do_ids: list[int], result: str) -> dict:
    settled_names: list[str] = []
    combined_net: dict[int, float] = {}
    per_do_net: dict[str, dict[int, float]] = {}

    for d_id in do_ids:
        do = db.get_do_by_id(d_id)
        if not do or do.get("status") != "OPEN":
            continue
        d_name = do["name"]
        settled_names.append(d_name)
        part_net = settle_single_do_no_reopen(do, result)
        per_do_net[d_name] = part_net
        for uid, val in part_net.items():
            combined_net[uid] = combined_net.get(uid, 0.0) + val

    users_summary = []
    for uid, net in combined_net.items():
        u = db.get_user(uid)
        bal = float(u["balance"])
        debt = float(u["debt"])
        pnl = float(u["pnl_net"]) + net

        if net >= 0:
            if debt > 0:
                paid_debt = min(debt, net)
                debt -= paid_debt
                bal += (net - paid_debt)
            else:
                bal += net
        else:
            loss = abs(net)
            if bal >= loss:
                bal -= loss
            else:
                remain_loss = loss - bal
                bal = 0.0
                debt += remain_loss

        db.update_user_fields(uid, balance=bal, debt=debt, pnl_net=pnl)
        users_summary.append({
            "user_id": uid,
            "name": u["name"],
            "net": net,
            "balance": bal,
            "debt": debt,
        })

    open_after = db.get_open_dos()
    return {
        "do_names": settled_names,
        "result": result,
        "users": users_summary,
        "per_do_net": per_do_net,
        "open_dos": [d["name"] for d in open_after],
    }


def record_and_format_per_do_report(do_name: str, result: str, do_user_net: dict[int, float]) -> str:
    now = datetime.now(VN_TZ)
    day_key = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M - %d/%m/%Y")
    house_net = -sum(do_user_net.values())

    lines = [
        f"<b>\U0001f4cc B\xc1O C\xc1O \u0102N THUA \u2014 {do_name} (KQ: {result})</b>",
        f"<i>Th\u1eddi gian ch\u1ed1t: {time_str}</i>\n",
    ]
    if not do_user_net:
        lines.append("\u2022 <i>Kh\xf4ng c\xf3 m\xe3 c\u01b0\u1ee3c \u0103n/thua \u1edf \u0111\u1ed9 n\xe0y (h\xf2a/h\u1ee7y/kh\xf4ng c\xf3 k\xe8o).</i>")
    else:
        lines.append("<b>Chi ti\u1ebft t\u1eebng kh\xe1ch \u1edf \u0111\u1ed9 n\xe0y:</b>")
        for uid, net in do_user_net.items():
            u = db.get_user(uid)
            lines.append(
                f"\u2022 <b>{u['name']}</b> (<code>{uid}</code>): Kh\xe1ch {fmt_signed(net)} | "
                f"V\xed: <b>{fmt_num(float(u['balance']))}</b> | N\u1ee3: <b>{fmt_num(float(u['debt']))}</b>"
            )

    lines.append(f"\n\u2022 <b>\u0102n thua \u0111\u1ed9 n\xe0y (Master/C\u1ed5 \u0111\xf4ng):</b> {fmt_signed(house_net)}")
    summary_text = "\n".join(lines)
    db.insert_settled_do(do_name, result, summary_text, day_key, time_str)
    return summary_text


def build_live_report_text() -> str:
    open_dos = [d for d in db.get_open_dos() if d.get("status") == "OPEN"]
    if not open_dos:
        return "<b>TH\u1ed0NG K\xca C\xc1C \u0110\u1ed8 \u0110ANG M\u1ede</b>\n\n<i>Hi\u1ec7n kh\xf4ng c\xf3 \u0111\u1ed9 n\xe0o \u0111ang m\u1edf.</i>"

    blocks = []
    for do in open_dos:
        active_bets = db.get_active_bets(do_name=do["name"])
        tot_xanh = sum(float(b["amount"]) for b in active_bets if b["side"] == "XANH")
        tot_do = sum(float(b["amount"]) for b in active_bets if b["side"] == "DO")

        by_user: dict[int, dict[str, float]] = {}
        for b in active_bets:
            uid = int(b["user_id"])
            by_user.setdefault(uid, {"XANH": 0.0, "DO": 0.0, "count": 0})
            by_user[uid][b["side"]] += float(b["amount"])
            by_user[uid]["count"] += 1

        user_lines = []
        for uid, st in by_user.items():
            u = db.get_user(uid)
            user_lines.append(
                f"  \u2022 <b>{u['name']}</b> (<code>{uid}</code>): XANH <b>{fmt_num(st['XANH'])}</b> | "
                f"\u0110\u1ece <b>{fmt_num(st['DO'])}</b> ({int(st['count'])} m\xe3)"
            )

        code_lines = []
        for b in active_bets:
            u = db.get_user(int(b["user_id"]))
            side_icon = "\U0001f7e2 XANH" if b["side"] == "XANH" else "\U0001f534 \u0110\u1ece"
            code_lines.append(
                f"  \u2022 <code>{b['code']}</code> | <b>{u['name']}</b> | "
                f"{side_icon} <b>{fmt_num(float(b['amount']))}</b> (gi\xe1 {b['price_str']})"
            )

        blocks.append(
            f"<b>\U0001f4cc {do['name']}</b> (XANH <code>{float(do['xanh_win']):g}/{float(do['xanh_lose']):g}</code> | "
            f"\u0110\u1ece <code>{float(do['do_win']):g}/{float(do['do_lose']):g}</code>)\n"
            f"\u2022 T\u1ed5ng XANH: <b>{fmt_num(tot_xanh)}</b> | T\u1ed5ng \u0110\u1ece: <b>{fmt_num(tot_do)}</b>\n"
            + ("\n".join(user_lines) if user_lines else "  <i>Ch\u01b0a c\xf3 c\u01b0\u1ee3c.</i>")
            + ("\n" + "\n".join(code_lines) if code_lines else "")
        )

    return "<b>TH\u1ed0NG K\xca C\xc1C \u0110\u1ed8 \u0110ANG M\u1ede</b>\n\n" + "\n\n".join(blocks)


def build_pnl_report_text() -> str:
    now_dt = datetime.now(VN_TZ)
    now_str = now_dt.strftime("%H:%M - %d/%m/%Y")
    day_key = now_dt.strftime("%Y-%m-%d")
    settled_rows = db.get_settled_dos_by_day(day_key)
    users = db.get_all_users()
    realized = db.get_realized_profit()
    total_debt = sum(float(u["debt"]) for u in users)
    total_user_pnl = sum(float(u["pnl_net"]) for u in users)
    house_pnl = -total_user_pnl

    lines = [
        "<b>\U0001f451 B\xc1O C\xc1O MASTER \u2014 T\xccNH H\xccNH \u0110\u1ed8 & D\xd2NG TI\u1ec0N</b>",
        f"<i>C\u1eadp nh\u1eadt: {now_str}</i>\n",
    ]

    if settled_rows:
        lines.append("<b>C\xe1c \u0111\u1ed9 \u0111\xe3 ch\u1ed1t h\xf4m nay:</b>")
        for r in settled_rows[-8:]:
            lines.append(f"\u2022 <b>{r['do_name']}</b> \u2014 KQ: <b>{r['result']}</b> (<code>{r['settled_at']}</code>)")
        lines.append("")

    lines.append("<b>\u0102n thua l\u0169y k\u1ebf c\u1ee7a kh\xe1ch:</b>")
    for u in users:
        lines.append(
            f"\u2022 <b>{u['name']}</b> (<code>{u['user_id']}</code>): {fmt_signed(float(u['pnl_net']))} | "
            f"V\xed: <b>{fmt_num(float(u['balance']))}</b> | "
            f"N\u1ee3: <b>{fmt_num(float(u['debt']))}/{fmt_num(float(u['debt_limit']))}</b>"
        )

    lines.append("\n<b>T\u1ed5ng h\u1ee3p Master:</b>")
    lines.append(f"\u2022 \u0102n/thua r\xf2ng c\u1ee7a kh\xe1ch: {fmt_signed(total_user_pnl)}")
    lines.append(f"\u2022 \u0102n/thua r\xf2ng ph\xeda Master/C\u1ed5 \u0111\xf4ng: {fmt_signed(house_pnl)}")
    lines.append(f"\u2022 L\u1ee3i nhu\u1eadn th\u1ef1c thu: {fmt_signed(realized)}")
    lines.append(f"\u2022 C\xf4ng n\u1ee3 treo c\u1ed9ng d\u1ed3n: \U0001f534 <b>-{fmt_num(total_debt)}</b>")
    return "\n".join(lines)


def build_daily_report_text(day_key: str | None = None) -> str:
    now_dt = datetime.now(VN_TZ)
    target_day = day_key or now_dt.strftime("%Y-%m-%d")
    now_str = now_dt.strftime("%H:%M - %d/%m/%Y")
    rows = db.get_settled_dos_by_day(target_day)
    users = db.get_all_users()
    realized = db.get_realized_profit()
    total_debt = sum(float(u["debt"]) for u in users)
    total_user_pnl = sum(float(u["pnl_net"]) for u in users)
    house_pnl = -total_user_pnl

    lines = [
        f"<b>\U0001f4ca B\xc1O C\xc1O MASTER C\u1ea2 NG\xc0Y ({target_day})</b>",
        f"<i>C\u1eadp nh\u1eadt l\xfac: {now_str} | S\u1ed1 \u0111\u1ed9 \u0111\xe3 ch\u1ed1t: <b>{len(rows)}</b></i>\n",
    ]

    if rows:
        lines.append("<b>Danh s\xe1ch c\xe1c \u0111\u1ed9 \u0111\xe3 ch\u1ed1t trong ng\xe0y:</b>")
        for r in rows:
            lines.append(f"\u2022 <b>{r['do_name']}</b> \u2014 KQ: <b>{r['result']}</b> (<code>{r['settled_at']}</code>)")
        lines.append("")

    lines.append("<b>T\u1ed5ng \u0103n thua c\u1ea3 ng\xe0y t\u1eebng kh\xe1ch:</b>")
    for u in users:
        lines.append(
            f"\u2022 <b>{u['name']}</b> (<code>{u['user_id']}</code>): {fmt_signed(float(u['pnl_net']))} | "
            f"V\xed: <b>{fmt_num(float(u['balance']))}</b> | "
            f"N\u1ee3: <b>{fmt_num(float(u['debt']))}/{fmt_num(float(u['debt_limit']))}</b>"
        )

    lines.append("\n<b>T\u1ed5ng k\u1ebft c\u1ea3 ng\xe0y (Master):</b>")
    lines.append(f"\u2022 \u0102n/thua r\xf2ng c\u1ea3 ng\xe0y (Master/C\u1ed5 \u0111\xf4ng): {fmt_signed(house_pnl)}")
    lines.append(f"\u2022 L\u1ee3i nhu\u1eadn th\u1ef1c thu: {fmt_signed(realized)}")
    lines.append(f"\u2022 C\xf4ng n\u1ee3 treo c\u1ed9ng d\u1ed3n: \U0001f534 <b>-{fmt_num(total_debt)}</b>")
    return "\n".join(lines)


def close_day_accounting_logic(day_key: str | None = None) -> str:
    target_day = day_key or datetime.now(VN_TZ).strftime("%Y-%m-%d")
    report_snapshot = build_daily_report_text(target_day)
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET pnl_net = 0.0")
        conn.commit()
    db.clear_settled_dos_by_day(target_day)
    return report_snapshot
