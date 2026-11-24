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


# ==================== B\u1ed8 N\xdaT B\u1ea4M ====================
def kb_user_main(user_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\U0001f4b3 N\u1ea1p ti\u1ec1n", callback_data="u_deposit"),
        types.InlineKeyboardButton("\U0001f4b8 R\xfat ti\u1ec1n", callback_data="u_withdraw"),
    )
    kb.add(
        types.InlineKeyboardButton("\U0001f7e2 \u0110\xe1nh XANH", callback_data="u_side_XANH"),
        types.InlineKeyboardButton("\U0001f534 \u0110\xe1nh \u0110\u1ece", callback_data="u_side_DO"),
    )
    kb.add(types.InlineKeyboardButton("\U0001f4b0 S\u1ed1 d\u01b0 & M\xe3 \u0111ang \u0111\xe1nh", callback_data="u_wallet"))
    if is_admin(user_id):
        kb.add(types.InlineKeyboardButton("\U0001f6e0\ufe0f Menu Admin (Chat ri\xeang)", callback_data="a_panel_open"))
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
    kb.add(types.InlineKeyboardButton("\U0001f519 Quay l\u1ea1i", callback_data="u_back"))
    return kb


def kb_deposit_amounts() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=3)
    btns = [
        types.InlineKeyboardButton(fmt_num(amt), callback_data=f"u_dep_amt_{amt}")
        for amt in QUICK_DEPOSIT_AMOUNTS
    ]
    kb.add(*btns)
    kb.add(
        types.InlineKeyboardButton("\u270f\ufe0f Nh\u1eadp s\u1ed1 kh\xe1c", callback_data="u_dep_custom"),
        types.InlineKeyboardButton("\U0001f519 Quay l\u1ea1i", callback_data="u_back"),
    )
    return kb


def kb_user_deposit() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\u2705 T\xf4i \u0111\xe3 chuy\u1ec3n", callback_data="u_paid"),
        types.InlineKeyboardButton("\U0001f519 Quay l\u1ea1i", callback_data="u_back"),
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
        types.InlineKeyboardButton("\u270f\ufe0f Nh\u1eadp s\u1ed1 kh\xe1c", callback_data=f"u_custom_{side}_{do_id}"),
        types.InlineKeyboardButton("\U0001f519 Quay l\u1ea1i", callback_data="u_back"),
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
    kb.add(types.InlineKeyboardButton(f"\U0001f4b0 R\xfat h\u1ebft v\xed ({fmt_num(bal)})", callback_data="u_wd_all"))
    kb.add(
        types.InlineKeyboardButton("\u270f\ufe0f Nh\u1eadp s\u1ed1 r\xfat kh\xe1c", callback_data="u_wd_custom"),
        types.InlineKeyboardButton("\U0001f519 Quay l\u1ea1i", callback_data="u_back"),
    )
    return kb


def kb_master_viewer_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\U0001f4cb \u0110\u1ed9 \u0111ang ch\u1ea1y", callback_data="a_rep_live"),
        types.InlineKeyboardButton("\U0001f4c8 \u0102n thua t\u1eebng \u0111\u1ed9 / HT", callback_data="a_rep_pnl"),
    )
    kb.add(types.InlineKeyboardButton("\U0001f4ca B\xe1o c\xe1o \u0103n thua c\u1ea3 ng\xe0y", callback_data="a_rep_daily"))
    return kb


def kb_master_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\U0001f4cb \u0110\u1ed9 \u0111ang ch\u1ea1y", callback_data="a_rep_live"),
        types.InlineKeyboardButton("\U0001f4c8 \u0102n thua t\u1eebng \u0111\u1ed9 / HT", callback_data="a_rep_pnl"),
    )
    kb.add(types.InlineKeyboardButton("\U0001f4ca B\xe1o c\xe1o \u0103n thua c\u1ea3 ng\xe0y", callback_data="a_rep_daily"))
    kb.add(
        types.InlineKeyboardButton("\U0001f4b3 C\u1ea5p h\u1ea1n m\u1ee9c n\u1ee3", callback_data="a_set_debt_limit"),
        types.InlineKeyboardButton("\U0001f4b5 Thu n\u1ee3 / X\xf3a n\u1ee3", callback_data="a_debt_menu"),
    )
    kb.add(
        types.InlineKeyboardButton("\U0001f464 Ch\u1ec9 \u0111\u1ecbnh duy\u1ec7t", callback_data="a_assign_approver"),
        types.InlineKeyboardButton("\U0001f319 Ch\u1ed1t s\u1ed5 cu\u1ed1i ng\xe0y", callback_data="a_day_reset"),
    )
    return kb


def kb_price_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\u2795 M\u1edf \u0111\u1ed9 m\u1edbi", callback_data="a_new_do"),
        types.InlineKeyboardButton("\u270f\ufe0f \u0110\u1ed5i gi\xe1 \u0111\u1ed9", callback_data="a_set_price"),
    )
    kb.add(types.InlineKeyboardButton("\U0001f3c1 Ch\u1ed1t \u0111\u1ed9 / Nhi\u1ec1u \u0111\u1ed9", callback_data="a_multi_close_open"))
    return kb


def kb_bill_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("\U0001f464 Ch\u1ec9 \u0111\u1ecbnh nh\xe2n vi\xean duy\u1ec7t", callback_data="a_assign_approver"))
    return kb


def kb_full_admin_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\U0001f4cb \u0110\u1ed9 \u0111ang ch\u1ea1y", callback_data="a_rep_live"),
        types.InlineKeyboardButton("\U0001f4c8 \u0102n thua t\u1eebng \u0111\u1ed9 / HT", callback_data="a_rep_pnl"),
    )
    kb.add(types.InlineKeyboardButton("\U0001f4ca B\xe1o c\xe1o \u0103n thua c\u1ea3 ng\xe0y", callback_data="a_rep_daily"))
    kb.add(
        types.InlineKeyboardButton("\u2795 M\u1edf \u0111\u1ed9 m\u1edbi", callback_data="a_new_do"),
        types.InlineKeyboardButton("\u270f\ufe0f \u0110\u1ed5i gi\xe1 \u0111\u1ed9", callback_data="a_set_price"),
    )
    kb.add(types.InlineKeyboardButton("\U0001f3c1 Ch\u1ed1t \u0111\u1ed9 / Nhi\u1ec1u \u0111\u1ed9", callback_data="a_multi_close_open"))
    kb.add(
        types.InlineKeyboardButton("\U0001f4b3 C\u1ea5p h\u1ea1n m\u1ee9c n\u1ee3", callback_data="a_set_debt_limit"),
        types.InlineKeyboardButton("\U0001f4b5 Thu n\u1ee3 / X\xf3a n\u1ee3", callback_data="a_debt_menu"),
    )
    kb.add(
        types.InlineKeyboardButton("\U0001f464 Ch\u1ec9 \u0111\u1ecbnh duy\u1ec7t", callback_data="a_assign_approver"),
        types.InlineKeyboardButton("\U0001f319 Ch\u1ed1t s\u1ed5 cu\u1ed1i ng\xe0y", callback_data="a_day_reset"),
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
    kb.add(types.InlineKeyboardButton("\U0001f310 \u0110\u1ed5i chung t\u1ea5t c\u1ea3 \u0111\u1ed9 m\u1edf", callback_data="a_pdo_all"))
    return kb


def kb_price_side_picker(do_target: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\U0001f7e2 Ch\u1ecdn c\u1eeda XANH", callback_data=f"a_pside_XANH_{do_target}"),
        types.InlineKeyboardButton("\U0001f534 Ch\u1ecdn c\u1eeda \u0110\u1ece", callback_data=f"a_pside_DO_{do_target}"),
    )
    return kb


def kb_open_do_picker() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=4)
    btns = [
        types.InlineKeyboardButton(f"\u0110\u1ed9 {i}", callback_data=f"a_open_num_{i}")
        for i in range(1, 9)
    ]
    kb.add(*btns)
    kb.add(types.InlineKeyboardButton("\u270d\ufe0f Nh\u1eadp s\u1ed1 \u0111\u1ed9 kh\xe1c", callback_data="a_open_custom"))
    return kb


