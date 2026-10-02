import os
import sqlite3
from datetime import datetime, date
from flask import Flask, request
import requests

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = 7926491409
API = f"https://api.telegram.org/bot{TOKEN}"

app = Flask(__name__)

DB = "bot.db"

DAILY_BONUS = 20
REFERRAL_BONUS = 100
AD_REWARD = 50

MAX_COINS = 10000
MIN_WITHDRAW = 100

COINS_PER_TAKA = 100


def db():
    return sqlite3.connect(DB)


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            coins INTEGER,
            reason TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            method TEXT,
            number TEXT,
            coins INTEGER,
            status TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


def get_user(user_id, username=""):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,)
    )

    user = cur.fetchone()

    if not user:
        cur.execute("""
            INSERT INTO users
            (user_id, username, joined_at)
            VALUES (?, ?, ?)
        """, (
            user_id,
            username,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()

    else:
        cur.execute(
            "UPDATE users SET username=? WHERE user_id=?",
            (username, user_id)
        )
        conn.commit()

    conn.close()


def add_coins(user_id, amount, reason):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT coins, coins_earned FROM users WHERE user_id=?",
        (user_id,)
    )

    row = cur.fetchone()

    if not row:
        conn.close()
        return False

    coins = row[0]
    earned = row[1]

    new_coins = min(coins + amount, MAX_COINS)

    actual_added = new_coins - coins

    if actual_added <= 0:
        conn.close()
        return False

    cur.execute("""
        UPDATE users
        SET coins=?, coins_earned=?
        WHERE user_id=?
    """, (
        new_coins,
        earned + actual_added,
        user_id
    ))

    cur.execute("""
        INSERT INTO history
        (user_id, coins, reason, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        user_id,
        actual_added,
        reason,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()
    conn.close()

    return True


def send_message(chat_id, text, keyboard=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if keyboard:
        data["reply_markup"] = {
            "keyboard": keyboard,
            "resize_keyboard": True
        }

    requests.post(
        f"{API}/sendMessage",
        json=data,
        timeout=15
    )


def main_menu():
    return [
        [{"text": "🪙 My Coins"}, {"text": "📺 Watch Ad"}],
        [{"text": "🎁 Daily Bonus"}, {"text": "👥 Referral"}],
        [{"text": "💰 Withdraw"}, {"text": "🏆 Leaderboard"}],
        [{"text": "📜 History"}]
    ]


def get_stats(user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT coins, ads_watched, coins_earned
        FROM users
        WHERE user_id=?
    """, (user_id,))

    row = cur.fetchone()
    conn.close()

    return row or (0, 0, 0)


init_db()


@app.route("/", methods=["GET"])
def home():
    return "Found Coins Bot is running!"


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}

    if "message" not in data:
        return "OK"

    message = data["message"]
    chat_id = message["chat"]["id"]

    user = message.get("from", {})
    user_id = user.get("id")
    username = user.get("username", "")

    get_user(user_id, username)

    text = message.get("text", "").strip()

    # START + REFERRAL
    if text.startswith("/start"):

        parts = text.split()
        referral_id = None

        if len(parts) > 1:
            try:
                referral_id = int(parts[1])
            except:
                pass

        conn = db()
        cur = conn.cursor()

        cur.execute(
            "SELECT referred_by FROM users WHERE user_id=?",
            (user_id,)
        )

        current = cur.fetchone()

        if current and current[0] is None:
            if referral_id and referral_id != user_id:

                cur.execute(
                    "SELECT user_id FROM users WHERE user_id=?",
                    (referral_id,)
                )

                ref_user = cur.fetchone()

                if ref_user:
                    cur.execute("""
                        UPDATE users
                        SET referred_by=?
                        WHERE user_id=?
                    """, (referral_id, user_id))

                    conn.commit()
                    conn.close()

                    add_coins(
                        referral_id,
                        REFERRAL_BONUS,
                        "Referral bonus"
                    )

                    send_message(
                        referral_id,
                        f"🎉 New referral joined!\n"
                        f"🪙 +{REFERRAL_BONUS} Coins"
                    )

                else:
                    conn.close()
            else:
                conn.close()
        else:
            conn.close()

        send_message(
            chat_id,
            "🎉 Welcome to Found Coins!\n\n"
            "🪙 Start earning coins using the buttons below.",
            main_menu()
        )

        return "OK"


    # MY COINS
    if text == "🪙 My Coins":

        coins, ads, earned = get_stats(user_id)

        taka = coins / COINS_PER_TAKA

        send_message(
            chat_id,
            f"🪙 Coins: {coins}\n"
            f"💵 Balance: {taka:.2f} টাকা\n\n"
            f"📺 Ads Watched: {ads}\n"
            f"💰 Coins Earned: {earned}\n\n"
            f"💰 Maximum Withdraw:\n"
            f"{MAX_COINS} Coins = {MAX_COINS // COINS_PER_TAKA} টাকা"
        )

        return "OK"


    # WATCH AD
    if text == "📺 Watch Ad":

        send_message(
            chat_id,
            "📺 Watch Ad\n\n"
            f"প্রতি সম্পূর্ণ Ad-এর জন্য {AD_REWARD} Coins পাওয়া যাবে।\n\n"
            "⚠️ Ad Network এখনো সংযুক্ত করা হয়নি।\n"
            "পরে এখানে আসল বিজ্ঞাপন যুক্ত করা হবে।"
        )

        return "OK"


    # DAILY BONUS
    if text == "🎁 Daily Bonus":

        today = str(date.today())

        conn = db()
        cur = conn.cursor()

        cur.execute(
            "SELECT last_bonus FROM users WHERE user_id=?",
            (user_id,)
        )

        row = cur.fetchone()
        last_bonus = row[0] if row else None

        if last_bonus == today:
            conn.close()

            send_message(
                chat_id,
                "⏳ আজকের Daily Bonus ইতিমধ্যে নিয়েছো।\n"
                "আগামীকাল আবার নিতে পারবে।"
            )

            return "OK"

        cur.execute("""
            UPDATE users
            SET last_bonus=?
            WHERE user_id=?
        """, (today, user_id))

        conn.commit()
        conn.close()

        if add_coins(
            user_id,
            DAILY_BONUS,
            "Daily Bonus"
        ):
            send_message(
                chat_id,
                f"🎁 Daily Bonus Received!\n\n"
                f"🪙 +{DAILY_BONUS} Coins"
            )
        else:
            send_message(
                chat_id,
                "⚠️ তোমার Coins সর্বোচ্চ 10,000-এ পৌঁছে গেছে।"
            )

        return "OK"


    # REFERRAL
    if text == "👥 Referral":

        me = requests.get(
            f"{API}/getMe",
            timeout=15
        ).json()

        bot_username = me["result"]["username"]

        referral_link = (
            f"https://t.me/{bot_username}?start={user_id}"
        )

        send_message(
            chat_id,
            f"👥 Referral System\n\n"
            f"🎁 প্রতি সফল Referral = {REFERRAL_BONUS} Coins\n\n"
            f"🔗 তোমার Referral Link:\n"
            f"{referral_link}"
        )

        return "OK"


    # LEADERBOARD
    if text == "🏆 Leaderboard":

        conn = db()
        cur = conn.cursor()

        cur.execute("""
            SELECT username, coins
            FROM users
            ORDER BY coins DESC
            LIMIT 10
        """)

        rows = cur.fetchall()
        conn.close()

        leaderboard = "🏆 Top 10 Leaderboard\n\n"

        if not rows:
            leaderboard += "No users yet."

        for i, row in enumerate(rows, 1):

            name = row[0] or "User"

            leaderboard += (
                f"{i}. @{name} — 🪙 {row[1]}\n"
            )

        send_message(
            chat_id,
            leaderboard
        )

        return "OK"


    # HISTORY
    if text == "📜 History":

        conn = db()
        cur = conn.cursor()

        cur.execute("""
            SELECT coins, reason, created_at
            FROM history
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT 15
        """, (user_id,))

        rows = cur.fetchall()
        conn.close()

        if not rows:
            send_message(
                chat_id,
                "📜 তোমার এখনো কোনো earning history নেই।"
            )

            return "OK"

        history = "📜 Earning History\n\n"

        for coins, reason, created_at in rows:

            history += (
                f"🪙 +{coins} Coins\n"
                f"📌 {reason}\n"
                f"🕐 {created_at}\n\n"
            )

        send_message(
            chat_id,
            history
        )

        return "OK"


    # WITHDRAW
    if text == "💰 Withdraw":

        coins, _, _ = get_stats(user_id)

        taka = coins / COINS_PER_TAKA

        send_message(
            chat_id,
            f"💰 Withdraw\n\n"
            f"🪙 Your Coins: {coins}\n"
            f"💵 Balance: {taka:.2f} টাকা\n\n"
            f"Minimum Withdraw: {MIN_WITHDRAW} Coins\n"
            f"Maximum Withdraw: {MAX_COINS} Coins\n\n"
            "Withdraw করতে পাঠাও:\n\n"
            "bKash: 01XXXXXXXXX\n"
            "অথবা\n"
            "Nagad: 01XXXXXXXXX"
        )

        return "OK"


    # WITHDRAW REQUEST
    if text.startswith("bKash:") or text.startswith("Nagad:"):

        parts = text.split(":", 1)

        if len(parts) != 2:
            send_message(
                chat_id,
                "❌ সঠিক format ব্যবহার করো।"
            )
            return "OK"

        method = parts[0].strip()
        number = parts[1].strip()

        coins, _, _ = get_stats(user_id)

        if coins < MIN_WITHDRAW:
            send_message(
                chat_id,
                f"❌ Minimum Withdraw হলো "
                f"{MIN_WITHDRAW} Coins।"
            )
            return "OK"

        if coins > MAX_COINS:
            send_message(
                chat_id,
                "❌ Withdraw limit অতিক্রম করা যাবে না।"
            )
            return "OK"

        if not number.startswith("01") or len(number) != 11:
            send_message(
                chat_id,
                "❌ সঠিক ১১ সংখ্যার bKash/Nagad নম্বর দাও।"
            )
            return "OK"

        conn = db()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO withdrawals
            (user_id, username, method, number, coins, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            username,
            method,
            number,
            coins,
            "Pending",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

        withdrawal_id = cur.lastrowid

        conn.commit()
        conn.close()

        # Only admin receives payment information
        send_message(
            ADMIN_ID,
            f"🔔 NEW WITHDRAW REQUEST\n\n"
            f"🆔 Request: #{withdrawal_id}\n"
            f"👤 User ID: {user_id}\n"
            f"👤 Username: @{username or 'None'}\n"
            f"🪙 Coins: {coins}\n"
            f"💵 Amount: {coins / COINS_PER_TAKA:.2f} টাকা\n\n"
            f"💳 Method: {method}\n"
            f"📱 Number: {number}\n\n"
            f"📌 Status: Pending"
        )

        send_message(
            chat_id,
            "✅ Withdraw Request Submitted!\n\n"
            "তোমার payment information শুধু Admin-এর কাছে পাঠানো হয়েছে।"
        )

        return "OK"


    # UNKNOWN MESSAGE
    send_message(
        chat_id,
        "অনুগ্রহ করে নিচের Menu থেকে একটি অপশন নির্বাচন করো।",
        main_menu()
    )

    return "OK"


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
