from datetime import datetime
from config import FEE_RATE, MASTER_IDS
import database as db


def fmt_num(val: float) -> str:
    return f"{val:,.0f}".replace(",", ".")


def fmt_signed(val: float) -> str:
    if val > 0:
        return f"🟢 <b>+{fmt_num(val)}</b>"
    if val < 0:
        return f"🔴 <b>-{fmt_num(abs(val))}</b>"
    return "⚪ <b>0</b>"


def get_assigned_approver_id() -> int:
    raw = db.get_setting("assigned_approver_id", "0")
    return int(raw) if raw.isdigit() else 0


def set_assigned_approver_id(staff_id: int) -> None:
    db.set_setting("assigned_approver_id", str(staff_id))


def can_approve_item(actor_id: int, specific_assigned_id: int | None = None) -> tuple[bool, str]:
    """
    Master luôn có quyền duyệt.
    Nếu có nhân viên chỉ định cụ thể trên phiếu hoặc mặc định: chỉ người đó (hoặc Master) mới được duyệt.
    """
    if actor_id in MASTER_IDS:
        return True, ""

    target_id = specific_assigned_id if (specific_assigned_id and specific_assigned_id > 0) else get_assigned_approver_id()
    if target_id > 0 and actor_id != target_id:
        return False, f"Yêu cầu này đang chỉ định cho nhân viên ID <code>{target_id}</code> duyệt."
    return True, ""


def get_side_price(do: dict, side: str) -> tuple[float, float]:
    if side == "XANH":
        return float(do["xanh_win"]), float(do["xanh_lose"])
    return float(do["do_win"]), float(do["do_lose"])


def calc_bet_numbers(amount: float, win_u: float, lose_u: float) -> dict:
    win_u = win_u if win_u > 0 else 10.0
    lose_u = lose_u if lose_u > 0 else 10.0
    win_gross = amount
    lose_amount = amount * (lose_u / win_u)
    win_net = win_gross * (1.0 - FEE_RATE)  # Trừ 5% phế trên phần tiền lời
    return {
        "amount": amount,
        "win_net": win_net,
        "lose_amount": lose_amount,
    }


def max_worst_case_loss(user_id: int, do_name: str, extra_bet: dict | None = None) -> float:
    bets = db.get_active_bets(do_name=do_name, user_id=user_id)

    xanh_win_net = sum(b["win_net"] for b in bets if b["side"] == "XANH")
    xanh_lose = sum(b["lose_amount"] for b in bets if b["side"] == "XANH")

    do_win_net = sum(b["win_net"] for b in bets if b["side"] == "DO")
    do_lose = sum(b["lose_amount"] for b in bets if b["side"] == "DO")

    if extra_bet:
        if extra_bet["side"] == "XANH":
            xanh_win_net += extra_bet["win_net"]
            xanh_lose += extra_bet["lose_amount"]
        else:
            do_win_net += extra_bet["win_net"]
            do_lose += extra_bet["lose_amount"]

    pnl_if_xanh = xanh_win_net - do_lose
    pnl_if_do = do_win_net - xanh_lose

    loss_if_xanh = max(0.0, -pnl_if_xanh)
    loss_if_do = max(0.0, -pnl_if_do)
    return max(loss_if_xanh, loss_if_do)


def place_bet_logic(user_id: int, amount: float, side: str) -> tuple[bool, str]:
    u = db.get_user(user_id)
    do = db.get_current_do()

    if do["status"] != "OPEN":
        return False, "Độ hiện tại đang khóa hoặc đã chốt."
    if amount <= 0:
        return False, "Số tiền đặt phải lớn hơn 0."

    win_u, lose_u = get_side_price(do, side)
    calc = calc_bet_numbers(amount, win_u, lose_u)
    worst_loss = max_worst_case_loss(user_id, do["name"], extra_bet={"side": side, **calc})

    remaining_debt_room = max(0.0, float(u["debt_limit"]) - float(u["debt"]))
    max_capacity = float(u["balance"]) + remaining_debt_room

    if worst_loss > max_capacity + 1e-6:
        return (
            False,
            f"Vượt khả năng chi trả/hạn mức!\n"
            f"• Rủi ro tối đa sau mã này: <b>{fmt_num(worst_loss)}</b>\n"
            f"• Khả dụng (Ví + Hạn mức nợ còn): <b>{fmt_num(max_capacity)}</b>",
        )

    seq = int(do["bet_seq"])
    code = f"#M{seq:02d}"
    price_str = f"{win_u:g}/{lose_u:g}"

    db.add_bet_record(
        code=code,
        do_name=do["name"],
        user_id=user_id,
        side=side,
        amount=amount,
        win_net=calc["win_net"],
        lose_amount=calc["lose_amount"],
        price_str=price_str,
    )
    db.update_current_do(bet_seq=seq + 1)

    side_label = "🟢 XANH" if side == "XANH" else "🔴 ĐỎ"
    msg = (
        f"✅ <b>ĐÃ NHẬN KÈO — {do['name']} | {side_label}</b>\n"
        f"• Mã: <b>{code}</b> | Giá lúc đặt: <b>{price_str}</b>\n"
        f"• Mức đánh: <b>{fmt_num(amount)}</b> (Thua mã này: <b>{fmt_num(calc['lose_amount'])}</b>)\n"
        f"• Nếu {side_label} thắng: lời <b>{fmt_num(calc['win_net'])}</b> <i>(đã trừ 5% phế)</i>\n"
        f"• Ví: <b>{fmt_num(u['balance'])}</b> | Nợ: <b>{fmt_num(u['debt'])}/{fmt_num(u['debt_limit'])}</b>"
    )
    return True, msg