def kb_multi_close_picker(selected_ids: set[int]) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    for d in db.get_open_dos():
        d_id = int(d["id"])
        mark = "\u2705" if d_id in selected_ids else "\u2b1c"
        kb.add(
            types.InlineKeyboardButton(
                f"{mark} {d['name']} ({float(d['xanh_win']):g}/{float(d['xanh_lose']):g})",
                callback_data=f"a_mclose_toggle_{d_id}",
            )
        )
    kb.add(
        types.InlineKeyboardButton("\u26a1 Ch\u1ecdn t\u1ea5t c\u1ea3", callback_data="a_mclose_all"),
        types.InlineKeyboardButton("\U0001f5d1\ufe0f B\u1ecf ch\u1ecdn", callback_data="a_mclose_clear"),
    )
    kb.add(
        types.InlineKeyboardButton("\U0001f7e2 XANH th\u1eafng", callback_data="a_mclose_run_XANH"),
        types.InlineKeyboardButton("\U0001f534 \u0110\u1ece th\u1eafng", callback_data="a_mclose_run_DO"),
        types.InlineKeyboardButton("\U0001f91d H\xf2a", callback_data="a_mclose_run_DRAW"),
        types.InlineKeyboardButton("\U0001f6ab H\u1ee7y \u0111\u1ed9", callback_data="a_mclose_run_CANCEL"),
    )
    return kb


def kb_approve_deposit(user_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\u2705 Duy\u1ec7t c\u1ed9ng ti\u1ec1n", callback_data=f"a_dep_ok_{user_id}"),
        types.InlineKeyboardButton("\u274c T\u1eeb ch\u1ed1i", callback_data=f"a_dep_no_{user_id}"),
    )
    kb.add(types.InlineKeyboardButton("\U0001f504 Ch\u1ec9 \u0111\u1ecbnh ng\u01b0\u1eddi duy\u1ec7t", callback_data="a_assign_approver"))
    return kb


def kb_deposit_presets(user_id: int, suggested_amt: float = 0.0) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=3)
    if suggested_amt > 0:
        kb.add(
            types.InlineKeyboardButton(
                f"\u2705 \u0110\xfang s\u1ed1 kh\xe1ch ch\u1ecdn (+{fmt_num(suggested_amt)})",
                callback_data=f"a_dep_amt_{user_id}_{suggested_amt}",
            )
        )
    btns = [
        types.InlineKeyboardButton(f"+{fmt_num(amt)}", callback_data=f"a_dep_amt_{user_id}_{amt}")
        for amt in [250000, 500000, 1000000, 2000000, 5000000, 10000000]
    ]
    kb.add(*btns)
    kb.add(types.InlineKeyboardButton("\u270f\ufe0f Nh\u1eadp s\u1ed1 ti\u1ec1n kh\xe1c", callback_data=f"a_dep_custom_{user_id}"))
    return kb


def kb_withdrawal_actions(w_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("\u2705 \u0110\xe3 chuy\u1ec3n ti\u1ec1n", callback_data=f"a_wd_ok_{w_id}"),
        types.InlineKeyboardButton("\u274c T\u1eeb ch\u1ed1i r\xfat", callback_data=f"a_wd_no_{w_id}"),
    )
    kb.add(types.InlineKeyboardButton("\U0001f504 Ch\u1ec9 \u0111\u1ecbnh ng\u01b0\u1eddi duy\u1ec7t", callback_data="a_assign_approver"))
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
        bot.send_message(chat_id, "\u0110\u1ed9 n\xe0y \u0111\xe3 \u0111\xf3ng ho\u1eb7c kh\xf4ng t\u1ed3n t\u1ea1i.", reply_markup=kb_user_main(uid))
        return
    win_u = float(do["xanh_win"] if side == "XANH" else do["do_win"])
    lose_u = float(do["xanh_lose"] if side == "XANH" else do["do_lose"])
    lim = get_user_betting_limit_info(uid, do_id=do_id)
    max_side = lim["max_xanh"] if side == "XANH" else lim["max_do"]
    side_label = "\U0001f7e2 XANH" if side == "XANH" else "\U0001f534 \u0110\u1ece"
    txt = (
        f"<b>{do['name']} \u2014 C\u1eecA {side_label}</b>\n"
        f"\u2022 Gi\xe1 hi\u1ec7n t\u1ea1i: <b>{win_u:g}/{lose_u:g}</b> <i>(tr\u1eeb 5% ph\u1ebf tr\xean ti\u1ec1n l\u1eddi)</i>\n"
        f"\u2022 H\u1ea1n m\u1ee9c t\u1ed1i \u0111a c\u1eeda {side_label}: <b>{fmt_num(max_side)}</b>\n"
        f"\u2022 V\xed: <b>{fmt_num(float(u['balance']))}</b> | N\u1ee3: <b>{fmt_num(float(u['debt']))}/{fmt_num(float(u['debt_limit']))}</b>\n\n"
        f"Ch\u1ecdn m\u1ee9c ti\u1ec1n nhanh ho\u1eb7c b\u1ea5m <b>Nh\u1eadp s\u1ed1 kh\xe1c</b>:"
    )
    bot.send_message(chat_id, txt, reply_markup=kb_user_amounts(side, do_id))


def send_deposit_qr(chat_id: int, user_id: int, amount: float) -> None:
    PENDING_STATE[user_id] = {"action": "USER_DEPOSIT_PENDING", "amount": amount}
    qr_url = build_vietqr_url(user_id, amount)
    vnd_show = int(round(amount * QR_AMOUNT_MULTIPLIER))

    caption = (
        f"<b>M\xc3 QR N\u1ea0P TI\u1ec0N \u2014 {BANK_INFO['bank']}</b>\n"
        f"\u2022 Ng\xe2n h\xe0ng: <b>{BANK_INFO['bank']}</b>\n"
        f"\u2022 S\u1ed1 t\xe0i kho\u1ea3n: <code>{BANK_INFO['stk']}</code>\n"
        f"\u2022 Ch\u1ee7 t\xe0i kho\u1ea3n: <b>{BANK_INFO['owner']}</b>\n"
        f"\u2022 S\u1ed1 ti\u1ec1n n\u1ea1p: <b>{fmt_num(amount)}</b> (QR: <b>{fmt_num(vnd_show)}\u0111</b>)\n"
        f"\u2022 N\u1ed9i dung chuy\u1ec3n kho\u1ea3n: <code>{user_id}</code>\n\n"
        f"Qu\xe9t m\xe3 QR ho\u1eb7c chuy\u1ec3n kho\u1ea3n \u0111\xfang n\u1ed9i dung <code>{user_id}</code>, "
        f"sau \u0111\xf3 b\u1ea5m <b>\u2705 T\xf4i \u0111\xe3 chuy\u1ec3n</b> b\xean d\u01b0\u1edbi <i>(c\xf3 th\u1ec3 g\u1eedi k\xe8m \u1ea3nh bill)</i>."
    )
    try:
        bot.send_photo(chat_id, qr_url, caption=caption, reply_markup=kb_user_deposit())
    except Exception:
        bot.send_message(chat_id, caption, reply_markup=kb_user_deposit())


