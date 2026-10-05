import os
import psycopg2
import psycopg2.extras
import requests
from datetime import datetime, date, timedelta
from flask import Flask, request

# =========================================================
# CONFIG
# =========================================================

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = 7926491409

SECRET_KEY = os.environ.get("SECRET_KEY", "found-coins-secret-change-me")

ADSGRAM_TOKEN = os.environ.get("ADSGRAM_TOKEN", "")
ADSGRAM_BLOCK_ID = "49050"

DATABASE_URL = os.environ["DATABASE_URL"]

API = f"https://api.telegram.org/bot{TOKEN}"

DAILY_BONUS = 5
REFERRAL_BONUS = 100
AD_REWARD = 50

MIN_WITHDRAW = 10000
MAX_WITHDRAW = 10000

COINS_PER_TAKA = 100

app = Flask(__name__)
app.secret_key = SECRET_KEY


# =========================================================
# DATABASE - POSTGRESQL
# =========================================================

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

    # ===== New feature tables (additive; existing data is preserved) =====
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_task_claims (
            id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL,
            task_key TEXT NOT NULL, claim_date TEXT NOT NULL,
            reward INTEGER NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(user_id, task_key, claim_date)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL,
            question_key TEXT NOT NULL, attempt_date TEXT NOT NULL,
            correct INTEGER DEFAULT 0, created_at TEXT NOT NULL,
            UNIQUE(user_id, attempt_date)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id BIGINT PRIMARY KEY, task_completed INTEGER DEFAULT 0,
            daily_streak INTEGER DEFAULT 0, last_checkin TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS shop_items (
            id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL,
            description TEXT DEFAULT '', price INTEGER NOT NULL,
            active INTEGER DEFAULT 1, created_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS shop_purchases (
            id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL,
            item_id BIGINT NOT NULL, item_name TEXT NOT NULL,
            price INTEGER NOT NULL, status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL, processed_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_achievements (
            user_id BIGINT NOT NULL, achievement_key TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(user_id, achievement_key)
        )
    """)
    for k,v in {"daily_bonus":5,"ad_reward":50,"referral_bonus":100,"task_reward":5}.items():
        cur.execute("INSERT INTO bot_settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO NOTHING", (k,str(v)))
    cur.execute("SELECT COUNT(*) FROM shop_items")
    if cur.fetchone()[0] == 0:
        seed_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for item in [("🥉 Bronze Badge","Virtual profile badge",500),("🥈 Silver Badge","Virtual profile badge",1000),("🥇 Gold Badge","Virtual profile badge",2000),("👑 VIP Badge","Special virtual badge",5000)]:
            cur.execute("INSERT INTO shop_items(name,description,price,active,created_at) VALUES(%s,%s,%s,1,%s)", (*item, seed_time))

    conn.commit()
    cur.close()
    conn.close()


init_db()


# =========================================================
# HELPERS
# =========================================================

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

    cur.execute("INSERT INTO user_stats(user_id) VALUES(%s) ON CONFLICT(user_id) DO NOTHING", (user_id,))

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


def set_coins(user_id, amount, reason="Admin changed coins"):
    conn = db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        "SELECT coins FROM users WHERE user_id=%s",
        (user_id,)
    )

    old = cur.fetchone()

    if not old:
        cur.close()
        conn.close()
        return False

    new_amount = max(0, amount)

    cur.execute(
        "UPDATE users SET coins=%s WHERE user_id=%s",
        (new_amount, user_id)
    )

    conn.commit()
    cur.close()
    conn.close()

    add_history(
        user_id,
        new_amount - old["coins"],
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
    except Exception as e:
        print("Telegram sendMessage error:", e)
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
# NOTE: Ad system is kept separate. No reward is granted
# automatically here until the AdsGram reward flow is finalized.
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


def consume_pending_ad(user_id):
    conn = db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT created_at
        FROM pending_ads
        WHERE user_id=%s
    """, (user_id,))

    row = cur.fetchone()

    if not row:
        cur.close()
        conn.close()
        return False

    try:
        created = datetime.strptime(
            row["created_at"],
            "%Y-%m-%d %H:%M:%S"
        )

        if datetime.now() - created > timedelta(hours=1):
            cur.execute(
                "DELETE FROM pending_ads WHERE user_id=%s",
                (user_id,)
            )
            conn.commit()
            cur.close()
            conn.close()
            return False

    except Exception:
        cur.close()
        conn.close()
        return False

    cur.execute(
        "DELETE FROM pending_ads WHERE user_id=%s",
        (user_id,)
    )

    conn.commit()
    cur.close()
    conn.close()

    return True


# =========================================================
# TELEGRAM UPDATE
# =========================================================

def legacy_handle_update(update):

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
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

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

    # =====================================================
    # HISTORY
    # =====================================================

    if text == "📜 History":

        conn = db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

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
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

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

    # =====================================================
    # DEFAULT
    # =====================================================

    send_message(
        user_id,
        "Please choose an option from the menu.",
        main_menu()
    )


# =========================================================
# NEW FEATURES: DAILY TASKS / PROFILE / SHOP / ADMIN
# =========================================================

TASKS = {
    "bonus": "🎁 Daily Bonus",
    "quiz": "🎯 Daily Quiz",
    "checkin": "🪙 Coin Check-in",
    "knowledge": "🌍 Daily Knowledge",
    "world": "☀️ World Facts",
}

QUESTION_BANK = [
    ("q01","Which country is famous for the Sahara Desert?",["Egypt","Japan","Canada","Norway"],0),
    ("q02","Which country is famous for Mount Fuji?",["Japan","Brazil","India","Spain"],0),
    ("q03","Which country is famous for kangaroos?",["Australia","France","Egypt","Nepal"],0),
    ("q04","Which country is famous for the Eiffel Tower?",["France","Italy","Canada","Mexico"],0),
    ("q05","Which country is home to the Taj Mahal?",["India","China","Japan","Peru"],0),
    ("q06","Which country is famous for the Great Wall?",["China","Egypt","Brazil","Greece"],0),
    ("q07","Which country is famous for the pyramids of Giza?",["Egypt","India","Turkey","Canada"],0),
    ("q08","Which country is famous for the Great Barrier Reef?",["Australia","Japan","Italy","Kenya"],0),
    ("q09","Which country is known for the Amazon rainforest?",["Brazil","Norway","France","India"],0),
    ("q10","Which country is famous for Machu Picchu?",["Peru","Mexico","Spain","Chile"],0),
    ("q11","Which country is famous for the Colosseum?",["Italy","Greece","France","Egypt"],0),
    ("q12","Which country is famous for Petra?",["Jordan","Canada","India","Japan"],0),
    ("q13","Which country is famous for the Alps?",["Switzerland","Brazil","Thailand","Kenya"],0),
    ("q14","Which country is known for Bali?",["Indonesia","Italy","Egypt","Portugal"],0),
    ("q15","Which country is famous for tulips and windmills?",["Netherlands","Germany","Spain","Denmark"],0),
    ("q16","Which country is famous for the city of Cairo?",["Egypt","France","Japan","Peru"],0),
    ("q17","Which country is famous for the city of Tokyo?",["Japan","China","Korea","India"],0),
    ("q18","Which country is famous for the city of Paris?",["France","Italy","Spain","Belgium"],0),
    ("q19","Which country is famous for the city of Rome?",["Italy","Greece","France","Spain"],0),
    ("q20","Which country is famous for the city of London?",["United Kingdom","Canada","Australia","Ireland"],0),
    ("q21","Which country is famous for the city of Dhaka?",["Bangladesh","India","Nepal","Pakistan"],0),
    ("q22","Which country is famous for the city of Seoul?",["South Korea","Japan","China","Vietnam"],0),
    ("q23","Which country is famous for the city of Bangkok?",["Thailand","Malaysia","Indonesia","Singapore"],0),
    ("q24","Which country is famous for the city of Istanbul?",["Turkey","Greece","Italy","Egypt"],0),
    ("q25","Which country is famous for the city of Nairobi?",["Kenya","Tanzania","Ghana","Nigeria"],0),
    ("q26","Which country is famous for the city of Lima?",["Peru","Chile","Brazil","Bolivia"],0),
    ("q27","Which country is famous for the city of Oslo?",["Norway","Sweden","Finland","Iceland"],0),
    ("q28","Which country is famous for the city of Helsinki?",["Finland","Sweden","Norway","Denmark"],0),
    ("q29","Which country is famous for the city of Stockholm?",["Sweden","Finland","Norway","Germany"],0),
    ("q30","Which country is famous for the city of Lisbon?",["Portugal","Spain","France","Italy"],0),
    ("q31","Which country is famous for the city of Madrid?",["Spain","Portugal","France","Italy"],0),
    ("q32","Which country is famous for the city of Vienna?",["Austria","Germany","Switzerland","Hungary"],0),
    ("q33","Which country is famous for the city of Berlin?",["Germany","Austria","Poland","France"],0),
    ("q34","Which country is famous for the city of Prague?",["Czech Republic","Slovakia","Austria","Poland"],0),
    ("q35","Which country is famous for the city of Athens?",["Greece","Italy","Turkey","Cyprus"],0),
    ("q36","Which country is famous for the city of Moscow?",["Russia","Ukraine","Poland","Finland"],0),
    ("q37","Which country is famous for the city of Buenos Aires?",["Argentina","Brazil","Uruguay","Chile"],0),
    ("q38","Which country is famous for the city of Rio de Janeiro?",["Brazil","Argentina","Colombia","Peru"],0),
    ("q39","Which country is famous for the city of Havana?",["Cuba","Mexico","Spain","Dominican Republic"],0),
    ("q40","Which country is famous for the city of Marrakech?",["Morocco","Algeria","Tunisia","Egypt"],0),
]


def setting(key, default):
    conn=db(); cur=conn.cursor(); cur.execute("SELECT value FROM bot_settings WHERE key=%s",(key,)); row=cur.fetchone(); cur.close(); conn.close()
    try: return int(row[0]) if row else default
    except: return default


def set_setting(key, value):
    conn=db(); cur=conn.cursor(); cur.execute("INSERT INTO bot_settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",(key,str(max(0,int(value))))); conn.commit(); cur.close(); conn.close()


def claim_exists(uid,key):
    conn=db(); cur=conn.cursor(); cur.execute("SELECT 1 FROM daily_task_claims WHERE user_id=%s AND task_key=%s AND claim_date=%s",(uid,key,date.today().isoformat())); x=cur.fetchone(); cur.close(); conn.close(); return bool(x)


def claim_task_new(uid,key):
    if key not in TASKS or claim_exists(uid,key): return False
    reward=setting("task_reward",5)
    conn=db(); cur=conn.cursor()
    try:
        cur.execute("INSERT INTO daily_task_claims(user_id,task_key,claim_date,reward,created_at) VALUES(%s,%s,%s,%s,%s)",(uid,key,date.today().isoformat(),reward,now()))
        cur.execute("INSERT INTO user_stats(user_id) VALUES(%s) ON CONFLICT(user_id) DO NOTHING",(uid,))
        cur.execute("UPDATE user_stats SET task_completed=task_completed+1 WHERE user_id=%s",(uid,))
        conn.commit()
    except Exception:
        conn.rollback(); cur.close(); conn.close(); return False
    cur.close(); conn.close(); add_coins(uid,reward,f"Daily Task: {TASKS[key]}"); return True


def tasks_text(uid):
    lines=["📋 Daily Tasks","","Complete each task once per day:",""]
    for k,label in TASKS.items(): lines.append(("✅" if claim_exists(uid,k) else "⬜")+" "+label+f" — {setting("task_reward",5)} Coins")
    lines += ["",f"💰 Maximum: {setting("task_reward",5)*len(TASKS)} Coins/day"]
    return "\n".join(lines)


def tasks_markup():
    return {"inline_keyboard":[
        [{"text":"🎁 Daily Bonus","callback_data":"task:bonus"}],
        [{"text":"🎯 Daily Quiz","callback_data":"task:quiz"}],
        [{"text":"🪙 Coin Check-in","callback_data":"task:checkin"}],
        [{"text":"🌍 Daily Knowledge","callback_data":"task:knowledge"}],
        [{"text":"☀️ World Facts","callback_data":"task:world"}],
    ]}


def daily_q(): return QUESTION_BANK[date.today().toordinal()%len(QUESTION_BANK)]


def profile_text_new(uid):
    u=get_user(uid)
    conn=db(); cur=conn.cursor(); cur.execute("SELECT task_completed,daily_streak FROM user_stats WHERE user_id=%s",(uid,)); st=cur.fetchone() or (0,0); cur.close(); conn.close()
    return (f"👤 My Profile\n\n🆔 User ID: {uid}\n🪙 Coins: {u['coins']:,}\n📺 Ads Watched: {u['ads_watched']:,}\n📋 Tasks Completed: {st[0]:,}\n🔥 Daily Streak: {st[1]:,}\n📈 Total Earned: {u['coins_earned']:,}")


def shop_items_new():
    conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute("SELECT * FROM shop_items WHERE active=1 ORDER BY id"); rows=cur.fetchall(); cur.close(); conn.close(); return rows


def shop_markup():
    return {"inline_keyboard":[[{"text":f"{x['name']} — {x['price']:,}","callback_data":f"shop:{x['id']}"}] for x in shop_items_new()]}


def handle_new_callback(update):
    c=update.get("callback_query"); uid=c.get("from",{}).get("id"); data=c.get("data","")
    if not uid: return
    if data.startswith("task:"):
        key=data.split(":",1)[1]
        if key=="bonus":
            u=get_user(uid)
            if u["last_bonus"]!=date.today().isoformat():
                r=setting("daily_bonus",5); conn=db(); cur=conn.cursor(); cur.execute("UPDATE users SET coins=coins+%s,coins_earned=coins_earned+%s,last_bonus=%s WHERE user_id=%s",(r,r,date.today().isoformat(),uid)); conn.commit(); cur.close(); conn.close(); add_history(uid,r,"Daily bonus")
            ok=claim_task_new(uid,"bonus"); send_message(uid,tasks_text(uid),tasks_markup()); return
        if key=="quiz":
            q=daily_q(); conn=db(); cur=conn.cursor(); cur.execute("SELECT 1 FROM quiz_attempts WHERE user_id=%s AND attempt_date=%s",(uid,date.today().isoformat())); done=cur.fetchone(); cur.close(); conn.close()
            if done: send_message(uid,"🎯 Today's quiz is already completed."); return
            send_message(uid,"🎯 Daily Quiz\n\n"+q[1],{"inline_keyboard":[[{"text":a,"callback_data":f"quiz:{i}"}] for i,a in enumerate(q[2])]}); return
        if key=="checkin":
            conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute("SELECT last_checkin,daily_streak FROM user_stats WHERE user_id=%s",(uid,)); st=cur.fetchone() or {"last_checkin":None,"daily_streak":0}; yesterday=(date.today()-timedelta(days=1)).isoformat(); streak=st["daily_streak"]+1 if st["last_checkin"]==yesterday else 1; cur.execute("UPDATE user_stats SET last_checkin=%s,daily_streak=%s WHERE user_id=%s",(date.today().isoformat(),streak,uid)); conn.commit(); cur.close(); conn.close(); claim_task_new(uid,"checkin"); send_message(uid,tasks_text(uid),tasks_markup()); return
        if key in ("knowledge","world"):
            q=daily_q(); label="🌍 Daily Knowledge" if key=="knowledge" else "☀️ World Facts"; claim_task_new(uid,key); send_message(uid,f"{label}\n\n{q[1]}\n\nআজকের task complete হয়েছে।",tasks_markup()); return
    if data.startswith("quiz:"):
        try: ans=int(data.split(":",1)[1])
        except: return
        q=daily_q(); conn=db(); cur=conn.cursor(); cur.execute("SELECT 1 FROM quiz_attempts WHERE user_id=%s AND attempt_date=%s",(uid,date.today().isoformat()));
        if cur.fetchone(): cur.close(); conn.close(); send_message(uid,"🎯 Today's quiz is already completed."); return
        correct=int(ans==q[3]); cur.execute("INSERT INTO quiz_attempts(user_id,question_key,attempt_date,correct,created_at) VALUES(%s,%s,%s,%s,%s)",(uid,q[0],date.today().isoformat(),correct,now())); conn.commit(); cur.close(); conn.close()
        if correct: claim_task_new(uid,"quiz"); send_message(uid,"🎉 Correct! +5 Coins");
        else: send_message(uid,"❌ Wrong answer. Correct answer: "+q[2][q[3]])
        send_message(uid,tasks_text(uid),tasks_markup()); return
    if data.startswith("shop:"):
        try: iid=int(data.split(":",1)[1])
        except: return
        conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute("SELECT * FROM shop_items WHERE id=%s AND active=1",(iid,)); item=cur.fetchone()
        if not item: cur.close(); conn.close(); send_message(uid,"❌ Item unavailable."); return
        cur.execute("SELECT coins FROM users WHERE user_id=%s",(uid,)); u=cur.fetchone()
        if not u or u["coins"]<item["price"]: cur.close(); conn.close(); send_message(uid,"❌ Not enough Coins."); return
        cur.execute("UPDATE users SET coins=coins-%s WHERE user_id=%s AND coins>=%s",(item["price"],uid,item["price"]))
        if cur.rowcount!=1: conn.rollback(); cur.close(); conn.close(); send_message(uid,"❌ Balance changed. Try again."); return
        cur.execute("INSERT INTO shop_purchases(user_id,item_id,item_name,price,status,created_at) VALUES(%s,%s,%s,%s,'pending',%s) RETURNING id",(uid,iid,item["name"],item["price"],now())); pid=cur.fetchone()["id"]; conn.commit(); cur.close(); conn.close(); add_history(uid,-item["price"],f"Shop purchase pending: {item['name']}"); send_message(uid,f"🛍️ Purchase request #{pid} sent.\nCoins reserved until admin decision."); send_message(ADMIN_ID,f"🛍️ NEW SHOP PURCHASE #{pid}\nUser: {uid}\nItem: {item['name']}\nPrice: {item['price']:,}\n\nOpen Admin Panel to Approve/Reject."); return


def handle_update(update):
    # Callback queries are new functionality; legacy handler only processes messages.
    if "callback_query" in update:
        handle_new_callback(update); return
    if "message" not in update: return
    msg=update["message"]; uid=msg.get("from",{}).get("id"); text=msg.get("text","").strip()
    if not uid: return
    # Dynamic settings: admin edits affect future rewards without changing old data.
    globals()["DAILY_BONUS"]=setting("daily_bonus",5)
    globals()["AD_REWARD"]=setting("ad_reward",50)
    globals()["REFERRAL_BONUS"]=setting("referral_bonus",100)
    if text=="📋 Daily Tasks": send_message(uid,tasks_text(uid),tasks_markup()); return
    if text=="👤 My Profile": send_message(uid,profile_text_new(uid)); return
    if text=="🛍️ Coin Shop":
        items=shop_items_new()
        if not items: send_message(uid,"🛍️ Coin Shop\n\nNo items available."); return
        lines=["🛍️ Coin Shop","","Choose an item:",""]+[f"{x['name']} — {x['price']:,} Coins" for x in items]
        send_message(uid,"\n".join(lines),shop_markup()); return
    legacy_handle_update(update)

# =========================================================
# FLASK / WEBHOOK
# =========================================================

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


# =========================================================
# RUN
# =========================================================

# =========================================================
# ADMIN PANEL - full controls
# =========================================================

def admin_ok(): return session.get("admin") is True

def admin_page(body):
    return """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Found Coins Admin</title><style>body{font-family:Arial;background:#f3f4f6;padding:15px}.box{background:#fff;padding:15px;margin:10px auto;border-radius:12px;max-width:1200px;overflow:auto}table{width:100%;border-collapse:collapse;min-width:700px}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}input,button,select{padding:8px;margin:3px}a{margin:3px}</style></head><body><div class="box">"""+body+"</div></body></html>"

@app.route("/admin",methods=["GET","POST"])
def admin_login():
    if request.method=="POST":
        pw=request.form.get("password",""); expected=os.environ.get("ADMIN_PASSWORD","")
        if expected and secrets.compare_digest(pw,expected): session["admin"]=True; return redirect("/admin/dashboard")
        return admin_page("<h2>🛠️ Found Coins Admin</h2><p>Invalid password.</p><form method='post'><input type='password' name='password' required><button>Login</button></form>")
    if admin_ok(): return redirect("/admin/dashboard")
    return admin_page("<h2>🛠️ Found Coins Admin</h2><form method='post'><input type='password' name='password' placeholder='Admin Password' required><button>Login</button></form>")

@app.route("/admin/logout")
def admin_logout(): session.clear(); return redirect("/admin")

@app.route("/admin/dashboard")
def admin_dashboard():
    if not admin_ok(): return redirect("/admin")
    conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT COUNT(*) n FROM users"); total=cur.fetchone()["n"]
    cur.execute("SELECT COALESCE(SUM(coins),0) n FROM users"); coins=cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) n FROM withdrawals WHERE status='pending'"); pw=cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) n FROM shop_purchases WHERE status='pending'"); ps=cur.fetchone()["n"]
    cur.execute("SELECT user_id,username,coins,ads_watched,coins_earned,blocked FROM users ORDER BY joined_at DESC LIMIT 200"); users=cur.fetchall()
    cur.execute("SELECT id,user_id,username,method,number,coins,status FROM withdrawals ORDER BY id DESC LIMIT 100"); ws=cur.fetchall()
    cur.execute("SELECT id,user_id,item_name,price,status FROM shop_purchases ORDER BY id DESC LIMIT 100"); shops=cur.fetchall()
    cur.close(); conn.close()
    rows="".join(f"<tr><td>{u['user_id']}</td><td>@{html.escape(u['username'] or 'N/A')}</td><td>{u['coins']:,}</td><td>{u['ads_watched']}</td><td><form method='post' action='/admin/user/{u['user_id']}/coins'><input type='number' name='coins' value='{u['coins']}' min='0'><button>✏️ Edit</button></form></td><td><a href='/admin/user/{u['user_id']}'>👤 Profile</a></td></tr>" for u in users)
    wrows="".join(f"<tr><td>#{w['id']}</td><td>{w['user_id']}</td><td>{html.escape(w['method'])}</td><td>{html.escape(w['number'])}</td><td>{w['coins']:,}</td><td>{w['status']}</td><td>{('<a href=/admin/withdrawal/'+str(w['id'])+'/approve>Approve</a> <a href=/admin/withdrawal/'+str(w['id'])+'/reject>Reject</a>') if w['status']=='pending' else ''}</td></tr>" for w in ws)
    srows="".join(f"<tr><td>#{p['id']}</td><td>{p['user_id']}</td><td>{html.escape(p['item_name'])}</td><td>{p['price']:,}</td><td>{p['status']}</td><td>{('<a href=/admin/shop/'+str(p['id'])+'/approve>Approve</a> <a href=/admin/shop/'+str(p['id'])+'/reject>Reject</a>') if p['status']=='pending' else ''}</td></tr>" for p in shops)
    body=f"""<h1>🛠️ Found Coins Admin</h1><p><a href='/admin/logout'>Logout</a> | <a href='/admin/shop'>🛍️ Coin Shop Edit</a></p><p>👥 Users: <b>{total}</b> | 🪙 Coins: <b>{coins:,}</b> | 💰 Pending withdrawals: <b>{pw}</b> | 🛍️ Pending purchases: <b>{ps}</b></p><div class='box'><h2>⚙️ Edit Rewards</h2><form method='post' action='/admin/settings'>Daily Bonus <input name='daily_bonus' type='number' min='0' value='{setting('daily_bonus',5)}'> Ad Reward <input name='ad_reward' type='number' min='0' value='{setting('ad_reward',50)}'> Referral <input name='referral_bonus' type='number' min='0' value='{setting('referral_bonus',100)}'> Task Reward <input name='task_reward' type='number' min='0' value='{setting('task_reward',5)}'> <button>💾 Save</button></form></div><div class='box'><h2>👤 Profiles / Coins</h2><table><tr><th>ID</th><th>User</th><th>Coins</th><th>Ads</th><th>Edit Coins</th><th>Profile</th></tr>{rows}</table></div><div class='box'><h2>💰 Withdrawals</h2><table><tr><th>ID</th><th>User</th><th>Method</th><th>Number</th><th>Coins</th><th>Status</th><th>Actions</th></tr>{wrows}</table></div><div class='box'><h2>🛍️ Shop Purchases</h2><table><tr><th>ID</th><th>User</th><th>Item</th><th>Price</th><th>Status</th><th>Actions</th></tr>{srows}</table></div>"""
    return admin_page(body)

