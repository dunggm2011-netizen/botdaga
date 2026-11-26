from datetime import datetime, timezone, timedelta
from math import isfinite
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
    Nguyên tắc tiền biện (phế 5%):
    - Bên thắng: khấu trừ 5% trên phần tiền thắng.
    - Bên thua: thua đủ. Không tính tiền biện trên tiền vốn.
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


def get_do_pnl_scenarios(user_id: int, do_name: str, extra_bet: dict | None = None) -> dict:
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
    worst_loss = max(0.0, -pnl_if_xanh, -pnl_if_do)
    guaranteed_min_profit = max(0.0, min(pnl_if_xanh, pnl_if_do))

    return {
        "xanh_win_net": xanh_win_net,
        "xanh_lose": xanh_lose,
        "do_win_net": do_win_net,
        "do_lose": do_lose,
        "pnl_if_xanh": pnl_if_xanh,
        "pnl_if_do": pnl_if_do,
        "worst_loss": worst_loss,
        "guaranteed_min_profit": guaranteed_min_profit,
    }


def max_worst_case_loss(user_id: int, do_name: str, extra_bet: dict | None = None) -> float:
    return get_do_pnl_scenarios(user_id, do_name, extra_bet=extra_bet)["worst_loss"]


def total_unsettled_worst_case_loss(
    user_id: int,
    open_dos: list[dict],
    target_do_name: str | None = None,
    extra_bet: dict | None = None,
) -> float:
    total = 0.0
    for d in open_dos:
        d_name = d["name"]
        if target_do_name and d_name == target_do_name:
            total += max_worst_case_loss(user_id, d_name, extra_bet=extra_bet)
        else:
            total += max_worst_case_loss(user_id, d_name)
    return total


def _max_side_amount_within_capacity(
    user_id: int,
    do: dict,
    side: str,
    open_dos: list[dict],
    base_upper: float,
    max_capacity: float,
) -> float:
    """
    Siết mức đặt tối đa cho `side` trên `do`:
    1) Không vượt `base_upper`.
    2) Tổng rủi ro xấu nhất của toàn bộ các độ chưa quyết toán sau khi thêm mã
       không vượt `max_capacity`.
    """
    if base_upper <= 0.0 or max_capacity <= 0.0:
        return 0.0

    other_risk = sum(
        max_worst_case_loss(user_id, d["name"])
        for d in open_dos
        if d["id"] != do["id"]
    )
    if other_risk > max_capacity + 1e-6:
        return 0.0

    win_u, lose_u = get_side_price(do, side)

    def _within_cap(amt: float) -> bool:
        calc = calc_bet_numbers(amt, win_u, lose_u)
        cur_loss = max_worst_case_loss(
            user_id, do["name"], extra_bet={"side": side, **calc}
        )
        return (other_risk + cur_loss) <= (max_capacity + 1e-6)

    if _within_cap(base_upper):
        return base_upper

    lo, hi = 0.0, base_upper
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if _within_cap(mid):
            lo = mid
        else:
            hi = mid
    return max(0.0, lo)


def _calc_cycle_limit_for_side(
    side: str,
    do: dict,
    existing_bets: list[dict],
    remaining_capital: float,
    other_guaranteed_min: float,
) -> float:
    """
    - Nếu độ chưa có mã nào (mã đầu tiên): dựa trên tiền gốc/khả dụng của tài khoản + đầu lời chắc chắn độ khác.
    - Từ mã thứ 2 trở đi: lấy vốn gốc còn dư + tiền ăn được của mã đối diện (đã trừ 5% phế)
      - 5% phế trên phần ăn vượt/chênh lệch của mã trước đó để không bị tính lố khi xoay vòng nhiều mã.
    """
    opp_side = "DO" if side == "XANH" else "XANH"
    opp_win_net_sum = sum(float(b["win_net"]) for b in existing_bets if b["side"] == opp_side)
    opp_lose_sum = sum(float(b["lose_amount"]) for b in existing_bets if b["side"] == opp_side)

    same_win_net_sum = sum(float(b["win_net"]) for b in existing_bets if b["side"] == side)
    same_lose_sum = sum(float(b["lose_amount"]) for b in existing_bets if b["side"] == side)

    # Mã đầu tiên của độ
    if not existing_bets:
        return max(0.0, remaining_capital + other_guaranteed_min)

    # Từ mã thứ 2 trở đi: tiền ăn được của mã đối diện đã trừ 5% phế + vốn gốc còn dư
    # Trừ đi phần 5% của mã trước (nếu có lời ròng vượt mức đang đánh cùng cửa) để giữ biên an toàn
    win_u, lose_u = get_side_price(do, side)
    pay_ratio = (lose_u / win_u) if win_u > 0 else 1.0
    net_factor = (1.0 - FEE_RATE) * pay_ratio

    # Phần vốn gốc thực sự còn lại sau khi đã trừ đi phần gốc đã mất của cửa đối diện và cửa hiện tại
    # nhưng được cộng phần đầu lời ròng cửa đối diện (đã trừ 5% phế)
    raw_pool = remaining_capital + other_guaranteed_min + opp_win_net_sum

    # Nếu cửa đối diện đã đặt nhiều hơn cửa hiện tại, phần chênh lệch lời ròng trừ thêm 5% phế của vòng trước
    prev_last_bet = existing_bets[-1]
    prev_fee_deduction = 0.0
    if prev_last_bet["side"] == side:
        prev_fee_deduction = float(prev_last_bet["win_net"]) * FEE_RATE

    adjusted_pool = max(0.0, raw_pool - prev_fee_deduction)
    if net_factor <= 1e-9:
        return adjusted_pool
    return max(0.0, adjusted_pool)


