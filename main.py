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
    MIN_DEPOSIT_AMOUNT,
    MIN_WITHDRAW_AMOUNT,
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
    parse_single_side_ratio,
    get_user_betting_limit_info,
    can_open_do_by_time,
    open_specific_do_logic,
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


def get_settleable_dos_list() -> list[dict]:
    if hasattr(db, "get_unsettled_dos"):
        return [d for d in db.get_unsettled_dos() if d.get("status") != "CLOSED"]
    return [d for d in db.get_open_dos() if d.get("status") != "CLOSED"]


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
        types.InlineKeyboardButton("\U0001f4b3 Nạp tiền", callback_data="u_deposit"),
        types.InlineKeyboardButton("\U0001f4b8 Rút tiền", callback_data="u_withdraw"),
    )
    kb.add(
        types.InlineKeyboardButton("\U0001f7e2 Đánh XANH", callback_data="u_side_XANH"),
        types.InlineKeyboardButton("\U0001f534 Đánh ĐỎ", callback_data="u_side_DO"),
    )
    kb.add(types.InlineKeyboardButton("\U0001f4b0 Số dư & Mã đang đánh", callback_data="u_wallet"))
    if is_admin(user_id):
        kb.add(types.InlineKeyboardButton("\U0001f6e0\ufe0f Menu Admin (Chat riêng)", callback_data="a_panel_open"))
    return kb


def kb_user_do_picker(side: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    for d in db.get_open_dos():
        d_id = int(d["id"])
        price_lbl = (
            f"{float(d['xanh_win']):g}/{float(d['xanh_lose']):g}"
            if side == "XANH"
            else f"{float(d['do_win']):g}/{float(d['do_lose']):g}"
        )
        kb.add(
            types.InlineKeyboardButton(
                f"{d['name']} ({price_lbl})",
                callback_data=f"u_pickdo_{side}_{d_id}",
            )
        )
    kb.add(types.InlineKeyboardButton("\U0001f519 Quay lại", callback_data="u_back"))
    return kb


def kb_deposit_amounts() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=3)
    btns = [
        types.InlineKeyboardButton(fmt_num(amt), callback_data=f"u_dep_amt_{amt}")
        for amt in QUICK_DEPOSIT_AMOUNTS
    ]
    kb.add(*btns)
    kb.add(
        types.InlineKeyboardButton("\u270f\ufe0f Nhập số khác", callback_data="u_dep_custom"),
        types.InlineKeyboardButton("\U0001f519 Quay lại", callback_data="u_back"),
    )
    return kb


def kb_user_deposit() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\u2705 Tôi đã chuyển", callback_data="u_paid"),
        types.InlineKeyboardButton("\U0001f519 Quay lại", callback_data="u_back"),
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
        types.InlineKeyboardButton("\u270f\ufe0f Nhập số khác", callback_data=f"u_custom_{side}_{do_id}"),
        types.InlineKeyboardButton("\U0001f519 Quay lại", callback_data="u_back"),
    )
    return kb


def kb_withdraw_amounts(bal: float) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=3)
    btns = [
        types.InlineKeyboardButton(fmt_num(amt), callback_data=f"u_wd_amt_{amt}")
        for amt in QUICK_WITHDRAW_AMOUNTS
        if amt <= bal + 1e-6
    ]
    if btns:
        kb.add(*btns)
    kb.add(types.InlineKeyboardButton(f"\U0001f4b0 Rút hết ví ({fmt_num(bal)})", callback_data="u_wd_all"))
    kb.add(
        types.InlineKeyboardButton("\u270f\ufe0f Nhập số rút khác", callback_data="u_wd_custom"),
        types.InlineKeyboardButton("\U0001f519 Quay lại", callback_data="u_back"),
    )
    return kb


def kb_master_viewer_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\U0001f4cb Độ đang chạy", callback_data="a_rep_live"),
        types.InlineKeyboardButton("\U0001f4c8 Ăn thua từng độ / HT", callback_data="a_rep_pnl"),
    )
    kb.add(types.InlineKeyboardButton("\U0001f4ca Báo cáo ăn thua cả ngày", callback_data="a_rep_daily"))
    return kb


def kb_master_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\U0001f4cb Độ đang chạy", callback_data="a_rep_live"),
        types.InlineKeyboardButton("\U0001f4c8 Ăn thua từng độ / HT", callback_data="a_rep_pnl"),
    )
    kb.add(types.InlineKeyboardButton("\U0001f4ca Báo cáo ăn thua cả ngày", callback_data="a_rep_daily"))
    kb.add(
        types.InlineKeyboardButton("\U0001f4b3 Cấp hạn mức nợ", callback_data="a_set_debt_limit"),
        types.InlineKeyboardButton("\U0001f4b5 Thu nợ / Xóa nợ", callback_data="a_debt_menu"),
    )
    kb.add(
        types.InlineKeyboardButton("\U0001f464 Chỉ định duyệt", callback_data="a_assign_approver"),
        types.InlineKeyboardButton("\U0001f319 Chốt sổ cuối ngày", callback_data="a_day_reset"),
    )
    return kb


def kb_price_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\u2795 Mở độ mới", callback_data="a_new_do"),
        types.InlineKeyboardButton("\u270f\ufe0f Đổi giá độ", callback_data="a_set_price"),
    )
    kb.add(types.InlineKeyboardButton("\U0001f3c1 Chốt độ / Nhiều độ", callback_data="a_multi_close_open"))
    return kb


def kb_bill_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("\U0001f464 Chỉ định nhân viên duyệt", callback_data="a_assign_approver"))
    return kb


