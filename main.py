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


# ==================== Bá» NÃT Báº¤M ====================
def kb_user_main(user_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("ð³ Náº¡p tiá»n", callback_data="u_deposit"),
        types.InlineKeyboardButton("ð¸ RÃºt tiá»n", callback_data="u_withdraw"),
    )
    kb.add(
        types.InlineKeyboardButton("ð¢ ÄÃ¡nh XANH", callback_data="u_side_XANH"),
        types.InlineKeyboardButton("ð´ ÄÃ¡nh Äá»", callback_data="u_side_DO"),
    )
    kb.add(types.InlineKeyboardButton("ð° Sá» dÆ° & MÃ£ Äang ÄÃ¡nh", callback_data="u_wallet"))
    if is_admin(user_id):
        kb.add(types.InlineKeyboardButton("ð ï¸ Menu Admin (Chat riÃªng)", callback_data="a_panel_open"))
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
    kb.add(types.InlineKeyboardButton("ð Quay láº¡i", callback_data="u_back"))
    return kb


def kb_deposit_amounts() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=3)
    btns = [
        types.InlineKeyboardButton(fmt_num(amt), callback_data=f"u_dep_amt_{amt}")
        for amt in QUICK_DEPOSIT_AMOUNTS
    ]
    kb.add(*btns)
    kb.add(
        types.InlineKeyboardButton("âï¸ Nháº­p sá» khÃ¡c", callback_data="u_dep_custom"),
        types.InlineKeyboardButton("ð Quay láº¡i", callback_data="u_back"),
    )
    return kb


def kb_user_deposit() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("â TÃ´i ÄÃ£ chuyá»n", callback_data="u_paid"),
        types.InlineKeyboardButton("ð Quay láº¡i", callback_data="u_back"),
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
        types.InlineKeyboardButton("âï¸ Nháº­p sá» khÃ¡c", callback_data=f"u_custom_{side}_{do_id}"),
        types.InlineKeyboardButton("ð Quay láº¡i", callback_data="u_back"),
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
    kb.add(types.InlineKeyboardButton(f"ð° RÃºt háº¿t vÃ­ ({fmt_num(bal)})", callback_data="u_wd_all"))
    kb.add(
        types.InlineKeyboardButton("âï¸ Nháº­p sá» rÃºt khÃ¡c", callback_data="u_wd_custom"),
        types.InlineKeyboardButton("ð Quay láº¡i", callback_data="u_back"),
    )
    return kb


def kb_master_viewer_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("ð Äá» Äang cháº¡y", callback_data="a_rep_live"),
        types.InlineKeyboardButton("ð Än thua tá»«ng Äá» / HT", callback_data="a_rep_pnl"),
    )
    kb.add(types.InlineKeyboardButton("ð BÃ¡o cÃ¡o Än thua cáº£ ngÃ y", callback_data="a_rep_daily"))
    return kb


def kb_master_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("ð Äá» Äang cháº¡y", callback_data="a_rep_live"),
        types.InlineKeyboardButton("ð Än thua tá»«ng Äá» / HT", callback_data="a_rep_pnl"),
    )
    kb.add(types.InlineKeyboardButton("ð BÃ¡o cÃ¡o Än thua cáº£ ngÃ y", callback_data="a_rep_daily"))
    kb.add(
        types.InlineKeyboardButton("ð³ Cáº¥p háº¡n má»©c ná»£", callback_data="a_set_debt_limit"),
        types.InlineKeyboardButton("ðµ Thu ná»£ / XÃ³a ná»£", callback_data="a_debt_menu"),
    )
    kb.add(
        types.InlineKeyboardButton("ð¤ Chá» Äá»nh duyá»t", callback_data="a_assign_approver"),
        types.InlineKeyboardButton("ð Chá»t sá» cuá»i ngÃ y", callback_data="a_day_reset"),
    )
    return kb


def kb_price_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("â Má» Äá» má»i", callback_data="a_new_do"),
        types.InlineKeyboardButton("âï¸ Äá»i giÃ¡ Äá»", callback_data="a_set_price"),
    )
    kb.add(types.InlineKeyboardButton("ð Chá»t Äá» / Nhiá»u Äá»", callback_data="a_multi_close_open"))
    return kb


def kb_bill_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("ð¤ Chá» Äá»nh nhÃ¢n viÃªn duyá»t", callback_data="a_assign_approver"))
    return kb


def kb_full_admin_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("ð Äá» Äang cháº¡y", callback_data="a_rep_live"),
        types.InlineKeyboardButton("ð Än thua tá»«ng Äá» / HT", callback_data="a_rep_pnl"),
    )
    kb.add(types.InlineKeyboardButton("ð BÃ¡o cÃ¡o Än thua cáº£ ngÃ y", callback_data="a_rep_daily"))
    kb.add(
        types.InlineKeyboardButton("â Má» Äá» má»i", callback_data="a_new_do"),
        types.InlineKeyboardButton("âï¸ Äá»i giÃ¡ Äá»", callback_data="a_set_price"),
    )
    kb.add(types.InlineKeyboardButton("ð Chá»t Äá» / Nhiá»u Äá»", callback_data="a_multi_close_open"))
    kb.add(
        types.InlineKeyboardButton("ð³ Cáº¥p háº¡n má»©c ná»£", callback_data="a_set_debt_limit"),
        types.InlineKeyboardButton("ðµ Thu ná»£ / XÃ³a ná»£", callback_data="a_debt_menu"),
    )
    kb.add(
        types.InlineKeyboardButton("ð¤ Chá» Äá»nh duyá»t", callback_data="a_assign_approver"),
        types.InlineKeyboardButton("ð Chá»t sá» cuá»i ngÃ y", callback_data="a_day_reset"),
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
    kb.add(types.InlineKeyboardButton("ð Äá»i chung táº¥t cáº£ Äá» má»", callback_data="a_pdo_all"))
    return kb


def kb_price_side_picker(do_target: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("ð¢ Chá»n cá»­a XANH", callback_data=f"a_pside_XANH_{do_target}"),
        types.InlineKeyboardButton("ð´ Chá»n cá»­a Äá»", callback_data=f"a_pside_DO_{do_target}"),
    )
    return kb


def kb_open_do_picker() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=4)
    btns = [
        types.InlineKeyboardButton(f"Äá» {i}", callback_data=f"a_open_num_{i}")
        for i in range(1, 9)
    ]
    kb.add(*btns)
    kb.add(types.InlineKeyboardButton("âï¸ Nháº­p sá» Äá» khÃ¡c", callback_data="a_open_custom"))
    return kb


