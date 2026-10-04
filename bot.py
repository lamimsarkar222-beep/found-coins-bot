import os
import psycopg2
import psycopg2.extras
import requests

from datetime import datetime, date
from flask import Flask, request, session, redirect, url_for

# ============================================================
# BASIC CONFIG
# ============================================================

TOKEN = os.environ["BOT_TOKEN"]

ADMIN_ID = 7926491409

ADSGRAM_TOKEN = os.environ.get("ADSGRAM_TOKEN", "")

# IMPORTANT:
# AdsGram Block ID is fixed directly in the code.
# No Render Environment Variable is required for this.
ADSGRAM_BLOCK_ID = "49006"

DATABASE_URL = os.environ["DATABASE_URL"]

API = f"https://api.telegram.org/bot{TOKEN}"

# ============================================================
# BOT SETTINGS
# ============================================================

DAILY_BONUS = 20
REFERRAL_BONUS = 100

# Reward for one completed AdsGram ad
AD_REWARD = 50

MIN_WITHDRAW = 10000
MAX_WITHDRAW = 10000

app = Flask(__name__)

# Session secret.
# Uses ADMIN_PASSWORD as a fallback so another Render variable
# is not mandatory.
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    os.environ.get("ADMIN_PASSWORD", "found-coins-session-key")
)

# ============================================================
# DATABASE
# ============================================================

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


# ============================================================
# TIME
# ============================================================

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# USER FUNCTIONS
# ============================================================

def get_user(user_id):
    conn = db()
    cur = conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    )

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
        (
            user_id,
            username,
            coins,
            ads_watched,
            coins_earned,
            last_bonus,
            referred_by,
            joined_at,
            blocked
        )
        VALUES (
            %s,
            %s,
            0,
            0,
            0,
            NULL,
            %s,
            %s,
            0
        )
        ON CONFLICT (user_id)
        DO UPDATE SET
            username = EXCLUDED.username
    """, (
        user_id,
        username,
        referred_by,
        now()
    ))

    conn.commit()
    cur.close()
    conn.close()


# ============================================================
# HISTORY
# ============================================================

def add_history(user_id, amount, reason):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO history
        (
            user_id,
            amount,
            reason,
            created_at
        )
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


# ============================================================
# COINS
# ============================================================

def add_coins(user_id, amount, reason):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET
            coins = coins + %s,
            coins_earned =
                CASE
                    WHEN %s > 0
                    THEN coins_earned + %s
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

    add_history(
        user_id,
        amount,
        reason
    )


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

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

    except Exception as e:
        print("Telegram error:", e)
        return None


# ============================================================
# MAIN MENU
# ============================================================

def main_menu():
    return {
        "keyboard": [
            [
                {
                    "text": "🪙 My Coins"
                },
                {
                    "text": "📺 Watch Ad"
                }
            ],
            [
                {
                    "text": "🎁 Daily Bonus"
                },
                {
                    "text": "👥 Referral"
                }
            ],
            [
                {
                    "text": "💰 Withdraw"
                },
                {
                    "text": "🏆 Leaderboard"
                }
            ],
            [
                {
                    "text": "📜 History"
                }
            ]
        ],
        "resize_keyboard": True
    }


# ============================================================
# ADSGRAM
# ============================================================

def get_adsgram_ad(user_id):

    if not ADSGRAM_TOKEN:
        print("AdsGram token is missing.")
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

        print(
            "AdsGram response:",
            response.status_code,
            response.text[:1000]
        )

        if response.status_code != 200:
            return None

        data = response.json()

        if not isinstance(data, dict):
            return None

        return data

    except Exception as e:

        print(
            "AdsGram exception:",
            e
        )

        return None


# ============================================================
# PENDING ADS
# ============================================================

def create_pending_ad(user_id):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO pending_ads
        (
            user_id,
            created_at
        )
        VALUES (%s, %s)

        ON CONFLICT (user_id)
        DO UPDATE SET
            created_at = EXCLUDED.created_at
    """, (
        user_id,
        now()
    ))

    conn.commit()

    cur.close()
    conn.close()


def pending_ad_exists(user_id):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT user_id
        FROM pending_ads
        WHERE user_id=%s
    """, (
        user_id,
    ))

    row = cur.fetchone()

    cur.close()
    conn.close()

    return row is not None


def remove_pending_ad(user_id):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM pending_ads
        WHERE user_id=%s
    """, (
        user_id,
    ))

    conn.commit()

    cur.close()
    conn.close()


