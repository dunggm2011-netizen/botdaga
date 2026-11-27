from datetime import datetime, timezone, timedelta
from math import isfinite
from config import FEE_RATE, MASTER_IDS, MIN_WITHDRAW_AMOUNT
import database as db

VN_TZ = timezone(timedelta(hours=7))


# ============================================================
# FORMAT HELPERS
# ============================================================

def fmt_num(val: float) -> str:
    return f"{val:,.0f}".replace(",", ".")


def fmt_signed(val: float) -> str:
    if val > 0:
        return f"\U0001f7e2 <b>+{fmt_num(val)}</b>"
    if val < 0:
        return f"\U0001f534 <b>-{fmt_num(abs(val))}</b>"
    return "\u26aa <b>0</b>"


# ============================================================
# FEE / PROFIT
# ============================================================

def calc_fee_and_net_profit(gross_profit: float) -> dict:
    """
    Khấu trừ 5% trên lợi nhuận trước khấu trừ.
    Thực nhận = lợi nhuận * 95%.
    """
    profit = max(0.0, float(gross_profit))
    fee_amount = profit * FEE_RATE
    net_profit = profit - fee_amount
    return {
        "gross_profit": profit,
        "fee_amount": fee_amount,
        "net_profit": net_profit,
    }


# ============================================================
# APPROVER
# ============================================================

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


# ============================================================
# PRICE PARSERS
# ============================================================

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


# ============================================================
# BET MATH
# ============================================================

def get_side_price(do: dict, side: str) -> tuple[float, float]:
    if side == "XANH":
        return float(do["xanh_win"]), float(do["xanh_lose"])
    return float(do["do_win"]), float(do["do_lose"])


def calc_bet_numbers(amount: float, win_u: float, lose_u: float) -> dict:
    """
    win_u/lose_u là tỷ lệ kèo kiểu "bỏ win_u ăn lose_u".
    Ví dụ 10/10: đặt 10 ăn 10. 5/10: đặt 5 ăn 10.
    """
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
    return max(max(0.0, -pnl_if_xanh), max(0.0, -pnl_if_do))


def _sum_bets(bets: list[dict]) -> dict:
    """Tổng hợp các trường quan trọng của list bets."""
    return {
        "xanh_win_net": sum(float(b["win_net"]) for b in bets if b["side"] == "XANH"),
        "xanh_lose": sum(float(b["lose_amount"]) for b in bets if b["side"] == "XANH"),
        "do_win_net": sum(float(b["win_net"]) for b in bets if b["side"] == "DO"),
        "do_lose": sum(float(b["lose_amount"]) for b in bets if b["side"] == "DO"),
        "principal": sum(float(b["amount"]) for b in bets),
    }


def _min_edge_across(bets: list[dict]) -> float:
    """MIN(lợi nhuận nếu XANH thắng, lợi nhuận nếu ĐỎ thắng) trên tập bets."""
    s = _sum_bets(bets)
    pnl_xanh = s["xanh_win_net"] - s["do_lose"]
    pnl_do = s["do_win_net"] - s["xanh_lose"]
    return min(pnl_xanh, pnl_do)


# ============================================================
# BETTING LIMIT — CÔNG THỨC CỐT LÕI
# ============================================================

