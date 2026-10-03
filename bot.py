import os
import sqlite3
from datetime import datetime, date
from flask import Flask, request
import requests

# =========================
# CONFIG
# =========================

TOKEN = os.environ["BOT_TOKEN"]
ADSGRAM_TOKEN = os.environ["ADSGRAM_TOKEN"]

ADMIN_ID = 7926491409

API = f"https://api.telegram.org/bot{TOKEN}"
DB = "bot.db"

# AdsGram Block ID: bot-51644 -> use only numeric part
ADSGRAM_BLOCK_ID = "51644"

DAILY_BONUS = 20
REFERRAL_BONUS = 100
AD_REWARD = 50

MIN_WITHDRAW = 10000
MAX_WITHDRAW = 10000
COINS_PER_TAKA = 100

app = Flask(__name__)

# =========================
# DATABASE
# =========================

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            coins INTEGER DEFAULT 0,
            ads_watched INTEGER DEFAULT 0,
            coins_earned INTEGER DEFAULT 0,
            last_bonus TEXT,
            referred_by INTEGER,
            joined_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            reason TEXT,
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            method TEXT,
            number TEXT,
            coins INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ad_rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


init_db()

# =========================
# TELEGRAM HELPERS
# =========================

def tg(method, data=None):
    try:
        r = requests.post(
            f"{API}/{method}",
            data=data or {},
            timeout=20
        )
        return r.json()
    except Exception:
        return {}


def send_message(chat_id, text, keyboard=None, protect=False):
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }

    if keyboard:
        data["reply_markup"] = keyboard

    if protect:
        data["protect_content"] = True

    return tg("sendMessage", data)


def send_photo(chat_id, photo, caption="", keyboard=None):
    data = {
        "chat_id": chat_id,
        "photo": photo,
        "caption": caption,
        "parse_mode": "HTML",
        "protect_content": True
    }

    if keyboard:
        data["reply_markup"] = keyboard

    return tg("sendPhoto", data)


def delete_message(chat_id, message_id):
    return tg("deleteMessage", {
        "chat_id": chat_id,
        "message_id": message_id
    })


def answer_callback(callback_id, text=""):
    return tg("answerCallbackQuery", {
        "callback_query_id": callback_id,
        "text": text
    })


# =========================
# KEYBOARDS
# =========================

def main_menu():
    return {
        "keyboard": [
            [{"text": "🪙 My Coins"}, {"text": "📺 Watch Ad"}],
            [{"text": "🎁 Daily Bonus"}, {"text": "👥 Referral"}],
            [{"text": "💰 Withdraw"}, {"text": "🏆 Leaderboard"}],
            [{"text": "📜 History"}]
        ],
        "resize_keyboard": True
    }


def admin_menu():
    return {
        "keyboard": [
            [{"text": "📊 Dashboard"}, {"text": "👥 Users"}],
            [{"text": "💸 Withdrawals"}, {"text": "📢 Ads"}],
            [{"text": "🏆 Leaderboard"}, {"text": "📜 History"}],
            [{"text": "🏠 User Menu"}]
        ],
        "resize_keyboard": True
    }


# =========================
# USER FUNCTIONS
# =========================

def get_user(user_id, username=""):
    conn = db()
    user = conn.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()

    if not user:
        now = datetime.utcnow().isoformat()

        conn.execute("""
            INSERT INTO users
            (user_id, username, coins, ads_watched,
             coins_earned, joined_at)
            VALUES (?, ?, 0, 0, 0, ?)
        """, (user_id, username, now))

        conn.commit()

        user = conn.execute(
            "SELECT * FROM users WHERE user_id=?",
            (user_id,)
        ).fetchone()

    elif username and user["username"] != username:
        conn.execute(
            "UPDATE users SET username=? WHERE user_id=?",
            (username, user_id)
        )
        conn.commit()

        user = conn.execute(
            "SELECT * FROM users WHERE user_id=?",
            (user_id,)
        ).fetchone()

    conn.close()
    return user