def get_user_betting_limit_info(user_id: int, do_id: int | None = None) -> dict:
    """
    - Mã đầu tiên: dựa trên hạn mức tiền tài khoản khả dụng.
    - Từ mã thứ 2 trở đi: tính theo tiền gốc tài khoản + tiền ăn được của mã đối diện (đã trừ 5% phế)
      và khấu trừ phần 5% của mã trước, luôn bị siết bởi trần rủi ro xấu nhất <= max_capacity.
    """
    u = db.get_user(user_id)
    open_dos = [d for d in db.get_unsettled_dos() if d.get("status") != "CLOSED"]
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
    xanh_win_net_sum = sum(float(b["win_net"]) for b in existing_bets if b["side"] == "XANH")
    do_win_net_sum = sum(float(b["win_net"]) for b in existing_bets if b["side"] == "DO")
    xanh_lose_sum = sum(float(b["lose_amount"]) for b in existing_bets if b["side"] == "XANH")
    do_lose_sum = sum(float(b["lose_amount"]) for b in existing_bets if b["side"] == "DO")

    total_worst_now = total_unsettled_worst_case_loss(user_id, open_dos)
    other_risk = sum(
        max_worst_case_loss(user_id, d["name"])
        for d in open_dos
        if not do or d["id"] != do["id"]
    )
    cur_do_worst = max_worst_case_loss(user_id, do["name"]) if do else 0.0

    remaining_capital = max(0.0, max_capacity - total_worst_now)
    other_guaranteed_min = sum(
        get_do_pnl_scenarios(user_id, d["name"])["guaranteed_min_profit"]
        for d in open_dos
        if not do or d["id"] != do["id"]
    )

    if do:
        raw_max_xanh = _calc_cycle_limit_for_side(
            "XANH", do, existing_bets, remaining_capital, other_guaranteed_min
        )
        raw_max_do = _calc_cycle_limit_for_side(
            "DO", do, existing_bets, remaining_capital, other_guaranteed_min
        )
        max_xanh = _max_side_amount_within_capacity(
            user_id, do, "XANH", open_dos, raw_max_xanh, max_capacity
        )
        max_do = _max_side_amount_within_capacity(
            user_id, do, "DO", open_dos, raw_max_do, max_capacity
        )
    else:
        max_xanh = max(0.0, remaining_capital + other_guaranteed_min)
        max_do = max(0.0, remaining_capital + other_guaranteed_min)

    cur_sc = get_do_pnl_scenarios(user_id, do["name"]) if do else {
        "pnl_if_xanh": 0.0,
        "pnl_if_do": 0.0,
        "guaranteed_min_profit": 0.0,
    }

    return {
        "user": u,
        "do": do,
        "open_dos": open_dos,
        "all_open_bets": all_open_bets,
        "max_capacity": max_capacity,
        "total_open_principal": sum(float(b["amount"]) for b in all_open_bets),
        "locked_capital": other_risk + cur_do_worst,
        "unused_capital": remaining_capital,
        "other_guaranteed_min": other_guaranteed_min,
        "xanh_opp_win": do_win_net_sum,
        "do_opp_win": xanh_win_net_sum,
        "max_xanh": max_xanh,
        "max_do": max_do,
        "xanh_placed": xanh_lose_sum,
        "do_placed": do_lose_sum,
        "pnl_if_xanh": cur_sc["pnl_if_xanh"],
        "pnl_if_do": cur_sc["pnl_if_do"],
    }


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
    existing = db.get_do_by_name(do_name)
    if existing and existing["status"] == "CLOSED":
        return None
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

    new_id = db.create_do(
        name=do_name,
        xanh_win=xanh_win,
        xanh_lose=xanh_lose,
        do_win=do_win,
        do_lose=do_lose,
        status="OPEN",
    )
    return db.get_do_by_id(int(new_id))