def kb_full_admin_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\U0001f4cb Độ đang chạy", callback_data="a_rep_live"),
        types.InlineKeyboardButton("\U0001f4c8 Ăn thua từng độ / HT", callback_data="a_rep_pnl"),
    )
    kb.add(types.InlineKeyboardButton("\U0001f4ca Báo cáo ăn thua cả ngày", callback_data="a_rep_daily"))
    kb.add(
        types.InlineKeyboardButton("\u2795 Mở độ mới", callback_data="a_new_do"),
        types.InlineKeyboardButton("\u270f\ufe0f Đổi giá độ", callback_data="a_set_price"),
    )
    kb.add(types.InlineKeyboardButton("\U0001f3c1 Chốt độ / Nhiều độ", callback_data="a_multi_close_open"))
    kb.add(
        types.InlineKeyboardButton("\U0001f4b3 Cấp hạn mức nợ", callback_data="a_set_debt_limit"),
        types.InlineKeyboardButton("\U0001f4b5 Thu nợ / Xóa nợ", callback_data="a_debt_menu"),
    )
    kb.add(
        types.InlineKeyboardButton("\U0001f464 Chỉ định duyệt", callback_data="a_assign_approver"),
        types.InlineKeyboardButton("\U0001f319 Chốt sổ cuối ngày", callback_data="a_day_reset"),
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
    kb.add(types.InlineKeyboardButton("\U0001f310 Đổi chung tất cả độ mở", callback_data="a_pdo_all"))
    return kb


def kb_price_side_picker(do_target: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\U0001f7e2 Chọn cửa XANH", callback_data=f"a_pside_XANH_{do_target}"),
        types.InlineKeyboardButton("\U0001f534 Chọn cửa ĐỎ", callback_data=f"a_pside_DO_{do_target}"),
    )
    return kb


def kb_open_do_picker() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=4)
    btns = [
        types.InlineKeyboardButton(f"Độ {i}", callback_data=f"a_open_num_{i}")
        for i in range(1, 9)
    ]
    kb.add(*btns)
    kb.add(types.InlineKeyboardButton("\u270f\ufe0f Nhập số độ khác", callback_data="a_open_custom"))
    return kb


def kb_multi_close_picker(selected_ids: set[int]) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    for d in get_settleable_dos_list():
        d_id = int(d["id"])
        mark = "\u2705" if d_id in selected_ids else "\u2b1c"
        st_tag = f" [{d['status']}]" if d.get("status") != "OPEN" else ""
        kb.add(
            types.InlineKeyboardButton(
                f"{mark} {d['name']}{st_tag} ({float(d['xanh_win']):g}/{float(d['xanh_lose']):g})",
                callback_data=f"a_mclose_toggle_{d_id}",
            )
        )
    kb.add(
        types.InlineKeyboardButton("\u26a1 Chọn tất cả", callback_data="a_mclose_all"),
        types.InlineKeyboardButton("\U0001f5d1\ufe0f Bỏ chọn", callback_data="a_mclose_clear"),
    )
    kb.add(
        types.InlineKeyboardButton("\U0001f7e2 XANH thắng", callback_data="a_mclose_run_XANH"),
        types.InlineKeyboardButton("\U0001f534 ĐỎ thắng", callback_data="a_mclose_run_DO"),
        types.InlineKeyboardButton("\U0001f91d Hòa", callback_data="a_mclose_run_DRAW"),
        types.InlineKeyboardButton("\U0001f6ab Hủy độ", callback_data="a_mclose_run_CANCEL"),
    )
    return kb


def kb_approve_deposit(user_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\u2705 Duyệt cộng tiền", callback_data=f"a_dep_ok_{user_id}"),
        types.InlineKeyboardButton("\u274c Từ chối", callback_data=f"a_dep_no_{user_id}"),
    )
    kb.add(types.InlineKeyboardButton("\U0001f504 Chỉ định người duyệt", callback_data="a_assign_approver"))
    return kb


def kb_deposit_presets(user_id: int, suggested_amt: float = 0.0) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=3)
    if suggested_amt > 0:
        kb.add(
            types.InlineKeyboardButton(
                f"\u2705 Đúng số khách chọn (+{fmt_num(suggested_amt)})",
                callback_data=f"a_dep_amt_{user_id}_{suggested_amt}",
            )
        )
    btns = [
        types.InlineKeyboardButton(f"+{fmt_num(amt)}", callback_data=f"a_dep_amt_{user_id}_{amt}")
        for amt in [250000, 500000, 1000000, 2000000, 5000000, 10000000]
    ]
    kb.add(*btns)
    kb.add(types.InlineKeyboardButton("\u270f\ufe0f Nhập số tiền khác", callback_data=f"a_dep_custom_{user_id}"))
    return kb


def kb_withdrawal_actions(w_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\u2705 Đã chuyển tiền", callback_data=f"a_wd_ok_{w_id}"),
        types.InlineKeyboardButton("\u274c Từ chối rút", callback_data=f"a_wd_no_{w_id}"),
    )
    kb.add(types.InlineKeyboardButton("\U0001f504 Chỉ định người duyệt", callback_data="a_assign_approver"))
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
    lim = get_user_betting_limit_info(uid, do_id=do_id)
    max_side = lim["max_xanh"] if side == "XANH" else lim["max_do"]
    side_label = "\U0001f7e2 XANH" if side == "XANH" else "\U0001f534 ĐỎ"
    txt = (
        f"<b>{do['name']} — CỬA {side_label}</b>\n"
        f"\u2022 Giá hiện tại: <b>{win_u:g}/{lose_u:g}</b> <i>(trừ 5% phế trên tiền lời)</i>\n"
        f"\u2022 Hạn mức tối đa cửa {side_label}: <b>{fmt_num(max_side)}</b>\n"
        f"\u2022 Ví: <b>{fmt_num(float(u['balance']))}</b> | Nợ: <b>{fmt_num(float(u['debt']))}/{fmt_num(float(u['debt_limit']))}</b>\n\n"
        f"Chọn mức tiền nhanh hoặc bấm <b>Nhập số khác</b>:"
    )
    bot.send_message(chat_id, txt, reply_markup=kb_user_amounts(side, do_id))


