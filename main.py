import threading
from datetime import datetime
from urllib.parse import quote
from flask import Flask
import telebot
from telebot import types

from config import (
    BOT_TOKEN,
    MASTER_GROUP_ID,
    PRICE_GROUP_ID,
    BILL_GROUP_ID,
    MAIN_CHANNEL_ID,
    ADMIN_IDS,
    MASTER_IDS,
    BANK_INFO,
    QUICK_DEPOSIT_AMOUNTS,
    QR_AMOUNT_MULTIPLIER,
    QUICK_BET_AMOUNTS,
    QUICK_WITHDRAW_AMOUNTS,
    PORT,
)
import database as db
from logic import (
    fmt_num,
    fmt_signed,
    get_assigned_approver_id,
    set_assigned_approver_id,
    can_approve_item,
    parse_price_input,
    place_bet_logic,
    apply_deposit_logic,
    create_withdrawal_logic,
    confirm_withdrawal_logic,
    reject_withdrawal_logic,
    settle_multiple_dos_logic,
    record_and_format_per_do_report,
    build_live_report_text,
    build_pnl_report_text,
    build_daily_report_text,
    close_day_accounting_logic,
)

db.init_db()
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
PENDING_STATE: dict[int, dict] = {}

app = Flask(__name__)


@app.route("/")
def health_check():
    return "Bot Ga Don is running", 200


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_master(user_id: int) -> bool:
    return user_id in MASTER_IDS


def is_master_group(chat_id: int) -> bool:
    return bool(MASTER_GROUP_ID and chat_id == MASTER_GROUP_ID)


def can_view_master_report(user_id: int, chat_id: int) -> bool:
    return is_master(user_id) or is_master_group(chat_id)


def build_vietqr_url(user_id: int, amount: float) -> str:
    bank_bin = BANK_INFO.get("bank_bin", "970407")
    stk = BANK_INFO["stk"]
    owner = quote(BANK_INFO["owner"])
    add_info = quote(str(user_id))
    vnd_amount = max(0, int(round(amount * QR_AMOUNT_MULTIPLIER)))
    return (
        f"https://img.vietqr.io/image/{bank_bin}-{stk}-compact2.png"
        f"?amount={vnd_amount}&addInfo={add_info}&accountName={owner}"
    )


# ==================== BỘ NÚT BẤM ====================
def kb_user_main(user_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("💳 Nạp tiền", callback_data="u_deposit"),
        types.InlineKeyboardButton("💸 Rút tiền", callback_data="u_withdraw"),
    )
    kb.add(
        types.InlineKeyboardButton("🟢 Đánh XANH", callback_data="u_side_XANH"),
        types.InlineKeyboardButton("🔴 Đánh ĐỎ", callback_data="u_side_DO"),
    )
    kb.add(types.InlineKeyboardButton("💰 Số dư & Mã đang đánh", callback_data="u_wallet"))
    if is_admin(user_id):
        kb.add(types.InlineKeyboardButton("🛠️ Menu Admin (Chat riêng)", callback_data="a_panel_open"))
    return kb


def kb_user_do_picker(side: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    for d in db.get_open_dos():
        d_id = int(d["id"])
        price_lbl = f"{float(d['xanh_win']):g}/{float(d['xanh_lose']):g}" if side == "XANH" else f"{float(d['do_win']):g}/{float(d['do_lose']):g}"
        kb.add(types.InlineKeyboardButton(f"{d['name']} ({price_lbl})", callback_data=f"u_pickdo_{side}_{d_id}"))
    kb.add(types.InlineKeyboardButton("🔙 Quay lại", callback_data="u_back"))
    return kb


def kb_deposit_amounts() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=3)
    btns = [
        types.InlineKeyboardButton(fmt_num(amt), callback_data=f"u_dep_amt_{amt}")
        for amt in QUICK_DEPOSIT_AMOUNTS
    ]
    kb.add(*btns)
    kb.add(
        types.InlineKeyboardButton("✏️ Nhập số khác", callback_data="u_dep_custom"),
        types.InlineKeyboardButton("🔙 Quay lại", callback_data="u_back"),
    )
    return kb


def kb_user_deposit() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Tôi đã chuyển", callback_data="u_paid"),
        types.InlineKeyboardButton("🔙 Quay lại", callback_data="u_back"),
    )
    return kb


def kb_user_amounts(side: str, do_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    btns = [
        types.InlineKeyboardButton(fmt_num(amt), callback_data=f"u_bet_{side}_{do_id}_{amt}")
        for amt in QUICK_BET_AMOUNTS
    ]
    kb.add(*btns)
    kb.add(
        types.InlineKeyboardButton("✏️ Nhập số khác", callback_data=f"u_custom_{side}_{do_id}"),
        types.InlineKeyboardButton("🔙 Quay lại", callback_data="u_back"),
    )
    return kb


def kb_withdraw_amounts() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=3)
    btns = [
        types.InlineKeyboardButton(fmt_num(amt), callback_data=f"u_wd_amt_{amt}")
        for amt in QUICK_WITHDRAW_AMOUNTS
    ]
    kb.add(*btns)
    kb.add(
        types.InlineKeyboardButton("✏️ Nhập số rút khác", callback_data="u_wd_custom"),
        types.InlineKeyboardButton("🔙 Quay lại", callback_data="u_back"),
    )
    return kb


def kb_master_viewer_panel() -> types.InlineKeyboardMarkup:
    """Dành cho cổ đông / thành viên xem báo cáo trực tiếp ngay trong nhóm Master."""
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📋 Độ đang chạy", callback_data="a_rep_live"),
        types.InlineKeyboardButton("📈 Ăn thua từng độ / HT", callback_data="a_rep_pnl"),
    )
    kb.add(types.InlineKeyboardButton("📊 Báo cáo ăn thua cả ngày", callback_data="a_rep_daily"))
    return kb


def kb_master_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📋 Độ đang chạy", callback_data="a_rep_live"),
        types.InlineKeyboardButton("📈 Ăn thua từng độ / HT", callback_data="a_rep_pnl"),
    )
    kb.add(types.InlineKeyboardButton("📊 Báo cáo ăn thua cả ngày", callback_data="a_rep_daily"))
    kb.add(
        types.InlineKeyboardButton("💳 Cấp hạn mức nợ", callback_data="a_set_debt_limit"),
        types.InlineKeyboardButton("💵 Thu nợ / Xóa nợ", callback_data="a_debt_menu"),
    )
    kb.add(
        types.InlineKeyboardButton("👤 Chỉ định duyệt", callback_data="a_assign_approver"),
        types.InlineKeyboardButton("🌙 Chốt sổ cuối ngày", callback_data="a_day_reset"),
    )
    return kb


def kb_price_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ Mở độ mới", callback_data="a_new_do"),
        types.InlineKeyboardButton("✏️ Đổi giá độ", callback_data="a_set_price"),
    )
    kb.add(types.InlineKeyboardButton("🏁 Chốt độ / Nhiều độ", callback_data="a_multi_close_open"))
    return kb


def kb_bill_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("👤 Chỉ định nhân viên duyệt", callback_data="a_assign_approver"))
    return kb