def open_specific_do_logic(
    raw_do: str | int,
    xanh_win: float = 10.0,
    xanh_lose: float = 10.0,
    do_win: float = 10.0,
    do_lose: float = 10.0,
    exclusive_active: bool = False,
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
        return False, f"❌ Không thể mở <b>{do_name}</b>. Độ đã quyết toán không thể mở lại; hãy dùng tên độ mới.", None

    open_now = [str(d["name"]) for d in db.get_open_dos() if d.get("status") == "OPEN"]
    msg = (
        f"✅ <b>ĐÃ MỞ {do_name.upper()}</b> "
        f"(🟢 XANH <code>{xanh_win:g}/{xanh_lose:g}</code> | 🔴 ĐỎ <code>{do_win:g}/{do_lose:g}</code>).\n"
        f"• Các độ đang mở song song: <b>{', '.join(open_now)}</b>.\n"
        f"• Độ tiếp theo chỉ đánh trong phạm vi vốn gốc còn dư + đầu lời hợp lệ; hết vốn vui lòng nạp thêm."
    )
    if held_names:
        msg += f"\n• Đã chuyển sang tạm dừng: <b>{', '.join(held_names)}</b>."

    return True, msg, do_rec


def place_bet_logic(
    user_id: int,
    amount: float,
    side: str,
    do_id: int | None = None,
    staff_id: int = 0,
) -> tuple[bool, str]:
    side_norm = side.upper()
    if side_norm not in ("XANH", "DO"):
        return False, "Cửa đặt không hợp lệ."

    u = db.get_user(user_id)
    open_dos = [d for d in db.get_unsettled_dos() if d.get("status") != "CLOSED"]
    if do_id:
        do = db.get_do_by_id(do_id)
    else:
        active_open = [d for d in open_dos if d.get("status") == "OPEN"]
        do = active_open[-1] if active_open else None

    if not do or do.get("status") != "OPEN":
        return False, "Độ này hiện đang tạm dừng, chờ kết quả hoặc đã quyết toán."
    if not isfinite(float(amount)) or amount <= 0:
        return False, "Số tiền đặt phải hữu hạn và lớn hơn 0."

    bal = float(u["balance"])
    debt = float(u["debt"])
    debt_limit = float(u["debt_limit"])

    lim = get_user_betting_limit_info(user_id, do_id=int(do["id"]))
    max_capacity = lim["max_capacity"]
    unused_capital = lim["unused_capital"]

    existing_bets = db.get_active_bets(do_name=do["name"], user_id=user_id)
    opp_side = "XANH" if side_norm == "DO" else "DO"
    opp_win_net_sum = sum(float(b["win_net"]) for b in existing_bets if b["side"] == opp_side)

    win_u, lose_u = get_side_price(do, side_norm)
    calc = calc_bet_numbers(amount, win_u, lose_u)

    allowed_budget = lim["max_xanh"] if side_norm == "XANH" else lim["max_do"]
    if amount > allowed_budget + 1e-6:
        return (
            False,
            f"❌ Vượt hạn mức cược cửa {side_norm} ({do['name']})!\n"
            f"• Tiền gốc khả dụng còn dư: <b>{fmt_num(unused_capital)}</b>\n"
            f"• Lời ròng cửa {opp_side} (sau 5% biện): <b>{fmt_num(opp_win_net_sum)}</b>\n"
            f"• Hạn mức tối đa có thể đánh mã này: <b>{fmt_num(allowed_budget)}</b>\n"
            f"• Muốn đánh vượt mốc, khách vui lòng nạp thêm tiền vào tài khoản.",
        )

    other_dos_worst_loss = sum(
        max_worst_case_loss(user_id, d["name"])
        for d in open_dos
        if d["id"] != do["id"]
    )
    current_do_worst_loss = max_worst_case_loss(user_id, do["name"], extra_bet={"side": side_norm, **calc})
    total_worst_loss = other_dos_worst_loss + current_do_worst_loss

    if total_worst_loss > max_capacity + 1e-6:
        remain_risk = max(0.0, max_capacity - other_dos_worst_loss)
        return (
            False,
            f"❌ Vượt trần kiểm soát rủi ro của tài khoản!\n"
            f"• Rủi ro sau mã này: <b>{fmt_num(total_worst_loss)}</b> / Trần tối đa: <b>{fmt_num(max_capacity)}</b>\n"
            f"• Khả năng chịu rủi ro còn lại: <b>{fmt_num(remain_risk)}</b>",
        )

    seq = int(do["bet_seq"])
    code = f"#M{seq:02d}"
    price_str = f"{win_u:g}/{lose_u:g}"
    now_str = datetime.now(VN_TZ).strftime("%H:%M:%S - %d/%m/%Y")

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
        created_at=now_str,
        staff_id=staff_id,
    )

    lim_after = get_user_betting_limit_info(user_id, do_id=int(do["id"]))
    side_label = "\U0001f7e2 XANH" if side_norm == "XANH" else "\U0001f534 ĐỎ"
    msg = (
        f"\u2705 <b>ĐÃ GHI NHẬN GIAO DỊCH — {do['name']} | {side_label}</b>\n"
        f"\u2022 Mã GD: <b>{code}</b> | Giá lúc ghi nhận: <b>{price_str}</b>\n"
        f"\u2022 Số tiền đánh: <b>{fmt_num(amount)}</b> (Thua mã này: <b>{fmt_num(calc['lose_amount'])}</b>)\n"
        f"\u2022 Nếu {side_label} thắng: lời ròng <b>{fmt_num(calc['win_net'])}</b> <i>(đã trừ 5% tiền biện)</i>\n"
        f"\u2022 Hạn mức còn lại ({do['name']}): 🟢 XANH <b>{fmt_num(lim_after['max_xanh'])}</b> | 🔴 ĐỎ <b>{fmt_num(lim_after['max_do'])}</b>\n"
        f"\u2022 Số dư thực tế: <b>{fmt_num(bal)}</b> | Nợ: <b>{fmt_num(debt)}/{fmt_num(debt_limit)}</b>"
    )
    return True, msg