def kb_multi_close_picker(selected_ids: set[int]) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    for d in db.get_open_dos():
        d_id = int(d["id"])
        mark = "â" if d_id in selected_ids else "â¬"
        kb.add(
            types.InlineKeyboardButton(
                f"{mark} {d['name']} ({float(d['xanh_win']):g}/{float(d['xanh_lose']):g})",
                callback_data=f"a_mclose_toggle_{d_id}",
            )
        )
    kb.add(
        types.InlineKeyboardButton("â¡ Chá»n táº¥t cáº£", callback_data="a_mclose_all"),
        types.InlineKeyboardButton("ðï¸ Bá» chá»n", callback_data="a_mclose_clear"),
    )
    kb.add(
        types.InlineKeyboardButton("ð¢ XANH tháº¯ng", callback_data="a_mclose_run_XANH"),
        types.InlineKeyboardButton("ð´ Äá» tháº¯ng", callback_data="a_mclose_run_DO"),
        types.InlineKeyboardButton("ð¤ HÃ²a", callback_data="a_mclose_run_DRAW"),
        types.InlineKeyboardButton("ð« Há»§y Äá»", callback_data="a_mclose_run_CANCEL"),
    )
    return kb


def kb_approve_deposit(user_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("â Duyá»t cá»ng tiá»n", callback_data=f"a_dep_ok_{user_id}"),
        types.InlineKeyboardButton("â Tá»« chá»i", callback_data=f"a_dep_no_{user_id}"),
    )
    kb.add(types.InlineKeyboardButton("ð Chá» Äá»nh ngÆ°á»i duyá»t", callback_data="a_assign_approver"))
    return kb


def kb_deposit_presets(user_id: int, suggested_amt: float = 0.0) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=3)
    if suggested_amt > 0:
        kb.add(
            types.InlineKeyboardButton(
                f"â ÄÃºng sá» khÃ¡ch chá»n (+{fmt_num(suggested_amt)})",
                callback_data=f"a_dep_amt_{user_id}_{suggested_amt}",
            )
        )
    btns = [
        types.InlineKeyboardButton(f"+{fmt_num(amt)}", callback_data=f"a_dep_amt_{user_id}_{amt}")
        for amt in [250000, 500000, 1000000, 2000000, 5000000, 10000000]
    ]
    kb.add(*btns)
    kb.add(types.InlineKeyboardButton("âï¸ Nháº­p sá» tiá»n khÃ¡c", callback_data=f"a_dep_custom_{user_id}"))
    return kb


def kb_withdrawal_actions(w_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("â ÄÃ£ chuyá»n tiá»n", callback_data=f"a_wd_ok_{w_id}"),
        types.InlineKeyboardButton("â Tá»« chá»i rÃºt", callback_data=f"a_wd_no_{w_id}"),
    )
    kb.add(types.InlineKeyboardButton("ð Chá» Äá»nh ngÆ°á»i duyá»t", callback_data="a_assign_approver"))
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
        bot.send_message(chat_id, "Äá» nÃ y ÄÃ£ ÄÃ³ng hoáº·c khÃ´ng tá»n táº¡i.", reply_markup=kb_user_main(uid))
        return
    win_u = float(do["xanh_win"] if side == "XANH" else do["do_win"])
    lose_u = float(do["xanh_lose"] if side == "XANH" else do["do_lose"])
    lim = get_user_betting_limit_info(uid, do_id=do_id)
    max_side = lim["max_xanh"] if side == "XANH" else lim["max_do"]
    side_label = "ð¢ XANH" if side == "XANH" else "ð´ Äá»"
    txt = (
        f"<b>{do['name']} â Cá»¬A {side_label}</b>\n"
        f"â¢ GiÃ¡ hiá»n táº¡i: <b>{win_u:g}/{lose_u:g}</b> <i>(trá»« 5% pháº¿ trÃªn tiá»n lá»i)</i>\n"
        f"â¢ Háº¡n má»©c tá»i Äa cá»­a {side_label}: <b>{fmt_num(max_side)}</b>\n"
        f"â¢ VÃ­: <b>{fmt_num(float(u['balance']))}</b> | Ná»£: <b>{fmt_num(float(u['debt']))}/{fmt_num(float(u['debt_limit']))}</b>\n\n"
        f"Chá»n má»©c tiá»n nhanh hoáº·c báº¥m <b>Nháº­p sá» khÃ¡c</b>:"
    )
    bot.send_message(chat_id, txt, reply_markup=kb_user_amounts(side, do_id))


def send_deposit_qr(chat_id: int, user_id: int, amount: float) -> None:
    PENDING_STATE[user_id] = {"action": "USER_DEPOSIT_PENDING", "amount": amount}
    qr_url = build_vietqr_url(user_id, amount)
    vnd_show = int(round(amount * QR_AMOUNT_MULTIPLIER))

    caption = (
        f"<b>MÃ QR Náº P TIá»N â {BANK_INFO['bank']}</b>\n"
        f"â¢ NgÃ¢n hÃ ng: <b>{BANK_INFO['bank']}</b>\n"
        f"â¢ Sá» tÃ i khoáº£n: <code>{BANK_INFO['stk']}</code>\n"
        f"â¢ Chá»§ tÃ i khoáº£n: <b>{BANK_INFO['owner']}</b>\n"
        f"â¢ Sá» tiá»n náº¡p: <b>{fmt_num(amount)}</b> (QR: <b>{fmt_num(vnd_show)}Ä</b>)\n"
        f"â¢ Ná»i dung chuyá»n khoáº£n: <code>{user_id}</code>\n\n"
        f"QuÃ©t mÃ£ QR hoáº·c chuyá»n khoáº£n ÄÃºng ná»i dung <code>{user_id}</code>, "
        f"sau ÄÃ³ báº¥m <b>â TÃ´i ÄÃ£ chuyá»n</b> bÃªn dÆ°á»i <i>(cÃ³ thá» gá»­i kÃ¨m áº£nh bill)</i>."
    )
    try:
        bot.send_photo(chat_id, qr_url, caption=caption, reply_markup=kb_user_deposit())
    except Exception:
        bot.send_message(chat_id, caption, reply_markup=kb_user_deposit())


