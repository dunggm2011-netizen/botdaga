def place_bet_logic(user_id: int, amount: float, side: str, do_id: int | None = None) -> tuple[bool, str]:
    u = db.get_user(user_id)
    open_dos = db.get_open_dos()
    do = db.get_do_by_id(do_id) if do_id else open_dos[0]

    if not do or do["status"] != "OPEN":
        return False, "Độ đã chọn đang khóa hoặc đã chốt."
    if amount <= 0:
        return False, "Số tiền đặt phải lớn hơn 0."

    bal = float(u["balance"])
    debt = float(u["debt"])
    debt_limit = float(u["debt_limit"])
    remaining_debt_room = max(0.0, debt_limit - debt)
    max_capacity = bal + remaining_debt_room

    # Tổng tiền gốc đang dùng trên TẤT CẢ các độ đang mở (chưa chốt)
    all_open_bets: list[dict] = []
    for d in open_dos:
        all_open_bets.extend(db.get_active_bets(do_name=d["name"], user_id=user_id))

    total_open_principal = sum(float(b["amount"]) for b in all_open_bets)
    unused_capital = max(0.0, max_capacity - total_open_principal)

    # Kèo hiện tại của khách trên chính độ đang đặt
    existing_bets = [b for b in all_open_bets if b["do_name"] == do["name"]]
    opp_side = "XANH" if side == "DO" else "DO"
    same_principal = sum(float(b["amount"]) for b in existing_bets if b["side"] == side)
    opp_win_net = sum(float(b["win_net"]) for b in existing_bets if b["side"] == opp_side)

    win_u, lose_u = get_side_price(do, side)
    calc = calc_bet_numbers(amount, win_u, lose_u)

    # Đánh qua lại nhiều vòng trên độ hiện tại: dùng đầu lời ròng cửa đối diện (+ vốn gốc còn dư toàn cục)
    if opp_win_net > 0:
        allowed_budget = opp_win_net + unused_capital
        if amount > allowed_budget + 1e-6:
            return (
                False,
                f"❌ Vượt hạn mức đánh ngược cửa {side} ({do['name']})!\n"
                f"• Đầu lời ròng cửa {opp_side} tại {do['name']} (sau 5% phế): <b>{fmt_num(opp_win_net)}</b>\n"
                f"• Vốn gốc còn dư (tính trên mọi độ đang mở): <b>{fmt_num(unused_capital)}</b>\n"
                f"• Mức tối đa có thể đánh mã này: <b>{fmt_num(allowed_budget)}</b>",
            )
    else:
        # Không có đầu lời đối ứng ở độ này -> chỉ được dùng phần vốn gốc còn dư sau các độ đang mở
        if amount > unused_capital + 1e-6:
            return (
                False,
                f"❌ Không đủ vốn/hạn mức còn lại (đã dùng ở các độ đang mở)!\n"
                f"• Đã dùng tiền gốc các độ mở: <b>{fmt_num(total_open_principal)} / {fmt_num(max_capacity)}</b>\n"
                f"• Số tiền còn có thể đánh thêm: <b>{fmt_num(unused_capital)}</b>\n"
                f"• Đã đánh cửa {side} ở {do['name']}: <b>{fmt_num(same_principal)}</b>",
            )

    # Tổng lỗ xấu nhất cộng dồn trên TẤT CẢ các độ đang mở (chưa chốt)
    other_dos_worst_loss = sum(
        max_worst_case_loss(user_id, d["name"])
        for d in open_dos
        if d["name"] != do["name"]
    )
    current_do_worst_loss = max_worst_case_loss(user_id, do["name"], extra_bet={"side": side, **calc})
    total_worst_loss = other_dos_worst_loss + current_do_worst_loss

    allowed_risk_cap = max_capacity + (opp_win_net if opp_win_net > 0 else 0.0)
    if total_worst_loss > allowed_risk_cap + 1e-6:
        remain_risk = max(0.0, allowed_risk_cap - other_dos_worst_loss)
        return (
            False,
            f"❌ Vượt khả năng chi trả còn lại sau khi tính các độ đang mở!\n"
            f"• Rủi ro đang treo ở các độ khác: <b>{fmt_num(other_dos_worst_loss)}</b>\n"
            f"• Rủi ro sau mã này: <b>{fmt_num(total_worst_loss)}</b> / Trần cho phép: <b>{fmt_num(allowed_risk_cap)}</b>\n"
            f"• Khả năng chịu rủi ro còn lại: <b>{fmt_num(remain_risk)}</b>",
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
    db.update_do(int(do["id"]), bet_seq=seq + 1)

    side_label = "🟢 XANH" if side == "XANH" else "🔴 ĐỎ"
    msg = (
        f"✅ <b>ĐÃ NHẬN KÈO — {do['name']} | {side_label}</b>\n"
        f"• Mã: <b>{code}</b> | Giá lúc đặt: <b>{price_str}</b>\n"
        f"• Mức đánh: <b>{fmt_num(amount)}</b> (Thua mã này: <b>{fmt_num(calc['lose_amount'])}</b>)\n"
        f"• Nếu {side_label} thắng: lời <b>{fmt_num(calc['win_net'])}</b> <i>(đã trừ 5% phế)</i>\n"
        f"• Ví: <b>{fmt_num(bal)}</b> | Nợ: <b>{fmt_num(debt)}/{fmt_num(debt_limit)}</b>"
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


def create_withdrawal_logic(
    user_id: int, amount: float, payout_text: str = "", qr_file_id: str = ""
) -> tuple[bool, str, dict | None]:
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
        created_at=datetime.now().strftime("%H:%M - %d/%m/%Y"),
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
        if not do or do["status"] != "OPEN":
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
    now = datetime.now()
    day_key = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M - %d/%m/%Y")
    house_net = -sum(do_user_net.values())

    lines = [
        f"<b>📌 BÁO CÁO ĂN THUA — {do_name} (KQ: {result})</b>",
        f"<i>Thời gian chốt: {time_str}</i>\n",
    ]
    if not do_user_net:
        lines.append("• <i>Không có mã cược ăn/thua ở độ này.</i>")
    else:
        lines.append("<b>Chi tiết từng khách ở độ này:</b>")
        for uid, net in do_user_net.items():
            u = db.get_user(uid)
            lines.append(
                f"• <b>{u['name']}</b> (<code>{uid}</code>): Khách {fmt_signed(net)} | "
                f"Ví: <b>{fmt_num(float(u['balance']))}</b> | Nợ: <b>{fmt_num(float(u['debt']))}</b>"
            )

    lines.append(f"\n• <b>Ăn thua độ này (phía Master/Cổ đông):</b> {fmt_signed(house_net)}")
    summary_text = "\n".join(lines)
    db.insert_settled_do(do_name, result, summary_text, day_key, time_str)
    return summary_text


def build_live_report_text() -> str:
    open_dos = db.get_open_dos()
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
    now_str = datetime.now().strftime("%H:%M - %d/%m/%Y")
    day_key = datetime.now().strftime("%Y-%m-%d")
    settled_rows = db.get_settled_dos_by_day(day_key)
    users = db.get_all_users()
    realized = db.get_realized_profit()
    total_debt = sum(float(u["debt"]) for u in users)

    lines = [f"<b>BÁO CÁO ĂN THUA TỪNG ĐỘ & TỚI HIỆN TẠI ({now_str})</b>\n"]

    if settled_rows:
        lines.append("<b>Các độ đã chốt hôm nay:</b>")
        for r in settled_rows[-5:]:
            lines.append(f"• <b>{r['do_name']}</b> — KQ: <b>{r['result']}</b> (<code>{r['settled_at']}</code>)")
        lines.append("")

    lines.append("<b>Ăn thua lũy kế của khách:</b>")
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


def build_daily_report_text(day_key: str | None = None) -> str:
    target_day = day_key or datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%H:%M - %d/%m/%Y")
    rows = db.get_settled_dos_by_day(target_day)
    users = db.get_all_users()
    realized = db.get_realized_profit()
    total_debt = sum(float(u["debt"]) for u in users)
    total_user_pnl = sum(float(u["pnl_net"]) for u in users)
    house_pnl = -total_user_pnl

    lines = [
        f"<b>📊 BÁO CÁO ĂN THUA CẢ NGÀY ({target_day})</b>",
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

    lines.append("\n<b>Tổng kết cả ngày:</b>")
    lines.append(f"• Ăn/thua ròng cả ngày (Master/Cổ đông): {fmt_signed(house_pnl)}")
    lines.append(f"• Lợi nhuận thực thu: {fmt_signed(realized)}")
    lines.append(f"• Công nợ treo cộng dồn: 🔴 <b>-{fmt_num(total_debt)}</b>")
    return "\n".join(lines)


def close_day_accounting_logic(day_key: str | None = None) -> str:
    target_day = day_key or datetime.now().strftime("%Y-%m-%d")
    report_snapshot = build_daily_report_text(target_day)
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET pnl_net = 0.0")
        conn.commit()
    db.clear_settled_dos_by_day(target_day)
    return report_snapshot