def get_user_betting_limit_info(user_id: int, do_id: int | None = None) -> dict:
    """
    Hạn mức cược còn lại của khách:
      max_capacity    = balance + (debt_limit - debt)   [trần thanh toán]
      locked_capital  = max(0, -MIN(pnl_xanh, pnl_do)) trên TOÀN BỘ độ chưa chốt
      base_unused     = max(0, max_capacity - locked_capital)
      edge_bonus      = max(0, MIN(pnl_xanh, pnl_do))
      Hạn mức cửa A   = base_unused + (phần lời chắc chắn từ cửa đối diện của các độ khác)
                        nhưng KHÔNG vượt max_capacity - other_risk
    """
    u = db.get_user(user_id)
    open_dos = db.get_unsettled_dos()
    if do_id is not None:
        do = db.get_do_by_id(do_id)
    else:
        active = [d for d in open_dos if d.get("status") == "OPEN"]
        do = active[-1] if active else None

    bal = float(u["balance"])
    debt = float(u["debt"])
    debt_limit = float(u["debt_limit"])
    max_capacity = max(0.0, bal + max(0.0, debt_limit - debt))

    all_bets: list[dict] = []
    for d in open_dos:
        all_bets.extend(db.get_active_bets(do_name=d["name"], user_id=user_id))

    total_sum = _sum_bets(all_bets)
    pnl_xanh_all = total_sum["xanh_win_net"] - total_sum["do_lose"]
    pnl_do_all = total_sum["do_win_net"] - total_sum["xanh_lose"]
    min_edge_all = min(pnl_xanh_all, pnl_do_all)

    locked_capital = max(0.0, -min_edge_all)
    edge_bonus = max(0.0, min_edge_all)
    base_unused = max(0.0, max_capacity - locked_capital)

    if do:
        cur_bets = [b for b in all_bets if b["do_name"] == do["name"]]
        other_bets = [b for b in all_bets if b["do_name"] != do["name"]]

        cur_sum = _sum_bets(cur_bets)
        other_min_edge = _min_edge_across(other_bets)  # có thể âm
        other_risk = max(0.0, -other_min_edge)

        max_xanh = _solve_max_side_amount(
            side="XANH",
            cur_xanh_win_net=cur_sum["xanh_win_net"],
            cur_xanh_lose=cur_sum["xanh_lose"],
            cur_do_win_net=cur_sum["do_win_net"],
            cur_do_lose=cur_sum["do_lose"],
            other_risk=other_risk,
            max_capacity=max_capacity,
            base_unused=base_unused,
            do=do,
        )
        max_do = _solve_max_side_amount(
            side="DO",
            cur_xanh_win_net=cur_sum["xanh_win_net"],
            cur_xanh_lose=cur_sum["xanh_lose"],
            cur_do_win_net=cur_sum["do_win_net"],
            cur_do_lose=cur_sum["do_lose"],
            other_risk=other_risk,
            max_capacity=max_capacity,
            base_unused=base_unused,
            do=do,
        )
    else:
        max_xanh = base_unused + edge_bonus
        max_do = base_unused + edge_bonus

    return {
        "user": u,
        "do": do,
        "open_dos": open_dos,
        "all_open_bets": all_bets,
        "max_capacity": max_capacity,
        "total_open_principal": total_sum["principal"],
        "locked_capital": locked_capital,
        "base_unused": base_unused,
        "edge_bonus": edge_bonus,
        "unused_capital": base_unused,       # giữ key cũ cho main.py
        "xanh_opp_win": total_sum["do_win_net"],
        "do_opp_win": total_sum["xanh_win_net"],
        "max_xanh": max_xanh,
        "max_do": max_do,
        "xanh_total_cap": (cur_sum["xanh_lose"] if do else 0.0) + max_xanh,
        "do_total_cap": (cur_sum["do_lose"] if do else 0.0) + max_do,
        "xanh_placed": cur_sum["xanh_lose"] if do else 0.0,
        "do_placed": cur_sum["do_lose"] if do else 0.0,
    }