def kb_full_admin_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📋 Độ đang chạy", callback_data="a_rep_live"),
        types.InlineKeyboardButton("📈 Ăn thua từng độ / HT", callback_data="a_rep_pnl"),
    )
    kb.add(types.InlineKeyboardButton("📊 Báo cáo ăn thua cả ngày", callback_data="a_rep_daily"))
    kb.add(
        types.InlineKeyboardButton("➕ Mở độ mới", callback_data="a_new_do"),
        types.InlineKeyboardButton("✏️ Đổi giá độ", callback_data="a_set_price"),
    )
    kb.add(types.InlineKeyboardButton("🏁 Chốt độ / Nhiều độ", callback_data="a_multi_close_open"))
    kb.add(
        types.InlineKeyboardButton("💳 Cấp hạn mức nợ", callback_data="a_set_debt_limit"),
        types.InlineKeyboardButton("💵 Thu nợ / Xóa nợ", callback_data="a_debt_menu"),
    )
    kb.add(
        types.InlineKeyboardButton("👤 Chỉ định duyệt", callback_data="a_assign_approver"),
        types.InlineKeyboardButton("🌙 Chốt sổ cuối ngày", callback_data="a_day_reset"),
    )
    return kb


def kb_price_do_picker() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    for d in db.get_open_dos():
        d_id = int(d["id"])
        kb.add(
            types.InlineKeyboardButton(
                f"{d['name']} ({float(d['xanh_win']):g}/{float(d['xanh_lose']):g})",
                callback_data=f"a_pdo_one_{d_id}",
            )
        )
    kb.add(types.InlineKeyboardButton("🌐 Đổi chung tất cả độ mở", callback_data="a_pdo_all"))
    return kb


def kb_multi_close_picker(selected_ids: set[int]) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    for d in db.get_open_dos():
        d_id = int(d["id"])
        mark = "✅" if d_id in selected_ids else "⬜"
        kb.add(
            types.InlineKeyboardButton(
                f"{mark} {d['name']} ({float(d['xanh_win']):g}/{float(d['xanh_lose']):g})",
                callback_data=f"a_mclose_toggle_{d_id}",
            )
        )
    kb.add(
        types.InlineKeyboardButton("⚡ Chọn tất cả", callback_data="a_mclose_all"),
        types.InlineKeyboardButton("🗑️ Bỏ chọn", callback_data="a_mclose_clear"),
    )
    kb.add(
        types.InlineKeyboardButton("🟢 XANH thắng", callback_data="a_mclose_run_XANH"),
        types.InlineKeyboardButton("🔴 ĐỎ thắng", callback_data="a_mclose_run_DO"),
        types.InlineKeyboardButton("🤝 Hòa", callback_data="a_mclose_run_DRAW"),
        types.InlineKeyboardButton("🚫 Hủy độ", callback_data="a_mclose_run_CANCEL"),
    )
    return kb


def kb_approve_deposit(user_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Duyệt cộng tiền", callback_data=f"a_dep_ok_{user_id}"),
        types.InlineKeyboardButton("❌ Từ chối", callback_data=f"a_dep_no_{user_id}"),
    )
    kb.add(types.InlineKeyboardButton("🔄 Chỉ định người duyệt", callback_data="a_assign_approver"))
    return kb


def kb_deposit_presets(user_id: int, suggested_amt: float = 0.0) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=3)
    if suggested_amt > 0:
        kb.add(
            types.InlineKeyboardButton(
                f"✅ Đúng số khách chọn (+{fmt_num(suggested_amt)})",
                callback_data=f"a_dep_amt_{user_id}_{suggested_amt}",
            )
        )
    btns = [
        types.InlineKeyboardButton(f"+{fmt_num(amt)}", callback_data=f"a_dep_amt_{user_id}_{amt}")
        for amt in [5000, 10000, 20000, 50000, 100000]
    ]
    kb.add(*btns)
    kb.add(types.InlineKeyboardButton("✏️ Nhập số tiền khác", callback_data=f"a_dep_custom_{user_id}"))
    return kb


def kb_withdrawal_actions(w_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Đã chuyển tiền", callback_data=f"a_wd_ok_{w_id}"),
        types.InlineKeyboardButton("❌ Từ chối rút", callback_data=f"a_wd_no_{w_id}"),
    )
    kb.add(types.InlineKeyboardButton("🔄 Chỉ định người duyệt", callback_data="a_assign_approver"))
    return kb


def get_panel_by_chat(chat_id: int, chat_type: str, user_id: int = 0) -> types.InlineKeyboardMarkup:
    if chat_type == "private":
        return kb_full_admin_panel()
    if chat_id == MASTER_GROUP_ID:
        return kb_master_panel() if is_master(user_id) else kb_master_viewer_panel()
    if chat_id == PRICE_GROUP_ID:
        return kb_price_panel()
    if chat_id == BILL_GROUP_ID:
        return kb_bill_panel()
    return kb_full_admin_panel()


def open_bet_amount_menu(chat_id: int, uid: int, u: dict, side: str, do_id: int) -> None:
    do = db.get_do_by_id(do_id)
    if not do or do["status"] != "OPEN":
        bot.send_message(chat_id, "Độ này đã đóng hoặc không tồn tại.", reply_markup=kb_user_main(uid))
        return
    win_u = float(do["xanh_win"] if side == "XANH" else do["do_win"])
    lose_u = float(do["xanh_lose"] if side == "XANH" else do["do_lose"])
    side_label = "🟢 XANH" if side == "XANH" else "🔴 ĐỎ"
    txt = (
        f"<b>{do['name']} — CỬA {side_label}</b>\n"
        f"• Giá hiện tại: <b>{win_u:g}/{lose_u:g}</b> <i>(trừ 5% phế trên tiền lời)</i>\n"
        f"• Ví: <b>{fmt_num(float(u['balance']))}</b> | Nợ: <b>{fmt_num(float(u['debt']))}/{fmt_num(float(u['debt_limit']))}</b>\n\n"
        f"Chọn mức tiền nhanh hoặc bấm <b>Nhập số khác</b>:"
    )
    bot.send_message(chat_id, txt, reply_markup=kb_user_amounts(side, do_id))


def send_deposit_qr(chat_id: int, user_id: int, amount: float) -> None:
    PENDING_STATE[user_id] = {"action": "USER_DEPOSIT_PENDING", "amount": amount}
    qr_url = build_vietqr_url(user_id, amount)
    vnd_show = int(round(amount * QR_AMOUNT_MULTIPLIER))

    caption = (
        f"<b>MÃ QR NẠP TIỀN — {BANK_INFO['bank']}</b>\n"
        f"• Ngân hàng: <b>{BANK_INFO['bank']}</b>\n"
        f"• Số tài khoản: <code>{BANK_INFO['stk']}</code>\n"
        f"• Chủ tài khoản: <b>{BANK_INFO['owner']}</b>\n"
        f"• Số tiền nạp: <b>{fmt_num(amount)}</b> (QR: <b>{fmt_num(vnd_show)}đ</b>)\n"
        f"• Nội dung chuyển khoản: <code>{user_id}</code>\n\n"
        f"Quét mã QR hoặc chuyển khoản đúng nội dung <code>{user_id}</code>, "
        f"sau đó bấm <b>✅ Tôi đã chuyển</b> bên dưới <i>(có thể gửi kèm ảnh bill)</i>."
    )
    try:
        bot.send_photo(chat_id, qr_url, caption=caption, reply_markup=kb_user_deposit())
    except Exception:
        bot.send_message(chat_id, caption, reply_markup=kb_user_deposit())


