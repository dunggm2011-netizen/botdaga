import threading
from datetime import datetime
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
    place_bet_logic,
    apply_deposit_logic,
    create_withdrawal_logic,
    confirm_withdrawal_logic,
    reject_withdrawal_logic,
    settle_do_logic,
    build_live_report_text,
    build_pnl_report_text,
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


def kb_user_deposit() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Tôi đã chuyển", callback_data="u_paid"),
        types.InlineKeyboardButton("🔙 Quay lại", callback_data="u_back"),
    )
    return kb


def kb_user_amounts(side: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=3)
    btns = [
        types.InlineKeyboardButton(fmt_num(amt), callback_data=f"u_bet_{side}_{amt}")
        for amt in QUICK_BET_AMOUNTS
    ]
    kb.add(*btns)
    kb.add(
        types.InlineKeyboardButton("✏️ Nhập số khác", callback_data=f"u_custom_{side}"),
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


def kb_master_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📋 Độ & Mã đang chạy", callback_data="a_rep_live"),
        types.InlineKeyboardButton("📈 Ăn thua tới hiện tại", callback_data="a_rep_pnl"),
    )
    kb.add(
        types.InlineKeyboardButton("💳 Cấp hạn mức nợ", callback_data="a_set_debt_limit"),
        types.InlineKeyboardButton("💵 Thu nợ / Xóa nợ", callback_data="a_debt_menu"),
    )
    kb.add(types.InlineKeyboardButton("👤 Chỉ định nhân viên duyệt", callback_data="a_assign_approver"))
    return kb


def kb_price_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✏️ Đổi giá độ", callback_data="a_set_price"),
        types.InlineKeyboardButton("🏁 Chốt độ", callback_data="a_close_menu"),
    )
    return kb


def kb_bill_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("👤 Chỉ định nhân viên duyệt", callback_data="a_assign_approver"))
    return kb


def kb_full_admin_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📋 Độ & Mã đang chạy", callback_data="a_rep_live"),
        types.InlineKeyboardButton("📈 Ăn thua tới hiện tại", callback_data="a_rep_pnl"),
    )
    kb.add(
        types.InlineKeyboardButton("✏️ Đổi giá độ", callback_data="a_set_price"),
        types.InlineKeyboardButton("🏁 Chốt độ", callback_data="a_close_menu"),
    )
    kb.add(
        types.InlineKeyboardButton("💳 Cấp hạn mức nợ", callback_data="a_set_debt_limit"),
        types.InlineKeyboardButton("💵 Thu nợ / Xóa nợ", callback_data="a_debt_menu"),
    )
    kb.add(types.InlineKeyboardButton("👤 Chỉ định nhân viên duyệt", callback_data="a_assign_approver"))
    return kb


def kb_close_results() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🟢 XANH thắng", callback_data="a_res_XANH"),
        types.InlineKeyboardButton("🔴 ĐỎ thắng", callback_data="a_res_DO"),
        types.InlineKeyboardButton("🤝 Hòa", callback_data="a_res_DRAW"),
        types.InlineKeyboardButton("🚫 Hủy độ", callback_data="a_res_CANCEL"),
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


def kb_deposit_presets(user_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=3)
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


def get_panel_by_chat(chat_id: int, chat_type: str) -> types.InlineKeyboardMarkup:
    if chat_type == "private":
        return kb_full_admin_panel()
    if chat_id == MASTER_GROUP_ID:
        return kb_master_panel()
    if chat_id == PRICE_GROUP_ID:
        return kb_price_panel()
    if chat_id == BILL_GROUP_ID:
        return kb_bill_panel()
    return kb_full_admin_panel()


# ==================== LỆNH KHỞI TẠO ====================
@bot.message_handler(commands=["start", "menu"])
def cmd_start(message: types.Message):
    uid = message.from_user.id
    if message.chat.type == "private":
        u = db.get_user(uid, message.from_user.first_name, message.from_user.username)
        txt = (
            f"Xin chào <b>{u['name']}</b> (ID: <code>{u['user_id']}</code>)\n"
            f"• Số dư ví: <b>{fmt_num(float(u['balance']))}</b>\n"
            f"• Hạn mức ghi nợ: <b>{fmt_num(float(u['debt']))}</b> / <b>{fmt_num(float(u['debt_limit']))}</b>\n\n"
            f"Chọn nút bên dưới để thao tác:"
        )
        bot.send_message(message.chat.id, txt, reply_markup=kb_user_main(uid))
    elif is_admin(uid):
        bot.send_message(
            message.chat.id,
            "<b>BẢNG ĐIỀU KHIỂN VẬN HÀNH</b>",
            reply_markup=get_panel_by_chat(message.chat.id, message.chat.type),
        )