def _solve_max_side_amount(
    side: str,
    cur_xanh_win_net: float,
    cur_xanh_lose: float,
    cur_do_win_net: float,
    cur_do_lose: float,
    other_risk: float,
    max_capacity: float,
    base_unused: float,
    do: dict,
) -> float:
    """
    Số tiền tối đa đặt thêm vào `side` của độ hiện tại sao cho:
      other_risk + worst_case_loss_của_độ_này <= max_capacity
    và không vượt base_unused.
    """
    if base_unused <= 0.0:
        return 0.0

    remain_risk = max(0.0, max_capacity - other_risk)
    if remain_risk <= 0.0:
        return 0.0

    win_u, lose_u = get_side_price(do, side)
    unit = calc_bet_numbers(1.0, win_u, lose_u)
    u_win = float(unit["win_net"])
    u_lose = float(unit["lose_amount"])

    if side == "XANH":
        # Nếu ĐỎ thắng: lỗ = (cur_xanh_lose + u_lose*x) - cur_do_win_net
        cap_by_do = ((remain_risk - (cur_xanh_lose - cur_do_win_net)) / u_lose) if u_lose > 1e-12 else float("inf")
        # Nếu XANH thắng: lỗ = (cur_do_lose) - (cur_xanh_win_net + u_win*x)
        cur_loss_xanh = cur_do_lose - cur_xanh_win_net
        if cur_loss_xanh > remain_risk and u_win > 1e-12:
            cap_by_xanh = (cur_loss_xanh - remain_risk) / u_win
        else:
            cap_by_xanh = float("inf")
        return max(0.0, min(base_unused, cap_by_do, cap_by_xanh))

    # side == "DO"
    cap_by_xanh = ((remain_risk - (cur_do_lose - cur_xanh_win_net)) / u_lose) if u_lose > 1e-12 else float("inf")
    cur_loss_do = cur_xanh_lose - cur_do_win_net
    if cur_loss_do > remain_risk and u_win > 1e-12:
        cap_by_do = (cur_loss_do - remain_risk) / u_win
    else:
        cap_by_do = float("inf")
    return max(0.0, min(base_unused, cap_by_xanh, cap_by_do))


# ============================================================
# NOTICE
# ============================================================

def build_parallel_do_notice(do_name: str | None = None) -> str:
    head = f"<b>📢 THÔNG BÁO MỞ {do_name.upper()}</b>\n" if do_name else "<b>📢 THÔNG BÁO QUY TẮC ĐÁNH NHIỀU ĐỘ</b>\n"
    return (
        f"{head}"
        f"Độ tiếp theo khách muốn đánh sẽ chỉ đánh nếu tk gốc còn. "
        f"Khách muốn đánh bắt buộc phải nạp thêm tiền. "
        f"Tiền ăn thua độ trước sẽ được cộng dồn vào để chơi độ tiếp. "
        f"Và có thể cho rút bớt tiền lời từ tk gốc bất kì lúc nào."
    )


# ============================================================
# DO OPEN
# ============================================================