def dispatch_withdrawal_to_bill_group(user: dict, w_rec: dict) -> None:
    if not BILL_GROUP_ID:
        return
    assigned_id = int(w_rec["assigned_staff_id"] or 0)
    approver_txt = f"<code>{assigned_id}</code>" if assigned_id > 0 else "<i>Táº¥t cáº£ nhÃ¢n viÃªn</i>"
    w_id = int(w_rec["id"])
    amt = float(w_rec["amount"])
    payout_txt = w_rec["payout_text"] or "Xem áº£nh mÃ£ QR ÄÃ­nh kÃ¨m"

    caption = (
        f"ð¸ <b>YÃU Cáº¦U RÃT TIá»N (#R{w_id:03d})</b>\n"
        f"â¢ KhÃ¡ch: <b>{user['name']}</b> (@{user['username'] or 'khÃ´ng_cÃ³'}) | ID: <code>{user['user_id']}</code>\n"
        f"â¢ Sá» tiá»n rÃºt: <b>{fmt_num(amt)}</b>\n"
        f"â¢ ThÃ´ng tin nháº­n: <code>{payout_txt}</code>\n"
        f"â¢ NhÃ¢n viÃªn chá» Äá»nh duyá»t: {approver_txt}\n"
        f"â¢ Thá»i gian: <code>{w_rec['created_at']}</code>"
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
            bot.send_message(source_chat_id, "â ÄÃ£ gá»­i bÃ¡o cÃ¡o vá» <b>nhÃ³m Master</b>.", reply_markup=kb_full_admin_panel())
    else:
        bot.send_message(source_chat_id, report_txt, reply_markup=get_panel_by_chat(source_chat_id, source_chat_type, actor_id))


# ==================== Lá»NH KHá»I Táº O ====================
@bot.message_handler(commands=["start", "menu"])
def cmd_start(message: types.Message):
    uid = message.from_user.id
    chat_id = message.chat.id
    chat_type = message.chat.type

    if chat_type == "private":
        u = db.get_user(uid, message.from_user.first_name, message.from_user.username)
        txt = (
            f"Xin chÃ o <b>{u['name']}</b> (ID: <code>{u['user_id']}</code>)\n"
            f"â¢ Sá» dÆ° vÃ­: <b>{fmt_num(float(u['balance']))}</b>\n"
            f"â¢ Háº¡n má»©c ghi ná»£: <b>{fmt_num(float(u['debt']))}</b> / <b>{fmt_num(float(u['debt_limit']))}</b>\n\n"
            f"Chá»n nÃºt bÃªn dÆ°á»i Äá» thao tÃ¡c:"
        )
        bot.send_message(chat_id, txt, reply_markup=kb_user_main(uid))
    elif is_master_group(chat_id):
        bot.send_message(
            chat_id,
            "<b>Báº¢NG BÃO CÃO & QUáº¢N LÃ NHÃM MASTER</b>",
            reply_markup=get_panel_by_chat(chat_id, chat_type, uid),
        )
    elif is_admin(uid):
        bot.send_message(
            chat_id,
            "<b>Báº¢NG ÄIá»U KHIá»N Váº¬N HÃNH</b>",
            reply_markup=get_panel_by_chat(chat_id, chat_type, uid),
        )


@bot.message_handler(commands=["admin"])
def cmd_admin(message: types.Message):
    uid = message.from_user.id
    if is_admin(uid) or is_master_group(message.chat.id):
        bot.send_message(
            message.chat.id,
            "<b>Báº¢NG ÄIá»U KHIá»N</b>",
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


# ==================== Xá»¬ LÃ NÃT Báº¤M ====================
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
            f"<b>MENU CHÃNH</b> â ID: <code>{uid}</code>\nSá» dÆ° vÃ­: <b>{fmt_num(float(u['balance']))}</b>",
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
                f"  â¢ {b['do_name']} | {b['code']} | {b['side']} | {fmt_num(float(b['amount']))} (giÃ¡ {b['price_str']})"
                for b in active_bets
            ]
        ) or "  <i>ChÆ°a cÃ³ mÃ£ nÃ o Äang cháº¡y.</i>"

        if not open_dos:
            limit_line = f"â¢ Háº¡n má»©c cÃ²n cÆ°á»£c ÄÆ°á»£c: <b>{fmt_num(lim['unused_capital'])}</b>"
        elif len(open_dos) == 1:
            d0 = open_dos[0]
            d_lim = get_user_betting_limit_info(uid, do_id=int(d0["id"]))
            limit_line = (
                f"â¢ Háº¡n má»©c cÃ²n cÆ°á»£c ÄÆ°á»£c ({d0['name']}): "
                f"ð¢ XANH <b>{fmt_num(d_lim['max_xanh'])}</b> | ð´ Äá» <b>{fmt_num(d_lim['max_do'])}</b>"
            )
        else:
            sub_lines = []
            for d in open_dos:
                d_lim = get_user_betting_limit_info(uid, do_id=int(d["id"]))
                sub_lines.append(
                    f"  - {d['name']}: ð¢ XANH <b>{fmt_num(d_lim['max_xanh'])}</b> | ð´ Äá» <b>{fmt_num(d_lim['max_do'])}</b>"
                )
            limit_line = "â¢ Háº¡n má»©c cÃ²n cÆ°á»£c ÄÆ°á»£c theo tá»«ng Äá»:\n" + "\n".join(sub_lines)

        txt = (
            f"<b>TÃI KHOáº¢N Cá»¦A Báº N</b> (ID: <code>{uid}</code>)\n"
            f"â¢ Sá» dÆ° vÃ­: <b>{fmt_num(float(u['balance']))}</b>\n"
            f"â¢ Äang ghi ná»£: <b>{fmt_num(float(u['debt']))}</b> / Háº¡n má»©c <b>{fmt_num(float(u['debt_limit']))}</b>\n"
            f"{limit_line}\n"
            f"â¢ Än/thua tá»i hiá»n táº¡i: {fmt_signed(float(u['pnl_net']))}\n\n"
            f"<b>MÃ£ Äang ÄÃ¡nh:</b>\n{bets_txt}"
        )
        bot.send_message(chat_id, txt, reply_markup=kb_user_main(uid))

    elif data == "u_deposit":
        bot.send_message(
            chat_id,
            f"<b>CHá»N Sá» TIá»N Cáº¦N Náº P</b>\n"
            f"â¢ Náº¡p tá»i thiá»u: <b>{fmt_num(MIN_DEPOSIT_AMOUNT)}</b>\n"
            f"â¢ NgÃ¢n hÃ ng: <b>{BANK_INFO['bank']}</b> â STK: <code>{BANK_INFO['stk']}</code>\n"
            f"â¢ Chá»§ TK: <b>{BANK_INFO['owner']}</b>\n"
            f"â¢ Ná»i dung CK máº·c Äá»nh: <code>{uid}</code>\n\n"
            f"Chá»n má»©c tiá»n nhanh hoáº·c báº¥m <b>Nháº­p sá» khÃ¡c</b> Äá» táº¡o mÃ£ QR:",
            reply_markup=kb_deposit_amounts(),
        )

    elif data.startswith("u_dep_amt_"):
        amt = float(data.split("_")[3])
        if amt < MIN_DEPOSIT_AMOUNT:
            bot.send_message(
                chat_id,
                f"â Sá» tiá»n náº¡p tá»i thiá»u lÃ  <b>{fmt_num(MIN_DEPOSIT_AMOUNT)}</b>.",
                reply_markup=kb_deposit_amounts(),
            )
            return
        send_deposit_qr(chat_id, uid, amt)

    elif data == "u_dep_custom":
        PENDING_STATE[uid] = {"action": "USER_DEP_CUSTOM_AMOUNT"}
        bot.send_message(
            chat_id,
            f"Nháº­p sá» tiá»n báº¡n muá»n náº¡p (Tá»i thiá»u: <b>{fmt_num(MIN_DEPOSIT_AMOUNT)}</b>):",
        )

    elif data == "u_paid":
        st = PENDING_STATE.get(uid)
        dep_amt = float(st["amount"]) if (st and st.get("action") == "USER_DEPOSIT_PENDING") else 0.0
        if dep_amt > 0:
            PENDING_STATE[uid] = {"action": "USER_LAST_DEP_AMT", "amount": dep_amt}
        approver_id = get_assigned_approver_id()
        approver_txt = f"<code>{approver_id}</code>" if approver_id > 0 else "<i>Táº¥t cáº£ nhÃ¢n viÃªn</i>"
        amt_line = f"â¢ Sá» tiá»n khÃ¡ch chá»n trÃªn QR: <b>{fmt_num(dep_amt)}</b>\n" if dep_amt > 0 else ""

        bot.send_message(
            chat_id,
            f"â ÄÃ£ gá»­i yÃªu cáº§u xÃ¡c nháº­n náº¡p tiá»n cho ID <code>{uid}</code>.\n"
            f"<i>Náº¿u cÃ³ áº£nh bill, hÃ£y gá»­i áº£nh vÃ o chat nÃ y Äá» nhÃ¢n viÃªn Äá»i soÃ¡t nhanh.</i>",
            reply_markup=kb_user_main(uid),
        )
        if BILL_GROUP_ID:
            bot.send_message(
                BILL_GROUP_ID,
                f"ð <b>YÃU Cáº¦U Náº P TIá»N Má»I</b>\n"
                f"â¢ KhÃ¡ch: <b>{u['name']}</b> (@{u['username'] or 'khÃ´ng_cÃ³'})\n"
                f"{amt_line}"
                f"â¢ Ná»i dung CK / ID: <code>{uid}</code>\n"
                f"â¢ NhÃ¢n viÃªn chá» Äá»nh duyá»t: {approver_txt}\n"
                f"â¢ Thá»i gian: <code>{datetime.now().strftime('%H:%M - %d/%m/%Y')}</code>",
                reply_markup=kb_approve_deposit(uid),
            )

    elif data == "u_withdraw":
        bal = float(u["balance"])
        if bal < MIN_WITHDRAW_AMOUNT:
            bot.send_message(
                chat_id,
                f"â Sá» dÆ° vÃ­ hiá»n táº¡i lÃ  <b>{fmt_num(bal)}</b>, chÆ°a Äáº¡t má»©c rÃºt tá»i thiá»u <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b>.",
                reply_markup=kb_user_main(uid),
            )
        else:
            bot.send_message(
                chat_id,
                f"<b>RÃT TIá»N Vá» TÃI KHOáº¢N</b>\n"
                f"â¢ RÃºt tá»i thiá»u: <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b> | Tá»i Äa: <b>{fmt_num(bal)}</b>\n\n"
                f"Chá»n má»©c tiá»n rÃºt nhanh hoáº·c báº¥m <b>RÃºt háº¿t vÃ­</b>:",
                reply_markup=kb_withdraw_amounts(bal),
            )

    elif data == "u_wd_all":
        bal = float(u["balance"])
        if bal < MIN_WITHDRAW_AMOUNT:
            bot.send_message(
                chat_id,
                f"â Sá» dÆ° vÃ­ hiá»n táº¡i lÃ  <b>{fmt_num(bal)}</b>, chÆ°a Äáº¡t má»©c rÃºt tá»i thiá»u <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b>.",
                reply_markup=kb_user_main(uid),
            )
        else:
            PENDING_STATE[uid] = {"action": "USER_WD_INFO", "amount": bal}
            bot.send_message(
                chat_id,
                f"<b>NHáº¬P THÃNG TIN NHáº¬N TIá»N RÃT Háº¾T VÃ ({fmt_num(bal)})</b>\n"
                f"Gá»­i <b>áº£nh mÃ£ QR</b> tÃ i khoáº£n nháº­n HOáº¶C nháº¯n theo máº«u:\n"
                f"<code>NgÃ¢n hÃ ng - Sá» tÃ i khoáº£n - TÃªn chá»§ TK</code>",
            )

    elif data.startswith("u_wd_amt_"):
        amt = float(data.split("_")[3])
        bal = float(u["balance"])
        if amt < MIN_WITHDRAW_AMOUNT:
            bot.send_message(
                chat_id,
                f"â Sá» tiá»n rÃºt tá»i thiá»u lÃ  <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b>.",
                reply_markup=kb_withdraw_amounts(bal),
            )
        elif amt > bal + 1e-6:
            bot.send_message(
                chat_id,
                f"â Sá» tiá»n rÃºt vÆ°á»£t sá» dÆ° vÃ­ (Tá»i Äa: <b>{fmt_num(bal)}</b>).",
                reply_markup=kb_withdraw_amounts(bal),
            )
        else:
            PENDING_STATE[uid] = {"action": "USER_WD_INFO", "amount": amt}
            bot.send_message(
                chat_id,
                f"<b>NHáº¬P THÃNG TIN NHáº¬N TIá»N RÃT ({fmt_num(amt)})</b>\n"
                f"Gá»­i <b>áº£nh mÃ£ QR</b> tÃ i khoáº£n nháº­n HOáº¶C nháº¯n theo máº«u:\n"
                f"<code>NgÃ¢n hÃ ng - Sá» tÃ i khoáº£n - TÃªn chá»§ TK</code>\n"
                f"<i>VÃ­ dá»¥: VCB - 0987654321 - TRAN VAN B</i>",
            )

    elif data == "u_wd_custom":
        bal = float(u["balance"])
        PENDING_STATE[uid] = {"action": "USER_WD_AMOUNT"}
        bot.send_message(
            chat_id,
            f"Nháº­p sá» tiá»n báº¡n muá»n rÃºt (Tá»i thiá»u: <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b> â Tá»i Äa: <b>{fmt_num(bal)}</b>):",
        )

    elif data.startswith("u_side_"):
        side = data.split("_")[2]
        if not open_dos:
            bot.send_message(chat_id, "â ï¸ Hiá»n chÆ°a cÃ³ Äá» nÃ o Äang má».", reply_markup=kb_user_main(uid))
        elif len(open_dos) > 1:
            side_label = "ð¢ XANH" if side == "XANH" else "ð´ Äá»"
            summary_lines = []
            for d in open_dos:
                p_lbl = (
                    f"{float(d['xanh_win']):g}/{float(d['xanh_lose']):g}"
                    if side == "XANH"
                    else f"{float(d['do_win']):g}/{float(d['do_lose']):g}"
                )
                summary_lines.append(f"â¢ <b>{d['name']}</b>: giÃ¡ {side_label} <code>{p_lbl}</code>")
            bot.send_message(
                chat_id,
                f"Äang má» <b>{len(open_dos)} Äá»</b> cÃ¹ng lÃºc:\n"
                + "\n".join(summary_lines)
                + f"\n\nChá»n Äá» báº¡n muá»n ÄÃ¡nh cá»­a <b>{side_label}</b>:",
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
        bot.send_message(chat_id, f"Nháº­p sá» tiá»n báº¡n muá»n ÄÃ¡nh cá»­a <b>{side}</b>:")

    # --- BÃO CÃO CHO MASTER & Cá» ÄÃNG TRONG NHÃM MASTER ---
    elif data in ("a_rep_live", "a_rep_pnl", "a_rep_daily"):
        if not can_view_master_report(uid, chat_id) and not (chat_type == "private" and is_admin(uid)):
            bot.answer_callback_query(call.id, "BÃ¡o cÃ¡o nÃ y chá» dÃ nh cho Master vÃ  nhÃ³m Master.", show_alert=True)
            return
        rtype = "live" if data == "a_rep_live" else ("daily" if data == "a_rep_daily" else "pnl")
        send_report_to_master(rtype, uid, chat_id, chat_type)

    elif data == "a_day_reset":
        if not is_master(uid):
            bot.answer_callback_query(call.id, "Chá» Master má»i ÄÆ°á»£c chá»t sá» cuá»i ngÃ y.", show_alert=True)
            return
        snapshot = close_day_accounting_logic()
        target_out = MASTER_GROUP_ID or chat_id
        bot.send_message(
            target_out,
            f"ð <b>ÄÃ CHá»T Sá» CUá»I NGÃY</b>\n\n{snapshot}",
            reply_markup=get_panel_by_chat(target_out, "group" if target_out != uid else "private", uid),
        )

    # --- ADMIN / MASTER / STAFF ACTIONS ---
    elif data.startswith("a_") and not is_admin(uid):
        bot.answer_callback_query(call.id, "Báº¡n khÃ´ng cÃ³ quyá»n thao tÃ¡c má»¥c nÃ y.", show_alert=True)
        return

    elif data == "a_panel_open":
        bot.send_message(chat_id, "<b>MENU ADMIN (CHAT RIÃNG)</b>", reply_markup=kb_full_admin_panel())

    elif data == "a_new_do":
        ok_time, time_msg = can_open_do_by_time()
        if not ok_time:
            bot.send_message(chat_id, time_msg, reply_markup=get_panel_by_chat(chat_id, chat_type, uid))
            return
        PENDING_STATE[uid] = {"action": "ADMIN_OPEN_SPECIFIC_DO"}
        bot.send_message(
            chat_id,
            "ð¯ <b>CHá»N HOáº¶C NHáº¬P Sá» Äá» MUá»N Má»</b>\n"
            "â¢ Há» trá»£ má» ngáº«u nhiÃªn theo thá»±c táº¿ dá»i lá»ch (vÃ­ dá»¥ má» <b>Äá» 3</b> trÆ°á»c, <b>Äá» 2</b> sau, hoáº·c <b>Äá» 5</b>).\n"
            "â¢ Má» Äá» nÃ o há» thá»ng sáº½ tá»± khÃ³a/hoÃ£n nháº­n kÃ¨o cÃ¡c Äá» khÃ¡c Äá» khÃ¡ch khÃ´ng ÄÃ¡nh nháº§m.",
            reply_markup=kb_open_do_picker(),
        )

    elif data.startswith("a_open_num_"):
        num_str = data.split("_")[3]
        PENDING_STATE.pop(uid, None)
        ok, msg, _ = open_specific_do_logic(num_str, exclusive_active=True)
        bot.send_message(chat_id, msg, reply_markup=get_panel_by_chat(chat_id, chat_type, uid) if ok else None)

    elif data == "a_open_custom":
        PENDING_STATE[uid] = {"action": "ADMIN_OPEN_SPECIFIC_DO"}
        bot.send_message(chat_id, "âï¸ Nháº­p sá» Äá» báº¡n muá»n má» (vÃ­ dá»¥: <code>3</code>, <code>5</code>, <code>12</code>):")

    elif data == "a_set_price":
        cur_open = db.get_open_dos()
        if not cur_open:
            bot.send_message(chat_id, "â ï¸ Hiá»n khÃ´ng cÃ³ Äá» nÃ o Äang má» Äá» Äá»i giÃ¡.")
            return
        if len(cur_open) == 1:
            d0 = cur_open[0]
            bot.send_message(
                chat_id,
                f"<b>Äá»I GIÃ {d0['name']}</b>\n"
                f"Hiá»n táº¡i: XANH <code>{float(d0['xanh_win']):g}/{float(d0['xanh_lose']):g}</code> | "
                f"Äá» <code>{float(d0['do_win']):g}/{float(d0['do_lose']):g}</code>\n\n"
                f"Chá»n 1 cá»­a Äá» nháº­p tá»· lá» (cá»­a cÃ²n láº¡i tá»± Äá»ng Äáº£o chiá»u):",
                reply_markup=kb_price_side_picker(str(d0["id"])),
            )
        else:
            bot.send_message(
                chat_id,
                "Chá»n Äá» cáº§n Äá»i giÃ¡ hoáº·c Äá»i chung táº¥t cáº£ Äá» Äang má»:",
                reply_markup=kb_price_do_picker(),
            )

    elif data.startswith("a_pdo_one_"):
        d_id = data.split("_")[3]
        d_obj = db.get_do_by_id(int(d_id))
        if not d_obj:
            bot.send_message(chat_id, "KhÃ´ng tÃ¬m tháº¥y Äá».")
            return
        bot.send_message(
            chat_id,
            f"<b>Äá»I GIÃ {d_obj['name']}</b> â Chá»n 1 cá»­a (cá»­a cÃ²n láº¡i tá»± Äáº£o):",
            reply_markup=kb_price_side_picker(d_id),
        )

    elif data == "a_pdo_all":
        bot.send_message(
            chat_id,
            "<b>Äá»I GIÃ CHUNG Táº¤T Cáº¢ Äá» Má»</b> â Chá»n 1 cá»­a (cá»­a cÃ²n láº¡i tá»± Äáº£o):",
            reply_markup=kb_price_side_picker("ALL"),
        )

    elif data.startswith("a_pside_"):
        _, _, chosen_side, do_target = data.split("_")
        if do_target == "ALL":
            target_ids = [int(d["id"]) for d in db.get_open_dos()]
            target_label = "Táº¥t cáº£ Äá» má»"
        else:
            d_obj = db.get_do_by_id(int(do_target))
            target_ids = [int(do_target)]
            target_label = d_obj["name"] if d_obj else "Äá» hiá»n táº¡i"

        PENDING_STATE[uid] = {
            "action": "ADMIN_SET_PRICE_ONE_SIDE",
            "side": chosen_side,
            "target_do_ids": target_ids,
            "target_label": target_label,
        }
        side_name = "ð¢ XANH" if chosen_side == "XANH" else "ð´ Äá»"
        opp_name = "ð´ Äá»" if chosen_side == "XANH" else "ð¢ XANH"
        bot.send_message(
            chat_id,
            f"Nháº­p tá»· lá» cho cá»­a <b>{side_name}</b> cá»§a <b>{target_label}</b>\n"
            f"<i>VÃ­ dá»¥ nháº­p <code>5/10</code> -> {side_name} lÃ  <b>5/10</b>, {opp_name} tá»± Äáº£o thÃ nh <b>10/5</b>:</i>",
        )

    elif data == "a_multi_close_open":
        cur_open = db.get_open_dos()
        default_sel = {int(cur_open[0]["id"])} if len(cur_open) == 1 else set()
        PENDING_STATE[uid] = {"action": "ADMIN_MULTI_CLOSE", "selected_ids": default_sel}
        bot.send_message(
            chat_id,
            "<b>CHá»T Äá» / CHá»T NHIá»U Äá» CÃNG LÃC</b>\n"
            "Báº¥m chá»n 1 hoáº·c nhiá»u Äá», sau ÄÃ³ báº¥m nÃºt káº¿t quáº£ bÃªn dÆ°á»i:",
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
            bot.answer_callback_query(call.id, "Báº¡n chÆ°a chá»n Äá» nÃ o Äá» chá»t.", show_alert=True)
            return

        PENDING_STATE.pop(uid, None)
        summary = settle_multiple_dos_logic(sel_ids, result_code)
        bot.answer_callback_query(call.id, f"ÄÃ£ chá»t {len(summary['do_names'])} Äá» ({result_code}).")

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
            bot.answer_callback_query(call.id, "Chá» Master má»i ÄÆ°á»£c cáº¥p háº¡n má»©c ná»£.", show_alert=True)
            return
        PENDING_STATE[uid] = {"action": "ADMIN_SET_DEBT_LIMIT"}
        bot.send_message(
            chat_id,
            "Nháº­p theo máº«u: <code>ID_KHACH HAN_MUC_NO</code>\n"
            "<i>VÃ­ dá»¥: <code>5770334027 10000000</code></i>",
        )

    elif data == "a_debt_menu":
        if not is_master(uid):
            bot.answer_callback_query(call.id, "Chá» Master má»i ÄÆ°á»£c xá»­ lÃ½ ná»£.", show_alert=True)
            return
        PENDING_STATE[uid] = {"action": "ADMIN_CLEAR_DEBT"}
        bot.send_message(
            chat_id,
            "Nháº­p theo máº«u: <code>ID_KHACH SO_TIEN_THU_NO</code> (nháº­p <code>0</code> hoáº·c <code>ALL</code> Äá» xÃ³a háº¿t ná»£):\n"
            "<i>VÃ­ dá»¥: <code>5770334027 2000000</code> hoáº·c <code>5770334027 ALL</code></i>",
        )

    elif data == "a_assign_approver":
        if not is_master(uid):
            bot.answer_callback_query(call.id, "Chá» Master má»i ÄÆ°á»£c chá» Äá»nh nhÃ¢n viÃªn duyá»t.", show_alert=True)
            return
        PENDING_STATE[uid] = {"action": "ADMIN_ASSIGN_APPROVER"}
        cur_app = get_assigned_approver_id()
        bot.send_message(
            chat_id,
            f"NhÃ¢n viÃªn duyá»t hiá»n táº¡i: <code>{cur_app or 'ChÆ°a chá» Äá»nh (má»i admin duyá»t)'}</code>\n"
            f"Nháº­p <b>User ID</b> nhÃ¢n viÃªn cáº§n chá» Äá»nh (hoáº·c nháº­p <code>0</code> Äá» má» cho táº¥t cáº£ admin):",
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
            f"Chá»n sá» tiá»n cá»ng vÃ o vÃ­ khÃ¡ch ID <code>{target_uid}</code>:",
            reply_markup=kb_deposit_presets(target_uid, suggested),
        )

    elif data.startswith("a_dep_no_"):
        target_uid = int(data.split("_")[3])
        ok, err = can_approve_item(uid)
        if not ok:
            bot.answer_callback_query(call.id, err, show_alert=True)
            return
        PENDING_STATE.pop(target_uid, None)
        bot.send_message(chat_id, f"â ÄÃ£ tá»« chá»i yÃªu cáº§u náº¡p cá»§a ID <code>{target_uid}</code>.")
        try:
            bot.send_message(target_uid, "â YÃªu cáº§u náº¡p tiá»n cá»§a báº¡n chÆ°a ÄÆ°á»£c duyá»t. Vui lÃ²ng liÃªn há» há» trá»£.")
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
            f"â ÄÃ£ cá»ng <b>{fmt_num(amt)}</b> cho <b>{u_after['name']}</b> (<code>{target_uid}</code>).\n"
            f"â¢ VÃ­ má»i: <b>{fmt_num(float(u_after['balance']))}</b>",
        )
        try:
            bot.send_message(
                target_uid,
                f"â Náº¡p thÃ nh cÃ´ng <b>{fmt_num(amt)}</b> vÃ o vÃ­!\n"
                f"â¢ Sá» dÆ° hiá»n táº¡i: <b>{fmt_num(float(u_after['balance']))}</b>",
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
        bot.send_message(chat_id, f"Nháº­p sá» tiá»n muá»n cá»ng cho khÃ¡ch ID <code>{target_uid}</code>:")

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
                    f"â Lá»nh rÃºt <code>#R{w_id:03d}</code> (<b>{fmt_num(float(w_rec['amount']))}</b>) ÄÃ£ ÄÆ°á»£c chuyá»n tiá»n!",
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
                    f"â Lá»nh rÃºt <code>#R{w_id:03d}</code> bá» tá»« chá»i vÃ  ÄÃ£ hoÃ n láº¡i <b>{fmt_num(float(w_rec['amount']))}</b> vÃ o vÃ­.",
                    reply_markup=kb_user_main(int(w_rec["user_id"])),
                )
            except Exception:
                pass

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


# ==================== Xá»¬ LÃ TIN NHáº®N & áº¢NH THEO TRáº NG THÃI ====================
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
        approver_txt = f"<code>{approver_id}</code>" if approver_id > 0 else "<i>Táº¥t cáº£ nhÃ¢n viÃªn</i>"
        cap = (
            f"ð§¾ <b>BILL Náº P TIá»N Tá»ª KHÃCH</b>\n"
            f"â¢ KhÃ¡ch: <b>{u['name']}</b> (@{u['username'] or 'khÃ´ng_cÃ³'}) | ID: <code>{uid}</code>\n"
            f"â¢ NhÃ¢n viÃªn chá» Äá»nh duyá»t: {approver_txt}\n"
            f"â¢ Thá»i gian: <code>{datetime.now().strftime('%H:%M - %d/%m/%Y')}</code>"
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
            bot.send_message(chat_id, "â Tá»· lá» khÃ´ng há»£p lá». VÃ­ dá»¥ nháº­p: <code>5/10</code> hoáº·c <code>10/8</code>.")
            return

        PENDING_STATE.pop(uid, None)
        for d_id in target_ids:
            db.update_do(d_id, **ratio_map)
        bot.send_message(
            chat_id,
            f"â ÄÃ£ cáº­p nháº­t giÃ¡ cho <b>{target_label}</b>:\n"
            f"â¢ XANH: <code>{ratio_map['xanh_win']:g}/{ratio_map['xanh_lose']:g}</code>\n"
            f"â¢ Äá»: <code>{ratio_map['do_win']:g}/{ratio_map['do_lose']:g}</code>",
            reply_markup=get_panel_by_chat(chat_id, chat_type, uid),
        )
        return

    if act == "ADMIN_SET_DEBT_LIMIT" and is_master(uid):
        parts = raw.split()
        if len(parts) != 2 or not parts[0].isdigit():
            bot.send_message(chat_id, "â Sai cÃº phÃ¡p. Nháº­p: <code>ID_KHACH HAN_MUC_NO</code>")
            return
        t_uid = int(parts[0])
        try:
            d_lim = float(parts[1].replace(".", "").replace(",", ""))
        except ValueError:
            bot.send_message(chat_id, "â Háº¡n má»©c ná»£ khÃ´ng há»£p lá».")
            return
        PENDING_STATE.pop(uid, None)
        db.get_user(t_uid)
        db.update_user_fields(t_uid, debt_limit=max(0.0, d_lim))
        u_after = db.get_user(t_uid)
        bot.send_message(
            chat_id,
            f"â ÄÃ£ cáº­p nháº­t háº¡n má»©c ná»£ cho ID <code>{t_uid}</code>: <b>{fmt_num(float(u_after['debt_limit']))}</b>",
            reply_markup=get_panel_by_chat(chat_id, chat_type, uid),
        )
        return

    if act == "ADMIN_CLEAR_DEBT" and is_master(uid):
        parts = raw.split()
        if len(parts) != 2 or not parts[0].isdigit():
            bot.send_message(chat_id, "â Sai cÃº phÃ¡p. Nháº­p: <code>ID_KHACH SO_TIEN_HOAC_ALL</code>")
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
                bot.send_message(chat_id, "â Sá» tiá»n khÃ´ng há»£p lá».")
                return
            new_debt = max(0.0, cur_debt - paid)
        db.update_user_fields(t_uid, debt=new_debt)
        bot.send_message(
            chat_id,
            f"â ÄÃ£ thu/xÃ³a <b>{fmt_num(paid)}</b> ná»£ cho ID <code>{t_uid}</code>. Ná»£ cÃ²n láº¡i: <b>{fmt_num(new_debt)}</b>",
            reply_markup=get_panel_by_chat(chat_id, chat_type, uid),
        )
        return

    if act == "ADMIN_ASSIGN_APPROVER" and is_master(uid):
        if not raw.isdigit():
            bot.send_message(chat_id, "â Vui lÃ²ng nháº­p User ID dáº¡ng sá» (hoáº·c <code>0</code>).")
            return
        staff_id = int(raw)
        PENDING_STATE.pop(uid, None)
        set_assigned_approver_id(staff_id)
        txt = f"â ÄÃ£ chá» Äá»nh nhÃ¢n viÃªn duyá»t ID <code>{staff_id}</code>." if staff_id > 0 else "â ÄÃ£ má» quyá»n duyá»t cho táº¥t cáº£ admin."
        bot.send_message(chat_id, txt, reply_markup=get_panel_by_chat(chat_id, chat_type, uid))
        return

    if act == "ADMIN_DEP_CUSTOM_AMT" and is_admin(uid):
        t_uid = int(st["target_uid"])
        try:
            amt = float(raw.replace(".", "").replace(",", ""))
            if amt <= 0:
                raise ValueError()
        except ValueError:
            bot.send_message(chat_id, "â Sá» tiá»n khÃ´ng há»£p lá».")
            return
        PENDING_STATE.pop(uid, None)
        res = apply_deposit_logic(t_uid, amt)
        u_after = res["user"]
        bot.send_message(
            chat_id,
            f"â ÄÃ£ cá»ng <b>{fmt_num(amt)}</b> cho ID <code>{t_uid}</code>. VÃ­ má»i: <b>{fmt_num(float(u_after['balance']))}</b>",
        )
        try:
            bot.send_message(
                t_uid,
                f"â Náº¡p thÃ nh cÃ´ng <b>{fmt_num(amt)}</b> vÃ o vÃ­!\nâ¢ Sá» dÆ° hiá»n táº¡i: <b>{fmt_num(float(u_after['balance']))}</b>",
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
            bot.send_message(chat_id, "â Vui lÃ²ng nháº­p sá» tiá»n há»£p lá».")
            return
        if amt < MIN_DEPOSIT_AMOUNT:
            bot.send_message(chat_id, f"â Sá» tiá»n náº¡p tá»i thiá»u lÃ  <b>{fmt_num(MIN_DEPOSIT_AMOUNT)}</b>.")
            return
        PENDING_STATE.pop(uid, None)
        send_deposit_qr(chat_id, uid, amt)
        return

    if act == "USER_WD_AMOUNT":
        try:
            amt = float(raw.replace(".", "").replace(",", ""))
        except ValueError:
            bot.send_message(chat_id, "â Vui lÃ²ng nháº­p sá» tiá»n há»£p lá».")
            return
        u_cur = db.get_user(uid)
        bal = float(u_cur["balance"])
        if amt < MIN_WITHDRAW_AMOUNT:
            bot.send_message(chat_id, f"â Sá» tiá»n rÃºt tá»i thiá»u lÃ  <b>{fmt_num(MIN_WITHDRAW_AMOUNT)}</b>.")
            return
        if amt > bal + 1e-6:
            bot.send_message(chat_id, f"â Sá» tiá»n rÃºt vÆ°á»£t sá» dÆ° vÃ­ (Tá»i Äa: <b>{fmt_num(bal)}</b>).")
            return
        PENDING_STATE[uid] = {"action": "USER_WD_INFO", "amount": amt}
        bot.send_message(
            chat_id,
            f"<b>NHáº¬P THÃNG TIN NHáº¬N TIá»N RÃT ({fmt_num(amt)})</b>\n"
            f"Gá»­i <b>áº£nh mÃ£ QR</b> tÃ i khoáº£n nháº­n HOáº¶C nháº¯n theo máº«u:\n"
            f"<code>NgÃ¢n hÃ ng - Sá» tÃ i khoáº£n - TÃªn chá»§ TK</code>",
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
            bot.send_message(chat_id, "â Vui lÃ²ng nháº­p sá» tiá»n há»£p lá».")
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