def apply_deposit_logic(target_uid: int, amount: float, staff_id: int = 0) -> dict:
    if not isfinite(float(amount)) or amount <= 0:
        raise ValueError("Số tiền nạp phải hữu hạn và lớn hơn 0.")
    u = db.get_user(target_uid)
    bal_before = float(u["balance"])
    new_balance = bal_before + amount
    db.update_user_fields(target_uid, balance=new_balance)

    now_dt = datetime.now(VN_TZ)
    db.log_wallet_transaction(
        tx_type="DEPOSIT",
        user_id=target_uid,
        amount=amount,
        balance_before=bal_before,
        balance_after=new_balance,
        staff_id=staff_id,
        note="Nạp tiền vào tài khoản",
        day_key=now_dt.strftime("%Y-%m-%d"),
        created_at=now_dt.strftime("%H:%M:%S - %d/%m/%Y"),
    )

    return {
        "user": db.get_user(target_uid),
        "balance_before": bal_before,
        "new_balance": new_balance,
        "realized_profit": db.get_realized_profit(),
    }


def create_withdrawal_logic(
    user_id: int, amount: float, payout_text: str = "", qr_file_id: str = ""
) -> tuple[bool, str, dict | None]:
    if not isfinite(float(amount)) or amount <= 0:
        return False, "\u274c Số tiền rút phải lớn hơn 0.", None
    u = db.get_user(user_id)
    bal = float(u["balance"])

    lim = get_user_betting_limit_info(user_id)
    withdrawable_max = min(bal, lim["unused_capital"])

    if amount < MIN_WITHDRAW_AMOUNT:
        return (
            False,
            f"\u274c Số tiền rút tối thiểu là <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b>.",
            None,
        )
    if amount > withdrawable_max + 1e-6:
        return (
            False,
            f"\u274c Số tiền rút vượt quá số dư khả dụng (Tối đa rút được: <b>{fmt_num(withdrawable_max)}</b>; "
            f"đang khóa rủi ro: <b>{fmt_num(lim['locked_capital'])}</b>).",
            None,
        )

    new_bal = bal - amount
    info_save = payout_text if payout_text else "[Ảnh mã QR]"
    db.update_user_fields(user_id, balance=new_bal, last_payout_info=info_save)

    now_dt = datetime.now(VN_TZ)
    now_str = now_dt.strftime("%H:%M - %d/%m/%Y")
    assigned_id = get_assigned_approver_id()
    w_id = db.create_withdrawal(
        user_id=user_id,
        amount=amount,
        payout_text=payout_text,
        qr_file_id=qr_file_id,
        assigned_staff_id=assigned_id,
        created_at=now_str,
    )
    db.log_wallet_transaction(
        tx_type="WITHDRAW_REQUEST",
        user_id=user_id,
        amount=-amount,
        balance_before=bal,
        balance_after=new_bal,
        staff_id=0,
        ref_code=f"#R{w_id:03d}",
        note="Tạo yêu cầu rút tiền",
        day_key=now_dt.strftime("%Y-%m-%d"),
        created_at=now_dt.strftime("%H:%M:%S - %d/%m/%Y"),
    )
    w_rec = db.get_withdrawal(w_id)
    return (
        True,
        f"\u2705 Đã tạo lệnh rút <b>{fmt_num(amount)}</b> (Mã rút: <code>#R{w_id:03d}</code>), đang chờ duyệt.",
        w_rec,
    )