def add_coins(user_id, amount, reason):
    conn = db()

    conn.execute("""
        UPDATE users
        SET coins = coins + ?,
            coins_earned = coins_earned + ?
        WHERE user_id=?
    """, (amount, amount, user_id))

    conn.execute("""
        INSERT INTO history
        (user_id, amount, reason, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        user_id,
        amount,
        reason,
        datetime.utcnow().isoformat()
    ))

    conn.commit()
    conn.close()


def remove_coins(user_id, amount, reason):
    conn = db()

    conn.execute("""
        UPDATE users
        SET coins = coins - ?
        WHERE user_id=? AND coins >= ?
    """, (amount, user_id, amount))

    conn.execute("""
        INSERT INTO history
        (user_id, amount, reason, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        user_id,
        -amount,
        reason,
        datetime.utcnow().isoformat()
    ))

    conn.commit()
    conn.close()


def set_pending_ad(user_id):
    conn = db()

    conn.execute(
        "DELETE FROM ad_rewards WHERE user_id=?",
        (user_id,)
    )

    conn.execute("""
        INSERT INTO ad_rewards
        (user_id, created_at)
        VALUES (?, ?)
    """, (
        user_id,
        datetime.utcnow().isoformat()
    ))

    conn.commit()
    conn.close()


def claim_ad_reward(user_id):
    conn = db()

    row = conn.execute("""
        SELECT * FROM ad_rewards
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (user_id,)).fetchone()

    if not row:
        conn.close()
        return False

    # Reward request has been consumed
    conn.execute(
        "DELETE FROM ad_rewards WHERE id=?",
        (row["id"],)
    )

    conn.execute("""
        UPDATE users
        SET coins = coins + ?,
            ads_watched = ads_watched + 1,
            coins_earned = coins_earned + ?
        WHERE user_id=?
    """, (
        AD_REWARD,
        AD_REWARD,
        user_id
    ))

    conn.execute("""
        INSERT INTO history
        (user_id, amount, reason, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        user_id,
        AD_REWARD,
        "AdsGram Ad Reward",
        datetime.utcnow().isoformat()
    ))

    conn.commit()
    conn.close()

    return True


# =========================
# ADSGRAM
# =========================

def get_adsgram_ad(user_id):
    try:
        url = "https://api.adsgram.ai/advbot"

        params = {
            "tgid": user_id,
            "blockid": ADSGRAM_BLOCK_ID,
            "language": "en",
            "token": ADSGRAM_TOKEN
        }

        response = requests.get(
            url,
            params=params,
            timeout=20
        )

        if response.status_code != 200:
            return None

        return response.json()

    except Exception:
        return None


def show_adsgram_ad(user_id):
    ad = get_adsgram_ad(user_id)

    if not ad:
        return False

    text_html = ad.get("text_html", "Sponsored Ad")
    click_url = ad.get("click_url")
    reward_url = ad.get("reward_url")
    button_name = ad.get("button_name", "Open Ad")
    reward_name = ad.get(
        "button_reward_name",
        "Claim Reward"
    )
    image_url = ad.get("image_url")

    if not click_url or not reward_url:
        return False

    # User has an active ad session
    set_pending_ad(user_id)

    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": button_name,
                    "url": click_url
                }
            ],
            [
                {
                    "text": reward_name,
                    "url": reward_url
                }
            ]
        ]
    }

    if image_url:
        send_photo(
            user_id,
            image_url,
            text_html,
            keyboard
        )
    else:
        send_message(
            user_id,
            text_html,
            keyboard,
            protect=True
        )

    return True


# =========================
# WITHDRAWAL
# =========================

def withdrawal_request(user_id, method, number):
    user = get_user(user_id)

    if user["coins"] != MIN_WITHDRAW:
        return False

    conn = db()

    conn.execute("""
        INSERT INTO withdrawals
        (user_id, username, method, number,
         coins, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
    """, (
        user_id,
        user["username"] or "",
        method,
        number,
        MIN_WITHDRAW,
        datetime.utcnow().isoformat()
    ))

    conn.commit()

    withdrawal_id = conn.execute(
        "SELECT last_insert_rowid()"
    ).fetchone()[0]

    conn.close()

    return withdrawal_id


def get_pending_withdrawals():
    conn = db()

    rows = conn.execute("""
        SELECT * FROM withdrawals
        WHERE status='pending'
        ORDER BY id DESC
    """).fetchall()

    conn.close()
    return rows


# =========================
# ADMIN
# =========================

def is_admin(user_id):
    return user_id == ADMIN_ID