def apply_deposit_logic(target_uid: int, amount: float) -> dict:
    u = db.get_user(target_uid)
    new_balance = float(u["balance"]) + amount
    db.update_user_fields(target_uid, balance=new_balance)
    new_profit = db.add_realized_profit(amount)
    return {
        "user": db.get_user(target_uid),
        "new_balance": new_balance,
        "realized_profit": new_profit,
    }


def create_withdrawal_logic(user_id: int, amount: float, payout_text: str = "", qr_file_id: str = "") -> tuple[bool, str, dict | None]:
    u = db.get_user(user_id)
    bal = float(u["balance"])

    if amount <= 0:
        return False, "Số tiền rút phải lớn hơn 0.", None
    if amount > bal + 1e-6:
        return False, f"Số dư ví không đủ. Số dư hiện tại của bạn là <b>{fmt_num(bal)}</b>.", None

    # Tạm giữ số dư ngay khi tạo lệnh để tránh rút trùng vượt số dư
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
        created_at=datetime.now().strftime("%H:%M - %d/%m/%Y"),
    )
    w_rec = db.get_withdrawal(w_id)
    return True, f"✅ Đã tạo lệnh rút <b>{fmt_num(amount)}</b> (Mã rút: <code>#R{w_id:03d}</code>), đang chờ duyệt.", w_rec


def confirm_withdrawal_logic(w_id: int) -> tuple[bool, str, dict | None]:
    w = db.get_withdrawal(w_id)
    if not w:
        return False, "Không tìm thấy lệnh rút.", None
    if w["status"] != "PENDING":
        return False, f"Lệnh rút <code>#R{w_id:03d}</code> đã được xử lý trước đó ({w['status']}).", None

    amt = float(w["amount"])
    db.update_withdrawal(w_id, status="APPROVED")
    # Chi tiền mặt ra cho khách: trừ vào lợi nhuận thực thu
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


def settle_do_logic(result: str) -> dict:
    do = db.get_current_do()
    do_name = do["name"]
    bets = db.get_active_bets(do_name=do_name)

    if result == "CANCEL":
        db.update_bets_status_by_do(do_name, "CANCELLED")
        next_num = int("".join(ch for ch in do_name if ch.isdigit()) or "1") + 1
        db.update_current_do(name=f"Độ {next_num}", status="OPEN", bet_seq=1)
        return {"mode": "CANCEL", "do_name": do_name, "next_do": f"Độ {next_num}"}

    if result == "DRAW":
        db.update_bets_status_by_do(do_name, "DRAW")
        next_num = int("".join(ch for ch in do_name if ch.isdigit()) or "1") + 1
        db.update_current_do(name=f"Độ {next_num}", status="OPEN", bet_seq=1)
        return {"mode": "DRAW", "do_name": do_name, "next_do": f"Độ {next_num}"}

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

    settled_users = []
    for uid, net in per_user_net.items():
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
        settled_users.append({
            "user_id": uid,
            "name": u["name"],
            "net": net,
            "balance": bal,
            "debt": debt,
        })

    next_num = int("".join(ch for ch in do_name if ch.isdigit()) or "1") + 1
    db.update_current_do(name=f"Độ {next_num}", status="OPEN", bet_seq=1)

    return {
        "mode": "SETTLED",
        "do_name": do_name,
        "result": result,
        "users": settled_users,
        "next_do": f"Độ {next_num}",
    }


def build_live_report_text() -> str:
    do = db.get_current_do()
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
            f"• <b>{u['name']}</b> (<code>{uid}</code>): XANH <b>{fmt_num(st['XANH'])}</b> | "
            f"ĐỎ <b>{fmt_num(st['DO'])}</b> ({int(st['count'])} mã)"
        )

    code_lines = []
    for b in active_bets:
        u = db.get_user(int(b["user_id"]))
        side_icon = "🟢 XANH" if b["side"] == "XANH" else "🔴 ĐỎ"
        code_lines.append(
            f"• <code>{b['code']}</code> | <b>{u['name']}</b> | "
            f"{side_icon} <b>{fmt_num(float(b['amount']))}</b> (giá {b['price_str']})"
        )

    return (
        f"<b>THỐNG KÊ {do['name']} ĐANG DIỄN RA</b>\n"
        f"• Tổng XANH: <b>{fmt_num(tot_xanh)}</b> | Tổng ĐỎ: <b>{fmt_num(tot_do)}</b>\n\n"
        f"<b>Theo khách:</b>\n" + ("\n".join(user_lines) or "<i>Chưa có cược.</i>") + "\n\n"
        f"<b>Mã cược đang diễn ra:</b>\n" + ("\n".join(code_lines) or "<i>Không có mã nào.</i>")
    )


def build_pnl_report_text() -> str:
    now_str = datetime.now().strftime("%H:%M - %d/%m/%Y")
    users = db.get_all_users()
    realized = db.get_realized_profit()
    total_debt = sum(float(u["debt"]) for u in users)

    lines = [f"<b>BÁO CÁO ĂN THUA (TÍNH TỚI {now_str})</b>\n", "<b>Tình hình ăn thua của khách:</b>"]
    for u in users:
        lines.append(
            f"• <b>{u['name']}</b> (<code>{u['user_id']}</code>): {fmt_signed(float(u['pnl_net']))} | "
            f"Ví: <b>{fmt_num(float(u['balance']))}</b> | "
            f"Nợ: <b>{fmt_num(float(u['debt']))}/{fmt_num(float(u['debt_limit']))}</b>"
        )

    lines.append("\n<b>Tổng hợp dòng tiền & công nợ:</b>")
    lines.append(f"• Lợi nhuận thực thu: {fmt_signed(realized)}")
    lines.append(f"• Công nợ treo cộng dồn: 🔴 <b>-{fmt_num(total_debt)}</b>")
    return "\n".join(lines)