def dispatch_withdrawal_to_bill_group(user: dict, w_rec: dict) -> None:
    if not BILL_GROUP_ID:
        return
    assigned_id = int(w_rec["assigned_staff_id"] or 0)
    approver_txt = f"<code>{assigned_id}</code>" if assigned_id > 0 else "<i>Tất cả nhân viên</i>"
    w_id = int(w_rec["id"])
    amt = float(w_rec["amount"])
    payout_txt = w_rec["payout_text"] or "Xem ảnh mã QR đính kèm"

    caption = (
        f"💸 <b>YÊU CẦU RÚT TIỀN (#R{w_id:03d})</b>\n"
        f"• Khách: <b>{user['name']}</b> (@{user['username'] or 'không_có'}) | ID: <code>{user['user_id']}</code>\n"
        f"• Số tiền rút: <b>{fmt_num(amt)}</b>\n"
        f"• Thông tin nhận: <code>{payout_txt}</code>\n"
        f"• Nhân viên chỉ định duyệt: {approver_txt}\n"
        f"• Thời gian: <code>{w_rec['created_at']}</code>"
    )
    if w_rec["qr_file_id"]:
        bot.send_photo(BILL_GROUP_ID, w_rec["qr_file_id"], caption=caption, reply_markup=kb_withdrawal_actions(w_id))
    else:
        bot.send_message(BILL_GROUP_ID, caption, reply_markup=kb_withdrawal_actions(w_id))


def send_report_to_master(report_type: str, actor_id: int, source_chat_id: int, source_chat_type: str) -> None:
    if report_type == "live":
        report_txt = build_live_report_text()
    elif report_type == "daily":
        report_txt = build_daily_report_text()
    else:
        report_txt = build_pnl_report_text()

    if source_chat_id == MASTER_GROUP_ID:
        bot.send_message(MASTER_GROUP_ID, report_txt, reply_markup=get_panel_by_chat(MASTER_GROUP_ID, "group", actor_id))
    elif MASTER_GROUP_ID:
        bot.send_message(MASTER_GROUP_ID, report_txt, reply_markup=get_panel_by_chat(MASTER_GROUP_ID, "group", 0))
        if source_chat_type == "private":
            bot.send_message(source_chat_id, "✅ Đã gửi báo cáo về <b>nhóm Master</b>.", reply_markup=kb_full_admin_panel())
    else:
        bot.send_message(source_chat_id, report_txt, reply_markup=get_panel_by_chat(source_chat_id, source_chat_type, actor_id))


# ==================== LỆNH KHỞI TẠO ====================
@bot.message_handler(commands=["start", "menu"])
def cmd_start(message: types.Message):
    uid = message.from_user.id
    chat_id = message.chat.id
    chat_type = message.chat.type

    if chat_type == "private":
        u = db.get_user(uid, message.from_user.first_name, message.from_user.username)
        txt = (
            f"Xin chào <b>{u['name']}</b> (ID: <code>{u['user_id']}</code>)\n"
            f"• Số dư ví: <b>{fmt_num(float(u['balance']))}</b>\n"
            f"• Hạn mức ghi nợ: <b>{fmt_num(float(u['debt']))}</b> / <b>{fmt_num(float(u['debt_limit']))}</b>\n\n"
            f"Chọn nút bên dưới để thao tác:"
        )
        bot.send_message(chat_id, txt, reply_markup=kb_user_main(uid))
    elif is_master_group(chat_id):
        bot.send_message(
            chat_id,
            "<b>BẢNG BÁO CÁO & QUẢN LÝ NHÓM MASTER</b>",
            reply_markup=get_panel_by_chat(chat_id, chat_type, uid),
        )
    elif is_admin(uid):
        bot.send_message(
            chat_id,
            "<b>BẢNG ĐIỀU KHIỂN VẬN HÀNH</b>",
            reply_markup=get_panel_by_chat(chat_id, chat_type, uid),
        )


@bot.message_handler(commands=["admin"])
def cmd_admin(message: types.Message):
    uid = message.from_user.id
    if is_admin(uid) or is_master_group(message.chat.id):
        bot.send_message(
            message.chat.id,
            "<b>BẢNG ĐIỀU KHIỂN</b>",
            reply_markup=get_panel_by_chat(message.chat.id, message.chat.type, uid),
        )


@bot.message_handler(commands=["baocao", "pnl", "cangay"])
def cmd_report_shortcut(message: types.Message):
    uid = message.from_user.id
    chat_id = message.chat.id
    chat_type = message.chat.type
    if not can_view_master_report(uid, chat_id) and not (chat_type == "private" and is_admin(uid)):
        return
    cmd = message.text.lower().split()[0]
    if cmd == "/baocao":
        rtype = "live"
    elif cmd == "/cangay":
        rtype = "daily"
    else:
        rtype = "pnl"
    send_report_to_master(rtype, uid, chat_id, chat_type)