@app.route("/admin/settings",methods=["POST"])
def admin_settings():
    if not admin_ok(): return redirect("/admin")
    for k in ("daily_bonus","ad_reward","referral_bonus","task_reward"):
        try: set_setting(k,int(request.form.get(k,"0")))
        except: pass
    return redirect("/admin/dashboard")

@app.route("/admin/user/<int:uid>")
def admin_user(uid):
    if not admin_ok(): return redirect("/admin")
    u=get_user(uid)
    if not u: return admin_page("User not found")
    conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute("SELECT * FROM history WHERE user_id=%s ORDER BY id DESC LIMIT 30",(uid,)); hs=cur.fetchall(); cur.close(); conn.close()
    history="".join(f"<tr><td>{h['amount']}</td><td>{html.escape(h['reason'])}</td><td>{h['created_at']}</td></tr>" for h in hs)
    return admin_page(f"<h1>👤 User Profile</h1><p><a href='/admin/dashboard'>← Dashboard</a></p><p>ID: {uid}<br>Username: @{html.escape(u['username'] or 'N/A')}<br>Coins: <b>{u['coins']:,}</b><br>Ads: {u['ads_watched']}<br>Total Earned: {u['coins_earned']:,}</p><h3>✏️ Edit Coins</h3><form method='post' action='/admin/user/{uid}/coins'><input name='coins' type='number' min='0' value='{u['coins']}'><button>Save</button></form><h3>📜 History</h3><table><tr><th>Amount</th><th>Reason</th><th>Date</th></tr>{history}</table>")