def can_open_do_by_time(now_dt: datetime | None = None) -> tuple[bool, str]:
    current = now_dt.astimezone(VN_TZ) if now_dt else datetime.now(VN_TZ)
    if current.hour < 7:
        return (
            False,
            f"\u23f0 Chưa tới giờ mở kỳ/độ! Hệ thống chỉ mở từ <b>07:00</b> sáng "
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
    existing = db.get_do_by_name(do_name) if hasattr(db, "get_do_by_name") else None
    if existing and existing["status"] == "CLOSED":
        return None
    if existing:
        db.update_do(
            int(existing["id"]),
            status="OPEN",
            xanh_win=xanh_win, xanh_lose=xanh_lose,
            do_win=do_win, do_lose=do_lose,
        )
        return db.get_do_by_id(int(existing["id"]))
    return db.create_new_do(name=do_name, xanh_win=xanh_win, xanh_lose=xanh_lose, do_win=do_win, do_lose=do_lose)


def open_specific_do_logic(
    raw_do: str | int,
    xanh_win: float = 10.0, xanh_lose: float = 10.0,
    do_win: float = 10.0, do_lose: float = 10.0,
    exclusive_active: bool = False,
) -> tuple[bool, str, dict | None]:
    ok_time, time_msg = can_open_do_by_time()
    if not ok_time:
        return False, time_msg, None

    do_name = normalize_do_name(raw_do)
    if not do_name or do_name == "Độ":
        return False, "❌ Số hoặc tên độ không hợp lệ.", None

    held_names: list[str] = []
    if exclusive_active:
        for d in db.get_open_dos():
            if d.get("status") == "OPEN" and d.get("name") != do_name:
                db.update_do(int(d["id"]), status="HOLD")
                held_names.append(str(d["name"]))

    do_rec = _get_or_create_do_record(do_name, xanh_win, xanh_lose, do_win, do_lose)
    if not do_rec:
        return False, f"❌ Không thể mở <b>{do_name}</b>. Độ đã chốt không thể mở lại.", None

    open_now = [str(d["name"]) for d in db.get_open_dos() if d.get("status") == "OPEN"]
    msg = (
        f"✅ <b>ĐÃ MỞ {do_name.upper()}</b> "
        f"(XANH <code>{xanh_win:g}/{xanh_lose:g}</code> | ĐỎ <code>{do_win:g}/{do_lose:g}</code>).\n"
        f"• Có thể đánh song song: <b>{', '.join(open_now)}</b>.\n"
        f"• Độ mới chỉ đánh trong phạm vi tiền gốc còn dư; muốn vượt mốc cần nạp thêm."
    )
    if held_names:
        msg += f"\n• Đã tạm hoãn: <b>{', '.join(held_names)}</b>."
    return True, msg, do_rec


# ============================================================
# PLACE BET
# ============================================================

def place_bet_logic(user_id: int, amount: float, side: str, do_id: int | None = None) -> tuple[bool, str]:
    side_norm = side.upper()
    if side_norm not in ("XANH", "DO"):
        return False, "Cửa đặt không hợp lệ."

    u = db.get_user(user_id)
    open_dos = db.get_unsettled_dos()
    do = db.get_do_by_id(do_id) if do_id else (open_dos[-1] if open_dos else None)

    if not do or do.get("status") != "OPEN":
        return False, "Độ này hiện đang tạm hoãn, khóa hoặc đã chốt."
    if not isfinite(float(amount)) or amount <= 0:
        return False, "Số tiền đặt phải hữu hạn và lớn hơn 0."

    lim = get_user_betting_limit_info(user_id, do_id=int(do["id"]))
    allowed_budget = lim["max_xanh"] if side_norm == "XANH" else lim["max_do"]

    if amount > allowed_budget + 1e-6:
        return (
            False,
            f"❌ Vượt hạn mức cược cửa {side_norm} ({do['name']})!\n"
            f"• Vốn gốc còn dư: <b>{fmt_num(lim['base_unused'])}</b>\n"
            f"• Lời chắc chắn từ các cửa khác: <b>{fmt_num(lim['edge_bonus'])}</b>\n"
            f"• Hạn mức tối đa mã này: <b>{fmt_num(allowed_budget)}</b>\n"
            f"• Muốn đánh vượt mốc, vui lòng nạp thêm tiền.",
        )

    win_u, lose_u = get_side_price(do, side_norm)
    calc = calc_bet_numbers(amount, win_u, lose_u)

    seq = int(do["bet_seq"])
    code = f"#M{seq:02d}"
    price_str = f"{win_u:g}/{lose_u:g}"

    db.add_bet_and_advance_seq(
        do_id=int(do["id"]),
        expected_seq=seq,
        code=code,
        do_name=do["name"],
        user_id=user_id,
        side=side_norm,
        amount=amount,
        win_net=calc["win_net"],
        lose_amount=calc["lose_amount"],
        price_str=price_str,
    )

    side_label = "\U0001f7e2 XANH" if side_norm == "XANH" else "\U0001f534 ĐỎ"
    msg = (
        f"\u2705 <b>ĐÃ NHẬN KÈO — {do['name']} | {side_label}</b>\n"
        f"\u2022 Mã: <b>{code}</b> | Giá lúc đặt: <b>{price_str}</b>\n"
        f"\u2022 Mức đánh: <b>{fmt_num(amount)}</b> (Thua mã này: <b>{fmt_num(calc['lose_amount'])}</b>)\n"
        f"\u2022 Nếu {side_label} thắng: lời <b>{fmt_num(calc['win_net'])}</b> <i>(đã trừ 5% phế)</i>\n"
        f"\u2022 Ví: <b>{fmt_num(float(u['balance']))}</b> | Nợ: <b>{fmt_num(float(u['debt']))}/{fmt_num(float(u['debt_limit']))}</b>"
    )
    return True, msg


# ============================================================
# DEPOSIT / WITHDRAW
# ============================================================

def apply_deposit_logic(target_uid: int, amount: float) -> dict:
    if not isfinite(float(amount)) or amount <= 0:
        raise ValueError("Số tiền nạp phải hữu hạn và lớn hơn 0.")
    u = db.get_user(target_uid)
    new_balance = float(u["balance"]) + amount
    db.update_user_fields(target_uid, balance=new_balance)
    return {
        "user": db.get_user(target_uid),
        "new_balance": new_balance,
        "realized_profit": db.get_realized_profit(),
    }


def create_withdrawal_logic(user_id: int, amount: float, payout_text: str = "", qr_file_id: str = "") -> tuple[bool, str, dict | None]:
    if not isfinite(float(amount)) or amount <= 0:
        return False, "\u274c Số tiền rút phải lớn hơn 0.", None
    u = db.get_user(user_id)
    bal = float(u["balance"])
    if amount < MIN_WITHDRAW_AMOUNT:
        return False, f"\u274c Số tiền rút tối thiểu là <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b>.", None
    if amount > bal + 1e-6:
        return False, f"\u274c Số tiền rút vượt quá số dư ví (Tối đa: <b>{fmt_num(bal)}</b>).", None

    new_bal = bal - amount
    info_save = payout_text if payout_text else "[Ảnh mã QR]"
    db.update_user_fields(user_id, balance=new_bal, last_payout_info=info_save)

    assigned_id = get_assigned_approver_id()
    w_id = db.create_withdrawal(
        user_id=user_id, amount=amount, payout_text=payout_text,
        qr_file_id=qr_file_id, assigned_staff_id=assigned_id,
        created_at=datetime.now(VN_TZ).strftime("%H:%M - %d/%m/%Y"),
    )
    w_rec = db.get_withdrawal(w_id)
    return True, f"\u2705 Đã tạo lệnh rút <b>{fmt_num(amount)}</b> (Mã <code>#R{w_id:03d}</code>), đang chờ duyệt.", w_rec


def confirm_withdrawal_logic(w_id: int) -> tuple[bool, str, dict | None]:
    w = db.get_withdrawal(w_id)
    if not w:
        return False, "Không tìm thấy lệnh rút.", None
    if w["status"] != "PENDING":
        return False, f"Lệnh rút <code>#R{w_id:03d}</code> đã xử lý ({w['status']}).", None

    amt = float(w["amount"])
    db.update_withdrawal(w_id, status="APPROVED")
    db.add_realized_profit(-amt)
    u = db.get_user(int(w["user_id"]))
    return True, f"\u2705 Đã xác nhận chuyển tiền lệnh <code>#R{w_id:03d}</code> (<b>{fmt_num(amt)}</b>).", {"withdrawal": w, "user": u}


def reject_withdrawal_logic(w_id: int) -> tuple[bool, str, dict | None]:
    w = db.get_withdrawal(w_id)
    if not w:
        return False, "Không tìm thấy lệnh rút.", None
    if w["status"] != "PENDING":
        return False, f"Lệnh rút <code>#R{w_id:03d}</code> đã xử lý.", None

    amt = float(w["amount"])
    uid = int(w["user_id"])
    u = db.get_user(uid)
    refunded_bal = float(u["balance"]) + amt

    db.update_withdrawal(w_id, status="REJECTED")
    db.update_user_fields(uid, balance=refunded_bal)
    return True, f"\u274c Đã từ chối và hoàn lại <b>{fmt_num(amt)}</b> vào ví ID <code>{uid}</code>.", {"withdrawal": w, "balance": refunded_bal}


# ============================================================
# SETTLE
# ============================================================

def settle_single_do_no_reopen(do: dict, result: str) -> dict[int, float]:
    """
    Chốt 1 độ. Trả về per_user_net (âm = lỗ, dương = lời thực nhận).
    CANCEL/DRAW: trả net = 0 cho mọi user có bet để report hiển thị.
    """
    do_name = do["name"]
    bets = db.get_active_bets(do_name=do_name)

    if result in ("CANCEL", "DRAW"):
        status_val = "CANCELLED" if result == "CANCEL" else "DRAW"
        db.update_bets_status_by_do(do_name, status_val)
        db.update_do(int(do["id"]), status="CLOSED")
        return {int(b["user_id"]): 0.0 for b in bets}

    per_user_net: dict[int, float] = {}
    total_fee = 0.0
    for b in bets:
        uid = int(b["user_id"])
        per_user_net.setdefault(uid, 0.0)
        if b["side"] == result:
            db.update_bet_status(int(b["id"]), "WON")
            per_user_net[uid] += float(b["win_net"])
            gross = float(b["win_net"]) / (1 - FEE_RATE) if FEE_RATE < 1 else float(b["win_net"])
            total_fee += gross - float(b["win_net"])
        else:
            db.update_bet_status(int(b["id"]), "LOST")
            per_user_net[uid] -= float(b["lose_amount"])

    if total_fee > 0:
        db.add_realized_profit(total_fee)

    db.update_do(int(do["id"]), status="CLOSED")
    return per_user_net


def settle_multiple_dos_logic(do_ids: list[int], result: str) -> dict:
    settled_names: list[str] = []
    combined_net: dict[int, float] = {}
    per_do_net: dict[str, dict[int, float]] = {}

    for d_id in do_ids:
        do = db.get_do_by_id(d_id)
        if not do or do.get("status") not in ("OPEN", "HOLD"):
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
            "user_id": uid, "name": u["name"],
            "net": net, "balance": bal, "debt": debt,
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
    """
    Ghi settled_dos và trả text báo cáo.
    Lưu ý: bets đã bị đổi status trước đó, nên query theo status mới.
    """
    now = datetime.now(VN_TZ)
    day_key = now.strftime("%Y-%m-%d")

    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM bets WHERE do_name = ? AND status IN ('WON','LOST','CANCELLED','DRAW') ORDER BY id ASC",
            (do_name,),
        ).fetchall()
        settled_bets = [dict(r) for r in rows]

    total_stake = sum(float(b["amount"]) for b in settled_bets)
    total_win_net = sum(float(b["win_net"]) for b in settled_bets if b["status"] == "WON")
    total_fee = 0.0
    for b in settled_bets:
        if b["status"] == "WON":
            gross = float(b["win_net"]) / (1 - FEE_RATE) if FEE_RATE < 1 else float(b["win_net"])
            total_fee += gross - float(b["win_net"])

    result_label = {
        "XANH": "🟢 XANH THẮNG",
        "DO": "🔴 ĐỎ THẮNG",
        "DRAW": "🤝 HÒA (hoàn tiền)",
        "CANCEL": "🚫 HỦY ĐỘ (hoàn tiền)",
    }.get(result, result)

    lines = [
        f"<b>🏁 KẾT TOÁN {do_name.upper()}</b>",
        f"• Kết quả: <b>{result_label}</b>",
        f"• Tổng tiền cược: <b>{fmt_num(total_stake)}</b>",
        f"• Tổng lời ròng trả khách: <b>{fmt_num(total_win_net)}</b>",
        f"• Tổng phế 5% thu: <b>{fmt_num(total_fee)}</b>",
        f"• Thời gian: <code>{now.strftime('%H:%M - %d/%m/%Y')}</code>",
        "",
        "<b>Chi tiết theo khách:</b>",
    ]
    if do_user_net:
        for uid, net in sorted(do_user_net.items(), key=lambda x: -abs(x[1])):
            u = db.get_user(uid)
            lines.append(
                f"  • {u['name']} (<code>{uid}</code>): {fmt_signed(net)} "
                f"| Ví: {fmt_num(float(u['balance']))}"
            )
    else:
        lines.append("  <i>Không có khách nào đặt cược.</i>")

    summary_text = "\n".join(lines)
    db.insert_settled_do(do_name, result, summary_text, day_key, now.strftime("%H:%M - %d/%m/%Y"))
    return summary_text