def admin_dashboard():
    conn = db()

    users = conn.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    total_coins = conn.execute(
        "SELECT COALESCE(SUM(coins),0) FROM users"
    ).fetchone()[0]

    ads = conn.execute(
        "SELECT COALESCE(SUM(ads_watched),0) FROM users"
    ).fetchone()[0]

    pending = conn.execute("""
        SELECT COUNT(*) FROM withdrawals
        WHERE status='pending'
    """).fetchone()[0]

    conn.close()

    return (
        "📊 <b>Admin Dashboard</b>\n\n"
        f"👥 Users: {users}\n"
        f"🪙 Total Coins: {total_coins}\n"
        f"📺 Ads Watched: {ads}\n"
        f"💸 Pending Withdrawals: {pending}"
    )


def send_pending_withdrawals(chat_id):
    rows = get_pending_withdrawals()

    if not rows:
        send_message(
            chat_id,
            "✅ কোনো Pending Withdrawal নেই।",
            admin_menu()
        )
        return

    for row in rows:
        text = (
            "💸 <b>Withdrawal Request</b>\n\n"
            f"🆔 Request ID: <code>{row['id']}</code>\n"
            f"👤 User: @{row['username'] or 'No Username'}\n"
            f"🆔 Telegram ID: <code>{row['user_id']}</code>\n"
            f"💳 Method: {row['method']}\n"
            f"📱 Number: <code>{row['number']}</code>\n"
            f"🪙 Coins: {row['coins']}\n"
            f"💰 Amount: ৳{row['coins'] // COINS_PER_TAKA}\n"
            f"⏳ Status: {row['status']}"
        )

        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "✅ Approve",
                        "callback_data": f"approve_{row['id']}"
                    },
                    {
                        "text": "❌ Reject",
                        "callback_data": f"reject_{row['id']}"
                    }
                ]
            ]
        }

        send_message(
            chat_id,
            text,
            keyboard
        )


# =========================
# FLASK
# =========================

@app.route("/", methods=["GET", "HEAD"])
def home():
    return "Found Coins Bot is running!"


# =========================
# ADSGRAM REWARD URL
# =========================

@app.route("/adsgram/reward", methods=["GET"])
def adsgram_reward():
    user_id = request.args.get("userid")

    if not user_id:
        return "Invalid user", 400

    try:
        user_id = int(user_id)
    except:
        return "Invalid user", 400

    success = claim_ad_reward(user_id)

    if success:
        user = get_user(user_id)

        send_message(
            user_id,
            "🎉 <b>Ad Completed!</b>\n\n"
            f"🪙 +{AD_REWARD} Coins added!\n"
            f"💰 Your Balance: {user['coins']} Coins",
            main_menu()
        )

        return "Reward added successfully."

    return "Reward already claimed or invalid."


# =========================
# TELEGRAM WEBHOOK
# =========================