def confirm_withdrawal_logic(w_id: int, staff_id: int = 0) -> tuple[bool, str, dict | None]:
    w = db.get_withdrawal(w_id)
    if not w:
        return False, "Không tìm thấy lệnh rút.", None
    if w["status"] != "PENDING":
        return False, f"Lệnh rút <code>#R{w_id:03d}</code> đã được xử lý trước đó ({w['status']}).", None

    amt = float(w["amount"])
    uid = int(w["user_id"])
    u = db.get_user(uid)
    db.update_withdrawal(w_id, status="APPROVED", approved_by=staff_id)

    now_dt = datetime.now(VN_TZ)
    db.log_wallet_transaction(
        tx_type="WITHDRAW_APPROVED",
        user_id=uid,
        amount=0.0,
        balance_before=float(u["balance"]),
        balance_after=float(u["balance"]),
        staff_id=staff_id,
        ref_code=f"#R{w_id:03d}",
        note=f"Xác nhận chi trả lệnh rút {fmt_num(amt)}",
        day_key=now_dt.strftime("%Y-%m-%d"),
        created_at=now_dt.strftime("%H:%M:%S - %d/%m/%Y"),
    )
    return True, f"\u2705 Đã xác nhận chuyển tiền lệnh <code>#R{w_id:03d}</code> (<b>{fmt_num(amt)}</b>).", {"withdrawal": w, "user": u}


def reject_withdrawal_logic(w_id: int, staff_id: int = 0) -> tuple[bool, str, dict | None]:
    w = db.get_withdrawal(w_id)
    if not w:
        return False, "Không tìm thấy lệnh rút.", None
    if w["status"] != "PENDING":
        return False, f"Lệnh rút <code>#R{w_id:03d}</code> đã xử lý trước đó.", None

    amt = float(w["amount"])
    uid = int(w["user_id"])
    u = db.get_user(uid)
    bal_before = float(u["balance"])
    refunded_bal = bal_before + amt

    db.update_withdrawal(w_id, status="REJECTED", approved_by=staff_id)
    db.update_user_fields(uid, balance=refunded_bal)

    now_dt = datetime.now(VN_TZ)
    db.log_wallet_transaction(
        tx_type="WITHDRAW_REJECTED",
        user_id=uid,
        amount=amt,
        balance_before=bal_before,
        balance_after=refunded_bal,
        staff_id=staff_id,
        ref_code=f"#R{w_id:03d}",
        note="Từ chối rút tiền - hoàn số dư",
        day_key=now_dt.strftime("%Y-%m-%d"),
        created_at=now_dt.strftime("%H:%M:%S - %d/%m/%Y"),
    )
    return True, f"\u274c Đã từ chối và hoàn lại <b>{fmt_num(amt)}</b> vào ví ID <code>{uid}</code>.", {"withdrawal": w, "balance": refunded_bal}


