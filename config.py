import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "8385677064:AAFHuHhOd4Hjxdg8fjR2dGX3KQXvp0SwRlA")

# 3 nhóm vận hành chuyên biệt + 1 kênh chính
MASTER_GROUP_ID = int(os.getenv("MASTER_GROUP_ID", "-1004343434771"))
PRICE_GROUP_ID = int(os.getenv("PRICE_GROUP_ID", "-1004439749096"))
BILL_GROUP_ID = int(os.getenv("BILL_GROUP_ID", "-1004286708283"))
MAIN_CHANNEL_ID = int(os.getenv("MAIN_CHANNEL_ID", "-1004459438101"))


def _parse_ids(env_name: str, default_str: str) -> set[int]:
    raw = os.getenv(env_name, default_str)
    return {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}


# Master có toàn quyền báo cáo, cấp hạn mức nợ, thu/xóa nợ, chỉ định duyệt & vận hành
MASTER_IDS = _parse_ids("MASTER_IDS", "7709258303")
# Nhân viên trực vận hành (duyệt nạp/rút, đổi giá, chốt độ)
STAFF_IDS = _parse_ids("STAFF_IDS", "7709258303")
ADMIN_IDS = MASTER_IDS | STAFF_IDS

BANK_INFO = {
    "bank": os.getenv("BANK_NAME", "TECHCOMBANK"),
    "bank_bin": os.getenv("BANK_BIN", "970407"),
    "stk": os.getenv("BANK_STK", "2348312936"),
    "owner": os.getenv("BANK_OWNER", "LE DINH HOANG"),
}

# Mức tiền nạp nhanh trước khi sinh VietQR (chỉnh theo đơn vị thực tế của bạn)
QUICK_DEPOSIT_AMOUNTS = [5000, 10000, 20000, 50000, 100000]
QR_AMOUNT_MULTIPLIER = int(os.getenv("QR_AMOUNT_MULTIPLIER", "1"))

QUICK_BET_AMOUNTS = [500, 1000, 2000, 5000, 10000]
QUICK_WITHDRAW_AMOUNTS = [1000, 5000, 10000, 20000, 50000]
FEE_RATE = 0.05  # Trừ 5% phế trên phần tiền lời
DB_PATH = os.getenv("DB_PATH", "bot_ga_don.db")
PORT = int(os.getenv("PORT", "10000"))