# ==================== XỬ LÝ NÚT BẤM ====================
@bot.callback_query_handler(func=lambda call: True)
def on_callback(call: types.CallbackQuery):
    data = call.data
    uid = call.from_user.id
    chat_id = call.message.chat.id
    chat_type = call.message.chat.type
    u = db.get_user(uid, call.from_user.first_name, call.from_user.username)
    open_dos = db.get_open_dos()

    # --- USER ACTIONS ---
    if data == "u_back":
        bot.edit_message_text(
            f"<b>MENU CHÍNH</b> — ID: <code>{uid}</code>\nSố dư ví: <b>{fmt_num(float(u['balance']))}</b>",
            chat_id,
            call.message.message_id,
            reply_markup=kb_user_main(uid),
        )

    elif data == "u_wallet":
        active_bets = []
        for d in open_dos:
            active_bets.extend(db.get_active_bets(do_name=d["name"], user_id=uid))
        bets_txt = "\n".join(
            [f"  • {b['do_name']} | {b['code']} | {b['side']} | {fmt_num(float(b['amount']))} (giá {b['price_str']})" for b in active_bets]
        ) or "  <i>Chưa có mã nào đang chạy.</i>"
        txt = (
            f"<b>TÀI KHOẢN CỦA BẠN</b> (ID: <code>{uid}</code>)\n"
            f"• Số dư ví: <b>{fmt_num(float(u['balance']))}</b>\n"
            f"• Đang ghi nợ: <b>{fmt_num(float(u['debt']))}</b> / Hạn mức <b>{fmt_num(float(u['debt_limit']))}</b>\n"
            f"• Ăn/thua tới hiện tại: {fmt_signed(float(u['pnl_net']))}\n\n"
            f"<b>Mã đang đánh:</b>\n{bets_txt}"
        )
        bot.send_message(chat_id, txt, reply_markup=kb_user_main(uid))

    elif data == "u_deposit":
        bot.send_message(
            chat_id,
            f"<b>CHỌN SỐ TIỀN CẦN NẠP</b>\n"
            f"• Ngân hàng: <b>{BANK_INFO['bank']}</b> — STK: <code>{BANK_INFO['stk']}</code>\n"
            f"• Chủ TK: <b>{BANK_INFO['owner']}</b>\n"
            f"• Nội dung CK mặc định: <code>{uid}</code>\n\n"
            f"Chọn mức tiền nhanh hoặc bấm <b>Nhập số khác</b> để tạo mã QR:",
            reply_markup=kb_deposit_amounts(),
        )

    elif data.startswith("u_dep_amt_"):
        amt = float(data.split("_")[3])
        send_deposit_qr(chat_id, uid, amt)

    elif data == "u_dep_custom":
        PENDING_STATE[uid] = {"action": "USER_DEP_CUSTOM_AMOUNT"}
        bot.send_message(chat_id, "Nhập số tiền bạn muốn nạp:")

    elif data == "u_paid":
        st = PENDING_STATE.get(uid)
        dep_amt = float(st["amount"]) if (st and st.get("action") == "USER_DEPOSIT_PENDING") else 0.0
        if dep_amt > 0:
            PENDING_STATE[uid] = {"action": "USER_LAST_DEP_AMT", "amount": dep_amt}
        approver_id = get_assigned_approver_id()
        approver_txt = f"<code>{approver_id}</code>" if approver_id > 0 else "<i>Tất cả nhân viên</i>"
        amt_line = f"• Số tiền khách chọn trên QR: <b>{fmt_num(dep_amt)}</b>\n" if dep_amt > 0 else ""

        bot.send_message(
            chat_id,
            f"✅ Đã gửi yêu cầu xác nhận nạp tiền cho ID <code>{uid}</code>.\n"
            f"<i>Nếu có ảnh bill, hãy gửi ảnh vào chat này để nhân viên đối soát nhanh.</i>",
            reply_markup=kb_user_main(uid),
        )
        if BILL_GROUP_ID:
            bot.send_message(
                BILL_GROUP_ID,
                f"🔔 <b>YÊU CẦU NẠP TIỀN MỚI</b>\n"
                f"• Khách: <b>{u['name']}</b> (@{u['username'] or 'không_có'})\n"
                f"{amt_line}"
                f"• Nội dung CK / ID: <code>{uid}</code>\n"
                f"• Nhân viên chỉ định duyệt: {approver_txt}\n"
                f"• Thời gian: <code>{datetime.now().strftime('%H:%M - %d/%m/%Y')}</code>",
                reply_markup=kb_approve_deposit(uid),
            )

    elif data == "u_withdraw":
        bal = float(u["balance"])
        if bal <= 0:
            bot.send_message(
                chat_id,
                "❌ Số dư ví hiện tại của bạn đang là <b>0</b>, chưa thể rút tiền.",
                reply_markup=kb_user_main(uid),
            )
        else:
            bot.send_message(
                chat_id,
                f"<b>RÚT TIỀN VỀ TÀI KHOẢN</b>\n"
                f"• Số dư khả dụng: <b>{fmt_num(bal)}</b>\n\n"
                f"Chọn mức tiền rút nhanh hoặc bấm <b>Nhập số rút khác</b>:",
                reply_markup=kb_withdraw_amounts(),
            )

    elif data.startswith("u_wd_amt_"):
        amt = float(data.split("_")[3])
        if amt > float(u["balance"]) + 1e-6:
            bot.send_message(
                chat_id,
                f"❌ Số tiền rút vượt số dư ví (<b>{fmt_num(float(u['balance']))}</b>).",
                reply_markup=kb_user_main(uid),
            )
        else:
            PENDING_STATE[uid] = {"action": "USER_WD_INFO", "amount": amt}
            bot.send_message(
                chat_id,
                f"<b>NHẬP THÔNG TIN NHẬN TIỀN RÚT ({fmt_num(amt)})</b>\n"
                f"Gửi <b>ảnh mã QR</b> tài khoản nhận HOẶC nhắn theo mẫu:\n"
                f"<code>Ngân hàng - Số tài khoản - Tên chủ TK</code>\n"
                f"<i>Ví dụ: VCB - 0987654321 - TRAN VAN B</i>",
            )

    elif data == "u_wd_custom":
        PENDING_STATE[uid] = {"action": "USER_WD_AMOUNT"}
        bot.send_message(chat_id, f"Nhập số tiền bạn muốn rút (Tối đa: <b>{fmt_num(float(u['balance']))}</b>):")

    elif data.startswith("u_side_"):
        side = data.split("_")[2]
        if len(open_dos) > 1:
            side_label = "🟢 XANH" if side == "XANH" else "🔴 ĐỎ"
            bot.send_message(
                chat_id,
                f"Đang có <b>{len(open_dos)} độ mở</b>. Chọn độ bạn muốn đánh cửa <b>{side_label}</b>:",
                reply_markup=kb_user_do_picker(side),
            )
        else:
            open_bet_amount_menu(chat_id, uid, u, side, int(open_dos[0]["id"]))

    elif data.startswith("u_pickdo_"):
        _, _, side, do_id_s = data.split("_")
        open_bet_amount_menu(chat_id, uid, u, side, int(do_id_s))

    elif data.startswith("u_bet_"):
        _, _, side, do_id_s, amt_str = data.split("_")
        _, res_msg = place_bet_logic(uid, float(amt_str), side, do_id=int(do_id_s))
        bot.send_message(chat_id, res_msg, reply_markup=kb_user_main(uid))

    elif data.startswith("u_custom_"):
        _, _, side, do_id_s = data.split("_")
        PENDING_STATE[uid] = {"action": "USER_BET_AMOUNT", "side": side, "do_id": int(do_id_s)}
        bot.send_message(chat_id, f"Nhập số tiền bạn muốn đánh cửa <b>{side}</b>:")

    # --- BÁO CÁO CHO MASTER & CỔ ĐÔNG TRONG NHÓM MASTER ---
    elif data in ("a_rep_live", "a_rep_pnl", "a_rep_daily"):
        if not can_view_master_report(uid, chat_id) and not (chat_type == "private" and is_admin(uid)):
            bot.answer_callback_query(call.id, "Báo cáo này chỉ dành cho Master và nhóm Master.", show_alert=True)
            return
        rtype = "live" if data == "a_rep_live" else ("daily" if data == "a_rep_daily" else "pnl")
        send_report_to_master(rtype, uid, chat_id, chat_type)

    elif data == "a_day_reset":
        if not is_master(uid):
            bot.answer_callback_query(call.id, "Chỉ Master mới được chốt sổ cuối ngày.", show_alert=True)
            return
        snapshot = close_day_accounting_logic()
        target_out = MASTER_GROUP_ID or chat_id
        bot.send_message(
            target_out,
            f"🌙 <b>ĐÃ CHỐT SỔ CUỐI NGÀY</b>\n\n{snapshot}",
            reply_markup=get_panel_by_chat(target_out, "group" if target_out != uid else "private", uid),
        )

    # --- ADMIN / MASTER / STAFF ACTIONS ---
    elif data.startswith("a_") and not is_admin(uid):
        bot.answer_callback_query(call.id, "Bạn không có quyền thao tác mục này.", show_alert=True)
        return

    elif data == "a_panel_open":
        bot.send_message(chat_id, "<b>MENU ADMIN (CHAT RIÊNG)</b>", reply_markup=kb_full_admin_panel())

    elif data == "a_new_do":
        created = db.create_new_do()
        bot.send_message(
            chat_id,
            f"✅ Đã mở thêm <b>{created['name']}</b> (XANH <code>{float(created['xanh_win']):g}/{float(created['xanh_lose']):g}</code> | "
            f"ĐỎ <code>{float(created['do_win']):g}/{float(created['do_lose']):g}</code>).",
            reply_markup=get_panel_by_chat(chat_id, chat_type, uid),
        )

    elif data == "a_set_price":
        cur_open = db.get_open_dos()
        if len(cur_open) == 1:
            d0 = cur_open[0]
            PENDING_STATE[uid] = {"action": "ADMIN_SET_PRICE", "target_do_ids": [int(d0["id"])], "target_label": d0["name"]}
            bot.send_message(
                chat_id,
                f"<b>ĐỔI GIÁ {d0['name']}</b>\n"
                f"Hiện tại: XANH <code>{float(d0['xanh_win']):g}/{float(d0['xanh_lose']):g}</code> | "
                f"ĐỎ <code>{float(d0['do_win']):g}/{float(d0['do_lose']):g}</code>\n\n"
                f"Nhập 1 cửa để tự đảo (VD: <code>XANH 1/10</code>) hoặc đủ 2 cửa (VD: <code>XANH 1/10 | DO 10/5</code>):",
            )
        else:
            bot.send_message(
                chat_id,
                "Chọn độ cần đổi giá hoặc đổi chung tất cả độ đang mở:",
                reply_markup=kb_price_do_picker(),
            )

    elif data.startswith("a_pdo_one_"):
        d_id = int(data.split("_")[3])
        d_obj = db.get_do_by_id(d_id)
        if not d_obj:
            bot.send_message(chat_id, "Không tìm thấy độ.")
            return
        PENDING_STATE[uid] = {"action": "ADMIN_SET_PRICE", "target_do_ids": [d_id], "target_label": d_obj["name"]}
        bot.send_message(
            chat_id,
            f"<b>ĐỔI GIÁ {d_obj['name']}</b>\n"
            f"Hiện tại: XANH <code>{float(d_obj['xanh_win']):g}/{float(d_obj['xanh_lose']):g}</code> | "
            f"ĐỎ <code>{float(d_obj['do_win']):g}/{float(d_obj['do_lose']):g}</code>\n\n"
            f"Nhập 1 cửa để tự đảo (VD: <code>XANH 1/10</code>) hoặc đủ 2 cửa (VD: <code>XANH 1/10 | DO 10/5</code>):",
        )

    elif data == "a_pdo_all":
        all_ids = [int(d["id"]) for d in db.get_open_dos()]
        PENDING_STATE[uid] = {"action": "ADMIN_SET_PRICE", "target_do_ids": all_ids, "target_label": "Tất cả độ mở"}
        bot.send_message(
            chat_id,
            f"<b>ĐỔI GIÁ CHUNG TẤT CẢ ĐỘ MỞ ({len(all_ids)} độ)</b>\n"
            f"Nhập 1 cửa để tự đảo (VD: <code>XANH 1/10</code>) hoặc đủ 2 cửa (VD: <code>XANH 1/10 | DO 10/5</code>):",
        )

    elif data == "a_multi_close_open":
        cur_open = db.get_open_dos()
        default_sel = {int(cur_open[0]["id"])} if len(cur_open) == 1 else set()
        PENDING_STATE[uid] = {"action": "ADMIN_MULTI_CLOSE", "selected_ids": default_sel}
        bot.send_message(
            chat_id,
            "<b>CHỐT ĐỘ / CHỐT NHIỀU ĐỘ CÙNG LÚC</b>\n"
            "Bấm chọn 1 hoặc nhiều độ, sau đó bấm nút kết quả bên dưới:",
            reply_markup=kb_multi_close_picker(default_sel),
        )

    elif data.startswith("a_mclose_toggle_"):
        d_id = int(data.split("_")[3])
        st = PENDING_STATE.setdefault(uid, {"action": "ADMIN_MULTI_CLOSE", "selected_ids": set()})
        sel: set[int] = st.setdefault("selected_ids", set())
        if d_id in sel:
            sel.remove(d_id)
        else:
            sel.add(d_id)
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=kb_multi_close_picker(sel))

    elif data == "a_mclose_all":
        sel = {int(d["id"]) for d in db.get_open_dos()}
        PENDING_STATE[uid] = {"action": "ADMIN_MULTI_CLOSE", "selected_ids": sel}
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=kb_multi_close_picker(sel))

    elif data == "a_mclose_clear":
        sel: set[int] = set()
        PENDING_STATE[uid] = {"action": "ADMIN_MULTI_CLOSE", "selected_ids": sel}
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=kb_multi_close_picker(sel))

    elif data.startswith("a_mclose_run_"):
        result = data.split("_")[3]
        st = PENDING_STATE.get(uid) or {}
        sel: set[int] = set(st.get("selected_ids") or set())
        if not sel and len(db.get_open_dos()) == 1:
            sel = {int(db.get_open_dos()[0]["id"])}
        if not sel:
            bot.answer_callback_query(call.id, "Hãy chọn ít nhất 1 độ trước khi chốt!", show_alert=True)
            return

        PENDING_STATE.pop(uid, None)
        out = settle_multiple_dos_logic(list(sel), result)
        target_out_chat = MASTER_GROUP_ID if MASTER_GROUP_ID else (PRICE_GROUP_ID if chat_type != "private" and PRICE_GROUP_ID else chat_id)
        panel_out = get_panel_by_chat(target_out_chat, "group" if target_out_chat != uid else "private", uid)
        do_names_str = ", ".join(out["do_names"])

        if result == "CANCEL":
            bot.send_message(target_out_chat, f"🚫 Đã hủy <b>{do_names_str}</b>.", reply_markup=panel_out)
        elif result == "DRAW":
            bot.send_message(target_out_chat, f"🤝 <b>{do_names_str}</b> hòa.", reply_markup=panel_out)
        else:
            lines = [f"<b>TỔNG KẾT {do_names_str} — {result} THẮNG</b>"]
            for item in out["users"]:
                lines.append(
                    f"• <b>{item['name']}</b> (<code>{item['user_id']}</code>): {fmt_signed(item['net'])} | "
                    f"Ví: <b>{fmt_num(item['balance'])}</b> | Nợ: <b>{fmt_num(item['debt'])}</b>"
                )
                bot.send_message(
                    item["user_id"],
                    f"<b>KẾT QUẢ {do_names_str}: {result} THẮNG</b>\n"
                    f"• Tổng ăn/thua của bạn: {fmt_signed(item['net'])}\n"
                    f"• Ví: <b>{fmt_num(item['balance'])}</b> | Nợ: <b>{fmt_num(item['debt'])}</b>",
                    reply_markup=kb_user_main(item["user_id"]),
                )
            lines.append(f"\n📌 Độ đang mở hiện tại: <b>{', '.join(out['open_dos'])}</b>")
            bot.send_message(target_out_chat, "\n".join(lines), reply_markup=panel_out)

        # Gửi báo cáo ăn thua từng độ vào nhóm Master
        if MASTER_GROUP_ID:
            for d_name in out["do_names"]:
                per_do_txt = record_and_format_per_do_report(d_name, result, out["per_do_net"].get(d_name, {}))
                bot.send_message(MASTER_GROUP_ID, per_do_txt)

    elif data == "a_assign_approver":
        if not is_master(uid):
            bot.answer_callback_query(call.id, "Chỉ Master mới được chỉ định nhân viên duyệt.", show_alert=True)
            return
        PENDING_STATE[uid] = {"action": "MASTER_SET_APPROVER"}
        cur_app = get_assigned_approver_id()
        bot.send_message(
            chat_id,
            f"<b>CHỈ ĐỊNH NHÂN VIÊN DUYỆT NẠP / RÚT</b>\n"
            f"• Đang chỉ định: <code>{cur_app if cur_app > 0 else 'Tất cả nhân viên'}</code>\n\n"
            f"Nhập Telegram ID nhân viên (hoặc nhập <code>0</code> để mở cho mọi nhân viên):",
        )

    elif data.startswith("a_dep_no_"):
        ok_perm, perm_msg = can_approve_item(uid)
        if not ok_perm:
            bot.answer_callback_query(call.id, "Chưa tới lượt duyệt của bạn!", show_alert=True)
            bot.send_message(chat_id, perm_msg)
            return
        target_uid = int(data.split("_")[3])
        notify_group_id = BILL_GROUP_ID or chat_id
        bot.send_message(notify_group_id, f"❌ Đã từ chối yêu cầu nạp của ID <code>{target_uid}</code>.")
        bot.send_message(target_uid, "❌ Yêu cầu nạp tiền của bạn chưa được xác nhận.")

    elif data.startswith("a_dep_ok_"):
        ok_perm, perm_msg = can_approve_item(uid)
        if not ok_perm:
            bot.answer_callback_query(call.id, "Chưa tới lượt duyệt của bạn!", show_alert=True)
            bot.send_message(chat_id, perm_msg)
            return
        target_uid = int(data.split("_")[3])
        st_u = PENDING_STATE.get(target_uid)
        suggested = float(st_u["amount"]) if (st_u and "amount" in st_u) else 0.0
        bot.send_message(
            chat_id,
            f"Chọn số tiền cộng cho ID <code>{target_uid}</code> hoặc nhập số khác:",
            reply_markup=kb_deposit_presets(target_uid, suggested),
        )

    elif data.startswith("a_dep_amt_"):
        ok_perm, perm_msg = can_approve_item(uid)
        if not ok_perm:
            bot.answer_callback_query(call.id, "Chưa tới lượt duyệt của bạn!", show_alert=True)
            bot.send_message(chat_id, perm_msg)
            return
        _, _, _, target_uid_s, amt_s = data.split("_")
        target_uid = int(target_uid_s)
        amt = float(amt_s)
        res = apply_deposit_logic(target_uid, amt)
        u_info = res["user"]

        notify_group_id = BILL_GROUP_ID or chat_id
        bot.send_message(
            notify_group_id,
            f"✅ Đã duyệt cộng <b>+{fmt_num(amt)}</b> cho ID <code>{target_uid}</code> ({u_info['name']}). "
            f"Ví mới: <b>{fmt_num(res['new_balance'])}</b>.",
        )
        bot.send_message(
            target_uid,
            f"<b>NẠP TIỀN THÀNH CÔNG</b>\n"
            f"• ID: <code>{target_uid}</code>\n"
            f"• Đã cộng: <b>+{fmt_num(amt)}</b>\n"
            f"• Số dư hiện tại: <b>{fmt_num(res['new_balance'])}</b>",
            reply_markup=kb_user_main(target_uid),
        )
        if MAIN_CHANNEL_ID:
            bot.send_message(
                MAIN_CHANNEL_ID,
                f"<b>THÔNG BÁO NẠP ĐIỂM</b>\n"
                f"Tài khoản ID <code>{target_uid}</code> vừa được nạp thành công <b>+{fmt_num(amt)}</b>.",
            )

    elif data.startswith("a_dep_custom_"):
        ok_perm, perm_msg = can_approve_item(uid)
        if not ok_perm:
            bot.answer_callback_query(call.id, "Chưa tới lượt duyệt của bạn!", show_alert=True)
            bot.send_message(chat_id, perm_msg)
            return
        target_uid = int(data.split("_")[3])
        PENDING_STATE[uid] = {"action": "ADMIN_DEP_AMOUNT", "target_uid": target_uid}
        bot.send_message(chat_id, f"Nhập số tiền cần cộng cho ID <code>{target_uid}</code>:")

    elif data.startswith("a_wd_ok_"):
        w_id = int(data.split("_")[3])
        w_item = db.get_withdrawal(w_id)
        ok_perm, perm_msg = can_approve_item(uid, w_item["assigned_staff_id"] if w_item else 0)
        if not ok_perm:
            bot.answer_callback_query(call.id, "Không đúng nhân viên được chỉ định duyệt!", show_alert=True)
            bot.send_message(chat_id, perm_msg)
            return

        ok_wd, wd_msg, wd_info = confirm_withdrawal_logic(w_id)
        bot.send_message(chat_id, wd_msg, reply_markup=get_panel_by_chat(chat_id, chat_type, uid))
        if ok_wd and wd_info:
            w_rec = wd_info["withdrawal"]
            u_rec = wd_info["user"]
            bot.send_message(
                int(w_rec["user_id"]),
                f"<b>RÚT TIỀN THÀNH CÔNG</b>\n"
                f"• Mã rút: <code>#R{w_id:03d}</code>\n"
                f"• Số tiền đã chuyển: <b>-{fmt_num(float(w_rec['amount']))}</b>\n"
                f"• Số dư ví còn lại: <b>{fmt_num(float(u_rec['balance']))}</b>",
                reply_markup=kb_user_main(int(w_rec["user_id"])),
            )

    elif data.startswith("a_wd_no_"):
        w_id = int(data.split("_")[3])
        w_item = db.get_withdrawal(w_id)
        ok_perm, perm_msg = can_approve_item(uid, w_item["assigned_staff_id"] if w_item else 0)
        if not ok_perm:
            bot.answer_callback_query(call.id, "Không đúng nhân viên được chỉ định duyệt!", show_alert=True)
            bot.send_message(chat_id, perm_msg)
            return

        ok_wd, wd_msg, wd_info = reject_withdrawal_logic(w_id)
        bot.send_message(chat_id, wd_msg, reply_markup=get_panel_by_chat(chat_id, chat_type, uid))
        if ok_wd and wd_info:
            w_rec = wd_info["withdrawal"]
            bot.send_message(
                int(w_rec["user_id"]),
                f"❌ Lệnh rút <code>#R{w_id:03d}</code> (<b>{fmt_num(float(w_rec['amount']))}</b>) đã bị từ chối và hoàn lại ví.\n"
                f"• Ví hiện tại: <b>{fmt_num(float(wd_info['balance']))}</b>",
                reply_markup=kb_user_main(int(w_rec["user_id"])),
            )

    elif data == "a_set_debt_limit":
        if not is_master(uid):
            bot.answer_callback_query(call.id, "Chỉ Master mới được cấp hạn mức nợ.", show_alert=True)
            return
        PENDING_STATE[uid] = {"action": "MASTER_SET_DEBT_LIMIT"}
        bot.send_message(
            chat_id,
            "Nhập <code>USER_ID HẠN_MỨC_NỢ</code>\nVí dụ: <code>123456 20000000</code> (nhập <code>0</code> để khóa ghi nợ).",
        )

    elif data == "a_debt_menu":
        if not is_master(uid):
            bot.answer_callback_query(call.id, "Chỉ Master mới được thu/xóa nợ.", show_alert=True)
            return
        PENDING_STATE[uid] = {"action": "MASTER_COLLECT_OR_CLEAR_DEBT"}
        bot.send_message(
            chat_id,
            "<b>THU NỢ / XÓA NỢ CỘNG DỒN</b>\n"
            "• Thu tiền khách trả (tính lợi nhuận thực thu): <code>THU USER_ID SỐ_TIỀN</code>\n"
            "• Xóa nợ khi chốt mất: <code>XOA USER_ID</code>\n"
            "Ví dụ: <code>THU 123456 5000000</code>",
        )

    bot.answer_callback_query(call.id)