@app.route("/admin/user/<int:uid>/coins",methods=["POST"])
def admin_user_coins(uid):
    if not admin_ok(): return redirect("/admin")
    try: amount=max(0,int(request.form.get("coins","0")))
    except: amount=0
    if set_coins(uid,amount,"Admin edited coin balance"): send_message(uid,f"🛠️ Admin updated your balance.\n🪙 New balance: {amount:,}")
    return redirect(f"/admin/user/{uid}")

@app.route("/admin/withdrawal/<int:wid>/<action>")
def admin_withdrawal(wid,action):
    if not admin_ok() or action not in ("approve","reject"): return redirect("/admin/dashboard")
    conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute("SELECT * FROM withdrawals WHERE id=%s",(wid,)); w=cur.fetchone()
    if not w or w['status']!='pending': cur.close(); conn.close(); return redirect('/admin/dashboard')
    if action=='approve': cur.execute("UPDATE withdrawals SET status='approved' WHERE id=%s",(wid)); msg="✅ Your withdrawal was approved."
    else: cur.execute("UPDATE withdrawals SET status='rejected' WHERE id=%s",(wid)); cur.execute("UPDATE users SET coins=coins+%s WHERE user_id=%s",(w['coins'],w['user_id'])); msg=f"❌ Withdrawal rejected.\n+{w['coins']:,} Coins refunded."
    conn.commit(); cur.close(); conn.close(); send_message(w['user_id'],msg); return redirect('/admin/dashboard')