# ============================================================
# REPORTS (placeholder nếu main.py cần)
# ============================================================

def build_live_report_text() -> str:
    """Báo cáo độ đang chạy."""
    lines = ["<b>📋 ĐỘ ĐANG CHẠY</b>"]
    dos = db.get_unsettled_dos()
    if not dos:
        lines.append("<i>Không có độ nào đang mở.</i>")
    for d in dos:
        bets = db.get_active_bets(do_name=d["name"])
        s = _sum_bets(bets)
        pnl_xanh = s["xanh_win_net"] - s["do_lose"]
        pnl_do = s["do_win_net"] - s["xanh_lose"]
        lines.append(
            f"\n<b>{d['name']}</b> [{d['status']}] — "
            f"XANH <code>{float(d['xanh_win']):g}/{float(d['xanh_lose']):g}</code> | "
            f"ĐỎ <code>{float(d['do_win']):g}/{float(d['do_lose']):g}</code>\n"
            f"  • Tổng cược: {fmt_num(s['principal'])} | Số mã: {len(bets)}\n"
            f"  • Nếu XANH thắng (góc casino): {fmt_signed(-pnl_xanh)}\n"
            f"  • Nếu ĐỎ thắng (góc casino): {fmt_signed(-pnl_do)}"
        )
    return "\n".join(lines)