# ==================== XỬ LÝ ẢNH (QR RÚT TIỀN HOẶC BILL NẠP) ====================
@bot.message_handler(content_types=["photo"])
def on_photo(message: types.Message):
    if message.chat.type != "private":
        return
    uid = message.from_user.id
    u = db.get_user(uid, message.from_user.first_name, message.from_user.username)
    file_id = message.photo[-1].file_id
    state = PENDING_STATE.get(uid)

    if state and state.get("action") == "USER_WD_INFO":
        PENDING_STATE.pop(uid, None)
        amt = float(state["amount"])
        ok, msg, w_rec = create_withdrawal_logic(uid, amt, payout_text="", qr_file_id=file_id)
        bot.send_message(message.chat.id, msg, reply_markup=kb_user_main(uid))
        if ok and w_rec:
            dispatch_withdrawal_to_bill_group(u, w_rec)
        return

    bot.send_message(
        message.chat.id,
        f"✅ Đã nhận ảnh bill từ ID <code>{uid}</code>, đang chuyển cho nhân viên kiểm tra.",
        reply_markup=kb_user_main(uid),
    )
    if BILL_GROUP_ID:
        approver_id = get_assigned_approver_id()
        approver_txt = f"<code>{approver_id}</code>" if approver_id > 0 else "<i>Tất cả nhân viên</i>"
        bot.send_photo(
            BILL_GROUP_ID,
            file_id,
            caption=(
                f"🧾 <b>BILL NẠP TIỀN</b>\n"
                f"• Khách: <b>{u['name']}</b> (@{u['username'] or 'không_có'})\n"
                f"• Nội dung CK / ID: <code>{uid}</code>\n"
                f"• Nhân viên chỉ định duyệt: {approver_txt}"
            ),
            reply_markup=kb_approve_deposit(uid),
        )


