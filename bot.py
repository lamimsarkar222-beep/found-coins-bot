import os
import psycopg2
import psycopg2.extras
import requests
from datetime import datetime, date, timedelta
from flask import Flask, request

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = 7926491409

ADSGRAM_TOKEN = os.environ.get("ADSGRAM_TOKEN", "")
ADSGRAM_BLOCK_ID = os.environ.get("ADSGRAM_BLOCK_ID", "51644")

DATABASE_URL = os.environ["DATABASE_URL"]
API = f"https://api.telegram.org/bot{TOKEN}"

DAILY_BONUS = 20
REFERRAL_BONUS = 100
AD_REWARD = 50

MIN_WITHDRAW = 10000
MAX_WITHDRAW = 10000

app = Flask(__name__)


def db():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            coins INTEGER DEFAULT 0,
            ads_watched INTEGER DEFAULT 0,
            coins_earned INTEGER DEFAULT 0,
            last_bonus TEXT,
            referred_by BIGINT,
            joined_at TEXT,
            blocked INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT,
            amount INTEGER,
            reason TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT,
            username TEXT,
            method TEXT,
            number TEXT,
            coins INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_ads (
            user_id BIGINT PRIMARY KEY,
            created_at TEXT
        )
    """)

    conn.commit()
    cur.close()
    conn.close()


init_db()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_user(user_id):
    conn = db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        "SELECT * FROM users WHERE user_id=%s",
        (user_id,)
    )

    user = cur.fetchone()

    cur.close()
    conn.close()

    return user


def create_user(user_id, username=None, referred_by=None):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO users
        (user_id, username, coins, ads_watched, coins_earned,
         last_bonus, referred_by, joined_at, blocked)
        VALUES (%s, %s, 0, 0, 0, NULL, %s, %s, 0)
        ON CONFLICT (user_id)
        DO UPDATE SET username = EXCLUDED.username
    """, (
        user_id,
        username,
        referred_by,
        now()
    ))

    conn.commit()
    cur.close()
    conn.close()


def add_history(user_id, amount, reason):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO history
        (user_id, amount, reason, created_at)
        VALUES (%s, %s, %s, %s)
    """, (
        user_id,
        amount,
        reason,
        now()
    ))

    conn.commit()
    cur.close()
    conn.close()


def add_coins(user_id, amount, reason):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET coins = coins + %s,
            coins_earned = CASE
                WHEN %s > 0 THEN coins_earned + %s
                ELSE coins_earned
            END
        WHERE user_id=%s
    """, (
        amount,
        amount,
        amount,
        user_id
    ))

    conn.commit()
    cur.close()
    conn.close()

    add_history(user_id, amount, reason)


