import os
import sqlite3
import secrets
from datetime import datetime, date, timedelta
from functools import wraps

import requests
from flask import Flask, request, redirect, session, render_template_string

# =========================================================
# CONFIG
# =========================================================

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = 7926491409

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "found-coins-secret-change-me")

ADSGRAM_TOKEN = os.environ.get("ADSGRAM_TOKEN", "")
ADSGRAM_BLOCK_ID = os.environ.get("ADSGRAM_BLOCK_ID", "51644")

API = f"https://api.telegram.org/bot{TOKEN}"
DB = "bot.db"

DAILY_BONUS = 20
REFERRAL_BONUS = 100
AD_REWARD = 50

MIN_WITHDRAW = 10000
MAX_WITHDRAW = 10000

COINS_PER_TAKA = 100

app = Flask(__name__)
app.secret_key = SECRET_KEY


# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


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
            joined_at TEXT,
            blocked INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
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
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_ads (
            user_id INTEGER PRIMARY KEY,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


init_db()


# =========================================================
# HELPERS
# =========================================================

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_user(user_id):
    conn = db()
    user = conn.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()
    conn.close()
    return user


def create_user(user_id, username=None, referred_by=None):
    conn = db()

    existing = conn.execute(
        "SELECT user_id FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()

    if not existing:
        conn.execute("""
            INSERT INTO users
            (user_id, username, coins, ads_watched, coins_earned,
             last_bonus, referred_by, joined_at, blocked)
            VALUES (?, ?, 0, 0, 0, NULL, ?, ?, 0)
        """, (
            user_id,
            username,
            referred_by,
            now()
        ))
    else:
        conn.execute(
            "UPDATE users SET username=? WHERE user_id=?",
            (username, user_id)
        )

    conn.commit()
    conn.close()


def add_history(user_id, amount, reason):
    conn = db()

    conn.execute("""
        INSERT INTO history
        (user_id, amount, reason, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        user_id,
        amount,
        reason,
        now()
    ))

    conn.commit()
    conn.close()


def add_coins(user_id, amount, reason):
    conn = db()

    conn.execute("""
        UPDATE users
        SET coins = coins + ?,
            coins_earned = CASE
                WHEN ? > 0 THEN coins_earned + ?
                ELSE coins_earned
            END
        WHERE user_id=?
    """, (
        amount,
        amount,
        amount,
        user_id
    ))

    conn.commit()
    conn.close()

    add_history(user_id, amount, reason)


def set_coins(user_id, amount, reason="Admin changed coins"):
    conn = db()

    old = conn.execute(
        "SELECT coins FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()

    if not old:
        conn.close()
        return False

    conn.execute(
        "UPDATE users SET coins=? WHERE user_id=?",
        (max(0, amount), user_id)
    )

    conn.commit()
    conn.close()

    add_history(
        user_id,
        amount - old["coins"],
        reason
    )

    return True


def send_message(
    chat_id,
    text,
    reply_markup=None,
    protect=False,
    parse_mode=None
):
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
    except Exception:
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


# =========================================================
# ADSGRAM
# =========================================================

def get_adsgram_ad(user_id, language="en"):
    if not ADSGRAM_TOKEN:
        return None

    try:
        response = requests.get(
            "https://api.adsgram.ai/advbot",
            params={
                "tgid": user_id,
                "blockid": ADSGRAM_BLOCK_ID,
                "language": language,
                "token": ADSGRAM_TOKEN
            },
            timeout=20
        )

        if response.status_code != 200:
            print("AdsGram error:", response.status_code, response.text)
            return None

        return response.json()

    except Exception as e:
        print("AdsGram exception:", e)
        return None


def create_pending_ad(user_id):
    conn = db()

    conn.execute("""
        INSERT OR REPLACE INTO pending_ads
        (user_id, created_at)
        VALUES (?, ?)
    """, (
        user_id,
        now()
    ))

    conn.commit()
    conn.close()


def consume_pending_ad(user_id):
    conn = db()

    row = conn.execute("""
        SELECT created_at
        FROM pending_ads
        WHERE user_id=?
    """, (user_id,)).fetchone()

    if not row:
        conn.close()
        return False

    try:
        created = datetime.strptime(
            row["created_at"],
            "%Y-%m-%d %H:%M:%S"
        )

        if datetime.now() - created > timedelta(hours=1):
            conn.execute(
                "DELETE FROM pending_ads WHERE user_id=?",
                (user_id,)
            )
            conn.commit()
            conn.close()
            return False

    except Exception:
        conn.close()
        return False

    conn.execute(
        "DELETE FROM pending_ads WHERE user_id=?",
        (user_id,)
    )

    conn.commit()
    conn.close()

    return True


# =========================================================
# TELEGRAM UPDATE
# =========================================================

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

    # =====================================================
    # START
    # =====================================================

    if text.startswith("/start"):

        referred_by = None
        parts = text.split()

        if len(parts) > 1:
            try:
                referred_by = int(parts[1])
            except Exception:
                referred_by = None

        existing = get_user(user_id)

        if (
            referred_by
            and referred_by != user_id
            and existing
            and existing["coins"] == 0
            and existing["ads_watched"] == 0
            and existing["referred_by"] is None
        ):

            ref_user = get_user(referred_by)

            if ref_user:

                conn = db()

                conn.execute(
                    "UPDATE users SET referred_by=? WHERE user_id=?",
                    (referred_by, user_id)
                )

                conn.commit()
                conn.close()

                add_coins(
                    referred_by,
                    REFERRAL_BONUS,
                    "Referral bonus"
                )

                send_message(
                    referred_by,
                    f"🎉 Referral Bonus!\n\n"
                    f"+{REFERRAL_BONUS} Coins added."
                )

        send_message(
            user_id,
            "🎁 Welcome to Found Coins!\n\n"
            "🪙 Earn Coins by completing available tasks.\n"
            "💰 10,000 Coins = ৳100\n\n"
            "Choose an option below:",
            main_menu()
        )

        return

    # =====================================================
    # MY COINS
    # =====================================================

    if text == "🪙 My Coins":

        user = get_user(user_id)

        taka = user["coins"] / COINS_PER_TAKA

        send_message(
            user_id,
            f"🪙 Your Coins\n\n"
            f"💰 Balance: {user['coins']:,} Coins\n"
            f"💵 Value: ৳{taka:.2f}\n\n"
            f"📺 Ads Watched: {user['ads_watched']}\n"
            f"📈 Total Earned: {user['coins_earned']:,} Coins"
        )

        return

    # =====================================================
    # WATCH AD
    # =====================================================

    if text == "📺 Watch Ad":

        ad = get_adsgram_ad(user_id, "en")

        if not ad:

            send_message(
                user_id,
                "📺 No ad is available right now.\n\n"
                "Please try again later."
            )

            return

        create_pending_ad(user_id)

        text_html = ad.get(
            "text_html",
            "Sponsored advertisement"
        )

        click_url = ad.get("click_url")
        reward_url = ad.get("reward_url")

        button_name = ad.get(
            "button_name",
            "Open Ad"
        )

        reward_name = ad.get(
            "button_reward_name",
            "Claim Reward"
        )

        keyboard = []

        if click_url:
            keyboard.append([
                {
                    "text": button_name,
                    "url": click_url
                }
            ])

        if reward_url:
            keyboard.append([
                {
                    "text": reward_name,
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

    # =====================================================
    # DAILY BONUS
    # =====================================================

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

        conn.execute("""
            UPDATE users
            SET coins = coins + ?,
                coins_earned = coins_earned + ?,
                last_bonus = ?
            WHERE user_id=?
        """, (
            DAILY_BONUS,
            DAILY_BONUS,
            today,
            user_id
        ))

        conn.commit()
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

    # =====================================================
    # REFERRAL
    # =====================================================

    if text == "👥 Referral":

        link = (
            f"https://t.me/FoundCoinsBot?start={user_id}"
        )

        send_message(
            user_id,
            f"👥 Referral Program\n\n"
            f"Invite friends and earn "
            f"{REFERRAL_BONUS} Coins for each successful referral.\n\n"
            f"🔗 Your Referral Link:\n{link}"
        )

        return

    # =====================================================
    # LEADERBOARD
    # =====================================================

    if text == "🏆 Leaderboard":

        conn = db()

        rows = conn.execute("""
            SELECT username, user_id, coins
            FROM users
            WHERE blocked=0
            ORDER BY coins DESC
            LIMIT 10
        """).fetchall()

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

    # =====================================================
    # HISTORY
    # =====================================================

    if text == "📜 History":

        conn = db()

        rows = conn.execute("""
            SELECT amount, reason, created_at
            FROM history
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT 10
        """, (user_id,)).fetchall()

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

    # =====================================================
    # WITHDRAW
    # =====================================================

    if text == "💰 Withdraw":

        user = get_user(user_id)

        if user["coins"] < MIN_WITHDRAW:

            remaining = MIN_WITHDRAW - user["coins"]

            send_message(
                user_id,
                f"💰 Withdrawal\n\n"
                f"Your Coins: {user['coins']:,}\n"
                f"Minimum Withdrawal: {MIN_WITHDRAW:,} Coins\n"
                f"Value: ৳100\n\n"
                f"❌ You need {remaining:,} more Coins."
            )

            return

        send_message(
            user_id,
            "💰 Withdrawal\n\n"
            "You have enough Coins to withdraw ৳100.\n\n"
            "Reply with ONE of these formats:\n\n"
            "bKash: 01XXXXXXXXX\n"
            "Nagad: 01XXXXXXXXX"
        )

        return

    # =====================================================
    # WITHDRAW SUBMISSION
    # =====================================================

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
                f"❌ Withdrawal is only available at exactly "
                f"{MAX_WITHDRAW:,} Coins.\n\n"
                f"Your balance: {user['coins']:,} Coins."
            )

            return

        conn = db()

        existing = conn.execute("""
            SELECT id
            FROM withdrawals
            WHERE user_id=?
            AND status='pending'
        """, (user_id,)).fetchone()

        if existing:

            conn.close()

            send_message(
                user_id,
                "⏳ You already have a pending withdrawal."
            )

            return

        conn.execute("""
            INSERT INTO withdrawals
            (user_id, username, method, number, coins,
             status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """, (
            user_id,
            user["username"],
            method_name,
            number,
            MAX_WITHDRAW,
            now()
        ))

        conn.execute(
            "UPDATE users SET coins=0 WHERE user_id=?",
            (user_id,)
        )

        conn.commit()
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

    # =====================================================
    # DEFAULT
    # =====================================================

    send_message(
        user_id,
        "Please choose an option from the menu.",
        main_menu()
    )


# =========================================================
# ADSGRAM REWARD
# =========================================================

@app.route("/adsgram/reward", methods=["GET"])
def adsgram_reward():

    user_id = request.args.get("userid")

    if not user_id:
        return "Missing userid", 400

    try:
        user_id = int(user_id)
    except Exception:
        return "Invalid userid", 400

    user = get_user(user_id)

    if not user:
        return "User not found", 404

    if user["blocked"]:
        return "User blocked", 403

    if not consume_pending_ad(user_id):
        return "Reward already claimed or expired", 400

    conn = db()

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

    conn.commit()
    conn.close()

    add_history(
        user_id,
        AD_REWARD,
        "AdsGram ad reward"
    )

    updated = get_user(user_id)

    send_message(
        user_id,
        f"🎉 Ad Completed!\n\n"
        f"+{AD_REWARD} Coins added.\n\n"
        f"🪙 Your balance is now "
        f"{updated['coins']:,} Coins."
    )

    return "OK", 200


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@app.route("/webhook", methods=["POST"])
def webhook():

    try:
        update = request.get_json(force=True)
        handle_update(update)

    except Exception as e:
        print("Webhook error:", e)

    return "OK", 200


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return "Found Coins Bot is running!"


@app.route("/health")
def health():
    return "OK", 200


# =========================================================
# ADMIN AUTH
# =========================================================

def admin_required(func):

    @wraps(func)
    def wrapper(*args, **kwargs):

        if not session.get("admin_logged_in"):
            return redirect("/admin/login")

        return func(*args, **kwargs)

    return wrapper


# =========================================================
# ADMIN LOGIN
# =========================================================

LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Found Coins Admin</title>

<style>
body {
    background:#111827;
    color:white;
    font-family:Arial,sans-serif;
    display:flex;
    justify-content:center;
    align-items:center;
    min-height:100vh;
    margin:0;
}

.box {
    width:90%;
    max-width:380px;
    background:#1f2937;
    padding:25px;
    border-radius:15px;
}

input {
    width:100%;
    box-sizing:border-box;
    padding:13px;
    border-radius:8px;
    border:1px solid #374151;
    background:#111827;
    color:white;
    margin:10px 0;
}

button {
    width:100%;
    padding:13px;
    border:0;
    border-radius:8px;
    background:#2563eb;
    color:white;
    font-weight:bold;
}

.error {
    color:#f87171;
}
</style>
</head>

<body>

<div class="box">

<h2>🔐 Found Coins Admin</h2>

<p>Enter your Admin Password.</p>

<form method="POST">

<input
    type="password"
    name="password"
    placeholder="Admin Password"
    required
>

<button type="submit">
Login
</button>

</form>

{% if error %}
<p class="error">{{ error }}</p>
{% endif %}

</div>

</body>
</html>
"""


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():

    if request.method == "POST":

        password = request.form.get(
            "password",
            ""
        )

        if ADMIN_PASSWORD and secrets.compare_digest(
            password,
            ADMIN_PASSWORD
        ):

            session["admin_logged_in"] = True

            return redirect("/admin")

        return render_template_string(
            LOGIN_HTML,
            error="❌ Wrong password."
        )

    return render_template_string(
        LOGIN_HTML,
        error=None
    )


@app.route("/admin/logout")
def admin_logout():

    session.clear()

    return redirect("/admin/login")


# =========================================================
# ADMIN PANEL
# =========================================================

ADMIN_HTML = """
<!DOCTYPE html>
<html>

<head>

<meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Found Coins Admin</title>

<style>

body {
    margin:0;
    font-family:Arial,sans-serif;
    background:#0f172a;
    color:white;
}

header {
    background:#111827;
    padding:18px;
    display:flex;
    justify-content:space-between;
    align-items:center;
}

header a {
    color:white;
    text-decoration:none;
}

.container {
    padding:15px;
    max-width:1100px;
    margin:auto;
}

.cards {
    display:grid;
    grid-template-columns:repeat(
        auto-fit,
        minmax(160px,1fr)
    );
    gap:12px;
}

.card {
    background:#1e293b;
    padding:18px;
    border-radius:12px;
}

.card h3 {
    margin:0 0 8px;
}

.card .num {
    font-size:25px;
    font-weight:bold;
}

.section {
    background:#1e293b;
    padding:15px;
    margin-top:15px;
    border-radius:12px;
}

table {
    width:100%;
    border-collapse:collapse;
}

th, td {
    padding:10px;
    border-bottom:1px solid #334155;
    text-align:left;
    font-size:14px;
}

.btn {
    display:inline-block;
    padding:8px 12px;
    border-radius:7px;
    text-decoration:none;
    color:white;
    background:#2563eb;
    margin:3px;
}

.red {
    background:#dc2626;
}

.green {
    background:#16a34a;
}

.gray {
    background:#475569;
}

@media(max-width:600px) {

    table {
        display:block;
        overflow-x:auto;
    }

    header {
        flex-direction:column;
        gap:10px;
    }

}

</style>

</head>

<body>

<header>

<strong>🪙 Found Coins Admin</strong>

<a href="/admin/logout">Logout</a>

</header>

<div class="container">

<div class="cards">

<div class="card">
<h3>👥 Users</h3>
<div class="num">{{ stats.users }}</div>
</div>

<div class="card">
<h3>🪙 Total Coins</h3>
<div class="num">{{ stats.coins }}</div>
</div>

<div class="card">
<h3>📺 Ads Watched</h3>
<div class="num">{{ stats.ads }}</div>
</div>

<div class="card">
<h3>💰 Pending Withdrawals</h3>
<div class="num">{{ stats.pending }}</div>
</div>

</div>


<div class="section">

<h2>💰 Pending Withdrawals</h2>

{% if withdrawals %}

<table>

<tr>
<th>ID</th>
<th>User</th>
<th>Method</th>
<th>Number</th>
<th>Coins</th>
<th>Action</th>
</tr>

{% for w in withdrawals %}

<tr>

<td>{{ w.id }}</td>

<td>
{{ w.username or 'N/A' }}<br>
{{ w.user_id }}
</td>

<td>{{ w.method }}</td>

<td>{{ w.number }}</td>

<td>{{ w.coins }}</td>

<td>

<a class="btn green"
href="/admin/withdraw/{{ w.id }}/approve">
Approve
</a>

<a class="btn red"
href="/admin/withdraw/{{ w.id }}/reject">
Reject
</a>

</td>

</tr>

{% endfor %}

</table>

{% else %}

<p>No pending withdrawals.</p>

{% endif %}

</div>


<div class="section">

<h2>👥 Users</h2>

<table>

<tr>
<th>ID</th>
<th>Username</th>
<th>Coins</th>
<th>Ads</th>
<th>Status</th>
<th>Action</th>
</tr>

{% for u in users %}

<tr>

<td>{{ u.user_id }}</td>

<td>@{{ u.username or 'N/A' }}</td>

<td>{{ u.coins }}</td>

<td>{{ u.ads_watched }}</td>

<td>
{% if u.blocked %}
🚫 Blocked
{% else %}
✅ Active
{% endif %}
</td>

<td>

<a class="btn gray"
href="/admin/user/{{ u.user_id }}/edit">
Edit
</a>

{% if u.blocked %}

<a class="btn green"
href="/admin/user/{{ u.user_id }}/unblock">
Unblock
</a>

{% else %}

<a class="btn red"
href="/admin/user/{{ u.user_id }}/block">
Block
</a>

{% endif %}

</td>

</tr>

{% endfor %}

</table>

</div>


<div class="section">

<h2>📜 Recent History</h2>

<table>

<tr>
<th>User</th>
<th>Amount</th>
<th>Reason</th>
<th>Date</th>
</tr>

{% for h in history %}

<tr>
<td>{{ h.user_id }}</td>
<td>{{ h.amount }}</td>
<td>{{ h.reason }}</td>
<td>{{ h.created_at }}</td>
</tr>

{% endfor %}

</table>

</div>


<div class="section">

<h2>🏆 Leaderboard</h2>

<table>

<tr>
<th>Rank</th>
<th>User</th>
<th>Coins</th>
</tr>

{% for u in leaderboard %}

<tr>
<td>{{ loop.index }}</td>
<td>@{{ u.username or 'N/A' }}</td>
<td>{{ u.coins }}</td>
</tr>

{% endfor %}

</table>

</div>


<div class="section">

<h2>⚙️ Bot Settings</h2>

<p>📺 Ad Reward: {{ settings.ad_reward }} Coins</p>
<p>🎁 Daily Bonus: {{ settings.daily_bonus }} Coins</p>
<p>👥 Referral Bonus: {{ settings.referral_bonus }} Coins</p>
<p>💰 Minimum Withdrawal: {{ settings.min_withdraw }} Coins</p>
<p>💵 Withdrawal Value: ৳100</p>
<p>📢 AdsGram Block ID: {{ settings.block_id }}</p>

</div>

</div>

</body>
</html>
"""


@app.route("/admin")
@admin_required
def admin_panel():

    conn = db()

    users = conn.execute("""
        SELECT *
        FROM users
        ORDER BY joined_at DESC
        LIMIT 200
    """).fetchall()

    withdrawals = conn.execute("""
        SELECT *
        FROM withdrawals
        WHERE status='pending'
        ORDER BY id DESC
    """).fetchall()

    history = conn.execute("""
        SELECT *
        FROM history
        ORDER BY id DESC
        LIMIT 100
    """).fetchall()

    leaderboard = conn.execute("""
        SELECT username, user_id, coins
        FROM users
        WHERE blocked=0
        ORDER BY coins DESC
        LIMIT 10
    """).fetchall()

    total_users = conn.execute(
        "SELECT COUNT(*) AS c FROM users"
    ).fetchone()["c"]

    total_coins = conn.execute(
        "SELECT COALESCE(SUM(coins),0) AS c FROM users"
    ).fetchone()["c"]

    total_ads = conn.execute(
        "SELECT COALESCE(SUM(ads_watched),0) AS c FROM users"
    ).fetchone()["c"]

    pending = conn.execute("""
        SELECT COUNT(*) AS c
        FROM withdrawals
        WHERE status='pending'
    """).fetchone()["c"]

    conn.close()

    stats = {
        "users": total_users,
        "coins": total_coins,
        "ads": total_ads,
        "pending": pending
    }

    settings = {
        "ad_reward": AD_REWARD,
        "daily_bonus": DAILY_BONUS,
        "referral_bonus": REFERRAL_BONUS,
        "min_withdraw": MIN_WITHDRAW,
        "block_id": ADSGRAM_BLOCK_ID
    }

    return render_template_string(
        ADMIN_HTML,
        users=users,
        withdrawals=withdrawals,
        history=history,
        leaderboard=leaderboard,
        stats=stats,
        settings=settings
    )


# =========================================================
# ADMIN WITHDRAW APPROVE
# =========================================================

@app.route(
    "/admin/withdraw/<int:withdraw_id>/approve"
)
@admin_required
def approve_withdraw(withdraw_id):

    conn = db()

    withdrawal = conn.execute("""
        SELECT *
        FROM withdrawals
        WHERE id=?
    """, (withdraw_id,)).fetchone()

    if not withdrawal:
        conn.close()
        return redirect("/admin")

    if withdrawal["status"] != "pending":
        conn.close()
        return redirect("/admin")

    conn.execute("""
        UPDATE withdrawals
        SET status='approved'
        WHERE id=?
    """, (withdraw_id,))

    conn.commit()
    conn.close()

    send_message(
        withdrawal["user_id"],
        "✅ Payment Successful!\n\n"
        "💵 Amount: ৳100\n"
        f"💳 Method: {withdrawal['method']}\n\n"
        "Thank you for using Found Coins."
    )

    return redirect("/admin")


# =========================================================
# ADMIN WITHDRAW REJECT
# =========================================================

@app.route(
    "/admin/withdraw/<int:withdraw_id>/reject"
)
@admin_required
def reject_withdraw(withdraw_id):

    conn = db()

    withdrawal = conn.execute("""
        SELECT *
        FROM withdrawals
        WHERE id=?
    """, (withdraw_id,)).fetchone()

    if not withdrawal:
        conn.close()
        return redirect("/admin")

    if withdrawal["status"] != "pending":
        conn.close()
        return redirect("/admin")

    conn.execute("""
        UPDATE withdrawals
        SET status='rejected'
        WHERE id=?
    """, (withdraw_id,))

    conn.execute("""
        UPDATE users
        SET coins=coins+?
        WHERE user_id=?
    """, (
        withdrawal["coins"],
        withdrawal["user_id"]
    ))

    conn.commit()
    conn.close()

    add_history(
        withdrawal["user_id"],
        withdrawal["coins"],
        "Withdrawal rejected - refund"
    )

    send_message(
        withdrawal["user_id"],
        "❌ Withdrawal Rejected\n\n"
        "Your Coins have been refunded.\n"
        f"🪙 Refunded: {withdrawal['coins']:,} Coins"
    )

    return redirect("/admin")


# =========================================================
# ADMIN BLOCK USER
# =========================================================

@app.route(
    "/admin/user/<int:user_id>/block"
)
@admin_required
def block_user(user_id):

    if user_id == ADMIN_ID:
        return redirect("/admin")

    conn = db()

    conn.execute(
        "UPDATE users SET blocked=1 WHERE user_id=?",
        (user_id,)
    )

    conn.commit()
    conn.close()

    return redirect("/admin")


# =========================================================
# ADMIN UNBLOCK USER
# =========================================================

@app.route(
    "/admin/user/<int:user_id>/unblock"
)
@admin_required
def unblock_user(user_id):

    conn = db()

    conn.execute(
        "UPDATE users SET blocked=0 WHERE user_id=?",
        (user_id,)
    )

    conn.commit()
    conn.close()

    return redirect("/admin")


# =========================================================
# ADMIN EDIT USER
# =========================================================

USER_EDIT_HTML = """
<!DOCTYPE html>
<html>

<head>

<meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Edit User</title>

<style>

body {
    background:#0f172a;
    color:white;
    font-family:Arial;
    padding:20px;
}

.box {
    max-width:450px;
    margin:auto;
    background:#1e293b;
    padding:20px;
    border-radius:12px;
}

input {
    width:100%;
    box-sizing:border-box;
    padding:12px;
    margin:8px 0;
    background:#0f172a;
    color:white;
    border:1px solid #475569;
    border-radius:8px;
}

button {
    width:100%;
    padding:12px;
    background:#2563eb;
    color:white;
    border:0;
    border-radius:8px;
}

a {
    color:white;
}

</style>

</head>

<body>

<div class="box">

<h2>👤 Edit User</h2>

<p>User ID: {{ user.user_id }}</p>

<p>
Username:
@{{ user.username or 'N/A' }}
</p>

<form method="POST">

<label>Set Coins</label>

<input
type="number"
name="coins"
value="{{ user.coins }}"
min="0"
required
>

<button type="submit">
Save Coins
</button>

</form>

<br>

<a href="/admin">← Back to Admin</a>

</div>

</body>
</html>
"""


@app.route(
    "/admin/user/<int:user_id>/edit",
    methods=["GET", "POST"]
)
@admin_required
def edit_user(user_id):

    user = get_user(user_id)

    if not user:
        return redirect("/admin")

    if request.method == "POST":

        try:

            coins = int(
                request.form.get(
                    "coins",
                    "0"
                )
            )

            coins = max(0, coins)

            set_coins(
                user_id,
                coins,
                "Admin changed balance"
            )

        except Exception:
            pass

        return redirect("/admin")

    return render_template_string(
        USER_EDIT_HTML,
        user=user
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