def settle_single_do_no_reopen(do: dict, result: str) -> tuple[dict[int, float], float]:
    do_name = do["name"]
    bets = db.get_active_bets(do_name=do_name)

    if result in ("CANCEL", "DRAW"):
        status_val = "CANCELLED" if result == "CANCEL" else "DRAW"
        db.update_bets_status_by_do(do_name, status_val)
        db.update_do(int(do["id"]), status="CLOSED")
        return {}, 0.0

    per_user_net: dict[int, float] = {}
    total_fee_5pct = 0.0
    for b in bets:
        uid = int(b["user_id"])
        per_user_net.setdefault(uid, 0.0)
        if b["side"] == result:
            db.update_bet_status(int(b["id"]), "WON")
            win_net = float(b["win_net"])
            fee_b = win_net * (FEE_RATE / (1.0 - FEE_RATE)) if FEE_RATE < 1.0 else 0.0
            total_fee_5pct += fee_b
            per_user_net[uid] += win_net
        else:
            db.update_bet_status(int(b["id"]), "LOST")
            per_user_net[uid] -= float(b["lose_amount"])

    db.update_do(int(do["id"]), status="CLOSED")
    return per_user_net, total_fee_5pct


def settle_multiple_dos_logic(do_ids: list[int], result: str, staff_id: int = 0) -> dict:
    settled_names: list[str] = []
    combined_net: dict[int, float] = {}
    per_do_net: dict[str, dict[int, float]] = {}
    per_do_fee: dict[str, float] = {}

    for d_id in do_ids:
        do = db.get_do_by_id(d_id)
        if not do or do.get("status") == "CLOSED":
            continue
        d_name = do["name"]
        settled_names.append(d_name)
        part_net, fee_5 = settle_single_do_no_reopen(do, result)
        per_do_net[d_name] = part_net
        per_do_fee[d_name] = fee_5
        for uid, val in part_net.items():
            combined_net[uid] = combined_net.get(uid, 0.0) + val

    now_dt = datetime.now(VN_TZ)
    day_key = now_dt.strftime("%Y-%m-%d")
    now_str = now_dt.strftime("%H:%M:%S - %d/%m/%Y")

    users_summary = []
    for uid, net in combined_net.items():
        u = db.get_user(uid)
        bal_before = float(u["balance"])
        bal = bal_before
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
        db.log_wallet_transaction(
            tx_type="SETTLEMENT",
            user_id=uid,
            amount=net,
            balance_before=bal_before,
            balance_after=bal,
            staff_id=staff_id,
            ref_code=",".join(settled_names),
            note=f"Quyết toán {', '.join(settled_names)} ({result})",
            day_key=day_key,
            created_at=now_str,
        )
        users_summary.append({
            "user_id": uid,
            "name": u["name"],
            "net": net,
            "balance": bal,
            "debt": debt,
        })

    house_delta = -sum(combined_net.values())
    if abs(house_delta) > 1e-9:
        db.add_realized_profit(house_delta)

    open_after = db.get_unsettled_dos()
    return {
        "do_names": settled_names,
        "result": result,
        "users": users_summary,
        "per_do_net": per_do_net,
        "per_do_fee": per_do_fee,
        "open_dos": [d["name"] for d in open_after],
    }


def record_and_format_per_do_report(
    do_name: str,
    result: str,
    do_user_net: dict[int, float],
    total_fee_5pct: float = 0.0,
) -> str:
    now = datetime.now(VN_TZ)
    day_key = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M - %d/%m/%Y")
    house_net = -sum(do_user_net.values())

    lines = [
        f"<b>\U0001f4cc BÁO CÁO QUYẾT TOÁN & ĐỐI SOÁT — {do_name} (KQ: {result})</b>",
        f"<i>Thời gian quyết toán: {time_str}</i>\n",
    ]
    if not do_user_net:
        lines.append("\u2022 <i>Không có mã cược ăn/thua ở độ này (hòa/hủy/không có kèo).</i>")
    else:
        lines.append("<b>Đối soát từng khách hàng:</b>")
        for uid, net in do_user_net.items():
            u = db.get_user(uid)
            lines.append(
                f"\u2022 <b>{u['name']}</b> (<code>{uid}</code>): Khách {fmt_signed(net)} | "
                f"Số dư sau: <b>{fmt_num(float(u['balance']))}</b> | Nợ: <b>{fmt_num(float(u['debt']))}</b>"
            )

    lines.append(f"\n\u2022 <b>Tiền biện 5% độ này:</b> <b>{fmt_num(total_fee_5pct)}</b>")
    lines.append(f"\u2022 <b>Lãi/Lỗ ròng Casino độ này:</b> {fmt_signed(house_net)}")
    summary_text = "\n".join(lines)
    db.insert_settled_do(
        do_name,
        result,
        summary_text,
        day_key,
        time_str,
        total_fee_5pct=total_fee_5pct,
        house_net=house_net,
    )
    return summary_text