@bot.message_handler(commands=["admin"])
def cmd_admin(message: types.Message):
    uid = message.from_user.id
    if is_admin(uid):
        bot.send_message(
            message.chat.id,
            "<b>BẢNG ĐIỀU KHIỂN ADMIN</b>",
            reply_markup=get_panel_by_chat(message.chat.id, message.chat.type),
        )


# ==================== XỬ LÝ NÚT BẤM ====================
@bot.callback_query_handler(func=lambda call: True)
def on_callback(call: types.CallbackQuery):
    data = call.data
    uid = call.from_user.id
    chat_id = call.message.chat.id
    chat_type = call.message.chat.type
    u = db.get_user(uid, call.from_user.first_name, call.from_user.username)
    do = db.get_current_do()

    # --- USER ACTIONS ---
    if data == "u_back":
        bot.edit_message_text(
            f"<b>MENU CHÍNH</b> — ID: <code>{uid}</code>\nSố dư ví: <b>{fmt_num(float(u['balance']))}</b>",
            chat_id,
            call.message.message_id,
            reply_markup=kb_user_main(uid),
        )

    elif data == "u_wallet":
        active_bets = db.get_active_bets(do_name=do["name"], user_id=uid)
        bets_txt = "\n".join(
            [f"  • {b['code']} | {b['side']} | {fmt_num(float(b['amount']))} (giá {b['price_str']})" for b in active_bets]
        ) or "  <i>Chưa có mã nào đang chạy.</i>"
        txt = (
            f"<b>TÀI KHOẢN CỦA BẠN</b> (ID: <code>{uid}</code>)\n"
            f"• Số dư ví: <b>{fmt_num(float(u['balance']))}</b>\n"
            f"• Đang ghi nợ: <b>{fmt_num(float(u['debt']))}</b> / Hạn mức <b>{fmt_num(float(u['debt_limit']))}</b>\n"
            f"• Ăn/thua tới hiện tại: {fmt_signed(float(u['pnl_net']))}\n\n"
            f"<b>Mã đang đánh ({do['name']}):</b>\n{bets_txt}"
        )
        bot.send_message(chat_id, txt, reply_markup=kb_user_main(uid))

    elif data == "u_deposit":
        caption = (
            f"<b>THÔNG TIN NẠP TIỀN</b>\n"
            f"• Ngân hàng: <b>{BANK_INFO['bank']}</b>\n"
            f"• Số tài khoản: <code>{BANK_INFO['stk']}</code>\n"
            f"• Chủ tài khoản: <b>{BANK_INFO['owner']}</b>\n"
            f"• Nội dung chuyển khoản: <code>{uid}</code>\n\n"
            f"Chuyển xong bấm <b>✅ Tôi đã chuyển</b> bên dưới <i>(có thể gửi kèm ảnh bill)</i>."
        )
        try:
            bot.send_photo(chat_id, BANK_INFO["qr_url"], caption=caption, reply_markup=kb_user_deposit())
        except Exception:
            bot.send_message(chat_id, caption, reply_markup=kb_user_deposit())

    elif data == "u_paid":
        approver_id = get_assigned_approver_id()
        approver_txt = f"<code>{approver_id}</code>" if approver_id > 0 else "<i>Nhân viên trực</i>"
        bot.send_message(
            chat_id,
            f"✅ Đã gửi yêu cầu xác nhận nạp tiền cho ID <code>{uid}</code>.\n"
            f"<i>Nếu có ảnh bill, hãy gửi ảnh vào chat này để duyệt nhanh hơn.</i>",
            reply_markup=kb_user_main(uid),
        )
        if BILL_GROUP_ID:
            bot.send_message(
                BILL_GROUP_ID,
                f"🔔 <b>YÊU CẦU NẠP TIỀN MỚI</b>\n"
                f"• Khách: <b>{u['name']}</b> (@{u['username'] or 'không_có'})\n"
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
        win_u = float(do["xanh_win"] if side == "XANH" else do["do_win"])
        lose_u = float(do["xanh_lose"] if side == "XANH" else do["do_lose"])
        side_label = "🟢 XANH" if side == "XANH" else "🔴 ĐỎ"
        txt = (
            f"<b>{do['name']} — CỬA {side_label}</b>\n"
            f"• Giá hiện tại: <b>{win_u:g}/{lose_u:g}</b> <i>(trừ 5% phế trên tiền lời)</i>\n"
            f"• Ví: <b>{fmt_num(float(u['balance']))}</b> | Nợ: <b>{fmt_num(float(u['debt']))}/{fmt_num(float(u['debt_limit']))}</b>\n\n"
            f"Chọn mức tiền nhanh hoặc bấm <b>Nhập số khác</b>:"
        )
        bot.send_message(chat_id, txt, reply_markup=kb_user_amounts(side))

    elif data.startswith("u_bet_"):
        _, _, side, amt_str = data.split("_")
        _, res_msg = place_bet_logic(uid, float(amt_str), side)
        bot.send_message(chat_id, res_msg, reply_markup=kb_user_main(uid))

    elif data.startswith("u_custom_"):
        side = data.split("_")[2]
        PENDING_STATE[uid] = {"action": "USER_BET_AMOUNT", "side": side}
        bot.send_message(chat_id, f"Nhập số tiền bạn muốn đánh cửa <b>{side}</b>:")

    # --- ADMIN / MASTER / STAFF ACTIONS ---
    elif data.startswith("a_") and not is_admin(uid):
        bot.answer_callback_query(call.id, "Bạn không có quyền thao tác mục này.", show_alert=True)
        return

    elif data == "a_panel_open":
        bot.send_message(chat_id, "<b>MENU ADMIN (CHAT RIÊNG)</b>", reply_markup=kb_full_admin_panel())

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
        if BILL_GROUP_ID:
            bot.send_message(BILL_GROUP_ID, f"❌ Đã từ chối yêu cầu nạp của ID <code>{target_uid}</code>.")
        bot.send_message(target_uid, "❌ Yêu cầu nạp tiền của bạn chưa được xác nhận.")

    elif data.startswith("a_dep_ok_"):
        ok_perm, perm_msg = can_approve_item(uid)
        if not ok_perm:
            bot.answer_callback_query(call.id, "Chưa tới lượt duyệt của bạn!", show_alert=True)
            bot.send_message(chat_id, perm_msg)
            return
        target_uid = int(data.split("_")[3])
        bot.send_message(
            chat_id,
            f"Chọn nhanh số tiền cộng cho ID <code>{target_uid}</code> hoặc nhập số khác:",
            reply_markup=kb_deposit_presets(target_uid),
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
        from database import get_withdrawal
        w_item = get_withdrawal(w_id)
        ok_perm, perm_msg = can_approve_item(uid, w_item["assigned_staff_id"] if w_item else 0)
        if not ok_perm:
            bot.answer_callback_query(call.id, "Không đúng nhân viên được chỉ định duyệt!", show_alert=True)
            bot.send_message(chat_id, perm_msg)
            return

        ok_wd, wd_msg, wd_info = confirm_withdrawal_logic(w_id)
        bot.send_message(chat_id, wd_msg, reply_markup=get_panel_by_chat(chat_id, chat_type))
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
        from database import get_withdrawal
        w_item = get_withdrawal(w_id)
        ok_perm, perm_msg = can_approve_item(uid, w_item["assigned_staff_id"] if w_item else 0)
        if not ok_perm:
            bot.answer_callback_query(call.id, "Không đúng nhân viên được chỉ định duyệt!", show_alert=True)
            bot.send_message(chat_id, perm_msg)
            return

        ok_wd, wd_msg, wd_info = reject_withdrawal_logic(w_id)
        bot.send_message(chat_id, wd_msg, reply_markup=get_panel_by_chat(chat_id, chat_type))
        if ok_wd and wd_info:
            w_rec = wd_info["withdrawal"]
            bot.send_message(
                int(w_rec["user_id"]),
                f"❌ Lệnh rút <code>#R{w_id:03d}</code> (<b>{fmt_num(float(w_rec['amount']))}</b>) đã bị từ chối và hoàn lại ví.\n"
                f"• Ví hiện tại: <b>{fmt_num(float(wd_info['balance']))}</b>",
                reply_markup=kb_user_main(int(w_rec["user_id"])),
            )

    elif data == "a_set_price":
        PENDING_STATE[uid] = {"action": "ADMIN_SET_PRICE"}
        bot.send_message(
            chat_id,
            f"<b>ĐỔI GIÁ {do['name']}</b>\n"
            f"Hiện tại: XANH <code>{float(do['xanh_win']):g}/{float(do['xanh_lose']):g}</code> | "
            f"ĐỎ <code>{float(do['do_win']):g}/{float(do['do_lose']):g}</code>\n\n"
            f"Nhập theo mẫu: <code>XANH 10/10 | DO 10/5</code>",
        )

    elif data == "a_close_menu":
        bot.send_message(
            chat_id,
            f"Chọn kết quả chốt cho <b>{do['name']}</b>:",
            reply_markup=kb_close_results(),
        )

    elif data.startswith("a_res_"):
        result = data.split("_")[2]
        out = settle_do_logic(result)
        target_out_chat = PRICE_GROUP_ID if chat_type != "private" and PRICE_GROUP_ID else chat_id
        panel_out = get_panel_by_chat(target_out_chat, "group" if target_out_chat != uid else "private")

        if out["mode"] == "CANCEL":
            bot.send_message(target_out_chat, f"🚫 Đã hủy <b>{out['do_name']}</b>. Sang <b>{out['next_do']}</b>.", reply_markup=panel_out)
        elif out["mode"] == "DRAW":
            bot.send_message(target_out_chat, f"🤝 <b>{out['do_name']}</b> hòa. Sang <b>{out['next_do']}</b>.", reply_markup=panel_out)
        else:
            lines = [f"<b>TỔNG KẾT {out['do_name']} — {out['result']} THẮNG</b>"]
            for item in out["users"]:
                lines.append(
                    f"• <b>{item['name']}</b> (<code>{item['user_id']}</code>): {fmt_signed(item['net'])} | "
                    f"Ví: <b>{fmt_num(item['balance'])}</b> | Nợ: <b>{fmt_num(item['debt'])}</b>"
                )
                bot.send_message(
                    item["user_id"],
                    f"<b>KẾT QUẢ {out['do_name']}: {out['result']} THẮNG</b>\n"
                    f"• Kết quả của bạn: {fmt_signed(item['net'])}\n"
                    f"• Ví: <b>{fmt_num(item['balance'])}</b> | Nợ: <b>{fmt_num(item['debt'])}</b>",
                    reply_markup=kb_user_main(item["user_id"]),
                )
            lines.append(f"\n➡️ Chuyển sang <b>{out['next_do']}</b>.")
            bot.send_message(target_out_chat, "\n".join(lines), reply_markup=panel_out)

    elif data == "a_set_debt_limit":
        if not is_master(uid):
            bot.answer_callback_query(call.id, "Chỉ Master mới được cấp hạn mức nợ.", show_alert=True)
            return
        PENDING_STATE[uid] = {"action": "MASTER_SET_DEBT_LIMIT"}
        bot.send_message(
            chat_id,
            "Nhập <code>USER_ID HẠN_MỨC_NỢ</code>\nVí dụ: <code>123456 50000</code> (nhập <code>0</code> để khóa ghi nợ).",
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
            "Ví dụ: <code>THU 123456 25000</code>",
        )

    elif data == "a_rep_live":
        if not is_master(uid) and chat_type != "private":
            bot.answer_callback_query(call.id, "Chỉ Master mới xem báo cáo này.", show_alert=True)
            return
        bot.send_message(chat_id, build_live_report_text(), reply_markup=get_panel_by_chat(chat_id, chat_type))

    elif data == "a_rep_pnl":
        if not is_master(uid) and chat_type != "private":
            bot.answer_callback_query(call.id, "Chỉ Master mới xem báo cáo này.", show_alert=True)
            return
        bot.send_message(chat_id, build_pnl_report_text(), reply_markup=get_panel_by_chat(chat_id, chat_type))

    bot.answer_callback_query(call.id)


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


# ==================== XỬ LÝ ẢNH (BILL NẠP HOẶC QR RÚT TIỀN) ====================
@bot.message_handler(content_types=["photo"])
def on_photo(message: types.Message):
    if message.chat.type != "private":
        return
    uid = message.from_user.id
    u = db.get_user(uid, message.from_user.first_name, message.from_user.username)
    file_id = message.photo[-1].file_id
    state = PENDING_STATE.get(uid)

    # Nếu đang chờ thông tin nhận tiền rút -> nhận ảnh QR rút tiền
    if state and state.get("action") == "USER_WD_INFO":
        PENDING_STATE.pop(uid, None)
        amt = float(state["amount"])
        ok, msg, w_rec = create_withdrawal_logic(uid, amt, payout_text="", qr_file_id=file_id)
        bot.send_message(message.chat.id, msg, reply_markup=kb_user_main(uid))
        if ok and w_rec:
            dispatch_withdrawal_to_bill_group(u, w_rec)
        return

    # Mặc định ảnh ở chat riêng là bill nạp tiền
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

        if act == "USER_BET_AMOUNT":
            PENDING_STATE.pop(uid, None)
            try:
                amt = float(txt.replace(".", "").replace(",", ""))
            except ValueError:
                bot.send_message(message.chat.id, "Vui lòng nhập số hợp lệ.", reply_markup=kb_user_main(uid))
                return
            _, res_msg = place_bet_logic(uid, amt, state["side"])
            bot.send_message(message.chat.id, res_msg, reply_markup=kb_user_main(uid))
            return

        if act == "USER_WD_AMOUNT":
            try:
                amt = float(txt.replace(".", "").replace(",", ""))
            except ValueError:
                bot.send_message(message.chat.id, "Vui lòng nhập số tiền hợp lệ.", reply_markup=kb_user_main(uid))
                PENDING_STATE.pop(uid, None)
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
                    reply_markup=get_panel_by_chat(message.chat.id, message.chat.type),
                )
            except ValueError:
                bot.send_message(message.chat.id, "Vui lòng nhập Telegram ID dạng số (ví dụ <code>222222222</code> hoặc <code>0</code>).")
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
                updates = {}
                for part in [p.strip() for p in txt.split("|")]:
                    tokens = part.upper().split()
                    side = tokens[0]
                    win_v, lose_v = [float(x) for x in tokens[1].split("/")]
                    if side == "XANH":
                        updates["xanh_win"] = win_v
                        updates["xanh_lose"] = lose_v
                    elif side == "DO":
                        updates["do_win"] = win_v
                        updates["do_lose"] = lose_v
                db.update_current_do(**updates)
                do_new = db.get_current_do()
                bot.send_message(
                    message.chat.id,
                    f"✅ Đã cập nhật giá <b>{do_new['name']}</b>:\n"
                    f"• XANH: <b>{float(do_new['xanh_win']):g}/{float(do_new['xanh_lose']):g}</b>\n"
                    f"• ĐỎ: <b>{float(do_new['do_win']):g}/{float(do_new['do_lose']):g}</b>",
                    reply_markup=get_panel_by_chat(message.chat.id, message.chat.type),
                )
            except Exception:
                bot.send_message(
                    message.chat.id,
                    "Sai định dạng. Mẫu đúng: <code>XANH 10/10 | DO 10/5</code>",
                    reply_markup=get_panel_by_chat(message.chat.id, message.chat.type),
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
                    reply_markup=get_panel_by_chat(message.chat.id, message.chat.type),
                )
            except Exception:
                bot.send_message(
                    message.chat.id,
                    "Sai mẫu. Ví dụ đúng: <code>123456 50000</code>",
                    reply_markup=get_panel_by_chat(message.chat.id, message.chat.type),
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
                        reply_markup=get_panel_by_chat(message.chat.id, message.chat.type),
                    )
                elif cmd_type == "XOA":
                    db.update_user_fields(target_uid, debt=0.0)
                    bot.send_message(
                        message.chat.id,
                        f"🗑️ Đã xóa nợ treo <b>{fmt_num(cur_debt)}</b> cho ID <code>{target_uid}</code>.",
                        reply_markup=get_panel_by_chat(message.chat.id, message.chat.type),
                    )
                else:
                    bot.send_message(
                        message.chat.id,
                        "Dùng <code>THU ID SỐ_TIỀN</code> hoặc <code>XOA ID</code>.",
                        reply_markup=get_panel_by_chat(message.chat.id, message.chat.type),
                    )
            except Exception:
                bot.send_message(
                    message.chat.id,
                    "Sai mẫu thu/xóa nợ.",
                    reply_markup=get_panel_by_chat(message.chat.id, message.chat.type),
                )


def run_bot() -> None:
    print("Bot đá gà đang khởi chạy...")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=25)


if __name__ == "__main__":
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=PORT)