def send_deposit_qr(chat_id: int, user_id: int, amount: float) -> None:
    PENDING_STATE[user_id] = {"action": "USER_DEPOSIT_PENDING", "amount": amount}
    qr_url = build_vietqr_url(user_id, amount)
    vnd_show = int(round(amount * QR_AMOUNT_MULTIPLIER))

    caption = (
        f"<b>MÃ QR NẠP TIỀN — {BANK_INFO['bank']}</b>\n"
        f"\u2022 Ngân hàng: <b>{BANK_INFO['bank']}</b>\n"
        f"\u2022 Số tài khoản: <code>{BANK_INFO['stk']}</code>\n"
        f"\u2022 Chủ tài khoản: <b>{BANK_INFO['owner']}</b>\n"
        f"\u2022 Số tiền nạp: <b>{fmt_num(amount)}</b> (QR: <b>{fmt_num(vnd_show)}đ</b>)\n"
        f"\u2022 Nội dung chuyển khoản: <code>{user_id}</code>\n\n"
        f"Quét mã QR hoặc chuyển khoản đúng nội dung <code>{user_id}</code>, "
        f"sau đó bấm <b>\u2705 Tôi đã chuyển</b> bên dưới <i>(có thể gửi kèm ảnh bill)</i>."
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
        f"\U0001f4b8 <b>YÊU CẦU RÚT TIỀN (#R{w_id:03d})</b>\n"
        f"\u2022 Khách: <b>{user['name']}</b> (@{user['username'] or 'không_có'}) | ID: <code>{user['user_id']}</code>\n"
        f"\u2022 Số tiền rút: <b>{fmt_num(amt)}</b>\n"
        f"\u2022 Thông tin nhận: <code>{payout_txt}</code>\n"
        f"\u2022 Nhân viên chỉ định duyệt: {approver_txt}\n"
        f"\u2022 Thời gian: <code>{w_rec['created_at']}</code>"
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
            bot.send_message(source_chat_id, "\u2705 Đã gửi báo cáo về <b>nhóm Master</b>.", reply_markup=kb_full_admin_panel())
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
            f"\u2022 Số dư ví: <b>{fmt_num(float(u['balance']))}</b>\n"
            f"\u2022 Hạn mức ghi nợ: <b>{fmt_num(float(u['debt']))}</b> / <b>{fmt_num(float(u['debt_limit']))}</b>\n\n"
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
    settleable_dos = get_settleable_dos_list()

    # --- USER ACTIONS ---
    if data == "u_back":
        bot.edit_message_text(
            f"<b>MENU CHÍNH</b> — ID: <code>{uid}</code>\nSố dư ví: <b>{fmt_num(float(u['balance']))}</b>",
            chat_id,
            call.message.message_id,
            reply_markup=kb_user_main(uid),
        )

    elif data == "u_wallet":
        lim = get_user_betting_limit_info(uid)
        active_bets = []
        for d in settleable_dos:
            active_bets.extend(db.get_active_bets(do_name=d["name"], user_id=uid))
        bets_txt = "\n".join(
            [
                f"  \u2022 {b['do_name']} | {b['code']} | {b['side']} | {fmt_num(float(b['amount']))} (giá {b['price_str']})"
                for b in active_bets
            ]
        ) or "  <i>Chưa có mã nào đang chạy.</i>"

        if not open_dos:
            limit_line = f"\u2022 Hạn mức còn cược được: <b>{fmt_num(lim['unused_capital'])}</b>"
        elif len(open_dos) == 1:
            d0 = open_dos[0]
            d_lim = get_user_betting_limit_info(uid, do_id=int(d0["id"]))
            limit_line = (
                f"\u2022 Hạn mức còn cược được ({d0['name']}): "
                f"\U0001f7e2 XANH <b>{fmt_num(d_lim['max_xanh'])}</b> | \U0001f534 ĐỎ <b>{fmt_num(d_lim['max_do'])}</b>"
            )
        else:
            sub_lines = []
            for d in open_dos:
                d_lim = get_user_betting_limit_info(uid, do_id=int(d["id"]))
                sub_lines.append(
                    f"  - {d['name']}: \U0001f7e2 XANH <b>{fmt_num(d_lim['max_xanh'])}</b> | \U0001f534 ĐỎ <b>{fmt_num(d_lim['max_do'])}</b>"
                )
            limit_line = "\u2022 Hạn mức còn cược được theo từng độ:\n" + "\n".join(sub_lines)

        txt = (
            f"<b>TÀI KHOẢN CỦA BẠN</b> (ID: <code>{uid}</code>)\n"
            f"\u2022 Số dư ví: <b>{fmt_num(float(u['balance']))}</b>\n"
            f"\u2022 Đang ghi nợ: <b>{fmt_num(float(u['debt']))}</b> / Hạn mức <b>{fmt_num(float(u['debt_limit']))}</b>\n"
            f"{limit_line}\n"
            f"\u2022 Ăn/thua tới hiện tại: {fmt_signed(float(u['pnl_net']))}\n\n"
            f"<b>Mã đang đánh:</b>\n{bets_txt}"
        )
        bot.send_message(chat_id, txt, reply_markup=kb_user_main(uid))

    elif data == "u_deposit":
        bot.send_message(
            chat_id,
            f"<b>CHỌN SỐ TIỀN CẦN NẠP</b>\n"
            f"\u2022 Nạp tối thiểu: <b>{fmt_num(MIN_DEPOSIT_AMOUNT)}</b>\n"
            f"\u2022 Ngân hàng: <b>{BANK_INFO['bank']}</b> — STK: <code>{BANK_INFO['stk']}</code>\n"
            f"\u2022 Chủ TK: <b>{BANK_INFO['owner']}</b>\n"
            f"\u2022 Nội dung CK mặc định: <code>{uid}</code>\n\n"
            f"Chọn mức tiền nhanh hoặc bấm <b>Nhập số khác</b> để tạo mã QR:",
            reply_markup=kb_deposit_amounts(),
        )

    elif data.startswith("u_dep_amt_"):
        amt = float(data.split("_")[3])
        if amt < MIN_DEPOSIT_AMOUNT:
            bot.send_message(
                chat_id,
                f"\u274c Số tiền nạp tối thiểu là <b>{fmt_num(MIN_DEPOSIT_AMOUNT)}</b>.",
                reply_markup=kb_deposit_amounts(),
            )
            return
        send_deposit_qr(chat_id, uid, amt)

    elif data == "u_dep_custom":
        PENDING_STATE[uid] = {"action": "USER_DEP_CUSTOM_AMOUNT"}
        bot.send_message(
            chat_id,
            f"Nhập số tiền bạn muốn nạp (Tối thiểu: <b>{fmt_num(MIN_DEPOSIT_AMOUNT)}</b>):",
        )

    elif data == "u_paid":
        st = PENDING_STATE.get(uid)
        dep_amt = float(st["amount"]) if (st and st.get("action") == "USER_DEPOSIT_PENDING") else 0.0
        if dep_amt > 0:
            PENDING_STATE[uid] = {"action": "USER_LAST_DEP_AMT", "amount": dep_amt}
        approver_id = get_assigned_approver_id()
        approver_txt = f"<code>{approver_id}</code>" if approver_id > 0 else "<i>Tất cả nhân viên</i>"
        amt_line = f"\u2022 Số tiền khách chọn trên QR: <b>{fmt_num(dep_amt)}</b>\n" if dep_amt > 0 else ""

        bot.send_message(
            chat_id,
            f"\u2705 Đã gửi yêu cầu xác nhận nạp tiền cho ID <code>{uid}</code>.\n"
            f"<i>Nếu có ảnh bill, hãy gửi ảnh vào chat này để nhân viên đối soát nhanh.</i>",
            reply_markup=kb_user_main(uid),
        )
        if BILL_GROUP_ID:
            bot.send_message(
                BILL_GROUP_ID,
                f"\U0001f514 <b>YÊU CẦU NẠP TIỀN MỚI</b>\n"
                f"\u2022 Khách: <b>{u['name']}</b> (@{u['username'] or 'không_có'})\n"
                f"{amt_line}"
                f"\u2022 Nội dung CK / ID: <code>{uid}</code>\n"
                f"\u2022 Nhân viên chỉ định duyệt: {approver_txt}\n"
                f"\u2022 Thời gian: <code>{datetime.now().strftime('%H:%M - %d/%m/%Y')}</code>",
                reply_markup=kb_approve_deposit(uid),
            )

    elif data == "u_withdraw":
        bal = float(u["balance"])
        if bal < MIN_WITHDRAW_AMOUNT:
            bot.send_message(
                chat_id,
                f"\u274c Số dư ví hiện tại là <b>{fmt_num(bal)}</b>, chưa đạt mức rút tối thiểu <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b>.",
                reply_markup=kb_user_main(uid),
            )
        else:
            bot.send_message(
                chat_id,
                f"<b>RÚT TIỀN VỀ TÀI KHOẢN</b>\n"
                f"\u2022 Rút tối thiểu: <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b> | Tối đa: <b>{fmt_num(bal)}</b>\n\n"
                f"Chọn mức tiền rút nhanh hoặc bấm <b>Rút hết ví</b>:",
                reply_markup=kb_withdraw_amounts(bal),
            )

    elif data == "u_wd_all":
        bal = float(u["balance"])
        if bal < MIN_WITHDRAW_AMOUNT:
            bot.send_message(
                chat_id,
                f"\u274c Số dư ví hiện tại là <b>{fmt_num(bal)}</b>, chưa đạt mức rút tối thiểu <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b>.",
                reply_markup=kb_user_main(uid),
            )
        else:
            PENDING_STATE[uid] = {"action": "USER_WD_INFO", "amount": bal}
            bot.send_message(
                chat_id,
                f"<b>NHẬP THÔNG TIN NHẬN TIỀN RÚT HẾT VÍ ({fmt_num(bal)})</b>\n"
                f"Gửi <b>ảnh mã QR</b> tài khoản nhận HOẶC nhắn theo mẫu:\n"
                f"<code>Ngân hàng - Số tài khoản - Tên chủ TK</code>",
            )

    elif data.startswith("u_wd_amt_"):
        amt = float(data.split("_")[3])
        bal = float(u["balance"])
        if amt < MIN_WITHDRAW_AMOUNT:
            bot.send_message(
                chat_id,
                f"\u274c Số tiền rút tối thiểu là <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b>.",
                reply_markup=kb_withdraw_amounts(bal),
            )
        elif amt > bal + 1e-6:
            bot.send_message(
                chat_id,
                f"\u274c Số tiền rút vượt số dư ví (Tối đa: <b>{fmt_num(bal)}</b>).",
                reply_markup=kb_withdraw_amounts(bal),
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
        bal = float(u["balance"])
        PENDING_STATE[uid] = {"action": "USER_WD_AMOUNT"}
        bot.send_message(
            chat_id,
            f"Nhập số tiền bạn muốn rút (Tối thiểu: <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b> — Tối đa: <b>{fmt_num(bal)}</b>):",
        )

    elif data.startswith("u_side_"):
        side = data.split("_")[2]
        if not open_dos:
            bot.send_message(chat_id, "\u26a0\ufe0f Hiện chưa có độ nào đang mở.", reply_markup=kb_user_main(uid))
        elif len(open_dos) > 1:
            side_label = "\U0001f7e2 XANH" if side == "XANH" else "\U0001f534 ĐỎ"
            summary_lines = []
            for d in open_dos:
                p_lbl = (
                    f"{float(d['xanh_win']):g}/{float(d['xanh_lose']):g}"
                    if side == "XANH"
                    else f"{float(d['do_win']):g}/{float(d['do_lose']):g}"
                )
                summary_lines.append(f"\u2022 <b>{d['name']}</b>: giá {side_label} <code>{p_lbl}</code>")
            bot.send_message(
                chat_id,
                f"Đang mở <b>{len(open_dos)} độ</b> cùng lúc:\n"
                + "\n".join(summary_lines)
                + f"\n\nChọn độ bạn muốn đánh cửa <b>{side_label}</b>:",
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
            f"\U0001f319 <b>ĐÃ CHỐT SỔ CUỐI NGÀY</b>\n\n{snapshot}",
            reply_markup=get_panel_by_chat(target_out, "group" if target_out != uid else "private", uid),
        )

    # --- ADMIN / MASTER / STAFF ACTIONS ---
    elif data.startswith("a_") and not is_admin(uid):
        bot.answer_callback_query(call.id, "Bạn không có quyền thao tác mục này.", show_alert=True)
        return

    elif data == "a_panel_open":
        bot.send_message(chat_id, "<b>MENU ADMIN (CHAT RIÊNG)</b>", reply_markup=kb_full_admin_panel())

    elif data == "a_new_do":
        ok_time, time_msg = can_open_do_by_time()
        if not ok_time:
            bot.send_message(chat_id, time_msg, reply_markup=get_panel_by_chat(chat_id, chat_type, uid))
            return
        PENDING_STATE[uid] = {"action": "ADMIN_OPEN_SPECIFIC_DO"}
        bot.send_message(
            chat_id,
            "\U0001f3af <b>CHỌN HOẶC NHẬP SỐ ĐỘ MUỐN MỞ</b>\n"
            "\u2022 Hỗ trợ mở ngẫu nhiên theo thực tế dời lịch (ví dụ mở <b>Độ 3</b> trước, <b>Độ 2</b> sau, hoặc <b>Độ 5</b>).\n"
            "\u2022 Có thể chạy song song nhiều độ chưa chốt; độ cũ vẫn giữ nguyên để chốt khi có kết quả.",
            reply_markup=kb_open_do_picker(),
        )

    elif data.startswith("a_open_num_"):
        num_str = data.split("_")[3]
        PENDING_STATE.pop(uid, None)
        ok, msg, _ = open_specific_do_logic(num_str, exclusive_active=False)
        bot.send_message(chat_id, msg, reply_markup=get_panel_by_chat(chat_id, chat_type, uid) if ok else None)

    elif data == "a_open_custom":
        PENDING_STATE[uid] = {"action": "ADMIN_OPEN_SPECIFIC_DO"}
        bot.send_message(chat_id, "\u270f\ufe0f Nhập số độ bạn muốn mở (ví dụ: <code>3</code>, <code>5</code>, <code>12</code>):")

    elif data == "a_set_price":
        cur_open = db.get_open_dos()
        if not cur_open:
            bot.send_message(chat_id, "\u26a0\ufe0f Hiện không có độ nào đang mở để đổi giá.")
            return
        if len(cur_open) == 1:
            d0 = cur_open[0]
            bot.send_message(
                chat_id,
                f"<b>ĐỔI GIÁ {d0['name']}</b>\n"
                f"Hiện tại: XANH <code>{float(d0['xanh_win']):g}/{float(d0['xanh_lose']):g}</code> | "
                f"ĐỎ <code>{float(d0['do_win']):g}/{float(d0['do_lose']):g}</code>\n\n"
                f"Chọn 1 cửa để nhập tỷ lệ (cửa còn lại tự động đảo chiều):",
                reply_markup=kb_price_side_picker(str(d0["id"])),
            )
        else:
            bot.send_message(
                chat_id,
                "Chọn độ cần đổi giá hoặc đổi chung tất cả độ đang mở:",
                reply_markup=kb_price_do_picker(),
            )

    elif data.startswith("a_pdo_one_"):
        d_id = data.split("_")[3]
        d_obj = db.get_do_by_id(int(d_id))
        if not d_obj:
            bot.send_message(chat_id, "Không tìm thấy độ.")
            return
        bot.send_message(
            chat_id,
            f"<b>ĐỔI GIÁ {d_obj['name']}</b> — Chọn 1 cửa (cửa còn lại tự đảo):",
            reply_markup=kb_price_side_picker(d_id),
        )

    elif data == "a_pdo_all":
        bot.send_message(
            chat_id,
            "<b>ĐỔI GIÁ CHUNG TẤT CẢ ĐỘ MỞ</b> — Chọn 1 cửa (cửa còn lại tự đảo):",
            reply_markup=kb_price_side_picker("ALL"),
        )

    elif data.startswith("a_pside_"):
        _, _, chosen_side, do_target = data.split("_")
        if do_target == "ALL":
            target_ids = [int(d["id"]) for d in db.get_open_dos()]
            target_label = "Tất cả độ mở"
        else:
            d_obj = db.get_do_by_id(int(do_target))
            target_ids = [int(do_target)]
            target_label = d_obj["name"] if d_obj else "Độ hiện tại"

        PENDING_STATE[uid] = {
            "action": "ADMIN_SET_PRICE_ONE_SIDE",
            "side": chosen_side,
            "target_do_ids": target_ids,
            "target_label": target_label,
        }
        side_name = "\U0001f7e2 XANH" if chosen_side == "XANH" else "\U0001f534 ĐỎ"
        opp_name = "\U0001f534 ĐỎ" if chosen_side == "XANH" else "\U0001f7e2 XANH"
        bot.send_message(
            chat_id,
            f"Nhập tỷ lệ cho cửa <b>{side_name}</b> của <b>{target_label}</b>\n"
            f"<i>Ví dụ nhập <code>5/10</code> -> {side_name} là <b>5/10</b>, {opp_name} tự đảo thành <b>10/5</b>:</i>",
        )

    elif data == "a_multi_close_open":
        cur_open = settleable_dos
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
        sel = {int(d["id"]) for d in get_settleable_dos_list()}
        PENDING_STATE[uid] = {"action": "ADMIN_MULTI_CLOSE", "selected_ids": sel}
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=kb_multi_close_picker(sel))

    elif data == "a_mclose_clear":
        sel: set[int] = set()
        PENDING_STATE[uid] = {"action": "ADMIN_MULTI_CLOSE", "selected_ids": sel}
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=kb_multi_close_picker(sel))

    elif data.startswith("a_mclose_run_"):
        result_code = data.split("_")[3]
        st = PENDING_STATE.get(uid) or {}
        sel_ids = list(st.get("selected_ids") or [])
        if not sel_ids:
            cur_open = get_settleable_dos_list()
            if len(cur_open) == 1:
                sel_ids = [int(cur_open[0]["id"])]
        if not sel_ids:
            bot.answer_callback_query(call.id, "Bạn chưa chọn độ nào để chốt.", show_alert=True)
            return

        PENDING_STATE.pop(uid, None)
        summary = settle_multiple_dos_logic(sel_ids, result_code)
        bot.answer_callback_query(call.id, f"Đã chốt {len(summary['do_names'])} độ ({result_code}).")

        for d_name in summary["do_names"]:
            per_do_net = summary["per_do_net"].get(d_name, {})
            rep_txt = record_and_format_per_do_report(d_name, result_code, per_do_net)
            bot.send_message(chat_id, rep_txt, reply_markup=get_panel_by_chat(chat_id, chat_type, uid))
            if MASTER_GROUP_ID and chat_id != MASTER_GROUP_ID:
                bot.send_message(MASTER_GROUP_ID, rep_txt)
            if MAIN_CHANNEL_ID:
                try:
                    bot.send_message(MAIN_CHANNEL_ID, rep_txt)
                except Exception:
                    pass

    elif data == "a_set_debt_limit":
        if not is_master(uid):
            bot.answer_callback_query(call.id, "Chỉ Master mới được cấp hạn mức nợ.", show_alert=True)
            return
        PENDING_STATE[uid] = {"action": "ADMIN_SET_DEBT_LIMIT"}
        bot.send_message(
            chat_id,
            "Nhập theo mẫu: <code>ID_KHACH HAN_MUC_NO</code>\n"
            "<i>Ví dụ: <code>5770334027 10000000</code></i>",
        )

    elif data == "a_debt_menu":
        if not is_master(uid):
            bot.answer_callback_query(call.id, "Chỉ Master mới được xử lý nợ.", show_alert=True)
            return
        PENDING_STATE[uid] = {"action": "ADMIN_CLEAR_DEBT"}
        bot.send_message(
            chat_id,
            "Nhập theo mẫu: <code>ID_KHACH SO_TIEN_THU_NO</code> (nhập <code>0</code> hoặc <code>ALL</code> để xóa hết nợ):\n"
            "<i>Ví dụ: <code>5770334027 2000000</code> hoặc <code>5770334027 ALL</code></i>",
        )

    elif data == "a_assign_approver":
        if not is_master(uid):
            bot.answer_callback_query(call.id, "Chỉ Master mới được chỉ định nhân viên duyệt.", show_alert=True)
            return
        PENDING_STATE[uid] = {"action": "ADMIN_ASSIGN_APPROVER"}
        cur_app = get_assigned_approver_id()
        bot.send_message(
            chat_id,
            f"Nhân viên duyệt hiện tại: <code>{cur_app or 'Chưa chỉ định (mọi admin duyệt)'}</code>\n"
            f"Nhập <b>User ID</b> nhân viên cần chỉ định (hoặc nhập <code>0</code> để mở cho tất cả admin):",
        )

    elif data.startswith("a_dep_ok_"):
        target_uid = int(data.split("_")[3])
        ok, err = can_approve_item(uid)
        if not ok:
            bot.answer_callback_query(call.id, err, show_alert=True)
            return
        st = PENDING_STATE.get(target_uid)
        suggested = float(st["amount"]) if (st and st.get("amount")) else 0.0
        bot.send_message(
            chat_id,
            f"Chọn số tiền cộng vào ví khách ID <code>{target_uid}</code>:",
            reply_markup=kb_deposit_presets(target_uid, suggested),
        )

    elif data.startswith("a_dep_no_"):
        target_uid = int(data.split("_")[3])
        ok, err = can_approve_item(uid)
        if not ok:
            bot.answer_callback_query(call.id, err, show_alert=True)
            return
        PENDING_STATE.pop(target_uid, None)
        bot.send_message(chat_id, f"\u274c Đã từ chối yêu cầu nạp của ID <code>{target_uid}</code>.")
        try:
            bot.send_message(target_uid, "\u274c Yêu cầu nạp tiền của bạn chưa được duyệt. Vui lòng liên hệ hỗ trợ.")
        except Exception:
            pass

    elif data.startswith("a_dep_amt_"):
        parts = data.split("_")
        target_uid = int(parts[3])
        amt = float(parts[4])
        ok, err = can_approve_item(uid)
        if not ok:
            bot.answer_callback_query(call.id, err, show_alert=True)
            return
        res = apply_deposit_logic(target_uid, amt)
        PENDING_STATE.pop(target_uid, None)
        u_after = res["user"]
        bot.send_message(
            chat_id,
            f"\u2705 Đã cộng <b>{fmt_num(amt)}</b> cho <b>{u_after['name']}</b> (<code>{target_uid}</code>).\n"
            f"\u2022 Ví mới: <b>{fmt_num(float(u_after['balance']))}</b>",
        )
        try:
            bot.send_message(
                target_uid,
                f"\u2705 Nạp thành công <b>{fmt_num(amt)}</b> vào ví!\n"
                f"\u2022 Số dư hiện tại: <b>{fmt_num(float(u_after['balance']))}</b>",
                reply_markup=kb_user_main(target_uid),
            )
        except Exception:
            pass

    elif data.startswith("a_dep_custom_"):
        target_uid = int(data.split("_")[3])
        ok, err = can_approve_item(uid)
        if not ok:
            bot.answer_callback_query(call.id, err, show_alert=True)
            return
        PENDING_STATE[uid] = {"action": "ADMIN_DEP_CUSTOM_AMT", "target_uid": target_uid}
        bot.send_message(chat_id, f"Nhập số tiền muốn cộng cho khách ID <code>{target_uid}</code>:")

    elif data.startswith("a_wd_ok_"):
        w_id = int(data.split("_")[3])
        w_rec = db.get_withdrawal(w_id)
        assigned = int(w_rec["assigned_staff_id"] or 0) if w_rec else 0
        ok, err = can_approve_item(uid, assigned if assigned > 0 else None)
        if not ok:
            bot.answer_callback_query(call.id, err, show_alert=True)
            return
        done, msg, info = confirm_withdrawal_logic(w_id)
        bot.send_message(chat_id, msg)
        if done and info:
            u_wd = info["user"]
            try:
                bot.send_message(
                    int(u_wd["user_id"]),
                    f"\u2705 Lệnh rút <code>#R{w_id:03d}</code> (<b>{fmt_num(float(w_rec['amount']))}</b>) đã được chuyển tiền!",
                    reply_markup=kb_user_main(int(u_wd["user_id"])),
                )
            except Exception:
                pass

    elif data.startswith("a_wd_no_"):
        w_id = int(data.split("_")[3])
        w_rec = db.get_withdrawal(w_id)
        assigned = int(w_rec["assigned_staff_id"] or 0) if w_rec else 0
        ok, err = can_approve_item(uid, assigned if assigned > 0 else None)
        if not ok:
            bot.answer_callback_query(call.id, err, show_alert=True)
            return
        done, msg, info = reject_withdrawal_logic(w_id)
        bot.send_message(chat_id, msg)
        if done and info and w_rec:
            try:
                bot.send_message(
                    int(w_rec["user_id"]),
                    f"\u274c Lệnh rút <code>#R{w_id:03d}</code> bị từ chối và đã hoàn lại <b>{fmt_num(float(w_rec['amount']))}</b> vào ví.",
                    reply_markup=kb_user_main(int(w_rec["user_id"])),
                )
            except Exception:
                pass

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