def build_live_report_text() -> str:
    open_dos = [d for d in db.get_unsettled_dos() if d.get("status") != "CLOSED"]
    if not open_dos:
        return "<b>THỐNG KÊ CÁC ĐỘ ĐANG CHẠY</b>\n\n<i>Hiện không có độ nào đang mở.</i>"

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
                f"  \u2022 <b>{u['name']}</b> (<code>{uid}</code>): 🟢 XANH <b>{fmt_num(st['XANH'])}</b> | "
                f"🔴 ĐỎ <b>{fmt_num(st['DO'])}</b> ({int(st['count'])} mã)"
            )

        code_lines = []
        for b in active_bets:
            u = db.get_user(int(b["user_id"]))
            side_icon = "\U0001f7e2 XANH" if b["side"] == "XANH" else "\U0001f534 ĐỎ"
            t_str = f" | {b['created_at']}" if b.get("created_at") else ""
            code_lines.append(
                f"  \u2022 <code>{b['code']}</code> | <b>{u['name']}</b> | "
                f"{side_icon} <b>{fmt_num(float(b['amount']))}</b> (giá {b['price_str']}{t_str})"
            )

        status_badge = f" [{do['status']}]" if do.get("status") != "OPEN" else ""
        blocks.append(
            f"<b>\U0001f4cc {do['name']}{status_badge}</b> (🟢 XANH <code>{float(do['xanh_win']):g}/{float(do['xanh_lose']):g}</code> | "
            f"🔴 ĐỎ <code>{float(do['do_win']):g}/{float(do['do_lose']):g}</code>)\n"
            f"\u2022 Tổng XANH: <b>{fmt_num(tot_xanh)}</b> | Tổng ĐỎ: <b>{fmt_num(tot_do)}</b>\n"
            + ("\n".join(user_lines) if user_lines else "  <i>Chưa có cược.</i>")
            + ("\n" + "\n".join(code_lines) if code_lines else "")
        )

    return "<b>THỐNG KÊ CÁC ĐỘ ĐANG CHẠY</b>\n\n" + "\n\n".join(blocks)


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
    total_fee = sum(float(r.get("total_fee_5pct") or 0.0) for r in settled_rows)

    lines = [
        "<b>\U0001f451 BÁO CÁO CHỦ BOT — TÌNH HÌNH ĐỘ & DÒNG TIỀN</b>",
        f"<i>Cập nhật: {now_str}</i>\n",
    ]

    if settled_rows:
        lines.append("<b>Các độ đã quyết toán hôm nay:</b>")
        for r in settled_rows[-8:]:
            lines.append(
                f"\u2022 <b>{r['do_name']}</b> — KQ: <b>{r['result']}</b> | "
                f"Phế 5%: <b>{fmt_num(float(r.get('total_fee_5pct') or 0.0))}</b> (<code>{r['settled_at']}</code>)"
            )
        lines.append("")

    lines.append("<b>Số dư & ăn thua lũy kế từng khách:</b>")
    for u in users:
        lim = get_user_betting_limit_info(int(u["user_id"]))
        lines.append(
            f"\u2022 <b>{u['name']}</b> (<code>{u['user_id']}</code>): {fmt_signed(float(u['pnl_net']))} | "
            f"Số dư: <b>{fmt_num(float(u['balance']))}</b> (Khả dụng: <b>{fmt_num(lim['unused_capital'])}</b>) | "
            f"Nợ: <b>{fmt_num(float(u['debt']))}/{fmt_num(float(u['debt_limit']))}</b>"
        )

    lines.append("\n<b>Tổng hợp Casino:</b>")
    lines.append(f"\u2022 Tổng tiền biện 5% hôm nay: <b>{fmt_num(total_fee)}</b>")
    lines.append(f"\u2022 Ăn/thua ròng phía Casino hôm nay: {fmt_signed(house_pnl)}")
    lines.append(f"\u2022 Lợi nhuận thực tế Casino: {fmt_signed(realized)}")
    lines.append(f"\u2022 Công nợ treo cộng dồn: \U0001f534 <b>-{fmt_num(total_debt)}</b>")
    return "\n".join(lines)