@app.route("/admin/shop")
def admin_shop():
    if not admin_ok(): return redirect('/admin')
    conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute("SELECT * FROM shop_items ORDER BY id"); items=cur.fetchall(); cur.close(); conn.close()
    rows="".join(f"<tr><td>{x['id']}</td><td>{html.escape(x['name'])}</td><td>{x['price']:,}</td><td>{'ON' if x['active'] else 'OFF'}</td><td><a href='/admin/shop/{x['id']}/edit'>✏️ Edit</a></td></tr>" for x in items)
    return admin_page(f"<h1>🛍️ Coin Shop Edit</h1><p><a href='/admin/dashboard'>← Dashboard</a></p><form method='post' action='/admin/shop/add'><input name='name' placeholder='Name' required><input name='description' placeholder='Description'><input name='price' type='number' min='0' placeholder='Price' required><button>➕ Add</button></form><table><tr><th>ID</th><th>Name</th><th>Price</th><th>Status</th><th>Edit</th></tr>{rows}</table>")

@app.route("/admin/shop/add",methods=["POST"])
def admin_shop_add():
    if not admin_ok(): return redirect('/admin')
    name=request.form.get('name','').strip(); desc=request.form.get('description','').strip()
    try: price=max(0,int(request.form.get('price','0')))
    except: price=0
    if name:
        conn=db(); cur=conn.cursor(); cur.execute("INSERT INTO shop_items(name,description,price,active,created_at) VALUES(%s,%s,%s,1,%s)",(name,desc,price,now())); conn.commit(); cur.close(); conn.close()
    return redirect('/admin/shop')