# ==================== XỬ LÝ TIN NHẮN & ẢNH THEO TRẠNG THÁI ====================
@bot.message_handler(content_types=["photo"])
def on_photo(message: types.Message):
    uid = message.from_user.id
    chat_id = message.chat.id
    st = PENDING_STATE.get(uid)
    photo_id = message.photo[-1].file_id

    if st and st.get("action") == "USER_WD_INFO":
        amt = float(st["amount"])
        PENDING_STATE.pop(uid, None)
        ok, msg, w_rec = create_withdrawal_logic(uid, amt, payout_text="", qr_file_id=photo_id)
        bot.send_message(chat_id, msg, reply_markup=kb_user_main(uid))
        if ok and w_rec:
            u = db.get_user(uid, message.from_user.first_name, message.from_user.username)
            dispatch_withdrawal_to_bill_group(u, w_rec)
        return

    if BILL_GROUP_ID:
        u = db.get_user(uid, message.from_user.first_name, message.from_user.username)
        approver_id = get_assigned_approver_id()
        approver_txt = f"<code>{approver_id}</code>" if approver_id > 0 else "<i>Tất cả nhân viên</i>"
        cap = (
            f"\U0001f9fe <b>BILL NẠP TIỀN TỪ KHÁCH</b>\n"
            f"\u2022 Khách: <b>{u['name']}</b> (@{u['username'] or 'không_có'}) | ID: <code>{uid}</code>\n"
            f"\u2022 Nhân viên chỉ định duyệt: {approver_txt}\n"
            f"\u2022 Thời gian: <code>{datetime.now().strftime('%H:%M - %d/%m/%Y')}</code>"
        )
        bot.send_photo(BILL_GROUP_ID, photo_id, caption=cap, reply_markup=kb_approve_deposit(uid))