def build_daily_report_text(day_key: str | None = None) -> str:
    now_dt = datetime.now(VN_TZ)
    target_day = day_key or now_dt.strftime("%Y-%m-%d")
    now_str = now_dt.strftime("%H:%M - %d/%m/%Y")

    rows = db.get_settled_dos_by_day(target_day)
    txs = db.get_wallet_transactions_by_day(target_day)
    users = db.get_all_users()
    unsettled_dos = [d for d in db.get_unsettled_dos() if d.get("status") != "CLOSED"]

    total_deposit = sum(float(t["amount"]) for t in txs if t["tx_type"] == "DEPOSIT")
    total_withdraw = sum(
        abs(float(t["amount"]))
        for t in txs
        if t["tx_type"] == "WITHDRAW_REQUEST"
    ) - sum(
        float(t["amount"])
        for t in txs
        if t["tx_type"] == "WITHDRAW_REJECTED"
    )
    total_withdraw = max(0.0, total_withdraw)

    total_user_pnl = sum(float(u["pnl_net"]) for u in users)
    house_pnl = -total_user_pnl
    total_fee_5pct = sum(float(r.get("total_fee_5pct") or 0.0) for r in rows)
    total_user_balance = sum(float(u["balance"]) for u in users)
    total_pending_withdraw = db.get_pending_withdrawals_total()
    total_debt = sum(float(u["debt"]) for u in users)
    realized = db.get_realized_profit()

    unsettled_principal = 0.0
    for d in unsettled_dos:
        unsettled_principal += sum(float(b["amount"]) for b in db.get_active_bets(do_name=d["name"]))

    expected_user_balance = total_deposit - total_withdraw + total_user_pnl - total_pending_withdraw
    diff_reconcile = 0.0 if len(txs) == 0 else (total_user_balance - expected_user_balance)

    lines = [
        f"<b>\U0001f4ca BÁO CÁO TỔNG HỢP CUỐI NGÀY ({target_day})</b>",
        f"<i>Thời điểm lập báo cáo: {now_str}</i>\n",
        f"1. <b>Tổng số khách hàng:</b> <b>{len(users)}</b> tài khoản",
        f"2. <b>Tổng số độ gà đã quyết toán trong ngày:</b> <b>{len(rows)}</b> độ",
        f"3. <b>Tổng tiền khách nạp trong ngày:</b> <b>{fmt_num(total_deposit)}</b> <i>(không tính vào doanh thu)</i>",
        f"4. <b>Tổng tiền khách rút trong ngày:</b> <b>{fmt_num(total_withdraw)}</b> (Đang chờ chi: <b>{fmt_num(total_pending_withdraw)}</b>)",
        f"5. <b>Tổng thắng/thua ròng (Casino):</b> {fmt_signed(house_pnl)} | Khách: {fmt_signed(total_user_pnl)}",
        f"6. <b>Tổng tiền biện 5%:</b> <b>{fmt_num(total_fee_5pct)}</b>",
        f"7. <b>Tổng số dư thực tế khách hàng:</b> <b>{fmt_num(total_user_balance)}</b> | Công nợ treo: <b>{fmt_num(total_debt)}</b>",
        f"8. <b>Tổng tiền chưa quyết toán ({len(unsettled_dos)} độ đang chạy):</b> <b>{fmt_num(unsettled_principal)}</b>",
        f"9. <b>Lợi nhuận thực tế của Casino:</b> {fmt_signed(realized)}",
        f"10. <b>Chênh lệch đối soát:</b> {fmt_signed(diff_reconcile)}\n",
    ]

    if rows:
        lines.append("<b>Chi tiết các độ đã quyết toán:</b>")
        for r in rows:
            lines.append(
                f"\u2022 <b>{r['do_name']}</b> — KQ: <b>{r['result']}</b> | "
                f"Phế 5%: <b>{fmt_num(float(r.get('total_fee_5pct') or 0.0))}</b> (<code>{r['settled_at']}</code>)"
            )
        lines.append("")

    lines.append("<b>Chi tiết từng khách hàng:</b>")
    for u in users:
        lines.append(
            f"\u2022 <b>{u['name']}</b> (<code>{u['user_id']}</code>): {fmt_signed(float(u['pnl_net']))} | "
            f"Số dư: <b>{fmt_num(float(u['balance']))}</b> | "
            f"Nợ: <b>{fmt_num(float(u['debt']))}/{fmt_num(float(u['debt_limit']))}</b>"
        )

    return "\n".join(lines)


def close_day_accounting_logic(day_key: str | None = None) -> str:
    target_day = day_key or datetime.now(VN_TZ).strftime("%Y-%m-%d")
    report_snapshot = build_daily_report_text(target_day)
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET pnl_net = 0.0")
        conn.commit()
    db.clear_settled_dos_by_day(target_day)
    return report_snapshot