@app.route("/admin/shop/<int:iid>/edit",methods=["GET","POST"])
def admin_shop_edit(iid):
    if not admin_ok(): return redirect('/admin')
    conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if request.method=='POST':
        name=request.form.get('name','').strip(); desc=request.form.get('description','').strip()
        try: price=max(0,int(request.form.get('price','0')))
        except: price=0
        active=1 if request.form.get('active') else 0
        cur.execute("UPDATE shop_items SET name=%s,description=%s,price=%s,active=%s WHERE id=%s",(name,desc,price,active,iid)); conn.commit(); cur.close(); conn.close(); return redirect('/admin/shop')
    cur.execute("SELECT * FROM shop_items WHERE id=%s",(iid,)); x=cur.fetchone(); cur.close(); conn.close()
    if not x: return admin_page('Item not found')
    return admin_page(f"<h1>✏️ Edit Shop Item</h1><form method='post'><input name='name' value='{html.escape(x['name'])}' required><input name='description' value='{html.escape(x['description'] or '')}'><input name='price' type='number' min='0' value='{x['price']}' required><label><input type='checkbox' name='active' {'checked' if x['active'] else ''}> Active</label><button>💾 Save</button></form>")

@app.route("/admin/shop/<int:pid>/<action>")
def admin_shop_action(pid,action):
    if not admin_ok() or action not in ('approve','reject'): return redirect('/admin/dashboard')
    conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute("SELECT * FROM shop_purchases WHERE id=%s",(pid,)); p=cur.fetchone()
    if not p or p['status']!='pending': cur.close(); conn.close(); return redirect('/admin/dashboard')
    if action=='approve': cur.execute("UPDATE shop_purchases SET status='approved',processed_at=%s WHERE id=%s",(now(),pid)); msg=f"✅ Purchase approved.\nItem: {p['item_name']}"
    else: cur.execute("UPDATE shop_purchases SET status='rejected',processed_at=%s WHERE id=%s",(now(),pid)); cur.execute("UPDATE users SET coins=coins+%s WHERE user_id=%s",(p['price'],p['user_id'])); msg=f"❌ Purchase rejected.\n+{p['price']:,} Coins refunded."; add_history(p['user_id'],p['price'],f"Shop purchase rejected/refunded: {p['item_name']}")
    conn.commit(); cur.close(); conn.close(); send_message(p['user_id'],msg); return redirect('/admin/dashboard')

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(
        host="0.0.0.0",
        port=port
    )
