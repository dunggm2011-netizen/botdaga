from datetime import datetime, timezone, timedelta
from math import isfinite
from config import FEE_RATE, MASTER_IDS, MIN_WITHDRAW_AMOUNT
import database as db

VN_TZ = timezone(timedelta(hours=7))


def fmt_num(val: float) -> str:
    return f"{val:,.0f}".replace(",", ".")


def fmt_signed(val: float) -> str:
    if val > 0:
        return f"🟢 <b>+{fmt_num(val)}</b>"
    if val < 0:
        return f"🔴 <b>-{fmt_num(abs(val))}</b>"
    return "⚪ <b>0</b>"


def calc_fee_and_net_profit(gross_profit: float) -> dict:
    """
    Nguyên tắc khấu trừ 5%:
    - Chỉ tính trên lợi nhuận trước khấu trừ.
    - Khấu trừ = lợi nhuận * 5%, thực nhận = lợi nhuận * 95%.
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
        return False, f"Yêu cầu này đang chỉ định cho nhân viên ID <code>{target_id}</code> duyệt."
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
        elif side in ("DO", "ĐỎ"):
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
    """
    Hạn mức dựa trên khả năng thanh toán thực tế:
    - Chỉ độ OPEN mới được nhận cược; độ HOLD vẫn được tính là khoản rủi ro đang treo.
    - Rủi ro của mỗi độ là khoản lỗ lớn nhất trong các kết quả có thể xảy ra.
    - Không lấy khoản lời chưa quyết toán ở độ khác làm vốn khả dụng.
    - Ở độ đang cược, lời ròng của cửa đối diện có thể dùng để bù rủi ro khi đánh cửa còn lại.
    """
    u = db.get_user(user_id)
    active_dos = [
        d for d in db.get_open_dos()
        if d.get("status") in ("OPEN", "HOLD")
    ]
    open_dos = [d for d in active_dos if d.get("status") == "OPEN"]

    if do_id:
        do = db.get_do_by_id(do_id)
    else:
        do = open_dos[-1] if open_dos else None

    bal = float(u["balance"])
    debt = float(u["debt"])
    debt_limit = float(u["debt_limit"])
    remaining_debt_room = max(0.0, debt_limit - debt)
    max_capacity = max(0.0, bal + remaining_debt_room)

    all_active_bets: list[dict] = []
    for d in active_dos:
        all_active_bets.extend(
            db.get_active_bets(do_name=d["name"], user_id=user_id)
        )

    existing_bets = [
        b for b in all_active_bets
        if do and b["do_name"] == do["name"]
    ]
    other_bets = [
        b for b in all_active_bets
        if not do or b["do_name"] != do["name"]
    ]

    xanh_win_net_sum = sum(
        float(b["win_net"]) for b in existing_bets if b["side"] == "XANH"
    )
    do_win_net_sum = sum(
        float(b["win_net"]) for b in existing_bets if b["side"] == "DO"
    )
    xanh_lose_sum = sum(
        float(b["lose_amount"]) for b in existing_bets if b["side"] == "XANH"
    )
    do_lose_sum = sum(
        float(b["lose_amount"]) for b in existing_bets if b["side"] == "DO"
    )

    loss_if_xanh = max(0.0, do_lose_sum - xanh_win_net_sum)
    loss_if_do = max(0.0, xanh_lose_sum - do_win_net_sum)
    cur_do_locked_capital = max(loss_if_xanh, loss_if_do)

    other_locked_risk = 0.0
    for d in active_dos:
        if do and d["name"] == do["name"]:
            continue
        other_locked_risk += max_worst_case_loss(user_id, d["name"])

    locked_capital = other_locked_risk + cur_do_locked_capital
    unused_capital = max(0.0, max_capacity - locked_capital)

    # Không cộng lãi chưa chốt từ độ khác vào hạn mức.
    edge_bonus = 0.0

    def _side_max(target_side: str) -> tuple[float, float]:
        """Trả về tiền thắng ròng cửa đối diện và hạn mức cược thêm của cửa này.

        Phương án B:
        - Nếu cửa đối diện đã có cược: tổng cược cửa này (đã cược + cược mới)
          không được vượt tiền thắng ròng thực tế của cửa đối diện.
        - Nếu chưa có cược cửa đối diện: chỉ dùng vốn khả dụng còn lại.
        - unused_capital đã trừ rủi ro đang treo, nên không cộng/trừ lại vốn
          hoặc phần cược cũ lần thứ hai.
        """
        if target_side == "XANH":
            opp_win_net = do_win_net_sum
            own_stake = xanh_lose_sum
            has_opposite_bets = any(b["side"] == "DO" for b in existing_bets)
        else:
            opp_win_net = xanh_win_net_sum
            own_stake = do_lose_sum
            has_opposite_bets = any(b["side"] == "XANH" for b in existing_bets)

        if has_opposite_bets:
            remaining = max(0.0, max(0.0, opp_win_net) - own_stake)
        else:
            remaining = max(0.0, unused_capital)
        return opp_win_net, remaining

    xanh_opp_win, max_xanh = _side_max("XANH")
    do_opp_win, max_do = _side_max("DO")

    total_open_principal = sum(float(b["amount"]) for b in all_active_bets)

    return {
        "user": u,
        "do": do,
        "open_dos": open_dos,
        "all_open_bets": all_active_bets,
        "max_capacity": max_capacity,
        "total_open_principal": total_open_principal,
        "locked_capital": locked_capital,
        "base_unused": unused_capital,
        "edge_bonus": edge_bonus,
        "unused_capital": unused_capital,
        "xanh_opp_win": xanh_opp_win,
        "do_opp_win": do_opp_win,
        "max_xanh": max_xanh,
        "max_do": max_do,
    }


def can_open_do_by_time(now_dt: datetime | None = None) -> tuple[bool, str]:
    current = now_dt.astimezone(VN_TZ) if now_dt else datetime.now(VN_TZ)
    if current.hour < 7:
        return (
            False,
            f"⏰ Chưa tới giờ mở kỳ/độ! Hệ thống chỉ mở từ <b>07:00</b> sáng "
            f"(Hiện tại: <b>{current.strftime('%H:%M - %d/%m/%Y')}</b>).",
        )
    return True, ""


def normalize_do_name(raw_do: str | int) -> str:
    s = str(raw_do).strip()
    if s.isdigit():
        return f"Độ {int(s)}"
    low = s.lower()
    if low.startswith("độ"):
        tail = s[2:].strip()
        if tail.isdigit():
            return f"Độ {int(tail)}"
        return f"Độ {tail}".strip()
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

    if existing and existing.get("status") in ("OPEN", "HOLD"):
        db.update_do(
            int(existing["id"]),
            status="OPEN",
            xanh_win=xanh_win,
            xanh_lose=xanh_lose,
            do_win=do_win,
            do_lose=do_lose,
        )
        return db.get_do_by_id(int(existing["id"]))

    # Nếu bản ghi cùng tên đã CLOSED, tạo bản ghi mới để giữ nguyên lịch sử.

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
    if not do_name or do_name == "Độ":
        return False, "❌ Số hoặc tên độ không hợp lệ (ví dụ nhập <code>3</code> hoặc <code>Độ 3</code>).", None

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
        return False, f"❌ Không thể mở <b>{do_name}</b>.", None

    msg = (
        f"✅ <b>ĐÃ MỞ {do_name.upper()}</b> "
        f"(XANH <code>{xanh_win:g}/{xanh_lose:g}</code> | ĐỎ <code>{do_win:g}/{do_lose:g}</code>).\n"
        f"• Hiện chỉ nhận cược cho <b>{do_name}</b> để tránh đánh nhầm độ."
    )
    if held_names:
        msg += f"\n• Đã chuyển sang trạng thái chờ/hoãn: <b>{', '.join(held_names)}</b>."

    return True, msg, do_rec


def place_bet_logic(user_id: int, amount: float, side: str, do_id: int | None = None) -> tuple[bool, str]:
    side_norm = side.upper()
    if side_norm not in ("XANH", "DO"):
        return False, "Cửa đặt không hợp lệ."

    u = db.get_user(user_id)
    open_dos = [d for d in db.get_open_dos() if d.get("status") == "OPEN"]
    if do_id:
        do = db.get_do_by_id(do_id)
    else:
        do = open_dos[-1] if open_dos else None

    if not do or do.get("status") != "OPEN":
        return False, "Độ này hiện đang tạm hoãn, khóa hoặc đã chốt."
    if not isfinite(float(amount)) or amount <= 0:
        return False, "Số tiền đặt phải là số hữu hạn lớn hơn 0."

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
    own_side_stake_sum = sum(float(b["lose_amount"]) for b in existing_bets if b["side"] == side_norm)
    win_u, lose_u = get_side_price(do, side_norm)
    calc = calc_bet_numbers(amount, win_u, lose_u)

    # Hạn mức trong lim đã trừ tổng tiền cửa này đã cược; đây là số được
    # phép đặt THÊM cho mã hiện tại, không phải hạn mức tổng.
    allowed_budget = lim["max_xanh"] if side_norm == "XANH" else lim["max_do"]

    if amount > allowed_budget + 1e-6:
        return (
            False,
            f"❌ Vượt hạn mức cho phép của cửa {side_norm} ({do['name']})!\n"
            f"• Tiền thắng ròng cửa đối diện {opp_side} (sau 5% phế): <b>{fmt_num(opp_win_net_sum)}</b>\n"
            f"• Tổng đã cược cửa {side_norm}: <b>{fmt_num(own_side_stake_sum)}</b>\n"
            f"• Số tiền còn được phép cược thêm: <b>{fmt_num(allowed_budget)}</b>",
        )

    risk_active_dos = [
        d for d in db.get_open_dos()
        if d.get("status") in ("OPEN", "HOLD")
    ]
    other_dos_worst_loss = sum(
        max_worst_case_loss(user_id, d["name"])
        for d in risk_active_dos
        if d["name"] != do["name"]
    )
    current_do_worst_loss = max_worst_case_loss(user_id, do["name"], extra_bet={"side": side_norm, **calc})
    total_worst_loss = other_dos_worst_loss + current_do_worst_loss

    hard_risk_cap = max_capacity
    if total_worst_loss > hard_risk_cap + 1e-6:
        remain_risk = max(0.0, hard_risk_cap - other_dos_worst_loss)
        return (
            False,
            f"❌ Vượt trần rủi ro tối đa của tài khoản!\n"
            f"• Rủi ro sau mã này: <b>{fmt_num(total_worst_loss)}</b> / Trần tối đa: <b>{fmt_num(hard_risk_cap)}</b>\n"
            f"• Khả năng chịu rủi ro còn lại: <b>{fmt_num(remain_risk)}</b>",
        )

    seq = int(do["bet_seq"])
    code = f"#M{seq:02d}"
    price_str = f"{win_u:g}/{lose_u:g}"

    db.add_bet_record(
        code=code,
        do_name=do["name"],
        user_id=user_id,
        side=side_norm,
        amount=amount,
        win_net=calc["win_net"],
        lose_amount=calc["lose_amount"],
        price_str=price_str,
    )
    db.update_do(int(do["id"]), bet_seq=seq + 1)

    side_label = "🟢 XANH" if side_norm == "XANH" else "🔴 ĐỎ"
    msg = (
        f"✅ <b>ĐÃ NHẬN KÈO — {do['name']} | {side_label}</b>\n"
        f"• Mã: <b>{code}</b> | Giá lúc đặt: <b>{price_str}</b>\n"
        f"• Mức đánh: <b>{fmt_num(amount)}</b> (Thua mã này: <b>{fmt_num(calc['lose_amount'])}</b>)\n"
        f"• Nếu {side_label} thắng: lời <b>{fmt_num(calc['win_net'])}</b> <i>(đã trừ 5% phế)</i>\n"
        f"• Ví: <b>{fmt_num(bal)}</b> | Nợ: <b>{fmt_num(debt)}/{fmt_num(debt_limit)}</b>"
    )
    return True, msg


def apply_deposit_logic(target_uid: int, amount: float) -> dict:
    if not isfinite(float(amount)) or amount <= 0:
        raise ValueError("Số tiền nạp phải là số hữu hạn lớn hơn 0.")
    u = db.get_user(target_uid)
    new_balance = float(u["balance"]) + amount
    db.update_user_fields(target_uid, balance=new_balance)
    new_profit = db.add_realized_profit(amount)
    return {
        "user": db.get_user(target_uid),
        "new_balance": new_balance,
        "realized_profit": new_profit,
    }


def create_withdrawal_logic(
    user_id: int, amount: float, payout_text: str = "", qr_file_id: str = ""
) -> tuple[bool, str, dict | None]:
    if not isfinite(float(amount)) or amount <= 0:
        return False, "❌ Số tiền rút phải lớn hơn 0.", None

    u = db.get_user(user_id)
    bal = float(u["balance"])

    if amount < MIN_WITHDRAW_AMOUNT:
        return (
            False,
            f"❌ Số tiền rút tối thiểu là <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b>.",
            None,
        )
    if amount > bal + 1e-6:
        return (
            False,
            f"❌ Số tiền rút vượt quá số dư ví (Tối đa có thể rút: <b>{fmt_num(bal)}</b>).",
            None,
        )

    new_bal = bal - amount
    info_save = payout_text if payout_text else "[Ảnh mã QR]"
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
        f"✅ Đã tạo lệnh rút <b>{fmt_num(amount)}</b> (Mã rút: <code>#R{w_id:03d}</code>), đang chờ duyệt.",
        w_rec,
    )


def confirm_withdrawal_logic(w_id: int) -> tuple[bool, str, dict | None]:
    w = db.get_withdrawal(w_id)
    if not w:
        return False, "Không tìm thấy lệnh rút.", None
    if w["status"] != "PENDING":
        return False, f"Lệnh rút <code>#R{w_id:03d}</code> đã được xử lý trước đó ({w['status']}).", None

    amt = float(w["amount"])
    db.update_withdrawal(w_id, status="APPROVED")
    db.add_realized_profit(-amt)
    u = db.get_user(int(w["user_id"]))
    return True, f"✅ Đã xác nhận chuyển tiền lệnh <code>#R{w_id:03d}</code> (<b>{fmt_num(amt)}</b>).", {"withdrawal": w, "user": u}


def reject_withdrawal_logic(w_id: int) -> tuple[bool, str, dict | None]:
    w = db.get_withdrawal(w_id)
    if not w:
        return False, "Không tìm thấy lệnh rút.", None
    if w["status"] != "PENDING":
        return False, f"Lệnh rút <code>#R{w_id:03d}</code> đã xử lý trước đó.", None

    amt = float(w["amount"])
    uid = int(w["user_id"])
    u = db.get_user(uid)
    refunded_bal = float(u["balance"]) + amt

    db.update_withdrawal(w_id, status="REJECTED")
    db.update_user_fields(uid, balance=refunded_bal)
    return True, f"❌ Đã từ chối và hoàn lại <b>{fmt_num(amt)}</b> vào ví ID <code>{uid}</code>.", {"withdrawal": w, "balance": refunded_bal}


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
        f"<b>📌 BÁO CÁO ĂN THUA — {do_name} (KQ: {result})</b>",
        f"<i>Thời gian chốt: {time_str}</i>\n",
    ]
    if not do_user_net:
        lines.append("• <i>Không có mã cược ăn/thua ở độ này (hòa/hủy/không có kèo).</i>")
    else:
        lines.append("<b>Chi tiết từng khách ở độ này:</b>")
        for uid, net in do_user_net.items():
            u = db.get_user(uid)
            lines.append(
                f"• <b>{u['name']}</b> (<code>{uid}</code>): Khách {fmt_signed(net)} | "
                f"Ví: <b>{fmt_num(float(u['balance']))}</b> | Nợ: <b>{fmt_num(float(u['debt']))}</b>"
            )

    lines.append(f"\n• <b>Ăn thua độ này (Master/Cổ đông):</b> {fmt_signed(house_net)}")
    summary_text = "\n".join(lines)
    db.insert_settled_do(do_name, result, summary_text, day_key, time_str)
    return summary_text


def build_live_report_text() -> str:
    open_dos = [d for d in db.get_open_dos() if d.get("status") == "OPEN"]
    if not open_dos:
        return "<b>THỐNG KÊ CÁC ĐỘ ĐANG MỞ</b>\n\n<i>Hiện không có độ nào đang mở.</i>"

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
                f"  • <b>{u['name']}</b> (<code>{uid}</code>): XANH <b>{fmt_num(st['XANH'])}</b> | "
                f"ĐỎ <b>{fmt_num(st['DO'])}</b> ({int(st['count'])} mã)"
            )

        code_lines = []
        for b in active_bets:
            u = db.get_user(int(b["user_id"]))
            side_icon = "🟢 XANH" if b["side"] == "XANH" else "🔴 ĐỎ"
            code_lines.append(
                f"  • <code>{b['code']}</code> | <b>{u['name']}</b> | "
                f"{side_icon} <b>{fmt_num(float(b['amount']))}</b> (giá {b['price_str']})"
            )

        blocks.append(
            f"<b>📌 {do['name']}</b> (XANH <code>{float(do['xanh_win']):g}/{float(do['xanh_lose']):g}</code> | "
            f"ĐỎ <code>{float(do['do_win']):g}/{float(do['do_lose']):g}</code>)\n"
            f"• Tổng XANH: <b>{fmt_num(tot_xanh)}</b> | Tổng ĐỎ: <b>{fmt_num(tot_do)}</b>\n"
            + ("\n".join(user_lines) if user_lines else "  <i>Chưa có cược.</i>")
            + ("\n" + "\n".join(code_lines) if code_lines else "")
        )

    return "<b>THỐNG KÊ CÁC ĐỘ ĐANG MỞ</b>\n\n" + "\n\n".join(blocks)


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
        "<b>👑 BÁO CÁO MASTER — TÌNH HÌNH ĐỘ & DÒNG TIỀN</b>",
        f"<i>Cập nhật: {now_str}</i>\n",
    ]

    if settled_rows:
        lines.append("<b>Các độ đã chốt hôm nay:</b>")
        for r in settled_rows[-8:]:
            lines.append(f"• <b>{r['do_name']}</b> — KQ: <b>{r['result']}</b> (<code>{r['settled_at']}</code>)")
        lines.append("")

    lines.append("<b>Ăn thua lũy kế của khách:</b>")
    for u in users:
        lines.append(
            f"• <b>{u['name']}</b> (<code>{u['user_id']}</code>): {fmt_signed(float(u['pnl_net']))} | "
            f"Ví: <b>{fmt_num(float(u['balance']))}</b> | "
            f"Nợ: <b>{fmt_num(float(u['debt']))}/{fmt_num(float(u['debt_limit']))}</b>"
        )

    lines.append("\n<b>Tổng hợp Master:</b>")
    lines.append(f"• Ăn/thua ròng của khách: {fmt_signed(total_user_pnl)}")
    lines.append(f"• Ăn/thua ròng phía Master/Cổ đông: {fmt_signed(house_pnl)}")
    lines.append(f"• Lợi nhuận thực thu: {fmt_signed(realized)}")
    lines.append(f"• Công nợ treo cộng dồn: 🔴 <b>-{fmt_num(total_debt)}</b>")
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
        f"<b>📊 BÁO CÁO MASTER CẢ NGÀY ({target_day})</b>",
        f"<i>Cập nhật lúc: {now_str} | Số độ đã chốt: <b>{len(rows)}</b></i>\n",
    ]

    if rows:
        lines.append("<b>Danh sách các độ đã chốt trong ngày:</b>")
        for r in rows:
            lines.append(f"• <b>{r['do_name']}</b> — KQ: <b>{r['result']}</b> (<code>{r['settled_at']}</code>)")
        lines.append("")

    lines.append("<b>Tổng ăn thua cả ngày từng khách:</b>")
    for u in users:
        lines.append(
            f"• <b>{u['name']}</b> (<code>{u['user_id']}</code>): {fmt_signed(float(u['pnl_net']))} | "
            f"Ví: <b>{fmt_num(float(u['balance']))}</b> | "
            f"Nợ: <b>{fmt_num(float(u['debt']))}/{fmt_num(float(u['debt_limit']))}</b>"
        )

    lines.append("\n<b>Tổng kết cả ngày (Master):</b>")
    lines.append(f"• Ăn/thua ròng cả ngày (Master/Cổ đông): {fmt_signed(house_pnl)}")
    lines.append(f"• Lợi nhuận thực thu: {fmt_signed(realized)}")
    lines.append(f"• Công nợ treo cộng dồn: 🔴 <b>-{fmt_num(total_debt)}</b>")
    return "\n".join(lines)


def close_day_accounting_logic(day_key: str | None = None) -> str:
    target_day = day_key or datetime.now(VN_TZ).strftime("%Y-%m-%d")
    report_snapshot = build_daily_report_text(target_day)
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET pnl_net = 0.0")
        conn.commit()
    db.clear_settled_dos_by_day(target_day)
    return report_snapshot