# ==================== XỬ LÝ TIN NHẮN VĂN BẢN ====================
@bot.message_handler(content_types=["text"])
def on_text(message: types.Message):
    txt = message.text.strip()
    if txt.startswith("/"):
        return

    uid = message.from_user.id
    u = db.get_user(uid, message.from_user.first_name, message.from_user.username)
    state = PENDING_STATE.get(uid)

    # --- USER INPUTS ---
    if message.chat.type == "private" and state:
        act = state.get("action")

        if act == "USER_DEP_CUSTOM_AMOUNT":
            PENDING_STATE.pop(uid, None)
            try:
                amt = float(txt.replace(".", "").replace(",", ""))
            except ValueError:
                bot.send_message(message.chat.id, "Vui lòng nhập số tiền hợp lệ.", reply_markup=kb_user_main(uid))
                return
            if amt <= 0:
                bot.send_message(message.chat.id, "Số tiền nạp phải lớn hơn 0.", reply_markup=kb_user_main(uid))
                return
            send_deposit_qr(message.chat.id, uid, amt)
            return

        if act == "USER_BET_AMOUNT":
            PENDING_STATE.pop(uid, None)
            try:
                amt = float(txt.replace(".", "").replace(",", ""))
            except ValueError:
                bot.send_message(message.chat.id, "Vui lòng nhập số hợp lệ.", reply_markup=kb_user_main(uid))
                return
            _, res_msg = place_bet_logic(uid, amt, state["side"], do_id=state.get("do_id"))
            bot.send_message(message.chat.id, res_msg, reply_markup=kb_user_main(uid))
            return

        if act == "USER_WD_AMOUNT":
            try:
                amt = float(txt.replace(".", "").replace(",", ""))
            except ValueError:
                PENDING_STATE.pop(uid, None)
                bot.send_message(message.chat.id, "Vui lòng nhập số tiền hợp lệ.", reply_markup=kb_user_main(uid))
                return

            if amt <= 0 or amt > float(u["balance"]) + 1e-6:
                PENDING_STATE.pop(uid, None)
                bot.send_message(
                    message.chat.id,
                    f"❌ Số tiền rút không hợp lệ hoặc vượt ví (<b>{fmt_num(float(u['balance']))}</b>).",
                    reply_markup=kb_user_main(uid),
                )
                return

            PENDING_STATE[uid] = {"action": "USER_WD_INFO", "amount": amt}
            bot.send_message(
                message.chat.id,
                f"<b>NHẬP THÔNG TIN NHẬN TIỀN RÚT ({fmt_num(amt)})</b>\n"
                f"Gửi <b>ảnh mã QR</b> HOẶC nhắn theo mẫu:\n"
                f"<code>Ngân hàng - Số tài khoản - Tên chủ TK</code>",
            )
            return

        if act == "USER_WD_INFO":
            PENDING_STATE.pop(uid, None)
            amt = float(state["amount"])
            ok, msg, w_rec = create_withdrawal_logic(uid, amt, payout_text=txt, qr_file_id="")
            bot.send_message(message.chat.id, msg, reply_markup=kb_user_main(uid))
            if ok and w_rec:
                dispatch_withdrawal_to_bill_group(u, w_rec)
            return

    # --- ADMIN / MASTER INPUTS ---
    if is_admin(uid) and state:
        act = state.get("action")
        PENDING_STATE.pop(uid, None)

        if act == "MASTER_SET_APPROVER":
            if not is_master(uid):
                bot.send_message(message.chat.id, "Chỉ Master mới có quyền này.")
                return
            try:
                staff_id = int(txt.strip())
                set_assigned_approver_id(staff_id)
                desc = f"ID <code>{staff_id}</code>" if staff_id > 0 else "<b>Tất cả nhân viên</b>"
                bot.send_message(
                    message.chat.id,
                    f"✅ Đã cập nhật nhân viên chỉ định duyệt mặc định: {desc}",
                    reply_markup=get_panel_by_chat(message.chat.id, message.chat.type, uid),
                )
            except ValueError:
                bot.send_message(message.chat.id, "Vui lòng nhập Telegram ID dạng số (ví dụ <code>7709258303</code> hoặc <code>0</code>).")
            return

        if act == "ADMIN_DEP_AMOUNT":
            ok_perm, perm_msg = can_approve_item(uid)
            if not ok_perm:
                bot.send_message(message.chat.id, perm_msg)
                return
            try:
                amt = float(txt.replace(".", "").replace(",", ""))
            except ValueError:
                bot.send_message(message.chat.id, "Số tiền không hợp lệ.")
                return
            target_uid = int(state["target_uid"])
            res = apply_deposit_logic(target_uid, amt)
            u_info = res["user"]

            notify_group_id = BILL_GROUP_ID or message.chat.id
            bot.send_message(
                notify_group_id,
                f"✅ Đã duyệt cộng <b>+{fmt_num(amt)}</b> cho ID <code>{target_uid}</code> ({u_info['name']}). "
                f"Ví mới: <b>{fmt_num(res['new_balance'])}</b>.",
            )
            bot.send_message(
                target_uid,
                f"<b>NẠP TIỀN THÀNH CÔNG</b>\n"
                f"• ID: <code>{target_uid}</code>\n"
                f"• Đã cộng: <b>+{fmt_num(amt)}</b>\n"
                f"• Số dư hiện tại: <b>{fmt_num(res['new_balance'])}</b>",
                reply_markup=kb_user_main(target_uid),
            )
            if MAIN_CHANNEL_ID:
                bot.send_message(
                    MAIN_CHANNEL_ID,
                    f"<b>THÔNG BÁO NẠP ĐIỂM</b>\n"
                    f"Tài khoản ID <code>{target_uid}</code> vừa được nạp thành công <b>+{fmt_num(amt)}</b>.",
                )

        elif act == "ADMIN_SET_PRICE":
            try:
                updates = parse_price_input(txt)
                target_ids = list(state.get("target_do_ids") or [])
                target_label = state.get("target_label") or "Độ hiện tại"
                for d_id in target_ids:
                    db.update_do(int(d_id), **updates)

                first_do = db.get_do_by_id(int(target_ids[0])) if target_ids else db.get_open_dos()[0]
                bot.send_message(
                    message.chat.id,
                    f"✅ Đã cập nhật giá cho <b>{target_label}</b>:\n"
                    f"• XANH: <b>{float(first_do['xanh_win']):g}/{float(first_do['xanh_lose']):g}</b>\n"
                    f"• ĐỎ: <b>{float(first_do['do_win']):g}/{float(first_do['do_lose']):g}</b>",
                    reply_markup=get_panel_by_chat(message.chat.id, message.chat.type, uid),
                )
            except Exception:
                bot.send_message(
                    message.chat.id,
                    "Sai định dạng. Nhập 1 cửa để tự đảo (VD: <code>XANH 1/10</code>) hoặc đủ 2 cửa (VD: <code>XANH 1/10 | DO 10/5</code>).",
                    reply_markup=get_panel_by_chat(message.chat.id, message.chat.type, uid),
                )

        elif act == "MASTER_SET_DEBT_LIMIT":
            if not is_master(uid):
                bot.send_message(message.chat.id, "Chỉ Master mới được cấp hạn mức nợ.")
                return
            try:
                parts = txt.split()
                target_uid = int(parts[0])
                limit_val = float(parts[1].replace(".", "").replace(",", ""))
                u_target = db.get_user(target_uid)
                db.update_user_fields(target_uid, debt_limit=limit_val)
                bot.send_message(
                    message.chat.id,
                    f"✅ Đã đặt hạn mức ghi nợ cho ID <code>{target_uid}</code> ({u_target['name']}): <b>{fmt_num(limit_val)}</b>",
                    reply_markup=get_panel_by_chat(message.chat.id, message.chat.type, uid),
                )
            except Exception:
                bot.send_message(
                    message.chat.id,
                    "Sai mẫu. Ví dụ đúng: <code>123456 20000000</code>",
                    reply_markup=get_panel_by_chat(message.chat.id, message.chat.type, uid),
                )

        elif act == "MASTER_COLLECT_OR_CLEAR_DEBT":
            if not is_master(uid):
                bot.send_message(message.chat.id, "Chỉ Master mới được thu/xóa nợ.")
                return
            try:
                parts = txt.upper().split()
                cmd_type = parts[0]
                target_uid = int(parts[1])
                u_target = db.get_user(target_uid)
                cur_debt = float(u_target["debt"])

                if cmd_type == "THU":
                    amt = float(parts[2].replace(".", "").replace(",", ""))
                    paid_debt = min(cur_debt, amt)
                    db.update_user_fields(target_uid, debt=cur_debt - paid_debt)
                    db.add_realized_profit(amt)
                    bot.send_message(
                        message.chat.id,
                        f"💵 Đã thu <b>+{fmt_num(amt)}</b> từ ID <code>{target_uid}</code>.\n"
                        f"• Trừ nợ: <b>{fmt_num(paid_debt)}</b> | Nợ còn: <b>{fmt_num(cur_debt - paid_debt)}</b>\n"
                        f"• Cộng vào lợi nhuận thực thu: <b>+{fmt_num(amt)}</b>",
                        reply_markup=get_panel_by_chat(message.chat.id, message.chat.type, uid),
                    )
                elif cmd_type == "XOA":
                    db.update_user_fields(target_uid, debt=0.0)
                    bot.send_message(
                        message.chat.id,
                        f"🗑️ Đã xóa nợ treo <b>{fmt_num(cur_debt)}</b> cho ID <code>{target_uid}</code>.",
                        reply_markup=get_panel_by_chat(message.chat.id, message.chat.type, uid),
                    )
                else:
                    bot.send_message(
                        message.chat.id,
                        "Dùng <code>THU ID SỐ_TIỀN</code> hoặc <code>XOA ID</code>.",
                        reply_markup=get_panel_by_chat(message.chat.id, message.chat.type, uid),
                    )
            except Exception:
                bot.send_message(
                    message.chat.id,
                    "Sai mẫu thu/xóa nợ.",
                    reply_markup=get_panel_by_chat(message.chat.id, message.chat.type, uid),
                )


def run_bot() -> None:
    print("Bot đá gà đang khởi chạy...")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=25)


if __name__ == "__main__":
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=PORT)
