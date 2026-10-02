
import os
import sqlite3
from datetime import datetime
from flask import Flask, request
import requests

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = 7926491409
API = f"https://api.telegram.org/bot{TOKEN}"

app = Flask(__name__)

DB = "bot.db"


def init_db():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            coins INTEGER DEFAULT 0,
            ads_watched INTEGER DEFAULT 0,
            coins_earned INTEGER DEFAULT 0,
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

    conn.commit()
    conn.close()


def get_user(user_id, username=""):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
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

    conn.close()


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

    requests.post(f"{API}/sendMessage", json=data)


def main_menu():
    return [
        [{"text": "🪙 My Coins"}, {"text": "📺 Watch Ad"}],
        [{"text": "🎁 Daily Bonus"}, {"text": "👥 Referral"}],
        [{"text": "💰 Withdraw"}, {"text": "🏆 Leaderboard"}],
        [{"text": "📜 History"}]
    ]


def get_stats(user_id):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute("""
        SELECT coins, ads_watched, coins_earned
        FROM users
        WHERE user_id=?
    """, (user_id,))

    result = cur.fetchone()
    conn.close()

    return result or (0, 0, 0)


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

    text = message.get("text", "")

    if text == "/start":
        send_message(
            chat_id,
            "🎉 Welcome to Found Coins!\n\n"
            "🪙 Start earning coins by using the buttons below.",
            main_menu()
        )
        return "OK"

    if text == "🪙 My Coins":
        coins, ads, earned = get_stats(user_id)

        send_message(
            chat_id,
            f"🪙 Your Coins: {coins}\n\n"
            f"📺 Ads Watched: {ads}\n"
            f"💰 Coins Earned: {earned}"
        )
        return "OK"

    if text == "📺 Watch Ad":
        send_message(
            chat_id,
            "📺 Ad system is not connected yet.\n\n"
            "The advertisement system will be added later."
        )
        return "OK"

    if text == "🎁 Daily Bonus":
        send_message(
            chat_id,
            "🎁 Daily Bonus\n\n"
            "Daily bonus system is ready to be connected."
        )
        return "OK"

    if text == "👥 Referral":
        send_message(
            chat_id,
            f"👥 Your Referral ID:\n\n"
            f"`{user_id}`\n\n"
            "Referral reward system will be added."
        )
        return "OK"

    if text == "🏆 Leaderboard":
        conn = sqlite3.connect(DB)
        cur = conn.cursor()

        cur.execute("""
            SELECT username, coins
            FROM users
            ORDER BY coins DESC
            LIMIT 10
        """)

        rows = cur.fetchall()
        conn.close()

        leaderboard = "🏆 Leaderboard\n\n"

        for i, row in enumerate(rows, 1):
            name = row[0] or "User"
            leaderboard += f"{i}. @{name} — 🪙 {row[1]}\n"

        send_message(chat_id, leaderboard)
        return "OK"

    if text == "📜 History":
        conn = sqlite3.connect(DB)
        cur = conn.cursor()

        cur.execute("""
            SELECT coins, reason, created_at
            FROM history
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT 10
        """, (user_id,))

        rows = cur.fetchall()
        conn.close()

        if not rows:
            send_message(chat_id, "📜 No earning history yet.")
            return "OK"

        history = "📜 Your History\n\n"

        for coins, reason, created_at in rows:
            history += (
                f"🪙 +{coins} coins\n"
                f"📌 {reason}\n"
                f"🕐 {created_at}\n\n"
            )

        send_message(chat_id, history)
        return "OK"

    if text == "💰 Withdraw":
        send_message(
            chat_id,
            "💰 Withdraw\n\n"
            "Send your payment number in this format:\n\n"
            "bKash: 01XXXXXXXXX\n"
            "Nagad: 01XXXXXXXXX"
        )
        return "OK"

    if text.startswith("bKash:") or text.startswith("Nagad:"):
        payment_info = text.strip()

        send_message(
            ADMIN_ID,
            "🔔 New Withdrawal Request\n\n"
            f"👤 User ID: {user_id}\n"
            f"👤 Username: @{username or 'None'}\n\n"
            f"💳 Payment Info:\n{payment_info}"
        )

        send_message(
            chat_id,
            "✅ Withdrawal request received.\n\n"
            "Your payment information has been sent to the admin."
        )
        return "OK"

    send_message(
        chat_id,
        "Please use the buttons below.",
        main_menu()
    )

    return "OK"


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