def send_message(chat_id, text, reply_markup=None, protect=False, parse_mode=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        data["reply_markup"] = reply_markup

    if protect:
        data["protect_content"] = True

    if parse_mode:
        data["parse_mode"] = parse_mode

    try:
        return requests.post(
            f"{API}/sendMessage",
            json=data,
            timeout=20
        ).json()
    except Exception as e:
        print("Telegram error:", e)
        return None


def main_menu():
    return {
        "keyboard": [
            [
                {"text": "🪙 My Coins"},
                {"text": "📺 Watch Ad"}
            ],
            [
                {"text": "🎁 Daily Bonus"},
                {"text": "👥 Referral"}
            ],
            [
                {"text": "💰 Withdraw"},
                {"text": "🏆 Leaderboard"}
            ],
            [
                {"text": "📜 History"}
            ]
        ],
        "resize_keyboard": True
    }


def get_adsgram_ad(user_id):
    if not ADSGRAM_TOKEN:
        return None

    try:
        response = requests.get(
            "https://api.adsgram.ai/advbot",
            params={
                "tgid": user_id,
                "blockid": ADSGRAM_BLOCK_ID,
                "language": "en",
                "token": ADSGRAM_TOKEN
            },
            timeout=20
        )

        if response.status_code != 200:
            print("AdsGram error:", response.status_code)
            return None

        return response.json()

    except Exception as e:
        print("AdsGram exception:", e)
        return None


def create_pending_ad(user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO pending_ads (user_id, created_at)
        VALUES (%s, %s)
        ON CONFLICT (user_id)
        DO UPDATE SET created_at = EXCLUDED.created_at
    """, (
        user_id,
        now()
    ))

    conn.commit()
    cur.close()
    conn.close()


def handle_update(update):

    if "message" not in update:
        return

    message = update["message"]

    if "from" not in message:
        return

    sender = message["from"]

    user_id = sender["id"]
    username = sender.get("username")
    text = message.get("text", "").strip()

    create_user(user_id, username)

    user = get_user(user_id)

    if user and user["blocked"]:
        send_message(
            user_id,
            "🚫 Your account is currently blocked."
        )
        return

    if text.startswith("/start"):

        parts = text.split()
        referred_by = None

        if len(parts) > 1:
            try:
                referred_by = int(parts[1])
            except:
                referred_by = None

        if referred_by and referred_by != user_id:

            existing = get_user(user_id)
            ref_user = get_user(referred_by)

            if (
                ref_user
                and existing
                and existing["referred_by"] is None
            ):

                conn = db()
                cur = conn.cursor()

                cur.execute(
                    "UPDATE users SET referred_by=%s WHERE user_id=%s",
                    (referred_by, user_id)
                )

                conn.commit()
                cur.close()
                conn.close()

                add_coins(
                    referred_by,
                    REFERRAL_BONUS,
                    "Referral bonus"
                )

                send_message(
                    referred_by,
                    f"🎉 You earned {REFERRAL_BONUS} Coins from a new referral!"
                )

        send_message(
            user_id,
            "🎉 Welcome to Found Coins!\n\n"
            "🪙 Earn Coins by completing available tasks.\n\n"
            f"💰 {MIN_WITHDRAW:,} Coins = ৳100",
            main_menu()
        )

        return

    if text == "🪙 My Coins":

        user = get_user(user_id)

        send_message(
            user_id,
            f"🪙 Your Coins: {user['coins']:,}\n\n"
            f"📺 Ads Watched: {user['ads_watched']:,}\n"
            f"💰 Coins Earned: {user['coins_earned']:,}\n\n"
            f"💵 10,000 Coins = ৳100",
            main_menu()
        )

        return

    if text == "📺 Watch Ad":

        ad = get_adsgram_ad(user_id)

        if not ad:

            send_message(
                user_id,
                "📺 No ad is available right now.\n\n"
                "Please try again later.",
                main_menu()
            )

            return

        create_pending_ad(user_id)

        text_html = ad.get(
            "text",
            "📺 Watch the advertisement."
        )

        click_url = ad.get("click_url")
        reward_url = ad.get("reward_url")

        keyboard = []

        if click_url:
            keyboard.append([
                {
                    "text": "▶️ Open Ad",
                    "url": click_url
                }
            ])

        if reward_url:
            keyboard.append([
                {
                    "text": "🎁 Continue",
                    "url": reward_url
                }
            ])

        markup = {
            "inline_keyboard": keyboard
        }

        if ad.get("image_url"):

            try:
                requests.post(
                    f"{API}/sendPhoto",
                    json={
                        "chat_id": user_id,
                        "photo": ad["image_url"],
                        "caption": text_html,
                        "parse_mode": "HTML",
                        "reply_markup": markup,
                        "protect_content": True
                    },
                    timeout=20
                )

            except Exception:
                send_message(
                    user_id,
                    text_html,
                    markup,
                    protect=True,
                    parse_mode="HTML"
                )

        else:

            send_message(
                user_id,
                text_html,
                markup,
                protect=True,
                parse_mode="HTML"
            )

        return

    if text == "🎁 Daily Bonus":

        user = get_user(user_id)
        today = str(date.today())

        if user["last_bonus"] == today:

            send_message(
                user_id,
                "⏳ You already claimed today's bonus.\n\n"
                "Come back tomorrow."
            )

            return

        conn = db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE users
            SET coins = coins + %s,
                coins_earned = coins_earned + %s,
                last_bonus = %s
            WHERE user_id=%s
        """, (
            DAILY_BONUS,
            DAILY_BONUS,
            today,
            user_id
        ))

        conn.commit()
        cur.close()
        conn.close()

        add_history(
            user_id,
            DAILY_BONUS,
            "Daily bonus"
        )

        send_message(
            user_id,
            f"🎁 Daily Bonus Claimed!\n\n"
            f"+{DAILY_BONUS} Coins added."
        )

        return

    if text == "👥 Referral":

        link = f"https://t.me/FoundCoinsBot?start={user_id}"

        send_message(
            user_id,
            f"👥 Referral Program\n\n"
            f"Invite friends and earn {REFERRAL_BONUS} Coins.\n\n"
            f"🔗 Your Referral Link:\n{link}"
        )

        return

    if text == "🏆 Leaderboard":

        conn = db()
        cur = conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        )

        cur.execute("""
            SELECT username, user_id, coins
            FROM users
            WHERE blocked=0
            ORDER BY coins DESC
            LIMIT 10
        """)

        rows = cur.fetchall()

        cur.close()
        conn.close()

        result = "🏆 Top 10 Leaderboard\n\n"

        if not rows:
            result += "No users yet."

        else:
            for i, row in enumerate(rows, 1):

                name = (
                    f"@{row['username']}"
                    if row["username"]
                    else str(row["user_id"])
                )

                result += (
                    f"{i}. {name} — "
                    f"{row['coins']:,} Coins\n"
                )

        send_message(user_id, result)

        return

    if text == "📜 History":

        conn = db()
        cur = conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        )

        cur.execute("""
            SELECT amount, reason, created_at
            FROM history
            WHERE user_id=%s
            ORDER BY id DESC
            LIMIT 10
        """, (user_id,))

        rows = cur.fetchall()

        cur.close()
        conn.close()

        result = "📜 Your Recent History\n\n"

        if not rows:
            result += "No history yet."

        else:
            for row in rows:

                sign = "+" if row["amount"] >= 0 else ""

                result += (
                    f"{sign}{row['amount']} Coins — "
                    f"{row['reason']}\n"
                    f"{row['created_at']}\n\n"
                )

        send_message(user_id, result)

        return

    if text == "💰 Withdraw":

        user = get_user(user_id)

        if user["coins"] < MIN_WITHDRAW:

            remaining = MIN_WITHDRAW - user["coins"]

            send_message(
                user_id,
                f"💰 Withdrawal\n\n"
                f"Your Coins: {user['coins']:,}\n"
                f"Minimum: {MIN_WITHDRAW:,} Coins\n"
                f"Value: ৳100\n\n"
                f"❌ You need {remaining:,} more Coins."
            )

            return

        send_message(
            user_id,
            "💰 Withdrawal\n\n"
            "You have enough Coins to withdraw ৳100.\n\n"
            "Send one of these:\n\n"
            "bKash: 01XXXXXXXXX\n"
            "Nagad: 01XXXXXXXXX"
        )

        return

    if (
        text.lower().startswith("bkash:")
        or text.lower().startswith("nagad:")
    ):

        parts = text.split(":", 1)

        if len(parts) != 2:
            return

        method = parts[0].strip().lower()
        number = parts[1].strip()

        method_name = (
            "bKash"
            if method == "bkash"
            else "Nagad"
        )

        if not number.startswith("01") or len(number) != 11:

            send_message(
                user_id,
                "❌ Invalid payment number.\n\n"
                "Example:\n"
                "bKash: 01XXXXXXXXX"
            )

            return

        user = get_user(user_id)

        if user["coins"] != MAX_WITHDRAW:

            send_message(
                user_id,
                f"❌ Withdrawal is available at "
                f"{MAX_WITHDRAW:,} Coins.\n\n"
                f"Your balance: {user['coins']:,} Coins."
            )

            return

        conn = db()
        cur = conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        )

        cur.execute("""
            SELECT id
            FROM withdrawals
            WHERE user_id=%s
            AND status='pending'
        """, (user_id,))

        existing = cur.fetchone()

        if existing:

            cur.close()
            conn.close()

            send_message(
                user_id,
                "⏳ You already have a pending withdrawal."
            )

            return

        cur.execute("""
            INSERT INTO withdrawals
            (user_id, username, method, number, coins,
             status, created_at)
            VALUES (%s, %s, %s, %s, %s, 'pending', %s)
        """, (
            user_id,
            user["username"],
            method_name,
            number,
            MAX_WITHDRAW,
            now()
        ))

        cur.execute(
            "UPDATE users SET coins=0 WHERE user_id=%s",
            (user_id,)
        )

        conn.commit()
        cur.close()
        conn.close()

        add_history(
            user_id,
            -MAX_WITHDRAW,
            "Withdrawal requested"
        )

        admin_text = (
            "💰 NEW WITHDRAWAL\n\n"
            f"👤 Username: @{user['username'] or 'N/A'}\n"
            f"🆔 User ID: {user_id}\n"
            f"💳 Method: {method_name}\n"
            f"📱 Number: {number}\n"
            f"🪙 Coins: {MAX_WITHDRAW:,}\n"
            f"💵 Amount: ৳100\n"
            f"⏳ Status: Pending"
        )

        send_message(
            ADMIN_ID,
            admin_text
        )

        send_message(
            user_id,
            "⏳ Payment Processing...\n\n"
            "Your withdrawal request has been submitted.\n"
            "Payment will be reviewed manually."
        )

        return

    send_message(
        user_id,
        "Please choose an option from the menu.",
        main_menu()
    )


@app.route("/", methods=["GET"])
def home():
    return "Found Coins Bot is running!"


@app.route("/webhook", methods=["POST"])
def webhook():

    try:
        update = request.get_json(silent=True)

        if update:
            handle_update(update)

        return "OK", 200

    except Exception as e:

        print("Webhook error:", e)

        return "OK", 200


if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 10000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