@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(silent=True) or {}

    # =====================
    # CALLBACK QUERY
    # =====================

    if "callback_query" in update:
        callback = update["callback_query"]

        callback_id = callback["id"]
        user_id = callback["from"]["id"]
        data = callback.get("data", "")

        answer_callback(callback_id)

        if not is_admin(user_id):
            return "ok"

        # APPROVE
        if data.startswith("approve_"):
            withdrawal_id = int(
                data.split("_")[1]
            )

            conn = db()

            row = conn.execute("""
                SELECT * FROM withdrawals
                WHERE id=? AND status='pending'
            """, (withdrawal_id,)).fetchone()

            if row:
                conn.execute("""
                    UPDATE withdrawals
                    SET status='approved'
                    WHERE id=?
                """, (withdrawal_id,))

                conn.commit()

            conn.close()

            if row:
                delete_message(
                    user_id,
                    callback["message"]["message_id"]
                )

                send_message(
                    row["user_id"],
                    "✅ <b>Payment Successful!</b>\n\n"
                    "আপনার withdrawal payment সফলভাবে সম্পন্ন হয়েছে।\n\n"
                    f"💰 Amount: ৳{row['coins'] // COINS_PER_TAKA}",
                    main_menu()
                )

                send_message(
                    user_id,
                    f"✅ Withdrawal #{withdrawal_id} Approved."
                )

        # REJECT
        elif data.startswith("reject_"):
            withdrawal_id = int(
                data.split("_")[1]
            )

            conn = db()

            row = conn.execute("""
                SELECT * FROM withdrawals
                WHERE id=? AND status='pending'
            """, (withdrawal_id,)).fetchone()

            if row:
                conn.execute("""
                    UPDATE withdrawals
                    SET status='rejected'
                    WHERE id=?
                """, (withdrawal_id,))

                conn.commit()

            conn.close()

            if row:
                add_coins(
                    row["user_id"],
                    row["coins"],
                    "Withdrawal Refund"
                )

                delete_message(
                    user_id,
                    callback["message"]["message_id"]
                )

                send_message(
                    row["user_id"],
                    "❌ <b>Payment Rejected</b>\n\n"
                    "আপনার withdrawal request rejected হয়েছে।\n"
                    "Coins আপনার balance-এ ফেরত দেওয়া হয়েছে।",
                    main_menu()
                )

                send_message(
                    user_id,
                    f"❌ Withdrawal #{withdrawal_id} Rejected."
                )

        return "ok"

    # =====================
    # NORMAL MESSAGE
    # =====================

    message = update.get("message")

    if not message:
        return "ok"

    user = message.get("from", {})
    user_id = user.get("id")
    username = user.get("username", "")
    text = message.get("text", "").strip()

    if not user_id:
        return "ok"

    get_user(user_id, username)

    # =====================
    # START
    # =====================

    if text.startswith("/start"):
        parts = text.split()

        if len(parts) > 1:
            try:
                referrer = int(parts[1])

                if referrer != user_id:
                    conn = db()

                    current = conn.execute("""
                        SELECT referred_by FROM users
                        WHERE user_id=?
                    """, (user_id,)).fetchone()

                    if current and current["referred_by"] is None:
                        ref_exists = conn.execute("""
                            SELECT user_id FROM users
                            WHERE user_id=?
                        """, (referrer,)).fetchone()

                        if ref_exists:
                            conn.execute("""
                                UPDATE users
                                SET referred_by=?
                                WHERE user_id=?
                            """, (referrer, user_id))

                            conn.commit()

                            add_coins(
                                referrer,
                                REFERRAL_BONUS,
                                "Referral Bonus"
                            )

                            send_message(
                                referrer,
                                f"🎉 Referral Bonus +{REFERRAL_BONUS} Coins!"
                            )

                    conn.close()

            except:
                pass

        send_message(
            user_id,
            "🎉 <b>Welcome to Found Coins!</b>\n\n"
            "🪙 Earn Coins by completing tasks and watching ads.\n\n"
            "💰 100 Coins = ৳1\n"
            "🎯 Withdrawal = 10,000 Coins = ৳100",
            main_menu()
        )

        return "ok"

    # =====================
    # ADMIN MENU
    # =====================

    if is_admin(user_id) and text == "/admin":
        send_message(
            user_id,
            admin_dashboard(),
            admin_menu()
        )
        return "ok"

    if is_admin(user_id) and text == "📊 Dashboard":
        send_message(
            user_id,
            admin_dashboard(),
            admin_menu()
        )
        return "ok"

    if is_admin(user_id) and text == "💸 Withdrawals":
        send_pending_withdrawals(user_id)
        return "ok"

    if is_admin(user_id) and text == "👥 Users":
        conn = db()

        count = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        conn.close()

        send_message(
            user_id,
            f"👥 <b>Total Users:</b> {count}",
            admin_menu()
        )
        return "ok"

    if is_admin(user_id) and text == "🏆 Leaderboard":
        conn = db()

        rows = conn.execute("""
            SELECT username, coins
            FROM users
            ORDER BY coins DESC
            LIMIT 10
        """).fetchall()

        conn.close()

        msg = "🏆 <b>Top 10 Leaderboard</b>\n\n"

        for i, row in enumerate(rows, 1):
            name = row["username"] or "User"
            msg += f"{i}. @{name} — {row['coins']} Coins\n"

        send_message(
            user_id,
            msg,
            admin_menu()
        )
        return "ok"

    if is_admin(user_id) and text == "📢 Ads":
        send_message(
            user_id,
            "📢 <b>AdsGram</b>\n\n"
            f"Block ID: {ADSGRAM_BLOCK_ID}\n"
            f"Reward: {AD_REWARD} Coins / completed ad",
            admin_menu()
        )
        return "ok"

    if is_admin(user_id) and text == "📜 History":
        conn = db()

        rows = conn.execute("""
            SELECT user_id, amount, reason, created_at
            FROM history
            ORDER BY id DESC
            LIMIT 20
        """).fetchall()

        conn.close()

        msg = "📜 <b>Recent History</b>\n\n"

        for row in rows:
            msg += (
                f"👤 {row['user_id']} | "
                f"{row['amount']} | "
                f"{row['reason']}\n"
            )

        send_message(
            user_id,
            msg,
            admin_menu()
        )
        return "ok"

    if is_admin(user_id) and text == "🏠 User Menu":
        send_message(
            user_id,
            "🏠 User Menu",
            main_menu()
        )
        return "ok"

    # =====================
    # MY COINS
    # =====================

    if text == "🪙 My Coins":
        user = get_user(user_id)

        taka = user["coins"] / COINS_PER_TAKA

        send_message(
            user_id,
            "🪙 <b>My Coins</b>\n\n"
            f"💰 Coins: <b>{user['coins']}</b>\n"
            f"💵 Value: <b>৳{taka:g}</b>\n\n"
            f"📺 Ads Watched: {user['ads_watched']}\n"
            f"🎯 Withdrawal: {MIN_WITHDRAW} Coins = ৳100",
            main_menu()
        )

        return "ok"

    # =====================
    # WATCH AD
    # =====================

    if text == "📺 Watch Ad":
        success = show_adsgram_ad(user_id)

        if not success:
            send_message(
                user_id,
                "⚠️ এখন কোনো Ad পাওয়া যাচ্ছে না। কিছুক্ষণ পরে আবার চেষ্টা করুন।",
                main_menu()
            )

        return "ok"

    # =====================
    # DAILY BONUS
    # =====================

    if text == "🎁 Daily Bonus":
        user = get_user(user_id)
        today = str(date.today())

        if user["last_bonus"] == today:
            send_message(
                user_id,
                "⏳ আজকের Daily Bonus ইতিমধ্যে নেওয়া হয়েছে।",
                main_menu()
            )
        else:
            conn = db()

            conn.execute("""
                UPDATE users
                SET last_bonus=?
                WHERE user_id=?
            """, (today, user_id))

            conn.commit()
            conn.close()

            add_coins(
                user_id,
                DAILY_BONUS,
                "Daily Bonus"
            )

            send_message(
                user_id,
                f"🎁 <b>Daily Bonus!</b>\n\n"
                f"🪙 +{DAILY_BONUS} Coins যোগ হয়েছে।",
                main_menu()
            )

        return "ok"

    # =====================
    # REFERRAL
    # =====================

    if text == "👥 Referral":
        link = f"https://t.me/FoundCoinsBot?start={user_id}"

        send_message(
            user_id,
            "👥 <b>Referral</b>\n\n"
            f"🎁 প্রতি successful referral-এ +{REFERRAL_BONUS} Coins\n\n"
            "🔗 আপনার Referral Link:\n"
            f"<code>{link}</code>",
            main_menu()
        )

        return "ok"

    # =====================
    # LEADERBOARD
    # =====================

    if text == "🏆 Leaderboard":
        conn = db()

        rows = conn.execute("""
            SELECT username, coins
            FROM users
            ORDER BY coins DESC
            LIMIT 10
        """).fetchall()

        conn.close()

        msg = "🏆 <b>Top 10 Leaderboard</b>\n\n"

        for i, row in enumerate(rows, 1):
            name = row["username"] or "User"
            msg += f"{i}. @{name} — {row['coins']} Coins\n"

        send_message(
            user_id,
            msg,
            main_menu()
        )

        return "ok"

    # =====================
    # HISTORY
    # =====================

    if text == "📜 History":
        conn = db()

        rows = conn.execute("""
            SELECT amount, reason, created_at
            FROM history
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT 20
        """, (user_id,)).fetchall()

        conn.close()

        if not rows:
            send_message(
                user_id,
                "📜 এখনো কোনো History নেই।",
                main_menu()
            )
            return "ok"

        msg = "📜 <b>Your History</b>\n\n"

        for row in rows:
            sign = "+" if row["amount"] > 0 else ""
            msg += (
                f"{sign}{row['amount']} Coins — "
                f"{row['reason']}\n"
            )

        send_message(
            user_id,
            msg,
            main_menu()
        )

        return "ok"

    # =====================
    # WITHDRAW
    # =====================

    if text == "💰 Withdraw":
        user = get_user(user_id)

        if user["coins"] < MIN_WITHDRAW:
            need = MIN_WITHDRAW - user["coins"]

            send_message(
                user_id,
                "❌ <b>Withdrawal করা যাবে না।</b>\n\n"
                f"🪙 আপনার Coins: {user['coins']}\n"
                f"🎯 প্রয়োজন: {MIN_WITHDRAW}\n"
                f"📈 আরও প্রয়োজন: {need} Coins\n\n"
                "10,000 Coins হলে ৳100 Withdraw করতে পারবেন।",
                main_menu()
            )

            return "ok"

        if user["coins"] > MAX_WITHDRAW:
            send_message(
                user_id,
                "⚠️ Withdrawal limit: 10,000 Coins = ৳100",
                main_menu()
            )

            return "ok"

        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "💳 bKash",
                        "callback_data": "withdraw_bkash"
                    },
                    {
                        "text": "💳 Nagad",
                        "callback_data": "withdraw_nagad"
                    }
                ]
            ]
        }

        send_message(
            user_id,
            "💰 <b>Withdraw ৳100</b>\n\n"
            "Payment Method নির্বাচন করুন:",
            keyboard
        )

        return "ok"

    # =====================
    # PAYMENT NUMBER
    # =====================

    if text.lower().startswith("bkash:"):
        user = get_user(user_id)

        if user["coins"] != MIN_WITHDRAW:
            send_message(
                user_id,
                "❌ আপনার 10,000 Coins নেই।",
                main_menu()
            )
            return "ok"

        number = text.split(":", 1)[1].strip()

        if not number.isdigit() or len(number) != 11:
            send_message(
                user_id,
                "❌ সঠিক 11-digit bKash number দিন।\n\n"
                "উদাহরণ: <code>01XXXXXXXXX</code>"
            )
            return "ok"

        withdrawal_id = withdrawal_request(
            user_id,
            "bKash",
            number
        )

        send_message(
            user_id,
            "⏳ <b>Payment Processing...</b>\n\n"
            "আপনার payment request review করা হচ্ছে।\n"
            "সাধারণত প্রায় 30 মিনিটের মধ্যে processing সম্পন্ন করা হবে।",
            main_menu()
        )

        send_message(
            ADMIN_ID,
            f"🔔 <b>New Withdrawal #{withdrawal_id}</b>\n\n"
            f"👤 @{username or 'No Username'}\n"
            f"🆔 {user_id}\n"
            f"💳 bKash\n"
            f"📱 <code>{number}</code>\n"
            f"🪙 10,000 Coins\n"
            f"💰 ৳100"
        )

        return "ok"

    if text.lower().startswith("nagad:"):
        user = get_user(user_id)

        if user["coins"] != MIN_WITHDRAW:
            send_message(
                user_id,
                "❌ আপনার 10,000 Coins নেই।",
                main_menu()
            )
            return "ok"

        number = text.split(":", 1)[1].strip()

        if not number.isdigit() or len(number) != 11:
            send_message(
                user_id,
                "❌ সঠিক 11-digit Nagad number দিন।\n\n"
                "উদাহরণ: <code>01XXXXXXXXX</code>"
            )
            return "ok"

        withdrawal_id = withdrawal_request(
            user_id,
            "Nagad",
            number
        )

        send_message(
            user_id,
            "⏳ <b>Payment Processing...</b>\n\n"
            "আপনার payment request review করা হচ্ছে।\n"
            "সাধারণত প্রায় 30 মিনিটের মধ্যে processing সম্পন্ন করা হবে।",
            main_menu()
        )

        send_message(
            ADMIN_ID,
            f"🔔 <b>New Withdrawal #{withdrawal_id}</b>\n\n"
            f"👤 @{username or 'No Username'}\n"
            f"🆔 {user_id}\n"
            f"💳 Nagad\n"
            f"📱 <code>{number}</code>\n"
            f"🪙 10,000 Coins\n"
            f"💰 ৳100"
        )

        return "ok"

    # =====================
    # WITHDRAW CALLBACK
    # =====================

    if text == "🏠":
        send_message(
            user_id,
            "🏠 Main Menu",
            main_menu()
        )

    return "ok"


# =========================
# RUN
# =========================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(
        host="0.0.0.0",
        port=port
    )