def build_pnl_report_text() -> str:
    """Ăn thua từng độ trong ngày + tổng."""
    now = datetime.now(VN_TZ)
    day_key = now.strftime("%Y-%m-%d")
    settled = db.get_settled_dos_by_day(day_key)
    lines = [f"<b>📈 ĂN THUA TỪNG ĐỘ — {now.strftime('%d/%m/%Y')}</b>"]
    if not settled:
        lines.append("<i>Chưa có độ nào chốt hôm nay.</i>")
    else:
        for s in settled:
            lines.append(f"\n<b>{s['do_name']}</b> — {s['result']}\n{s['summary_text']}")
    lines.append(f"\n💰 <b>Lợi nhuận thực tế (realized):</b> {fmt_num(db.get_realized_profit())}")
    return "\n".join(lines)


def build_daily_report_text() -> str:
    """Báo cáo tổng hợp cuối ngày."""
    now = datetime.now(VN_TZ)
    users = db.get_all_users()
    total_bal = sum(float(u["balance"]) for u in users)
    total_debt = sum(float(u["debt"]) for u in users)
    total_limit = sum(float(u["debt_limit"]) for u in users)
    open_dos = db.get_unsettled_dos()
    total_open_principal = 0.0
    for d in open_dos:
        total_open_principal += _sum_bets(db.get_active_bets(do_name=d["name"]))["principal"]

    return (
        f"<b>📊 BÁO CÁO CUỐI NGÀY — {now.strftime('%d/%m/%Y %H:%M')}</b>\n"
        f"• Tổng khách hàng: <b>{len(users)}</b>\n"
        f"• Độ đang chạy: <b>{len(open_dos)}</b>\n"
        f"• Tổng số dư khách: <b>{fmt_num(total_bal)}</b>\n"
        f"• Tổng nợ: <b>{fmt_num(total_debt)}</b> / Hạn mức: {fmt_num(total_limit)}\n"
        f"• Tổng tiền cược chưa chốt: <b>{fmt_num(total_open_principal)}</b>\n"
        f"• Lợi nhuận thực tế (realized): <b>{fmt_num(db.get_realized_profit())}</b>"
    )


def close_day_accounting_logic() -> str:
    """Chốt sổ cuối ngày — trả snapshot."""
    now = datetime.now(VN_TZ)
    snapshot = build_daily_report_text()
    db.clear_settled_dos_by_day(now.strftime("%Y-%m-%d"))
    return f"{snapshot}\n\n<i>Đã xóa settled_dos của ngày {now.strftime('%d/%m/%Y')}.</i>"