def dispatch_withdrawal_to_bill_group(user: dict, w_rec: dict) -> None:
    if not BILL_GROUP_ID:
        return
    assigned_id = int(w_rec["assigned_staff_id"] or 0)
    approver_txt = f"<code>{assigned_id}</code>" if assigned_id > 0 else "<i>T\u1ea5t c\u1ea3 nh\xe2n vi\xean</i>"
    w_id = int(w_rec["id"])
    amt = float(w_rec["amount"])
    payout_txt = w_rec["payout_text"] or "Xem \u1ea3nh m\xe3 QR \u0111\xednh k\xe8m"

    caption = (
        f"\U0001f4b8 <b>Y\xcaU C\u1ea6U R\xdaT TI\u1ec0N (#R{w_id:03d})</b>\n"
        f"\u2022 Kh\xe1ch: <b>{user['name']}</b> (@{user['username'] or 'kh\xf4ng_c\xf3'}) | ID: <code>{user['user_id']}</code>\n"
        f"\u2022 S\u1ed1 ti\u1ec1n r\xfat: <b>{fmt_num(amt)}</b>\n"
        f"\u2022 Th\xf4ng tin nh\u1eadn: <code>{payout_txt}</code>\n"
        f"\u2022 Nh\xe2n vi\xean ch\u1ec9 \u0111\u1ecbnh duy\u1ec7t: {approver_txt}\n"
        f"\u2022 Th\u1eddi gian: <code>{w_rec['created_at']}</code>"
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
            bot.send_message(source_chat_id, "\u2705 \u0110\xe3 g\u1eedi b\xe1o c\xe1o v\u1ec1 <b>nh\xf3m Master</b>.", reply_markup=kb_full_admin_panel())
    else:
        bot.send_message(source_chat_id, report_txt, reply_markup=get_panel_by_chat(source_chat_id, source_chat_type, actor_id))


# ==================== L\u1ec6NH KH\u1edeI T\u1ea0O ====================
@bot.message_handler(commands=["start", "menu"])
def cmd_start(message: types.Message):
    uid = message.from_user.id
    chat_id = message.chat.id
    chat_type = message.chat.type

    if chat_type == "private":
        u = db.get_user(uid, message.from_user.first_name, message.from_user.username)
        txt = (
            f"Xin ch\xe0o <b>{u['name']}</b> (ID: <code>{u['user_id']}</code>)\n"
            f"\u2022 S\u1ed1 d\u01b0 v\xed: <b>{fmt_num(float(u['balance']))}</b>\n"
            f"\u2022 H\u1ea1n m\u1ee9c ghi n\u1ee3: <b>{fmt_num(float(u['debt']))}</b> / <b>{fmt_num(float(u['debt_limit']))}</b>\n\n"
            f"Ch\u1ecdn n\xfat b\xean d\u01b0\u1edbi \u0111\u1ec3 thao t\xe1c:"
        )
        bot.send_message(chat_id, txt, reply_markup=kb_user_main(uid))
    elif is_master_group(chat_id):
        bot.send_message(
            chat_id,
            "<b>B\u1ea2NG B\xc1O C\xc1O & QU\u1ea2N L\xdd NH\xd3M MASTER</b>",
            reply_markup=get_panel_by_chat(chat_id, chat_type, uid),
        )
    elif is_admin(uid):
        bot.send_message(
            chat_id,
            "<b>B\u1ea2NG \u0110I\u1ec0U KHI\u1ec2N V\u1eacN H\xc0NH</b>",
            reply_markup=get_panel_by_chat(chat_id, chat_type, uid),
        )


@bot.message_handler(commands=["admin"])
def cmd_admin(message: types.Message):
    uid = message.from_user.id
    if is_admin(uid) or is_master_group(message.chat.id):
        bot.send_message(
            message.chat.id,
            "<b>B\u1ea2NG \u0110I\u1ec0U KHI\u1ec2N</b>",
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


# ==================== X\u1eec L\xdd N\xdaT B\u1ea4M ====================
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
            f"<b>MENU CH\xcdNH</b> \u2014 ID: <code>{uid}</code>\nS\u1ed1 d\u01b0 v\xed: <b>{fmt_num(float(u['balance']))}</b>",
            chat_id,
            call.message.message_id,
            reply_markup=kb_user_main(uid),
        )

    elif data == "u_wallet":
        lim = get_user_betting_limit_info(uid)
        active_bets = []
        for d in open_dos:
            active_bets.extend(db.get_active_bets(do_name=d["name"], user_id=uid))
        bets_txt = "\n".join(
            [
                f"  \u2022 {b['do_name']} | {b['code']} | {b['side']} | {fmt_num(float(b['amount']))} (gi\xe1 {b['price_str']})"
                for b in active_bets
            ]
        ) or "  <i>Ch\u01b0a c\xf3 m\xe3 n\xe0o \u0111ang ch\u1ea1y.</i>"

        if not open_dos:
            limit_line = f"\u2022 H\u1ea1n m\u1ee9c c\xf2n c\u01b0\u1ee3c \u0111\u01b0\u1ee3c: <b>{fmt_num(lim['unused_capital'])}</b>"
        elif len(open_dos) == 1:
            d0 = open_dos[0]
            d_lim = get_user_betting_limit_info(uid, do_id=int(d0["id"]))
            limit_line = (
                f"\u2022 H\u1ea1n m\u1ee9c c\xf2n c\u01b0\u1ee3c \u0111\u01b0\u1ee3c ({d0['name']}): "
                f"\U0001f7e2 XANH <b>{fmt_num(d_lim['max_xanh'])}</b> | \U0001f534 \u0110\u1ece <b>{fmt_num(d_lim['max_do'])}</b>"
            )
        else:
            sub_lines = []
            for d in open_dos:
                d_lim = get_user_betting_limit_info(uid, do_id=int(d["id"]))
                sub_lines.append(
                    f"  - {d['name']}: \U0001f7e2 XANH <b>{fmt_num(d_lim['max_xanh'])}</b> | \U0001f534 \u0110\u1ece <b>{fmt_num(d_lim['max_do'])}</b>"
                )
            limit_line = "\u2022 H\u1ea1n m\u1ee9c c\xf2n c\u01b0\u1ee3c \u0111\u01b0\u1ee3c theo t\u1eebng \u0111\u1ed9:\n" + "\n".join(sub_lines)

        txt = (
            f"<b>T\xc0I KHO\u1ea2N C\u1ee6A B\u1ea0N</b> (ID: <code>{uid}</code>)\n"
            f"\u2022 S\u1ed1 d\u01b0 v\xed: <b>{fmt_num(float(u['balance']))}</b>\n"
            f"\u2022 \u0110ang ghi n\u1ee3: <b>{fmt_num(float(u['debt']))}</b> / H\u1ea1n m\u1ee9c <b>{fmt_num(float(u['debt_limit']))}</b>\n"
            f"{limit_line}\n"
            f"\u2022 \u0102n/thua t\u1edbi hi\u1ec7n t\u1ea1i: {fmt_signed(float(u['pnl_net']))}\n\n"
            f"<b>M\xe3 \u0111ang \u0111\xe1nh:</b>\n{bets_txt}"
        )
        bot.send_message(chat_id, txt, reply_markup=kb_user_main(uid))

    elif data == "u_deposit":
        bot.send_message(
            chat_id,
            f"<b>CH\u1eccN S\u1ed0 TI\u1ec0N C\u1ea6N N\u1ea0P</b>\n"
            f"\u2022 N\u1ea1p t\u1ed1i thi\u1ec3u: <b>{fmt_num(MIN_DEPOSIT_AMOUNT)}</b>\n"
            f"\u2022 Ng\xe2n h\xe0ng: <b>{BANK_INFO['bank']}</b> \u2014 STK: <code>{BANK_INFO['stk']}</code>\n"
            f"\u2022 Ch\u1ee7 TK: <b>{BANK_INFO['owner']}</b>\n"
            f"\u2022 N\u1ed9i dung CK m\u1eb7c \u0111\u1ecbnh: <code>{uid}</code>\n\n"
            f"Ch\u1ecdn m\u1ee9c ti\u1ec1n nhanh ho\u1eb7c b\u1ea5m <b>Nh\u1eadp s\u1ed1 kh\xe1c</b> \u0111\u1ec3 t\u1ea1o m\xe3 QR:",
            reply_markup=kb_deposit_amounts(),
        )

    elif data.startswith("u_dep_amt_"):
        amt = float(data.split("_")[3])
        if amt < MIN_DEPOSIT_AMOUNT:
            bot.send_message(
                chat_id,
                f"\u274c S\u1ed1 ti\u1ec1n n\u1ea1p t\u1ed1i thi\u1ec3u l\xe0 <b>{fmt_num(MIN_DEPOSIT_AMOUNT)}</b>.",
                reply_markup=kb_deposit_amounts(),
            )
            return
        send_deposit_qr(chat_id, uid, amt)

    elif data == "u_dep_custom":
        PENDING_STATE[uid] = {"action": "USER_DEP_CUSTOM_AMOUNT"}
        bot.send_message(
            chat_id,
            f"Nh\u1eadp s\u1ed1 ti\u1ec1n b\u1ea1n mu\u1ed1n n\u1ea1p (T\u1ed1i thi\u1ec3u: <b>{fmt_num(MIN_DEPOSIT_AMOUNT)}</b>):",
        )

    elif data == "u_paid":
        st = PENDING_STATE.get(uid)
        dep_amt = float(st["amount"]) if (st and st.get("action") == "USER_DEPOSIT_PENDING") else 0.0
        if dep_amt > 0:
            PENDING_STATE[uid] = {"action": "USER_LAST_DEP_AMT", "amount": dep_amt}
        approver_id = get_assigned_approver_id()
        approver_txt = f"<code>{approver_id}</code>" if approver_id > 0 else "<i>T\u1ea5t c\u1ea3 nh\xe2n vi\xean</i>"
        amt_line = f"\u2022 S\u1ed1 ti\u1ec1n kh\xe1ch ch\u1ecdn tr\xean QR: <b>{fmt_num(dep_amt)}</b>\n" if dep_amt > 0 else ""

        bot.send_message(
            chat_id,
            f"\u2705 \u0110\xe3 g\u1eedi y\xeau c\u1ea7u x\xe1c nh\u1eadn n\u1ea1p ti\u1ec1n cho ID <code>{uid}</code>.\n"
            f"<i>N\u1ebfu c\xf3 \u1ea3nh bill, h\xe3y g\u1eedi \u1ea3nh v\xe0o chat n\xe0y \u0111\u1ec3 nh\xe2n vi\xean \u0111\u1ed1i so\xe1t nhanh.</i>",
            reply_markup=kb_user_main(uid),
        )
        if BILL_GROUP_ID:
            bot.send_message(
                BILL_GROUP_ID,
                f"\U0001f514 <b>Y\xcaU C\u1ea6U N\u1ea0P TI\u1ec0N M\u1edaI</b>\n"
                f"\u2022 Kh\xe1ch: <b>{u['name']}</b> (@{u['username'] or 'kh\xf4ng_c\xf3'})\n"
                f"{amt_line}"
                f"\u2022 N\u1ed9i dung CK / ID: <code>{uid}</code>\n"
                f"\u2022 Nh\xe2n vi\xean ch\u1ec9 \u0111\u1ecbnh duy\u1ec7t: {approver_txt}\n"
                f"\u2022 Th\u1eddi gian: <code>{datetime.now().strftime('%H:%M - %d/%m/%Y')}</code>",
                reply_markup=kb_approve_deposit(uid),
            )

    elif data == "u_withdraw":
        bal = float(u["balance"])
        if bal < MIN_WITHDRAW_AMOUNT:
            bot.send_message(
                chat_id,
                f"\u274c S\u1ed1 d\u01b0 v\xed hi\u1ec7n t\u1ea1i l\xe0 <b>{fmt_num(bal)}</b>, ch\u01b0a \u0111\u1ea1t m\u1ee9c r\xfat t\u1ed1i thi\u1ec3u <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b>.",
                reply_markup=kb_user_main(uid),
            )
        else:
            bot.send_message(
                chat_id,
                f"<b>R\xdaT TI\u1ec0N V\u1ec0 T\xc0I KHO\u1ea2N</b>\n"
                f"\u2022 R\xfat t\u1ed1i thi\u1ec3u: <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b> | T\u1ed1i \u0111a: <b>{fmt_num(bal)}</b>\n\n"
                f"Ch\u1ecdn m\u1ee9c ti\u1ec1n r\xfat nhanh ho\u1eb7c b\u1ea5m <b>R\xfat h\u1ebft v\xed</b>:",
                reply_markup=kb_withdraw_amounts(bal),
            )

    elif data == "u_wd_all":
        bal = float(u["balance"])
        if bal < MIN_WITHDRAW_AMOUNT:
            bot.send_message(
                chat_id,
                f"\u274c S\u1ed1 d\u01b0 v\xed hi\u1ec7n t\u1ea1i l\xe0 <b>{fmt_num(bal)}</b>, ch\u01b0a \u0111\u1ea1t m\u1ee9c r\xfat t\u1ed1i thi\u1ec3u <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b>.",
                reply_markup=kb_user_main(uid),
            )
        else:
            PENDING_STATE[uid] = {"action": "USER_WD_INFO", "amount": bal}
            bot.send_message(
                chat_id,
                f"<b>NH\u1eacP TH\xd4NG TIN NH\u1eacN TI\u1ec0N R\xdaT H\u1ebeT V\xcd ({fmt_num(bal)})</b>\n"
                f"G\u1eedi <b>\u1ea3nh m\xe3 QR</b> t\xe0i kho\u1ea3n nh\u1eadn HO\u1eb6C nh\u1eafn theo m\u1eabu:\n"
                f"<code>Ng\xe2n h\xe0ng - S\u1ed1 t\xe0i kho\u1ea3n - T\xean ch\u1ee7 TK</code>",
            )

    elif data.startswith("u_wd_amt_"):
        amt = float(data.split("_")[3])
        bal = float(u["balance"])
        if amt < MIN_WITHDRAW_AMOUNT:
            bot.send_message(
                chat_id,
                f"\u274c S\u1ed1 ti\u1ec1n r\xfat t\u1ed1i thi\u1ec3u l\xe0 <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b>.",
                reply_markup=kb_withdraw_amounts(bal),
            )
        elif amt > bal + 1e-6:
            bot.send_message(
                chat_id,
                f"\u274c S\u1ed1 ti\u1ec1n r\xfat v\u01b0\u1ee3t s\u1ed1 d\u01b0 v\xed (T\u1ed1i \u0111a: <b>{fmt_num(bal)}</b>).",
                reply_markup=kb_withdraw_amounts(bal),
            )
        else:
            PENDING_STATE[uid] = {"action": "USER_WD_INFO", "amount": amt}
            bot.send_message(
                chat_id,
                f"<b>NH\u1eacP TH\xd4NG TIN NH\u1eacN TI\u1ec0N R\xdaT ({fmt_num(amt)})</b>\n"
                f"G\u1eedi <b>\u1ea3nh m\xe3 QR</b> t\xe0i kho\u1ea3n nh\u1eadn HO\u1eb6C nh\u1eafn theo m\u1eabu:\n"
                f"<code>Ng\xe2n h\xe0ng - S\u1ed1 t\xe0i kho\u1ea3n - T\xean ch\u1ee7 TK</code>\n"
                f"<i>V\xed d\u1ee5: VCB - 0987654321 - TRAN VAN B</i>",
            )

    elif data == "u_wd_custom":
        bal = float(u["balance"])
        PENDING_STATE[uid] = {"action": "USER_WD_AMOUNT"}
        bot.send_message(
            chat_id,
            f"Nh\u1eadp s\u1ed1 ti\u1ec1n b\u1ea1n mu\u1ed1n r\xfat (T\u1ed1i thi\u1ec3u: <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b> \u2014 T\u1ed1i \u0111a: <b>{fmt_num(bal)}</b>):",
        )

    elif data.startswith("u_side_"):
        side = data.split("_")[2]
        if not open_dos:
            bot.send_message(chat_id, "\u26a0\ufe0f Hi\u1ec7n ch\u01b0a c\xf3 \u0111\u1ed9 n\xe0o \u0111ang m\u1edf.", reply_markup=kb_user_main(uid))
        elif len(open_dos) > 1:
            side_label = "\U0001f7e2 XANH" if side == "XANH" else "\U0001f534 \u0110\u1ece"
            summary_lines = []
            for d in open_dos:
                p_lbl = (
                    f"{float(d['xanh_win']):g}/{float(d['xanh_lose']):g}"
                    if side == "XANH"
                    else f"{float(d['do_win']):g}/{float(d['do_lose']):g}"
                )
                summary_lines.append(f"\u2022 <b>{d['name']}</b>: gi\xe1 {side_label} <code>{p_lbl}</code>")
            bot.send_message(
                chat_id,
                f"\u0110ang m\u1edf <b>{len(open_dos)} \u0111\u1ed9</b> c\xf9ng l\xfac:\n"
                + "\n".join(summary_lines)
                + f"\n\nCh\u1ecdn \u0111\u1ed9 b\u1ea1n mu\u1ed1n \u0111\xe1nh c\u1eeda <b>{side_label}</b>:",
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
        bot.send_message(chat_id, f"Nh\u1eadp s\u1ed1 ti\u1ec1n b\u1ea1n mu\u1ed1n \u0111\xe1nh c\u1eeda <b>{side}</b>:")

    # --- B\xc1O C\xc1O CHO MASTER & C\u1ed4 \u0110\xd4NG TRONG NH\xd3M MASTER ---
    elif data in ("a_rep_live", "a_rep_pnl", "a_rep_daily"):
        if not can_view_master_report(uid, chat_id) and not (chat_type == "private" and is_admin(uid)):
            bot.answer_callback_query(call.id, "B\xe1o c\xe1o n\xe0y ch\u1ec9 d\xe0nh cho Master v\xe0 nh\xf3m Master.", show_alert=True)
            return
        rtype = "live" if data == "a_rep_live" else ("daily" if data == "a_rep_daily" else "pnl")
        send_report_to_master(rtype, uid, chat_id, chat_type)

    elif data == "a_day_reset":
        if not is_master(uid):
            bot.answer_callback_query(call.id, "Ch\u1ec9 Master m\u1edbi \u0111\u01b0\u1ee3c ch\u1ed1t s\u1ed5 cu\u1ed1i ng\xe0y.", show_alert=True)
            return
        snapshot = close_day_accounting_logic()
        target_out = MASTER_GROUP_ID or chat_id
        bot.send_message(
            target_out,
            f"\U0001f319 <b>\u0110\xc3 CH\u1ed0T S\u1ed4 CU\u1ed0I NG\xc0Y</b>\n\n{snapshot}",
            reply_markup=get_panel_by_chat(target_out, "group" if target_out != uid else "private", uid),
        )

    # --- ADMIN / MASTER / STAFF ACTIONS ---
    elif data.startswith("a_") and not is_admin(uid):
        bot.answer_callback_query(call.id, "B\u1ea1n kh\xf4ng c\xf3 quy\u1ec1n thao t\xe1c m\u1ee5c n\xe0y.", show_alert=True)
        return

    elif data == "a_panel_open":
        bot.send_message(chat_id, "<b>MENU ADMIN (CHAT RI\xcaNG)</b>", reply_markup=kb_full_admin_panel())

    elif data == "a_new_do":
        ok_time, time_msg = can_open_do_by_time()
        if not ok_time:
            bot.send_message(chat_id, time_msg, reply_markup=get_panel_by_chat(chat_id, chat_type, uid))
            return
        PENDING_STATE[uid] = {"action": "ADMIN_OPEN_SPECIFIC_DO"}
        bot.send_message(
            chat_id,
            "\U0001f3af <b>CH\u1eccN HO\u1eb6C NH\u1eacP S\u1ed0 \u0110\u1ed8 MU\u1ed0N M\u1ede</b>\n"
            "\u2022 H\u1ed7 tr\u1ee3 m\u1edf ng\u1eabu nhi\xean theo th\u1ef1c t\u1ebf d\u1eddi l\u1ecbch (v\xed d\u1ee5 m\u1edf <b>\u0110\u1ed9 3</b> tr\u01b0\u1edbc, <b>\u0110\u1ed9 2</b> sau, ho\u1eb7c <b>\u0110\u1ed9 5</b>).\n"
            "\u2022 M\u1edf \u0111\u1ed9 n\xe0o h\u1ec7 th\u1ed1ng s\u1ebd t\u1ef1 kh\xf3a/ho\xe3n nh\u1eadn k\xe8o c\xe1c \u0111\u1ed9 kh\xe1c \u0111\u1ec3 kh\xe1ch kh\xf4ng \u0111\xe1nh nh\u1ea7m.",
            reply_markup=kb_open_do_picker(),
        )

    elif data.startswith("a_open_num_"):
        num_str = data.split("_")[3]
        PENDING_STATE.pop(uid, None)
        ok, msg, _ = open_specific_do_logic(num_str, exclusive_active=True)
        bot.send_message(chat_id, msg, reply_markup=get_panel_by_chat(chat_id, chat_type, uid) if ok else None)

    elif data == "a_open_custom":
        PENDING_STATE[uid] = {"action": "ADMIN_OPEN_SPECIFIC_DO"}
        bot.send_message(chat_id, "\u270d\ufe0f Nh\u1eadp s\u1ed1 \u0111\u1ed9 b\u1ea1n mu\u1ed1n m\u1edf (v\xed d\u1ee5: <code>3</code>, <code>5</code>, <code>12</code>):")

    elif data == "a_set_price":
        cur_open = db.get_open_dos()
        if not cur_open:
            bot.send_message(chat_id, "\u26a0\ufe0f Hi\u1ec7n kh\xf4ng c\xf3 \u0111\u1ed9 n\xe0o \u0111ang m\u1edf \u0111\u1ec3 \u0111\u1ed5i gi\xe1.")
            return
        if len(cur_open) == 1:
            d0 = cur_open[0]
            bot.send_message(
                chat_id,
                f"<b>\u0110\u1ed4I GI\xc1 {d0['name']}</b>\n"
                f"Hi\u1ec7n t\u1ea1i: XANH <code>{float(d0['xanh_win']):g}/{float(d0['xanh_lose']):g}</code> | "
                f"\u0110\u1ece <code>{float(d0['do_win']):g}/{float(d0['do_lose']):g}</code>\n\n"
                f"Ch\u1ecdn 1 c\u1eeda \u0111\u1ec3 nh\u1eadp t\u1ef7 l\u1ec7 (c\u1eeda c\xf2n l\u1ea1i t\u1ef1 \u0111\u1ed9ng \u0111\u1ea3o chi\u1ec1u):",
                reply_markup=kb_price_side_picker(str(d0["id"])),
            )
        else:
            bot.send_message(
                chat_id,
                "Ch\u1ecdn \u0111\u1ed9 c\u1ea7n \u0111\u1ed5i gi\xe1 ho\u1eb7c \u0111\u1ed5i chung t\u1ea5t c\u1ea3 \u0111\u1ed9 \u0111ang m\u1edf:",
                reply_markup=kb_price_do_picker(),
            )

    elif data.startswith("a_pdo_one_"):
        d_id = data.split("_")[3]
        d_obj = db.get_do_by_id(int(d_id))
        if not d_obj:
            bot.send_message(chat_id, "Kh\xf4ng t\xecm th\u1ea5y \u0111\u1ed9.")
            return
        bot.send_message(
            chat_id,
            f"<b>\u0110\u1ed4I GI\xc1 {d_obj['name']}</b> \u2014 Ch\u1ecdn 1 c\u1eeda (c\u1eeda c\xf2n l\u1ea1i t\u1ef1 \u0111\u1ea3o):",
            reply_markup=kb_price_side_picker(d_id),
        )

    elif data == "a_pdo_all":
        bot.send_message(
            chat_id,
            "<b>\u0110\u1ed4I GI\xc1 CHUNG T\u1ea4T C\u1ea2 \u0110\u1ed8 M\u1ede</b> \u2014 Ch\u1ecdn 1 c\u1eeda (c\u1eeda c\xf2n l\u1ea1i t\u1ef1 \u0111\u1ea3o):",
            reply_markup=kb_price_side_picker("ALL"),
        )

    elif data.startswith("a_pside_"):
        _, _, chosen_side, do_target = data.split("_")
        if do_target == "ALL":
            target_ids = [int(d["id"]) for d in db.get_open_dos()]
            target_label = "T\u1ea5t c\u1ea3 \u0111\u1ed9 m\u1edf"
        else:
            d_obj = db.get_do_by_id(int(do_target))
            target_ids = [int(do_target)]
            target_label = d_obj["name"] if d_obj else "\u0110\u1ed9 hi\u1ec7n t\u1ea1i"

        PENDING_STATE[uid] = {
            "action": "ADMIN_SET_PRICE_ONE_SIDE",
            "side": chosen_side,
            "target_do_ids": target_ids,
            "target_label": target_label,
        }
        side_name = "\U0001f7e2 XANH" if chosen_side == "XANH" else "\U0001f534 \u0110\u1ece"
        opp_name = "\U0001f534 \u0110\u1ece" if chosen_side == "XANH" else "\U0001f7e2 XANH"
        bot.send_message(
            chat_id,
            f"Nh\u1eadp t\u1ef7 l\u1ec7 cho c\u1eeda <b>{side_name}</b> c\u1ee7a <b>{target_label}</b>\n"
            f"<i>V\xed d\u1ee5 nh\u1eadp <code>5/10</code> -> {side_name} l\xe0 <b>5/10</b>, {opp_name} t\u1ef1 \u0111\u1ea3o th\xe0nh <b>10/5</b>:</i>",
        )

    elif data == "a_multi_close_open":
        cur_open = db.get_open_dos()
        default_sel = {int(cur_open[0]["id"])} if len(cur_open) == 1 else set()
        PENDING_STATE[uid] = {"action": "ADMIN_MULTI_CLOSE", "selected_ids": default_sel}
        bot.send_message(
            chat_id,
            "<b>CH\u1ed0T \u0110\u1ed8 / CH\u1ed0T NHI\u1ec0U \u0110\u1ed8 C\xd9NG L\xdaC</b>\n"
            "B\u1ea5m ch\u1ecdn 1 ho\u1eb7c nhi\u1ec1u \u0111\u1ed9, sau \u0111\xf3 b\u1ea5m n\xfat k\u1ebft qu\u1ea3 b\xean d\u01b0\u1edbi:",
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
        result_code = data.split("_")[3]
        st = PENDING_STATE.get(uid) or {}
        sel_ids = list(st.get("selected_ids") or [])
        if not sel_ids:
            cur_open = db.get_open_dos()
            if len(cur_open) == 1:
                sel_ids = [int(cur_open[0]["id"])]
        if not sel_ids:
            bot.answer_callback_query(call.id, "B\u1ea1n ch\u01b0a ch\u1ecdn \u0111\u1ed9 n\xe0o \u0111\u1ec3 ch\u1ed1t.", show_alert=True)
            return

        PENDING_STATE.pop(uid, None)
        summary = settle_multiple_dos_logic(sel_ids, result_code)
        bot.answer_callback_query(call.id, f"\u0110\xe3 ch\u1ed1t {len(summary['do_names'])} \u0111\u1ed9 ({result_code}).")

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
            bot.answer_callback_query(call.id, "Ch\u1ec9 Master m\u1edbi \u0111\u01b0\u1ee3c c\u1ea5p h\u1ea1n m\u1ee9c n\u1ee3.", show_alert=True)
            return
        PENDING_STATE[uid] = {"action": "ADMIN_SET_DEBT_LIMIT"}
        bot.send_message(
            chat_id,
            "Nh\u1eadp theo m\u1eabu: <code>ID_KHACH HAN_MUC_NO</code>\n"
            "<i>V\xed d\u1ee5: <code>5770334027 10000000</code></i>",
        )

    elif data == "a_debt_menu":
        if not is_master(uid):
            bot.answer_callback_query(call.id, "Ch\u1ec9 Master m\u1edbi \u0111\u01b0\u1ee3c x\u1eed l\xfd n\u1ee3.", show_alert=True)
            return
        PENDING_STATE[uid] = {"action": "ADMIN_CLEAR_DEBT"}
        bot.send_message(
            chat_id,
            "Nh\u1eadp theo m\u1eabu: <code>ID_KHACH SO_TIEN_THU_NO</code> (nh\u1eadp <code>0</code> ho\u1eb7c <code>ALL</code> \u0111\u1ec3 x\xf3a h\u1ebft n\u1ee3):\n"
            "<i>V\xed d\u1ee5: <code>5770334027 2000000</code> ho\u1eb7c <code>5770334027 ALL</code></i>",
        )

    elif data == "a_assign_approver":
        if not is_master(uid):
            bot.answer_callback_query(call.id, "Ch\u1ec9 Master m\u1edbi \u0111\u01b0\u1ee3c ch\u1ec9 \u0111\u1ecbnh nh\xe2n vi\xean duy\u1ec7t.", show_alert=True)
            return
        PENDING_STATE[uid] = {"action": "ADMIN_ASSIGN_APPROVER"}
        cur_app = get_assigned_approver_id()
        bot.send_message(
            chat_id,
            f"Nh\xe2n vi\xean duy\u1ec7t hi\u1ec7n t\u1ea1i: <code>{cur_app or 'Ch\u01b0a ch\u1ec9 \u0111\u1ecbnh (m\u1ecdi admin duy\u1ec7t)'}</code>\n"
            f"Nh\u1eadp <b>User ID</b> nh\xe2n vi\xean c\u1ea7n ch\u1ec9 \u0111\u1ecbnh (ho\u1eb7c nh\u1eadp <code>0</code> \u0111\u1ec3 m\u1edf cho t\u1ea5t c\u1ea3 admin):",
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
            f"Ch\u1ecdn s\u1ed1 ti\u1ec1n c\u1ed9ng v\xe0o v\xed kh\xe1ch ID <code>{target_uid}</code>:",
            reply_markup=kb_deposit_presets(target_uid, suggested),
        )

    elif data.startswith("a_dep_no_"):
        target_uid = int(data.split("_")[3])
        ok, err = can_approve_item(uid)
        if not ok:
            bot.answer_callback_query(call.id, err, show_alert=True)
            return
        PENDING_STATE.pop(target_uid, None)
        bot.send_message(chat_id, f"\u274c \u0110\xe3 t\u1eeb ch\u1ed1i y\xeau c\u1ea7u n\u1ea1p c\u1ee7a ID <code>{target_uid}</code>.")
        try:
            bot.send_message(target_uid, "\u274c Y\xeau c\u1ea7u n\u1ea1p ti\u1ec1n c\u1ee7a b\u1ea1n ch\u01b0a \u0111\u01b0\u1ee3c duy\u1ec7t. Vui l\xf2ng li\xean h\u1ec7 h\u1ed7 tr\u1ee3.")
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
            f"\u2705 \u0110\xe3 c\u1ed9ng <b>{fmt_num(amt)}</b> cho <b>{u_after['name']}</b> (<code>{target_uid}</code>).\n"
            f"\u2022 V\xed m\u1edbi: <b>{fmt_num(float(u_after['balance']))}</b>",
        )
        try:
            bot.send_message(
                target_uid,
                f"\u2705 N\u1ea1p th\xe0nh c\xf4ng <b>{fmt_num(amt)}</b> v\xe0o v\xed!\n"
                f"\u2022 S\u1ed1 d\u01b0 hi\u1ec7n t\u1ea1i: <b>{fmt_num(float(u_after['balance']))}</b>",
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
        bot.send_message(chat_id, f"Nh\u1eadp s\u1ed1 ti\u1ec1n mu\u1ed1n c\u1ed9ng cho kh\xe1ch ID <code>{target_uid}</code>:")

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
                    f"\u2705 L\u1ec7nh r\xfat <code>#R{w_id:03d}</code> (<b>{fmt_num(float(w_rec['amount']))}</b>) \u0111\xe3 \u0111\u01b0\u1ee3c chuy\u1ec3n ti\u1ec1n!",
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
                    f"\u274c L\u1ec7nh r\xfat <code>#R{w_id:03d}</code> b\u1ecb t\u1eeb ch\u1ed1i v\xe0 \u0111\xe3 ho\xe0n l\u1ea1i <b>{fmt_num(float(w_rec['amount']))}</b> v\xe0o v\xed.",
                    reply_markup=kb_user_main(int(w_rec["user_id"])),
                )
            except Exception:
                pass

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


# ==================== X\u1eec L\xdd TIN NH\u1eaeN & \u1ea2NH THEO TR\u1ea0NG TH\xc1I ====================
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
        approver_txt = f"<code>{approver_id}</code>" if approver_id > 0 else "<i>T\u1ea5t c\u1ea3 nh\xe2n vi\xean</i>"
        cap = (
            f"\U0001f9fe <b>BILL N\u1ea0P TI\u1ec0N T\u1eea KH\xc1CH</b>\n"
            f"\u2022 Kh\xe1ch: <b>{u['name']}</b> (@{u['username'] or 'kh\xf4ng_c\xf3'}) | ID: <code>{uid}</code>\n"
            f"\u2022 Nh\xe2n vi\xean ch\u1ec9 \u0111\u1ecbnh duy\u1ec7t: {approver_txt}\n"
            f"\u2022 Th\u1eddi gian: <code>{datetime.now().strftime('%H:%M - %d/%m/%Y')}</code>"
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
        ok, msg, _ = open_specific_do_logic(raw, exclusive_active=True)
        bot.send_message(chat_id, msg, reply_markup=get_panel_by_chat(chat_id, chat_type, uid) if ok else None)
        return

    if act == "ADMIN_SET_PRICE_ONE_SIDE" and is_admin(uid):
        target_ids: list[int] = st.get("target_do_ids") or []
        chosen_side = st.get("side") or "XANH"
        target_label = st.get("target_label") or ""
        try:
            ratio_map = parse_single_side_ratio(f"{chosen_side} {raw}", chosen_side)
        except Exception:
            bot.send_message(chat_id, "\u274c T\u1ef7 l\u1ec7 kh\xf4ng h\u1ee3p l\u1ec7. V\xed d\u1ee5 nh\u1eadp: <code>5/10</code> ho\u1eb7c <code>10/8</code>.")
            return

        PENDING_STATE.pop(uid, None)
        for d_id in target_ids:
            db.update_do(d_id, **ratio_map)
        bot.send_message(
            chat_id,
            f"\u2705 \u0110\xe3 c\u1eadp nh\u1eadt gi\xe1 cho <b>{target_label}</b>:\n"
            f"\u2022 XANH: <code>{ratio_map['xanh_win']:g}/{ratio_map['xanh_lose']:g}</code>\n"
            f"\u2022 \u0110\u1ece: <code>{ratio_map['do_win']:g}/{ratio_map['do_lose']:g}</code>",
            reply_markup=get_panel_by_chat(chat_id, chat_type, uid),
        )
        return

    if act == "ADMIN_SET_DEBT_LIMIT" and is_master(uid):
        parts = raw.split()
        if len(parts) != 2 or not parts[0].isdigit():
            bot.send_message(chat_id, "\u274c Sai c\xfa ph\xe1p. Nh\u1eadp: <code>ID_KHACH HAN_MUC_NO</code>")
            return
        t_uid = int(parts[0])
        try:
            d_lim = float(parts[1].replace(".", "").replace(",", ""))
        except ValueError:
            bot.send_message(chat_id, "\u274c H\u1ea1n m\u1ee9c n\u1ee3 kh\xf4ng h\u1ee3p l\u1ec7.")
            return
        PENDING_STATE.pop(uid, None)
        db.get_user(t_uid)
        db.update_user_fields(t_uid, debt_limit=max(0.0, d_lim))
        u_after = db.get_user(t_uid)
        bot.send_message(
            chat_id,
            f"\u2705 \u0110\xe3 c\u1eadp nh\u1eadt h\u1ea1n m\u1ee9c n\u1ee3 cho ID <code>{t_uid}</code>: <b>{fmt_num(float(u_after['debt_limit']))}</b>",
            reply_markup=get_panel_by_chat(chat_id, chat_type, uid),
        )
        return

    if act == "ADMIN_CLEAR_DEBT" and is_master(uid):
        parts = raw.split()
        if len(parts) != 2 or not parts[0].isdigit():
            bot.send_message(chat_id, "\u274c Sai c\xfa ph\xe1p. Nh\u1eadp: <code>ID_KHACH SO_TIEN_HOAC_ALL</code>")
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
                bot.send_message(chat_id, "\u274c S\u1ed1 ti\u1ec1n kh\xf4ng h\u1ee3p l\u1ec7.")
                return
            new_debt = max(0.0, cur_debt - paid)
        db.update_user_fields(t_uid, debt=new_debt)
        bot.send_message(
            chat_id,
            f"\u2705 \u0110\xe3 thu/x\xf3a <b>{fmt_num(paid)}</b> n\u1ee3 cho ID <code>{t_uid}</code>. N\u1ee3 c\xf2n l\u1ea1i: <b>{fmt_num(new_debt)}</b>",
            reply_markup=get_panel_by_chat(chat_id, chat_type, uid),
        )
        return

    if act == "ADMIN_ASSIGN_APPROVER" and is_master(uid):
        if not raw.isdigit():
            bot.send_message(chat_id, "\u274c Vui l\xf2ng nh\u1eadp User ID d\u1ea1ng s\u1ed1 (ho\u1eb7c <code>0</code>).")
            return
        staff_id = int(raw)
        PENDING_STATE.pop(uid, None)
        set_assigned_approver_id(staff_id)
        txt = f"\u2705 \u0110\xe3 ch\u1ec9 \u0111\u1ecbnh nh\xe2n vi\xean duy\u1ec7t ID <code>{staff_id}</code>." if staff_id > 0 else "\u2705 \u0110\xe3 m\u1edf quy\u1ec1n duy\u1ec7t cho t\u1ea5t c\u1ea3 admin."
        bot.send_message(chat_id, txt, reply_markup=get_panel_by_chat(chat_id, chat_type, uid))
        return

    if act == "ADMIN_DEP_CUSTOM_AMT" and is_admin(uid):
        t_uid = int(st["target_uid"])
        try:
            amt = float(raw.replace(".", "").replace(",", ""))
            if amt <= 0:
                raise ValueError()
        except ValueError:
            bot.send_message(chat_id, "\u274c S\u1ed1 ti\u1ec1n kh\xf4ng h\u1ee3p l\u1ec7.")
            return
        PENDING_STATE.pop(uid, None)
        res = apply_deposit_logic(t_uid, amt)
        u_after = res["user"]
        bot.send_message(
            chat_id,
            f"\u2705 \u0110\xe3 c\u1ed9ng <b>{fmt_num(amt)}</b> cho ID <code>{t_uid}</code>. V\xed m\u1edbi: <b>{fmt_num(float(u_after['balance']))}</b>",
        )
        try:
            bot.send_message(
                t_uid,
                f"\u2705 N\u1ea1p th\xe0nh c\xf4ng <b>{fmt_num(amt)}</b> v\xe0o v\xed!\n\u2022 S\u1ed1 d\u01b0 hi\u1ec7n t\u1ea1i: <b>{fmt_num(float(u_after['balance']))}</b>",
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
            bot.send_message(chat_id, "\u274c Vui l\xf2ng nh\u1eadp s\u1ed1 ti\u1ec1n h\u1ee3p l\u1ec7.")
            return
        if amt < MIN_DEPOSIT_AMOUNT:
            bot.send_message(chat_id, f"\u274c S\u1ed1 ti\u1ec1n n\u1ea1p t\u1ed1i thi\u1ec3u l\xe0 <b>{fmt_num(MIN_DEPOSIT_AMOUNT)}</b>.")
            return
        PENDING_STATE.pop(uid, None)
        send_deposit_qr(chat_id, uid, amt)
        return

    if act == "USER_WD_AMOUNT":
        try:
            amt = float(raw.replace(".", "").replace(",", ""))
        except ValueError:
            bot.send_message(chat_id, "\u274c Vui l\xf2ng nh\u1eadp s\u1ed1 ti\u1ec1n h\u1ee3p l\u1ec7.")
            return
        u_cur = db.get_user(uid)
        bal = float(u_cur["balance"])
        if amt < MIN_WITHDRAW_AMOUNT:
            bot.send_message(chat_id, f"\u274c S\u1ed1 ti\u1ec1n r\xfat t\u1ed1i thi\u1ec3u l\xe0 <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b>.")
            return
        if amt > bal + 1e-6:
            bot.send_message(chat_id, f"\u274c S\u1ed1 ti\u1ec1n r\xfat v\u01b0\u1ee3t s\u1ed1 d\u01b0 v\xed (T\u1ed1i \u0111a: <b>{fmt_num(bal)}</b>).")
            return
        PENDING_STATE[uid] = {"action": "USER_WD_INFO", "amount": amt}
        bot.send_message(
            chat_id,
            f"<b>NH\u1eacP TH\xd4NG TIN NH\u1eacN TI\u1ec0N R\xdaT ({fmt_num(amt)})</b>\n"
            f"G\u1eedi <b>\u1ea3nh m\xe3 QR</b> t\xe0i kho\u1ea3n nh\u1eadn HO\u1eb6C nh\u1eafn theo m\u1eabu:\n"
            f"<code>Ng\xe2n h\xe0ng - S\u1ed1 t\xe0i kho\u1ea3n - T\xean ch\u1ee7 TK</code>",
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
            bot.send_message(chat_id, "\u274c Vui l\xf2ng nh\u1eadp s\u1ed1 ti\u1ec1n h\u1ee3p l\u1ec7.")
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