# ============================================================
# ADSGRAM REWARD
# ============================================================

@app.route("/reward", methods=["GET"])
def reward():

    userid = request.args.get("userid")

    if not userid:
        return "Missing userid", 400

    try:
        user_id = int(userid)
    except ValueError:
        return "Invalid userid", 400

    user = get_user(user_id)

    if not user:
        return "User not found", 404

    # Prevent duplicate reward.
    if not pending_ad_exists(user_id):
        return "No pending ad", 400

    # Give reward.
    add_coins(
        user_id,
        AD_REWARD,
        "AdsGram ad reward"
    )

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET ads_watched = ads_watched + 1
        WHERE user_id=%s
    """, (
        user_id,
    ))

    conn.commit()

    cur.close()
    conn.close()

    remove_pending_ad(user_id)

    send_message(
        user_id,
        f"🎉 Ad Completed!\n\n"
        f"🪙 +{AD_REWARD} Coins added.\n\n"
        f"💰 Your balance has been updated.",
        main_menu()
    )

    return "Reward granted", 200


# ============================================================
# BOT UPDATE HANDLER
# ============================================================

def handle_update(update):

    if "message" not in update:
        return

    message = update["message"]

    if "from" not in message:
        return

    sender = message["from"]

    user_id = sender["id"]

    username = sender.get("username")

    text = message.get(
        "text",
        ""
    ).strip()

    # --------------------------------------------------------
    # CREATE / UPDATE USER
    # --------------------------------------------------------

    create_user(
        user_id,
        username
    )

    user = get_user(user_id)

    # --------------------------------------------------------
    # BLOCKED USER
    # --------------------------------------------------------

    if user and user["blocked"]:

        send_message(
            user_id,
            "🚫 Your account is currently blocked."
        )

        return

    # ========================================================
    # START
    # ========================================================

    if text.startswith("/start"):

        parts = text.split()

        referred_by = None

        if len(parts) > 1:

            try:
                referred_by = int(parts[1])

            except:
                referred_by = None

        if (
            referred_by
            and referred_by != user_id
        ):

            existing = get_user(user_id)

            ref_user = get_user(
                referred_by
            )

            if (
                ref_user
                and existing
                and existing["referred_by"] is None
            ):

                conn = db()
                cur = conn.cursor()

                cur.execute(
                    """
                    UPDATE users
                    SET referred_by=%s
                    WHERE user_id=%s
                    """,
                    (
                        referred_by,
                        user_id
                    )
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
                    f"🎉 You earned "
                    f"{REFERRAL_BONUS} Coins "
                    f"from a new referral!"
                )

        send_message(
            user_id,

            "🎉 Welcome to Found Coins!\n\n"
            "🪙 Earn Coins by completing "
            "available tasks.\n\n"
            f"💰 {MIN_WITHDRAW:,} Coins = ৳100",

            main_menu()
        )

        return

    # ========================================================
    # MY COINS
    # ========================================================

    if text == "🪙 My Coins":

        user = get_user(user_id)

        send_message(
            user_id,

            f"🪙 Your Coins: "
            f"{user['coins']:,}\n\n"

            f"📺 Ads Watched: "
            f"{user['ads_watched']:,}\n"

            f"💰 Coins Earned: "
            f"{user['coins_earned']:,}\n\n"

            f"💵 10,000 Coins = ৳100",

            main_menu()
        )

        return

    # ========================================================
    # WATCH AD
    # ========================================================

    if text == "📺 Watch Ad":

        ad = get_adsgram_ad(
            user_id
        )

        if not ad:

            send_message(
                user_id,

                "📺 No ad is available "
                "right now.\n\n"
                "Please try again later.",

                main_menu()
            )

            return

        # ----------------------------------------------------
        # AdsGram current response format
        # ----------------------------------------------------

        ad_text = (
            ad.get("text_html")
            or ad.get("text")
            or "📺 Sponsored advertisement."
        )

        click_url = ad.get(
            "click_url"
        )

        reward_url = ad.get(
            "reward_url"
        )

        button_name = (
            ad.get("button_name")
            or "▶️ Open Ad"
        )

        reward_button_name = (
            ad.get("button_reward_name")
            or "🎁 Claim Reward"
        )

        # ----------------------------------------------------
        # Build buttons
        # ----------------------------------------------------

        keyboard = []

        if click_url:

            keyboard.append([
                {
                    "text": button_name,
                    "url": click_url
                }
            ])

        if reward_url:

            # Reward URL from AdsGram becomes active
            # after the click URL has been opened.
            keyboard.append([
                {
                    "text": reward_button_name,
                    "url": reward_url
                }
            ])

        markup = {
            "inline_keyboard": keyboard
        }

        # ----------------------------------------------------
        # Save pending ad BEFORE sending it
        # ----------------------------------------------------

        create_pending_ad(
            user_id
        )

        # ----------------------------------------------------
        # Send image ad
        # ----------------------------------------------------

        image_url = ad.get(
            "image_url"
        )

        if image_url:

            try:

                result = requests.post(
                    f"{API}/sendPhoto",
                    json={
                        "chat_id": user_id,
                        "photo": image_url,
                        "caption": ad_text,
                        "parse_mode": "HTML",
                        "reply_markup": markup,
                        "protect_content": True
                    },
                    timeout=20
                ).json()

                if not result.get("ok"):

                    send_message(
                        user_id,
                        ad_text,
                        markup,
                        protect=True,
                        parse_mode="HTML"
                    )

            except Exception as e:

                print(
                    "sendPhoto error:",
                    e
                )

                send_message(
                    user_id,
                    ad_text,
                    markup,
                    protect=True,
                    parse_mode="HTML"
                )

        else:

            send_message(
                user_id,
                ad_text,
                markup,
                protect=True,
                parse_mode="HTML"
            )

        return

    # ========================================================
    # DAILY BONUS
    # ========================================================

    if text == "🎁 Daily Bonus":

        user = get_user(user_id)

        today = str(
            date.today()
        )

        if user["last_bonus"] == today:

            send_message(
                user_id,

                "⏳ You already claimed "
                "today's bonus.\n\n"
                "Come back tomorrow."
            )

            return

        conn = db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE users
            SET
                coins = coins + %s,
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
            f"+{DAILY_BONUS} Coins added.",

            main_menu()
        )

        return

    # ========================================================
    # REFERRAL
    # ========================================================

    if text == "👥 Referral":

        link = (
            f"https://t.me/FoundCoinsBot"
            f"?start={user_id}"
        )

        send_message(
            user_id,

            f"👥 Referral Program\n\n"
            f"Invite friends and earn "
            f"{REFERRAL_BONUS} Coins.\n\n"
            f"🔗 Your Referral Link:\n"
            f"{link}",

            main_menu()
        )

        return

    # ========================================================
    # LEADERBOARD
    # ========================================================

    if text == "🏆 Leaderboard":

        conn = db()

        cur = conn.cursor(
            cursor_factory=
            psycopg2.extras.RealDictCursor
        )

        cur.execute("""
            SELECT
                username,
                user_id,
                coins
            FROM users
            WHERE blocked=0
            ORDER BY coins DESC
            LIMIT 10
        """)

        rows = cur.fetchall()

        cur.close()
        conn.close()

        result = (
            "🏆 Top 10 Leaderboard\n\n"
        )

        if not rows:

            result += "No users yet."

        else:

            for i, row in enumerate(
                rows,
                1
            ):

                name = (
                    f"@{row['username']}"
                    if row["username"]
                    else str(row["user_id"])
                )

                result += (
                    f"{i}. {name} — "
                    f"{row['coins']:,} Coins\n"
                )

        send_message(
            user_id,
            result,
            main_menu()
        )

        return

    # ========================================================
    # HISTORY
    # ========================================================

    if text == "📜 History":

        conn = db()

        cur = conn.cursor(
            cursor_factory=
            psycopg2.extras.RealDictCursor
        )

        cur.execute("""
            SELECT
                amount,
                reason,
                created_at
            FROM history
            WHERE user_id=%s
            ORDER BY id DESC
            LIMIT 10
        """, (
            user_id,
        ))

        rows = cur.fetchall()

        cur.close()
        conn.close()

        result = (
            "📜 Your Recent History\n\n"
        )

        if not rows:

            result += "No history yet."

        else:

            for row in rows:

                sign = (
                    "+"
                    if row["amount"] >= 0
                    else ""
                )

                result += (
                    f"{sign}{row['amount']} Coins — "
                    f"{row['reason']}\n"
                    f"{row['created_at']}\n\n"
                )

        send_message(
            user_id,
            result,
            main_menu()
        )

        return

    # ========================================================
    # WITHDRAW
    # ========================================================

    if text == "💰 Withdraw":

        user = get_user(user_id)

        if user["coins"] < MIN_WITHDRAW:

            remaining = (
                MIN_WITHDRAW
                - user["coins"]
            )

            send_message(
                user_id,

                f"💰 Withdrawal\n\n"
                f"Your Coins: "
                f"{user['coins']:,}\n"
                f"Minimum: "
                f"{MIN_WITHDRAW:,} Coins\n"
                f"Value: ৳100\n\n"
                f"❌ You need "
                f"{remaining:,} more Coins.",

                main_menu()
            )

            return

        send_message(
            user_id,

            "💰 Withdrawal\n\n"
            "You have enough Coins "
            "to withdraw ৳100.\n\n"
            "Send one of these:\n\n"
            "bKash: 01XXXXXXXXX\n"
            "Nagad: 01XXXXXXXXX",

            main_menu()
        )

        return

    # ========================================================
    # PAYMENT NUMBER
    # ========================================================

    if (
        text.lower().startswith("bkash:")
        or text.lower().startswith("nagad:")
    ):

        parts = text.split(
            ":",
            1
        )

        if len(parts) != 2:
            return

        method = parts[0].strip().lower()

        number = parts[1].strip()

        method_name = (
            "bKash"
            if method == "bkash"
            else "Nagad"
        )

        # ----------------------------------------------------
        # Validate number
        # ----------------------------------------------------

        if (
            not number.startswith("01")
            or len(number) != 11
            or not number.isdigit()
        ):

            send_message(
                user_id,

                "❌ Invalid payment number.\n\n"
                "Example:\n"
                "bKash: 01XXXXXXXXX",

                main_menu()
            )

            return

        user = get_user(user_id)

        if user["coins"] < MAX_WITHDRAW:

            send_message(
                user_id,

                f"❌ Withdrawal is available "
                f"at {MAX_WITHDRAW:,} Coins.\n\n"
                f"Your balance: "
                f"{user['coins']:,} Coins.",

                main_menu()
            )

            return

        # ----------------------------------------------------
        # Check existing pending withdrawal
        # ----------------------------------------------------

        conn = db()

        cur = conn.cursor(
            cursor_factory=
            psycopg2.extras.RealDictCursor
        )

        cur.execute("""
            SELECT id
            FROM withdrawals
            WHERE user_id=%s
            AND status='pending'
            LIMIT 1
        """, (
            user_id,
        ))

        existing = cur.fetchone()

        if existing:

            cur.close()
            conn.close()

            send_message(
                user_id,

                "⏳ You already have "
                "a pending withdrawal.",

                main_menu()
            )

            return

        # ----------------------------------------------------
        # Create withdrawal
        # ----------------------------------------------------

        cur.execute("""
            INSERT INTO withdrawals
            (
                user_id,
                username,
                method,
                number,
                coins,
                status,
                created_at
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                'pending',
                %s
            )
        """, (
            user_id,
            user["username"],
            method_name,
            number,
            MAX_WITHDRAW,
            now()
        ))

        # Deduct exactly the withdrawal amount.
        cur.execute("""
            UPDATE users
            SET coins = coins - %s
            WHERE user_id=%s
        """, (
            MAX_WITHDRAW,
            user_id
        ))

        conn.commit()

        cur.close()
        conn.close()

        add_history(
            user_id,
            -MAX_WITHDRAW,
            "Withdrawal requested"
        )

        # ----------------------------------------------------
        # ADMIN NOTIFICATION
        # ----------------------------------------------------

        admin_text = (
            "💰 NEW WITHDRAWAL\n\n"

            f"👤 Username: "
            f"@{user['username'] or 'N/A'}\n"

            f"🆔 User ID: "
            f"{user_id}\n"

            f"💳 Method: "
            f"{method_name}\n"

            f"📱 Number: "
            f"{number}\n"

            f"🪙 Coins: "
            f"{MAX_WITHDRAW:,}\n"

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
            "Your withdrawal request "
            "has been submitted.\n"
            "Payment will be reviewed manually.",

            main_menu()
        )

        return

    # ========================================================
    # UNKNOWN MESSAGE
    # ========================================================

    send_message(
        user_id,
        "Please choose an option from the menu.",
        main_menu()
    )


# ============================================================
# WEBHOOK
# ============================================================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    try:

        update = request.get_json(
            silent=True
        )

        if update:
            handle_update(update)

        return "OK", 200

    except Exception as e:

        print(
            "Webhook error:",
            e
        )

        # Always return 200 so Telegram
        # does not repeatedly retry the update.
        return "OK", 200


# ============================================================
# HOME
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return (
        "Found Coins Bot is running!"
    )


# ============================================================
# ADMIN LOGIN
# ============================================================

@app.route(
    "/admin",
    methods=["GET", "POST"]
)
def admin():

    if request.method == "POST":

        password = request.form.get(
            "password",
            ""
        )

        correct_password = os.environ.get(
            "ADMIN_PASSWORD",
            ""
        )

        if (
            correct_password
            and password == correct_password
        ):

            session["admin"] = True

            return redirect(
                url_for("admin")
            )

        return """
        <html>
        <body>
        <h3>❌ Wrong password</h3>
        <a href="/admin">Try again</a>
        </body>
        </html>
        """

    if not session.get("admin"):

        return """
        <!DOCTYPE html>
        <html>
        <head>
            <meta name="viewport"
                  content="width=device-width,
                  initial-scale=1">
            <title>Found Coins Admin</title>
        </head>

        <body>

            <h2>🔐 Found Coins Admin</h2>

            <form method="post">

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

        </body>
        </html>
        """

    # --------------------------------------------------------
    # ADMIN DATA
    # --------------------------------------------------------

    conn = db()

    cur = conn.cursor(
        cursor_factory=
        psycopg2.extras.RealDictCursor
    )

    cur.execute("""
        SELECT
            COUNT(*) AS total_users,
            COUNT(*) FILTER (
                WHERE blocked=0
            ) AS active_users,
            COALESCE(
                SUM(coins),
                0
            ) AS total_coins,
            COALESCE(
                SUM(ads_watched),
                0
            ) AS total_ads
        FROM users
    """)

    stats = cur.fetchone()

    cur.execute("""
        SELECT
            user_id,
            username,
            coins,
            ads_watched,
            coins_earned,
            blocked,
            joined_at
        FROM users
        ORDER BY joined_at DESC
        LIMIT 100
    """)

    users = cur.fetchall()

    cur.execute("""
        SELECT
            id,
            user_id,
            username,
            method,
            number,
            coins,
            status,
            created_at
        FROM withdrawals
        ORDER BY id DESC
        LIMIT 100
    """)

    withdrawals = cur.fetchall()

    cur.close()
    conn.close()

    # --------------------------------------------------------
    # USERS HTML
    # --------------------------------------------------------

    users_html = ""

    for user in users:

        users_html += f"""
        <tr>
            <td>{user['user_id']}</td>
            <td>
                @{user['username']
                   or 'N/A'}
            </td>
            <td>
                {user['coins']:,}
            </td>
            <td>
                {user['ads_watched']:,}
            </td>
            <td>
                {user['coins_earned']:,}
            </td>
            <td>
                {user['blocked']}
            </td>
        </tr>
        """

    # --------------------------------------------------------
    # WITHDRAWAL HTML
    # --------------------------------------------------------

    withdrawals_html = ""

    for w in withdrawals:

        action = ""

        if w["status"] == "pending":

            action = f"""
            <a href="/admin/withdraw/{w['id']}/approve">
                ✅ Approve
            </a>

            &nbsp;&nbsp;

            <a href="/admin/withdraw/{w['id']}/reject">
                ❌ Reject
            </a>
            """

        withdrawals_html += f"""
        <tr>
            <td>{w['id']}</td>
            <td>{w['user_id']}</td>
            <td>
                @{w['username']
                   or 'N/A'}
            </td>
            <td>{w['method']}</td>
            <td>{w['number']}</td>
            <td>{w['coins']:,}</td>
            <td>{w['status']}</td>
            <td>{action}</td>
        </tr>
        """

    return f"""
    <!DOCTYPE html>

    <html>

    <head>

        <meta name="viewport"
              content="width=device-width,
              initial-scale=1">

        <title>
            Found Coins Admin Panel
        </title>

        <style>

            body {{
                font-family: Arial;
                padding: 15px;
                background: #f5f5f5;
            }}

            .box {{
                background: white;
                padding: 15px;
                margin-bottom: 15px;
                border-radius: 10px;
            }}

            table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
                margin-top: 10px;
            }}

            th, td {{
                border: 1px solid #ddd;
                padding: 8px;
                text-align: left;
                font-size: 13px;
            }}

            th {{
                background: #eee;
            }}

            a {{
                text-decoration: none;
            }}

        </style>

    </head>

    <body>

        <h2>
            🪙 Found Coins Admin Panel
        </h2>

        <div class="box">

            <h3>
                📊 Statistics
            </h3>

            <p>
                👥 Total Users:
                <b>{stats['total_users']}</b>
            </p>

            <p>
                🟢 Active Users:
                <b>{stats['active_users']}</b>
            </p>

            <p>
                🪙 Total Coins:
                <b>{stats['total_coins']:,}</b>
            </p>

            <p>
                📺 Total Ads Watched:
                <b>{stats['total_ads']:,}</b>
            </p>

        </div>


        <div class="box">

            <h3>
                💰 Withdrawals
            </h3>

            <table>

                <tr>
                    <th>ID</th>
                    <th>User ID</th>
                    <th>Username</th>
                    <th>Method</th>
                    <th>Number</th>
                    <th>Coins</th>
                    <th>Status</th>
                    <th>Action</th>
                </tr>

                {withdrawals_html}

            </table>

        </div>


        <div class="box">

            <h3>
                👥 Users
            </h3>

            <table>

                <tr>
                    <th>User ID</th>
                    <th>Username</th>
                    <th>Coins</th>
                    <th>Ads</th>
                    <th>Earned</th>
                    <th>Blocked</th>
                </tr>

                {users_html}

            </table>

        </div>

    </body>

    </html>
    """


# ============================================================
# ADMIN WITHDRAW APPROVE
# ============================================================

@app.route(
    "/admin/withdraw/<int:withdrawal_id>/approve",
    methods=["GET"]
)
def approve_withdrawal(
    withdrawal_id
):

    if not session.get("admin"):
        return redirect(
            url_for("admin")
        )

    conn = db()

    cur = conn.cursor(
        cursor_factory=
        psycopg2.extras.RealDictCursor
    )

    cur.execute("""
        SELECT *
        FROM withdrawals
        WHERE id=%s
    """, (
        withdrawal_id,
    ))

    withdrawal = cur.fetchone()

    if not withdrawal:

        cur.close()
        conn.close()

        return "Withdrawal not found", 404

    if withdrawal["status"] != "pending":

        cur.close()
        conn.close()

        return redirect(
            url_for("admin")
        )

    cur.execute("""
        UPDATE withdrawals
        SET status='approved'
        WHERE id=%s
    """, (
        withdrawal_id,
    ))

    conn.commit()

    cur.close()
    conn.close()

    send_message(
        withdrawal["user_id"],

        "✅ Withdrawal Approved!\n\n"
        "Your payment request has "
        "been approved.\n"
        "Please check your payment account."
    )

    return redirect(
        url_for("admin")
    )


# ============================================================
# ADMIN WITHDRAW REJECT
# ============================================================

@app.route(
    "/admin/withdraw/<int:withdrawal_id>/reject",
    methods=["GET"]
)
def reject_withdrawal(
    withdrawal_id
):

    if not session.get("admin"):
        return redirect(
            url_for("admin")
        )

    conn = db()

    cur = conn.cursor(
        cursor_factory=
        psycopg2.extras.RealDictCursor
    )

    cur.execute("""
        SELECT *
        FROM withdrawals
        WHERE id=%s
    """, (
        withdrawal_id,
    ))

    withdrawal = cur.fetchone()

    if not withdrawal:

        cur.close()
        conn.close()

        return "Withdrawal not found", 404

    if withdrawal["status"] != "pending":

        cur.close()
        conn.close()

        return redirect(
            url_for("admin")
        )

    # Return the withdrawn coins.
    cur.execute("""
        UPDATE users
        SET coins = coins + %s
        WHERE user_id=%s
    """, (
        withdrawal["coins"],
        withdrawal["user_id"]
    ))

    cur.execute("""
        UPDATE withdrawals
        SET status='rejected'
        WHERE id=%s
    """, (
        withdrawal_id,
    ))

    conn.commit()

    cur.close()
    conn.close()

    add_history(
        withdrawal["user_id"],
        withdrawal["coins"],
        "Withdrawal rejected - coins returned"
    )

    send_message(
        withdrawal["user_id"],

        "❌ Withdrawal Rejected.\n\n"
        f"{withdrawal['coins']:,} Coins "
        "have been returned to your balance."
    )

    return redirect(
        url_for("admin")
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route(
    "/admin/logout",
    methods=["GET"]
)
def admin_logout():

    session.clear()

    return redirect(
        url_for("admin")
    )


# ============================================================
# START SERVER
# ============================================================

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