@bot.message_handler(func=lambda m: bool(m.text and not m.text.startswith("/")))
def on_text(message: types.Message):
    uid = message.from_user.id
    chat_id = message.chat.id
    chat_type = message.chat.type
    raw = message.text.strip()
    st = PENDING_STATE.get(uid)

    if not st:
        return

    act = st.get("action")

    # --- ADMIN STATES ---
    if act == "ADMIN_OPEN_SPECIFIC_DO" and is_admin(uid):
        PENDING_STATE.pop(uid, None)
        ok, msg, _ = open_specific_do_logic(raw, exclusive_active=False)
        bot.send_message(chat_id, msg, reply_markup=get_panel_by_chat(chat_id, chat_type, uid) if ok else None)
        return

    if act == "ADMIN_SET_PRICE_ONE_SIDE" and is_admin(uid):
        target_ids: list[int] = st.get("target_do_ids") or []
        chosen_side = st.get("side") or "XANH"
        target_label = st.get("target_label") or ""
        try:
            ratio_map = parse_single_side_ratio(f"{chosen_side} {raw}", chosen_side)
        except Exception:
            bot.send_message(chat_id, "\u274c Tỷ lệ không hợp lệ. Ví dụ nhập: <code>5/10</code> hoặc <code>10/8</code>.")
            return

        PENDING_STATE.pop(uid, None)
        for d_id in target_ids:
            db.update_do(d_id, **ratio_map)
        bot.send_message(
            chat_id,
            f"\u2705 Đã cập nhật giá cho <b>{target_label}</b>:\n"
            f"\u2022 XANH: <code>{ratio_map['xanh_win']:g}/{ratio_map['xanh_lose']:g}</code>\n"
            f"\u2022 ĐỎ: <code>{ratio_map['do_win']:g}/{ratio_map['do_lose']:g}</code>",
            reply_markup=get_panel_by_chat(chat_id, chat_type, uid),
        )
        return

    if act == "ADMIN_SET_DEBT_LIMIT" and is_master(uid):
        parts = raw.split()
        if len(parts) != 2 or not parts[0].isdigit():
            bot.send_message(chat_id, "\u274c Sai cú pháp. Nhập: <code>ID_KHACH HAN_MUC_NO</code>")
            return
        t_uid = int(parts[0])
        try:
            d_lim = float(parts[1].replace(".", "").replace(",", ""))
        except ValueError:
            bot.send_message(chat_id, "\u274c Hạn mức nợ không hợp lệ.")
            return
        PENDING_STATE.pop(uid, None)
        db.get_user(t_uid)
        db.update_user_fields(t_uid, debt_limit=max(0.0, d_lim))
        u_after = db.get_user(t_uid)
        bot.send_message(
            chat_id,
            f"\u2705 Đã cập nhật hạn mức nợ cho ID <code>{t_uid}</code>: <b>{fmt_num(float(u_after['debt_limit']))}</b>",
            reply_markup=get_panel_by_chat(chat_id, chat_type, uid),
        )
        return

    if act == "ADMIN_CLEAR_DEBT" and is_master(uid):
        parts = raw.split()
        if len(parts) != 2 or not parts[0].isdigit():
            bot.send_message(chat_id, "\u274c Sai cú pháp. Nhập: <code>ID_KHACH SO_TIEN_HOAC_ALL</code>")
            return
        t_uid = int(parts[0])
        u_cur = db.get_user(t_uid)
        cur_debt = float(u_cur["debt"])
        PENDING_STATE.pop(uid, None)
        if parts[1].upper() == "ALL":
            paid = cur_debt
            new_debt = 0.0
        else:
            try:
                paid = float(parts[1].replace(".", "").replace(",", ""))
            except ValueError:
                bot.send_message(chat_id, "\u274c Số tiền không hợp lệ.")
                return
            new_debt = max(0.0, cur_debt - paid)
        db.update_user_fields(t_uid, debt=new_debt)
        bot.send_message(
            chat_id,
            f"\u2705 Đã thu/xóa <b>{fmt_num(paid)}</b> nợ cho ID <code>{t_uid}</code>. Nợ còn lại: <b>{fmt_num(new_debt)}</b>",
            reply_markup=get_panel_by_chat(chat_id, chat_type, uid),
        )
        return

    if act == "ADMIN_ASSIGN_APPROVER" and is_master(uid):
        if not raw.isdigit():
            bot.send_message(chat_id, "\u274c Vui lòng nhập User ID dạng số (hoặc <code>0</code>).")
            return
        staff_id = int(raw)
        PENDING_STATE.pop(uid, None)
        set_assigned_approver_id(staff_id)
        txt = f"\u2705 Đã chỉ định nhân viên duyệt ID <code>{staff_id}</code>." if staff_id > 0 else "\u2705 Đã mở quyền duyệt cho tất cả admin."
        bot.send_message(chat_id, txt, reply_markup=get_panel_by_chat(chat_id, chat_type, uid))
        return

    if act == "ADMIN_DEP_CUSTOM_AMT" and is_admin(uid):
        t_uid = int(st["target_uid"])
        try:
            amt = float(raw.replace(".", "").replace(",", ""))
            if amt <= 0:
                raise ValueError()
        except ValueError:
            bot.send_message(chat_id, "\u274c Số tiền không hợp lệ.")
            return
        PENDING_STATE.pop(uid, None)
        res = apply_deposit_logic(t_uid, amt)
        u_after = res["user"]
        bot.send_message(
            chat_id,
            f"\u2705 Đã cộng <b>{fmt_num(amt)}</b> cho ID <code>{t_uid}</code>. Ví mới: <b>{fmt_num(float(u_after['balance']))}</b>",
        )
        try:
            bot.send_message(
                t_uid,
                f"\u2705 Nạp thành công <b>{fmt_num(amt)}</b> vào ví!\n\u2022 Số dư hiện tại: <b>{fmt_num(float(u_after['balance']))}</b>",
                reply_markup=kb_user_main(t_uid),
            )
        except Exception:
            pass
        return

    # --- USER STATES ---
    if act == "USER_DEP_CUSTOM_AMOUNT":
        try:
            amt = float(raw.replace(".", "").replace(",", ""))
        except ValueError:
            bot.send_message(chat_id, "\u274c Vui lòng nhập số tiền hợp lệ.")
            return
        if amt < MIN_DEPOSIT_AMOUNT:
            bot.send_message(chat_id, f"\u274c Số tiền nạp tối thiểu là <b>{fmt_num(MIN_DEPOSIT_AMOUNT)}</b>.")
            return
        PENDING_STATE.pop(uid, None)
        send_deposit_qr(chat_id, uid, amt)
        return

    if act == "USER_WD_AMOUNT":
        try:
            amt = float(raw.replace(".", "").replace(",", ""))
        except ValueError:
            bot.send_message(chat_id, "\u274c Vui lòng nhập số tiền hợp lệ.")
            return
        u_cur = db.get_user(uid)
        bal = float(u_cur["balance"])
        if amt < MIN_WITHDRAW_AMOUNT:
            bot.send_message(chat_id, f"\u274c Số tiền rút tối thiểu là <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b>.")
            return
        if amt > bal + 1e-6:
            bot.send_message(chat_id, f"\u274c Số tiền rút vượt số dư ví (Tối đa: <b>{fmt_num(bal)}</b>).")
            return
        PENDING_STATE[uid] = {"action": "USER_WD_INFO", "amount": amt}
        bot.send_message(
            chat_id,
            f"<b>NHẬP THÔNG TIN NHẬN TIỀN RÚT ({fmt_num(amt)})</b>\n"
            f"Gửi <b>ảnh mã QR</b> tài khoản nhận HOẶC nhắn theo mẫu:\n"
            f"<code>Ngân hàng - Số tài khoản - Tên chủ TK</code>",
        )
        return

    if act == "USER_WD_INFO":
        amt = float(st["amount"])
        PENDING_STATE.pop(uid, None)
        ok, msg, w_rec = create_withdrawal_logic(uid, amt, payout_text=raw, qr_file_id="")
        bot.send_message(chat_id, msg, reply_markup=kb_user_main(uid))
        if ok and w_rec:
            u_cur = db.get_user(uid, message.from_user.first_name, message.from_user.username)
            dispatch_withdrawal_to_bill_group(u_cur, w_rec)
        return

    if act == "USER_BET_AMOUNT":
        try:
            amt = float(raw.replace(".", "").replace(",", ""))
        except ValueError:
            bot.send_message(chat_id, "\u274c Vui lòng nhập số tiền hợp lệ.")
            return
        side = st["side"]
        do_id = int(st["do_id"])
        PENDING_STATE.pop(uid, None)
        _, res_msg = place_bet_logic(uid, amt, side, do_id=do_id)
        bot.send_message(chat_id, res_msg, reply_markup=kb_user_main(uid))
        return


def run_bot() -> None:
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=int(PORT or 10000))
