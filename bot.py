import os
import html
import requests
import psycopg2
import psycopg2.extras

from datetime import datetime, date, timedelta
from flask import Flask, request, session, redirect, url_for

# =========================================================
# BASIC CONFIG
# =========================================================

TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]

ADMIN_ID = 7926491409

ADSGRAM_BLOCK_ID = "49006"
ADSGRAM_TOKEN = os.environ.get("ADSGRAM_TOKEN", "")

API = f"https://api.telegram.org/bot{TOKEN}"

app = Flask(__name__)
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    ADMIN_PASSWORD
)

# =========================================================
# BOT SETTINGS
# =========================================================

DAILY_BONUS = 20
REFERRAL_BONUS = 100
AD_REWARD = 50

# 5 Daily Tasks × 5 Coins = 25 Coins/day
DAILY_TASK_REWARD = 5

MIN_WITHDRAW = 10000
MAX_WITHDRAW = 10000

# Coin Shop Badge Prices
BADGE_1_PRICE = 2500
BADGE_2_PRICE = 3500
BADGE_3_PRICE = 4500
VIP_BADGE_PRICE = 5000

# Active means user interacted with bot
# within this many minutes.
ACTIVE_MINUTES = 10

# =========================================================
# DATABASE
# =========================================================

def db():
    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=10
    )


def init_db():

    conn = db()
    cur = conn.cursor()

    # Existing users table
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

    # Existing history
    cur.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT,
            amount INTEGER,
            reason TEXT,
            created_at TEXT
        )
    """)

    # Existing withdrawals
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

    # Existing pending ads
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_ads (
            user_id BIGINT PRIMARY KEY,
            created_at TEXT
        )
    """)

    # Daily task claims
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_task_claims (
            user_id BIGINT NOT NULL,
            task_date DATE NOT NULL,
            task_key TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            PRIMARY KEY (user_id, task_date, task_key)
        )
    """)

    # Quiz attempts
    cur.execute("""
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            user_id BIGINT NOT NULL,
            quiz_date DATE NOT NULL,
            question_id INTEGER NOT NULL,
            correct INTEGER DEFAULT 0,
            answered_at TEXT NOT NULL,
            PRIMARY KEY (user_id, quiz_date)
        )
    """)

    # -----------------------------------------------------
    # NEW PROFILE / BADGE / ACTIVITY FIELDS
    # -----------------------------------------------------

    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS profile_name TEXT
    """)

    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS profile_photo_file_id TEXT
    """)

    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS badge TEXT
    """)

    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS last_seen TEXT
    """)

    # Profile input state
    cur.execute("""
        CREATE TABLE IF NOT EXISTS profile_states (
            user_id BIGINT PRIMARY KEY,
            action TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()

    cur.close()
    conn.close()


init_db()

# =========================================================
# HELPERS
# =========================================================

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return date.today()


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
            blocked,
            last_seen
        )
        VALUES (%s, %s, 0, 0, 0, NULL, %s, %s, 0, %s)

        ON CONFLICT (user_id)
        DO UPDATE SET
            username = EXCLUDED.username,
            last_seen = EXCLUDED.last_seen
    """, (
        user_id,
        username,
        referred_by,
        now(),
        now()
    ))

    conn.commit()

    cur.close()
    conn.close()


def update_last_seen(user_id):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET last_seen=%s,
            blocked=0
        WHERE user_id=%s
    """, (
        now(),
        user_id
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

    changed = cur.rowcount

    conn.commit()

    cur.close()
    conn.close()

    if changed:
        add_history(
            user_id,
            amount,
            reason
        )

    return changed > 0


def coins_to_taka(coins):

    return coins / 100


# =========================================================
# PROFILE STATE
# =========================================================

def set_profile_state(user_id, action):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO profile_states
        (user_id, action, created_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id)
        DO UPDATE SET
            action=EXCLUDED.action,
            created_at=EXCLUDED.created_at
    """, (
        user_id,
        action,
        now()
    ))

    conn.commit()

    cur.close()
    conn.close()


def get_profile_state(user_id):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT action
        FROM profile_states
        WHERE user_id=%s
    """, (
        user_id,
    ))

    row = cur.fetchone()

    cur.close()
    conn.close()

    if row:
        return row[0]

    return None


def clear_profile_state(user_id):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM profile_states
        WHERE user_id=%s
    """, (
        user_id,
    ))

    conn.commit()

    cur.close()
    conn.close()


# =========================================================
# TELEGRAM
# =========================================================

def telegram(method, data=None):

    try:

        response = requests.post(
            f"{API}/{method}",
            json=data or {},
            timeout=20
        )

        return response.json()

    except Exception as e:

        print("Telegram API error:", e)

        return None


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

    if reply_markup is not None:
        data["reply_markup"] = reply_markup

    if protect:
        data["protect_content"] = True

    if parse_mode:
        data["parse_mode"] = parse_mode

    return telegram(
        "sendMessage",
        data
    )


def send_photo(
    chat_id,
    photo,
    caption,
    reply_markup=None,
    protect=False
):

    data = {
        "chat_id": chat_id,
        "photo": photo,
        "caption": caption
    }

    if reply_markup is not None:
        data["reply_markup"] = reply_markup

    if protect:
        data["protect_content"] = True

    return telegram(
        "sendPhoto",
        data
    )


def answer_callback(callback_id, text=None):

    data = {
        "callback_query_id": callback_id
    }

    if text:
        data["text"] = text

    return telegram(
        "answerCallbackQuery",
        data
    )


def edit_message(
    chat_id,
    message_id,
    text,
    reply_markup=None
):

    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text
    }

    if reply_markup is not None:
        data["reply_markup"] = reply_markup

    return telegram(
        "editMessageText",
        data
    )


# =========================================================
# MAIN MENU
# =========================================================

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
                    "text": "📋 Daily Tasks"
                },
                {
                    "text": "🎁 Daily Bonus"
                }
            ],
            [
                {
                    "text": "👤 My Profile"
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


# =========================================================
# DAILY TASKS
# =========================================================

TASK_NAMES = {
    "daily_bonus": "🎁 Daily Bonus",
    "daily_quiz": "🎯 Daily Quiz",
    "coin_checkin": "🪙 Coin Check-in",
    "daily_knowledge": "📚 Daily Knowledge",
    "world_facts": "🌍 World Facts"
}


def task_claimed(user_id, task_key):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT 1
        FROM daily_task_claims
        WHERE user_id=%s
        AND task_date=%s
        AND task_key=%s
    """, (
        user_id,
        today(),
        task_key
    ))

    result = cur.fetchone()

    cur.close()
    conn.close()

    return result is not None


def claim_task(user_id, task_key, reason):

    conn = db()
    cur = conn.cursor()

    try:

        cur.execute("""
            INSERT INTO daily_task_claims
            (
                user_id,
                task_date,
                task_key,
                claimed_at
            )
            VALUES (%s, %s, %s, %s)

            ON CONFLICT
            (user_id, task_date, task_key)
            DO NOTHING
        """, (
            user_id,
            today(),
            task_key,
            now()
        ))

        inserted = cur.rowcount == 1

        if inserted:

            cur.execute("""
                UPDATE users
                SET
                    coins = coins + %s,
                    coins_earned = coins_earned + %s
                WHERE user_id=%s
            """, (
                DAILY_TASK_REWARD,
                DAILY_TASK_REWARD,
                user_id
            ))

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
                DAILY_TASK_REWARD,
                reason,
                now()
            ))

        conn.commit()

        return inserted

    except Exception:

        conn.rollback()
        raise

    finally:

        cur.close()
        conn.close()


def task_order():

    keys = [
        "daily_bonus",
        "daily_quiz",
        "coin_checkin",
        "daily_knowledge",
        "world_facts"
    ]

    # Changes order every day
    shift = today().toordinal() % len(keys)

    return keys[shift:] + keys[:shift]


def daily_tasks_keyboard(user_id):

    rows = []

    for task_key in task_order():

        done = task_claimed(
            user_id,
            task_key
        )

        name = TASK_NAMES[task_key]

        rows.append([
            {
                "text":
                    (
                        f"✅ {name} — {DAILY_TASK_REWARD}"
                        if done
                        else f"{name} — {DAILY_TASK_REWARD}"
                    ),
                "callback_data":
                    f"task:{task_key}"
            }
        ])

    return {
        "inline_keyboard": rows
    }


def show_daily_tasks(user_id, message_id=None):

    task_keys = [
        "daily_bonus",
        "daily_quiz",
        "coin_checkin",
        "daily_knowledge",
        "world_facts"
    ]

    done = 0

    for key in task_keys:

        if task_claimed(user_id, key):
            done += 1

    total_reward = len(task_keys) * DAILY_TASK_REWARD
    earned_reward = done * DAILY_TASK_REWARD

    text = (
        "📋 Daily Tasks\n\n"
        "আজকের ৫টি Task সম্পন্ন করো।\n"
        "প্রতিদিন Task-এর order পরিবর্তন হবে।\n\n"
        f"✅ Completed: {done}/5\n"
        f"🏆 Earned Today: "
        f"{earned_reward}/{total_reward} Coins\n\n"
        f"প্রতিটি Task = {DAILY_TASK_REWARD} Coins\n\n"
        "আজকের Task List:"
    )

    markup = daily_tasks_keyboard(user_id)

    if message_id:

        edit_message(
            user_id,
            message_id,
            text,
            markup
        )

    else:

        send_message(
            user_id,
            text,
            markup
        )


# =========================================================
# DAILY QUIZ / KNOWLEDGE / WORLD FACTS
# =========================================================

QUIZ_QUESTIONS = [

    {
        "question": "বাংলাদেশের মুদ্রার নাম কী?",
        "options": [
            "টাকা",
            "রুপি",
            "ডলার",
            "ইউরো"
        ],
        "answer": 0
    },

    {
        "question": "ভারতের মুদ্রার নাম কী?",
        "options": [
            "রুপি",
            "টাকা",
            "দিনার",
            "ইউরো"
        ],
        "answer": 0
    },

    {
        "question": "যুক্তরাষ্ট্রের মুদ্রার নাম কী?",
        "options": [
            "ডলার",
            "রুপি",
            "টাকা",
            "পাউন্ড"
        ],
        "answer": 0
    },

    {
        "question": "জাপানের মুদ্রার নাম কী?",
        "options": [
            "ইয়েন",
            "রুপি",
            "ডলার",
            "দিনার"
        ],
        "answer": 0
    },

    {
        "question": "যুক্তরাজ্যের মুদ্রার নাম কী?",
        "options": [
            "পাউন্ড",
            "ডলার",
            "ইয়েন",
            "রুপি"
        ],
        "answer": 0
    },

    {
        "question": "কোনটি সাধারণত ধাতব মুদ্রা?",
        "options": [
            "কয়েন",
            "নোট",
            "চেক",
            "কার্ড"
        ],
        "answer": 0
    }
]


KNOWLEDGE_QUESTIONS = [

    {
        "question": "পৃথিবীর সবচেয়ে বড় মহাসাগর কোনটি?",
        "options": [
            "প্রশান্ত মহাসাগর",
            "আটলান্টিক মহাসাগর",
            "ভারত মহাসাগর",
            "আর্কটিক মহাসাগর"
        ],
        "answer": 0
    },

    {
        "question": "মানুষের শরীরে রক্ত পাম্প করে কোন অঙ্গ?",
        "options": [
            "হৃদপিণ্ড",
            "ফুসফুস",
            "কিডনি",
            "মস্তিষ্ক"
        ],
        "answer": 0
    },

    {
        "question": "সূর্যের সবচেয়ে কাছের গ্রহ কোনটি?",
        "options": [
            "বুধ",
            "শুক্র",
            "পৃথিবী",
            "মঙ্গল"
        ],
        "answer": 0
    },

    {
        "question": "পানির রাসায়নিক সংকেত কী?",
        "options": [
            "H₂O",
            "CO₂",
            "O₂",
            "NaCl"
        ],
        "answer": 0
    },

    {
        "question": "বাংলাদেশের জাতীয় ফুল কোনটি?",
        "options": [
            "শাপলা",
            "গোলাপ",
            "জবা",
            "সূর্যমুখী"
        ],
        "answer": 0
    }
]


WORLD_FACTS_QUESTIONS = [

    {
        "question": "জাপানের রাজধানী কোনটি?",
        "options": [
            "টোকিও",
            "ঢাকা",
            "বেইজিং",
            "সিউল"
        ],
        "answer": 0
    },

    {
        "question": "ফ্রান্সের রাজধানী কোনটি?",
        "options": [
            "প্যারিস",
            "লন্ডন",
            "রোম",
            "বার্লিন"
        ],
        "answer": 0
    },

    {
        "question": "মিশরের বিখ্যাত পিরামিড কোথায় অবস্থিত?",
        "options": [
            "গিজা",
            "টোকিও",
            "লন্ডন",
            "ঢাকা"
        ],
        "answer": 0
    },

    {
        "question": "চীনের রাজধানী কোনটি?",
        "options": [
            "বেইজিং",
            "সাংহাই",
            "টোকিও",
            "সিউল"
        ],
        "answer": 0
    },

    {
        "question": "অস্ট্রেলিয়ার রাজধানী কোনটি?",
        "options": [
            "ক্যানবেরা",
            "সিডনি",
            "মেলবোর্ন",
            "পারথ"
        ],
        "answer": 0
    }
]


def todays_question(question_list):

    index = today().toordinal() % len(question_list)

    return index, question_list[index]


def show_general_quiz(
    user_id,
    task_key,
    title,
    question_list,
    message_id=None
):

    question_id, quiz = todays_question(
        question_list
    )

    keyboard = []

    for index, option in enumerate(
        quiz["options"]
    ):

        keyboard.append([
            {
                "text": option,
                "callback_data":
                    f"mcq:{task_key}:{question_id}:{index}"
            }
        ])

    text = (
        f"{title}\n\n"
        f"{quiz['question']}\n\n"
        "সঠিক উত্তর নির্বাচন করো।\n"
        f"সঠিক হলে +{DAILY_TASK_REWARD} Coins পাবে।"
    )

    markup = {
        "inline_keyboard": keyboard
    }

    if message_id:

        edit_message(
            user_id,
            message_id,
            text,
            markup
        )

    else:

        send_message(
            user_id,
            text,
            markup
        )


def handle_general_mcq(
    user_id,
    callback_id,
    message_id,
    task_key,
    question_id,
    selected,
    question_list,
    title
):

    real_id, quiz = todays_question(
        question_list
    )

    if question_id != real_id:

        answer_callback(
            callback_id,
            "এই প্রশ্নটি আর সক্রিয় নেই।"
        )

        return

    if task_claimed(
        user_id,
        task_key
    ):

        answer_callback(
            callback_id,
            "আজকের Task ইতিমধ্যে সম্পন্ন।"
        )

        return

    attempt_key = f"{task_key}_{today()}"

    conn = db()
    cur = conn.cursor()

    try:

        cur.execute("""
            INSERT INTO quiz_attempts
            (
                user_id,
                quiz_date,
                question_id,
                correct,
                answered_at
            )
            VALUES (%s, %s, %s, %s, %s)

            ON CONFLICT
            (user_id, quiz_date)
            DO NOTHING
        """, (
            user_id,
            today(),
            question_id,
            1 if selected == quiz["answer"] else 0,
            now()
        ))

        # The original quiz_attempts table has one row/day.
        # To support 3 different quiz-type tasks safely,
        # use a separate unique task key table below.
        inserted = cur.rowcount == 1

        conn.rollback()

    except Exception:
        conn.rollback()
        inserted = True

    finally:
        cur.close()
        conn.close()

    # Daily claim is the actual protection against double reward.
    if selected == quiz["answer"]:

        success = claim_task(
            user_id,
            task_key,
            title
        )

        if success:

            answer_callback(
                callback_id,
                f"🎉 সঠিক! +{DAILY_TASK_REWARD} Coins"
            )

            send_message(
                user_id,
                f"🎉 সঠিক উত্তর!\n\n"
                f"{title} সম্পন্ন হয়েছে।\n"
                f"+{DAILY_TASK_REWARD} Coins যোগ হয়েছে।"
            )

        else:

            answer_callback(
                callback_id,
                "এই Task ইতিমধ্যে সম্পন্ন হয়েছে।"
            )

    else:

        answer_callback(
            callback_id,
            "❌ ভুল উত্তর। আজকের Task-এর Coins পাওয়া যাবে না।"
        )

        send_message(
            user_id,
            f"❌ উত্তরটি সঠিক হয়নি।\n\n"
            f"{title}-এর জন্য আজ আর Coins পাওয়া যাবে না।"
        )

    show_daily_tasks(
        user_id,
        message_id
    )


def show_quiz(user_id, message_id=None):

    show_general_quiz(
        user_id,
        "daily_quiz",
        "🎯 Daily Quiz",
        QUIZ_QUESTIONS,
        message_id
    )


def handle_quiz_answer(
    user_id,
    callback_id,
    message_id,
    question_id,
    selected
):

    handle_general_mcq(
        user_id,
        callback_id,
        message_id,
        "daily_quiz",
        question_id,
        selected,
        QUIZ_QUESTIONS,
        "🎯 Daily Quiz"
    )


# =========================================================
# BADGE / COIN SHOP
# =========================================================

BADGES = {
    "badge1": {
        "name": "🏅 Badge 1",
        "price": BADGE_1_PRICE
    },
    "badge2": {
        "name": "🥇 Badge 2",
        "price": BADGE_2_PRICE
    },
    "badge3": {
        "name": "💎 Badge 3",
        "price": BADGE_3_PRICE
    },
    "vip": {
        "name": "👑 VIP Badge",
        "price": VIP_BADGE_PRICE
    }
}


def badge_shop_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text": f"🏅 Badge 1 — {BADGE_1_PRICE:,}",
                    "callback_data": "buy_badge:badge1"
                }
            ],
            [
                {
                    "text": f"🥇 Badge 2 — {BADGE_2_PRICE:,}",
                    "callback_data": "buy_badge:badge2"
                }
            ],
            [
                {
                    "text": f"💎 Badge 3 — {BADGE_3_PRICE:,}",
                    "callback_data": "buy_badge:badge3"
                }
            ],
            [
                {
                    "text": f"👑 VIP Badge — {VIP_BADGE_PRICE:,}",
                    "callback_data": "buy_badge:vip"
                }
            ],
            [
                {
                    "text": "⬅️ Back to Profile",
                    "callback_data": "profile"
                }
            ]
        ]
    }


def show_badge_shop(user_id):

    text = (
        "🛍️ Badge Coin Shop\n\n"
        "Coins দিয়ে Badge কিনতে পারবে।\n"
        "একটি Badge কেনার পর সেটি তোমার Profile-এ Active থাকবে।\n\n"
        f"🏅 Badge 1 — {BADGE_1_PRICE:,} Coins\n"
        f"🥇 Badge 2 — {BADGE_2_PRICE:,} Coins\n"
        f"💎 Badge 3 — {BADGE_3_PRICE:,} Coins\n"
        f"👑 VIP Badge — {VIP_BADGE_PRICE:,} Coins\n\n"
        "👑 VIP Badge থাকলে VIP status দেখাবে।"
    )

    send_message(
        user_id,
        text,
        badge_shop_keyboard()
    )


def buy_badge(user_id, badge_key, callback_id):

    if badge_key not in BADGES:

        answer_callback(
            callback_id,
            "Badge পাওয়া যায়নি।"
        )

        return

    badge = BADGES[badge_key]

    conn = db()
    cur = conn.cursor()

    try:

        cur.execute("""
            SELECT coins, badge
            FROM users
            WHERE user_id=%s
            FOR UPDATE
        """, (
            user_id,
        ))

        user = cur.fetchone()

        if not user:

            conn.rollback()

            answer_callback(
                callback_id,
                "User পাওয়া যায়নি।"
            )

            return

        if user[1]:

            conn.rollback()

            answer_callback(
                callback_id,
                "তোমার একটি Badge ইতিমধ্যে Active আছে।"
            )

            return

        if user[0] < badge["price"]:

            conn.rollback()

            remaining = badge["price"] - user[0]

            answer_callback(
                callback_id,
                f"আর {remaining:,} Coins প্রয়োজন।"
            )

            return

        cur.execute("""
            UPDATE users
            SET
                coins = coins - %s,
                badge = %s
            WHERE user_id=%s
            AND coins >= %s
            AND badge IS NULL
        """, (
            badge["price"],
            badge["name"],
            user_id,
            badge["price"]
        ))

        if cur.rowcount != 1:

            conn.rollback()

            answer_callback(
                callback_id,
                "Badge purchase failed। আবার চেষ্টা করুন।"
            )

            return

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
            -badge["price"],
            f"Purchased {badge['name']}",
            now()
        ))

        conn.commit()

    except Exception as e:

        conn.rollback()

        print(
            "Badge purchase error:",
            e
        )

        answer_callback(
            callback_id,
            "Badge purchase-এ সমস্যা হয়েছে।"
        )

        return

    finally:

        cur.close()
        conn.close()

    answer_callback(
        callback_id,
        f"{badge['name']} Active হয়েছে!"
    )

    send_message(
        user_id,
        f"🎉 Badge Purchase Successful!\n\n"
        f"{badge['name']}\n"
        f"💰 Price: {badge['price']:,} Coins\n\n"
        "তোমার Profile-এ Badge এখন Active।"
    )

    show_profile(
        user_id
    )


# =========================================================
# MY PROFILE
# =========================================================

def profile_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text": "✏️ Customize Name",
                    "callback_data": "profile_name"
                }
            ],
            [
                {
                    "text": "📷 Set Profile Photo",
                    "callback_data": "profile_photo"
                }
            ],
            [
                {
                    "text": "🛍️ Badge Coin Shop",
                    "callback_data": "badge_shop"
                }
            ]
        ]
    }


def profile_text(user):

    profile_name = (
        user["profile_name"]
        or (
            f"@{user['username']}"
            if user["username"]
            else f"User {user['user_id']}"
        )
    )

    coins = user["coins"] or 0
    amount = coins_to_taka(coins)

    if amount == int(amount):
        amount_text = f"৳{int(amount)}"
    else:
        amount_text = f"৳{amount:.2f}"

    badge = user["badge"] or "No Badge"

    vip_text = ""

    if user["badge"] == "👑 VIP Badge":
        vip_text = (
            "\n👑 VIP Status: ACTIVE\n"
            "⭐ Special Service: Available\n"
        )

    return (
        "👤 My Profile\n\n"
        f"📝 Name: {profile_name}\n"
        f"🪙 My Coins: {coins:,} Coins\n"
        f"💵 My Balance: {amount_text}\n"
        f"🏅 Badge: {badge}\n"
        f"{vip_text}\n"
        "🛍️ নিচের Coin Shop থেকে Badge কিনতে পারবে।"
    )


def show_profile(user_id):

    user = get_user(user_id)

    if not user:
        return

    text = profile_text(user)

    photo = user.get(
        "profile_photo_file_id"
    )

    markup = profile_keyboard()

    if photo:

        result = send_photo(
            user_id,
            photo,
            text,
            markup,
            protect=True
        )

        if not result or not result.get("ok"):

            send_message(
                user_id,
                text,
                markup,
                protect=True
            )

    else:

        send_message(
            user_id,
            text,
            markup,
            protect=True
        )


# =========================================================
# ADSGRAM
# =========================================================

def get_adsgram_ad(user_id):

    if not ADSGRAM_TOKEN:

        print("ADSGRAM_TOKEN is missing.")
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
            "AdsGram status:",
            response.status_code
        )

        if response.status_code != 200:

            print(
                "AdsGram response:",
                response.text[:500]
            )

            return None

        data = response.json()

        if not isinstance(data, dict):

            return None

        if not data.get("reward_url"):

            print(
                "AdsGram response has no reward_url."
            )

            return None

        return data

    except Exception as e:

        print(
            "AdsGram exception:",
            e
        )

        return None


def create_pending_ad(user_id):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO pending_ads
        (user_id, created_at)
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


def reward_adsgram_user(user_id):

    conn = db()
    cur = conn.cursor()

    try:

        cur.execute("""
            SELECT created_at
            FROM pending_ads
            WHERE user_id=%s
            FOR UPDATE
        """, (user_id,))

        pending = cur.fetchone()

        if not pending:

            conn.rollback()
            return False

        try:

            created = datetime.strptime(
                pending[0],
                "%Y-%m-%d %H:%M:%S"
            )

            if datetime.now() - created > timedelta(
                hours=1
            ):

                cur.execute(
                    "DELETE FROM pending_ads WHERE user_id=%s",
                    (user_id,)
                )

                conn.commit()

                return False

        except Exception:
            pass

        cur.execute(
            "DELETE FROM pending_ads WHERE user_id=%s",
            (user_id,)
        )

        if cur.rowcount != 1:

            conn.rollback()
            return False

        cur.execute("""
            UPDATE users
            SET
                coins = coins + %s,
                coins_earned = coins_earned + %s,
                ads_watched = ads_watched + 1
            WHERE user_id=%s
        """, (
            AD_REWARD,
            AD_REWARD,
            user_id
        ))

        if cur.rowcount != 1:

            conn.rollback()
            return False

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
            AD_REWARD,
            "AdsGram Ad Reward",
            now()
        ))

        conn.commit()

        return True

    except Exception as e:

        conn.rollback()

        print(
            "Reward error:",
            e
        )

        return False

    finally:

        cur.close()
        conn.close()


# =========================================================
# UPDATE HANDLER
# =========================================================

def handle_update(update):

    # -----------------------------------------------------
    # CALLBACK QUERY
    # -----------------------------------------------------

    if "callback_query" in update:

        callback = update["callback_query"]

        callback_id = callback["id"]
        data = callback.get("data", "")

        sender = callback.get("from", {})

        user_id = sender.get("id")

        if not user_id:
            return

        username = sender.get(
            "username"
        )

        create_user(
            user_id,
            username
        )

        update_last_seen(
            user_id
        )

        user = get_user(user_id)

        if user and user["blocked"]:

            answer_callback(
                callback_id,
                "🚫 Your account is blocked."
            )

            return

        message = callback.get(
            "message",
            {}
        )

        message_id = message.get(
            "message_id"
        )

        # -------------------------------------------------
        # PROFILE
        # -------------------------------------------------

        if data == "profile":

            answer_callback(
                callback_id
            )

            show_profile(
                user_id
            )

            return

        if data == "profile_name":

            set_profile_state(
                user_id,
                "name"
            )

            answer_callback(
                callback_id
            )

            send_message(
                user_id,
                "✏️ তোমার নতুন Profile Name পাঠাও।\n\n"
                "সর্বোচ্চ 40 characters ব্যবহার করো।"
            )

            return

        if data == "profile_photo":

            set_profile_state(
                user_id,
                "photo"
            )

            answer_callback(
                callback_id
            )

            send_message(
                user_id,
                "📷 এখন একটি Photo পাঠাও।\n\n"
                "Photo পাঠালেই সেটি তোমার Profile Photo হিসেবে Save হবে।"
            )

            return

        if data == "badge_shop":

            answer_callback(
                callback_id
            )

            show_badge_shop(
                user_id
            )

            return

        if data.startswith("buy_badge:"):

            badge_key = data.split(
                ":",
                1
            )[1]

            buy_badge(
                user_id,
                badge_key,
                callback_id
            )

            return

        # -------------------------------------------------
        # DAILY BONUS TASK
        # -------------------------------------------------

        if data == "task:daily_bonus":

            if task_claimed(
                user_id,
                "daily_bonus"
            ):

                answer_callback(
                    callback_id,
                    "আজকের Daily Bonus Task সম্পন্ন।"
                )

                return

            user = get_user(
                user_id
            )

            today_string = str(
                today()
            )

            if user["last_bonus"] == today_string:

                success = claim_task(
                    user_id,
                    "daily_bonus",
                    "Daily Bonus Task"
                )

                if success:

                    answer_callback(
                        callback_id,
                        "+5 Coins!"
                    )

                else:

                    answer_callback(
                        callback_id,
                        "Task ইতিমধ্যে সম্পন্ন।"
                    )

            else:

                conn = db()
                cur = conn.cursor()

                cur.execute("""
                    UPDATE users
                    SET
                        coins = coins + %s,
                        coins_earned = coins_earned + %s,
                        last_bonus = %s
                    WHERE user_id=%s
                    AND (
                        last_bonus IS NULL
                        OR last_bonus <> %s
                    )
                """, (
                    DAILY_BONUS,
                    DAILY_BONUS,
                    today_string,
                    user_id,
                    today_string
                ))

                changed = cur.rowcount

                conn.commit()

                cur.close()
                conn.close()

                if changed:

                    add_history(
                        user_id,
                        DAILY_BONUS,
                        "Daily bonus"
                    )

                claim_task(
                    user_id,
                    "daily_bonus",
                    "Daily Bonus Task"
                )

                answer_callback(
                    callback_id,
                    "🎁 Daily Bonus +20 + Task +5"
                )

            show_daily_tasks(
                user_id,
                message_id
            )

            return

        # -------------------------------------------------
        # DAILY QUIZ
        # -------------------------------------------------

        if data == "task:daily_quiz":

            if task_claimed(
                user_id,
                "daily_quiz"
            ):

                answer_callback(
                    callback_id,
                    "আজকের Quiz সম্পন্ন।"
                )

                return

            answer_callback(
                callback_id
            )

            show_quiz(
                user_id,
                message_id
            )

            return

        # -------------------------------------------------
        # COIN CHECK-IN
        # -------------------------------------------------

        if data == "task:coin_checkin":

            if task_claimed(
                user_id,
                "coin_checkin"
            ):

                answer_callback(
                    callback_id,
                    "আজকের Check-in সম্পন্ন।"
                )

                return

            success = claim_task(
                user_id,
                "coin_checkin",
                "Daily Coin Check-in"
            )

            if success:

                answer_callback(
                    callback_id,
                    "🪙 +5 Coins!"
                )

                send_message(
                    user_id,
                    "🪙 Coin Check-in Complete!\n\n"
                    "+5 Coins যোগ হয়েছে।"
                )

            else:

                answer_callback(
                    callback_id,
                    "আজকের Check-in ইতিমধ্যে নেওয়া হয়েছে।"
                )

            show_daily_tasks(
                user_id,
                message_id
            )

            return

        # -------------------------------------------------
        # DAILY KNOWLEDGE
        # -------------------------------------------------

        if data == "task:daily_knowledge":

            if task_claimed(
                user_id,
                "daily_knowledge"
            ):

                answer_callback(
                    callback_id,
                    "আজকের Knowledge Task সম্পন্ন।"
                )

                return

            answer_callback(
                callback_id
            )

            show_general_quiz(
                user_id,
                "daily_knowledge",
                "📚 Daily Knowledge",
                KNOWLEDGE_QUESTIONS,
                message_id
            )

            return

        # -------------------------------------------------
        # WORLD FACTS
        # -------------------------------------------------

        if data == "task:world_facts":

            if task_claimed(
                user_id,
                "world_facts"
            ):

                answer_callback(
                    callback_id,
                    "আজকের World Facts Task সম্পন্ন।"
                )

                return

            answer_callback(
                callback_id
            )

            show_general_quiz(
                user_id,
                "world_facts",
                "🌍 World Facts",
                WORLD_FACTS_QUESTIONS,
                message_id
            )

            return

        # -------------------------------------------------
        # MCQ ANSWERS
        # -------------------------------------------------

        if data.startswith("mcq:"):

            try:

                parts = data.split(":")

                task_key = parts[1]
                question_id = int(parts[2])
                selected = int(parts[3])

                if task_key == "daily_knowledge":

                    handle_general_mcq(
                        user_id,
                        callback_id,
                        message_id,
                        task_key,
                        question_id,
                        selected,
                        KNOWLEDGE_QUESTIONS,
                        "📚 Daily Knowledge"
                    )

                elif task_key == "world_facts":

                    handle_general_mcq(
                        user_id,
                        callback_id,
                        message_id,
                        task_key,
                        question_id,
                        selected,
                        WORLD_FACTS_QUESTIONS,
                        "🌍 World Facts"
                    )

                else:

                    answer_callback(
                        callback_id,
                        "Invalid task."
                    )

            except Exception as e:

                print(
                    "MCQ error:",
                    e
                )

                answer_callback(
                    callback_id,
                    "Task-এ সমস্যা হয়েছে।"
                )

            return

        return

    # -----------------------------------------------------
    # NORMAL MESSAGE
    # -----------------------------------------------------

    if "message" not in update:
        return

    message = update["message"]

    if "from" not in message:
        return

    sender = message["from"]

    user_id = sender["id"]

    username = sender.get(
        "username"
    )

    text = message.get(
        "text",
        ""
    ).strip()

    create_user(
        user_id,
        username
    )

    update_last_seen(
        user_id
    )

    user = get_user(user_id)

    if user and user["blocked"]:

        send_message(
            user_id,
            "🚫 Your account is currently blocked."
        )

        return

    # -----------------------------------------------------
    # PROFILE INPUT: PHOTO
    # -----------------------------------------------------

    profile_state = get_profile_state(
        user_id
    )

    if profile_state == "photo":

        photos = message.get(
            "photo"
        )

        if photos:

            largest = photos[-1]

            file_id = largest.get(
                "file_id"
            )

            if file_id:

                conn = db()
                cur = conn.cursor()

                cur.execute("""
                    UPDATE users
                    SET profile_photo_file_id=%s
                    WHERE user_id=%s
                """, (
                    file_id,
                    user_id
                ))

                conn.commit()

                cur.close()
                conn.close()

                clear_profile_state(
                    user_id
                )

                send_message(
                    user_id,
                    "✅ Profile Photo successfully updated!"
                )

                show_profile(
                    user_id
                )

                return

        send_message(
            user_id,
            "📷 একটি Photo পাঠাও।"
        )

        return

    # -----------------------------------------------------
    # PROFILE INPUT: NAME
    # -----------------------------------------------------

    if profile_state == "name":

        if text:

            if len(text) > 40:

                send_message(
                    user_id,
                    "❌ Name সর্বোচ্চ 40 characters হতে পারবে।"
                )

                return

            safe_name = text.strip()

            if not safe_name:

                send_message(
                    user_id,
                    "❌ একটি valid name পাঠাও।"
                )

                return

            conn = db()
            cur = conn.cursor()

            cur.execute("""
                UPDATE users
                SET profile_name=%s
                WHERE user_id=%s
            """, (
                safe_name,
                user_id
            ))

            conn.commit()

            cur.close()
            conn.close()

            clear_profile_state(
                user_id
            )

            send_message(
                user_id,
                "✅ Profile Name successfully updated!"
            )

            show_profile(
                user_id
            )

            return

        send_message(
            user_id,
            "✏️ একটি নাম পাঠাও।"
        )

        return

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    if text.startswith("/start"):

        parts = text.split()

        referred_by = None

        if len(parts) > 1:

            try:
                referred_by = int(
                    parts[1]
                )

            except Exception:
                referred_by = None

        if (
            referred_by
            and referred_by != user_id
        ):

            existing = get_user(
                user_id
            )

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

                cur.execute("""
                    UPDATE users
                    SET referred_by=%s
                    WHERE user_id=%s
                    AND referred_by IS NULL
                """, (
                    referred_by,
                    user_id
                ))

                changed = cur.rowcount

                conn.commit()

                cur.close()
                conn.close()

                if changed:

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
            "🪙 Earn Coins by completing available tasks.\n\n"
            f"💰 {MIN_WITHDRAW:,} Coins = ৳100",
            main_menu()
        )

        return

    # -----------------------------------------------------
    # MY COINS
    # -----------------------------------------------------

    if text == "🪙 My Coins":

        user = get_user(
            user_id
        )

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

    # -----------------------------------------------------
    # MY PROFILE
    # -----------------------------------------------------

    if text == "👤 My Profile":

        show_profile(
            user_id
        )

        return

    # -----------------------------------------------------
    # DAILY TASKS
    # -----------------------------------------------------

    if text == "📋 Daily Tasks":

        show_daily_tasks(
            user_id
        )

        return

    # -----------------------------------------------------
    # WATCH AD
    # -----------------------------------------------------

    if text == "📺 Watch Ad":

        ad = get_adsgram_ad(
            user_id
        )

        if not ad:

            send_message(
                user_id,
                "📺 No ad is available right now.\n\n"
                "Please try again later.",
                main_menu()
            )

            return

        create_pending_ad(
            user_id
        )

        text_html = (
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
                    "text": reward_button_name,
                    "url": reward_url
                }
            ])

        markup = {
            "inline_keyboard": keyboard
        }

        image_url = ad.get(
            "image_url"
        )

        if image_url:

            result = telegram(
                "sendPhoto",
                {
                    "chat_id": user_id,
                    "photo": image_url,
                    "caption": text_html,
                    "parse_mode": "HTML",
                    "reply_markup": markup,
                    "protect_content": True
                }
            )

            if not result or not result.get("ok"):

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

    # -----------------------------------------------------
    # DAILY BONUS
    # -----------------------------------------------------

    if text == "🎁 Daily Bonus":

        user = get_user(
            user_id
        )

        today_string = str(
            today()
        )

        if user["last_bonus"] == today_string:

            send_message(
                user_id,
                "⏳ You already claimed "
                "today's bonus.\n\n"
                "Come back tomorrow.",
                main_menu()
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
            AND (
                last_bonus IS NULL
                OR last_bonus <> %s
            )
        """, (
            DAILY_BONUS,
            DAILY_BONUS,
            today_string,
            user_id,
            today_string
        ))

        changed = cur.rowcount

        conn.commit()

        cur.close()
        conn.close()

        if changed:

            add_history(
                user_id,
                DAILY_BONUS,
                "Daily bonus"
            )

            if not task_claimed(
                user_id,
                "daily_bonus"
            ):

                claim_task(
                    user_id,
                    "daily_bonus",
                    "Daily Bonus Task"
                )

            send_message(
                user_id,
                f"🎁 Daily Bonus Claimed!\n\n"
                f"+{DAILY_BONUS} Coins added.\n"
                f"+{DAILY_TASK_REWARD} Daily Task Coins added.",
                main_menu()
            )

        return

    # -----------------------------------------------------
    # REFERRAL
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # LEADERBOARD
    # -----------------------------------------------------

    if text == "🏆 Leaderboard":

        conn = db()

        cur = conn.cursor(
            cursor_factory=
            psycopg2.extras.RealDictCursor
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

    # -----------------------------------------------------
    # HISTORY
    # -----------------------------------------------------

    if text == "📜 History":

        conn = db()

        cur = conn.cursor(
            cursor_factory=
            psycopg2.extras.RealDictCursor
        )

        cur.execute("""
            SELECT amount, reason, created_at
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

    # -----------------------------------------------------
    # WITHDRAW
    # -----------------------------------------------------

    if text == "💰 Withdraw":

        user = get_user(
            user_id
        )

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
            "You have enough Coins to withdraw ৳100.\n\n"
            "Send one of these:\n\n"
            "bKash: 01XXXXXXXXX\n"
            "Nagad: 01XXXXXXXXX",
            main_menu()
        )

        return

    # -----------------------------------------------------
    # WITHDRAW REQUEST
    # -----------------------------------------------------

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

        if (
            not number.isdigit()
            or not number.startswith("01")
            or len(number) != 11
        ):

            send_message(
                user_id,
                "❌ Invalid payment number.\n\n"
                "Example:\n"
                "bKash: 01XXXXXXXXX",
                main_menu()
            )

            return

        user = get_user(
            user_id
        )

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
                %s, %s, %s, %s, %s,
                'pending', %s
            )
        """, (
            user_id,
            user["username"],
            method_name,
            number,
            MAX_WITHDRAW,
            now()
        ))

        cur.execute("""
            UPDATE users
            SET coins = coins - %s
            WHERE user_id=%s
            AND coins >= %s
        """, (
            MAX_WITHDRAW,
            user_id,
            MAX_WITHDRAW
        ))

        if cur.rowcount != 1:

            conn.rollback()

            cur.close()
            conn.close()

            send_message(
                user_id,
                "❌ Withdrawal failed. "
                "Please try again.",
                main_menu()
            )

            return

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
            f"👤 Username: "
            f"@{user['username'] or 'N/A'}\n"
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
            "Payment will be reviewed manually.",
            main_menu()
        )

        return

    # -----------------------------------------------------
    # FALLBACK
    # -----------------------------------------------------

    send_message(
        user_id,
        "Please choose an option from the menu.",
        main_menu()
    )


# =========================================================
# WEBHOOK
# =========================================================

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

            handle_update(
                update
            )

        return "OK", 200

    except Exception as e:

        print(
            "Webhook error:",
            e
        )

        return "OK", 200


# =========================================================
# ADSGRAM REWARD URL
# =========================================================

@app.route(
    "/reward",
    methods=["GET"]
)
def reward():

    user_id = request.args.get(
        "userid",
        ""
    ).strip()

    try:

        user_id = int(
            user_id
        )

    except Exception:

        return "Invalid user", 400

    success = reward_adsgram_user(
        user_id
    )

    if success:

        return (
            "Reward added successfully. "
            "You can return to the bot.",
            200
        )

    return (
        "Reward is unavailable, "
        "expired, or already claimed.",
        200
    )


# =========================================================
# HOME
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return (
        "Found Coins Bot is running!",
        200
    )


# =========================================================
# ADMIN LOGIN
# =========================================================

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

        if password == ADMIN_PASSWORD:

            session["admin"] = True

            return redirect(
                url_for("admin_dashboard")
            )

        return """
        <html>
        <body>
        <h3>Wrong password</h3>
        <a href="/admin">Try again</a>
        </body>
        </html>
        """

    if session.get("admin"):

        return redirect(
            url_for("admin_dashboard")
        )

    return """
    <!doctype html>
    <html>
    <head>
    <meta name="viewport"
          content="width=device-width,initial-scale=1">
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


# =========================================================
# ADMIN DASHBOARD
# =========================================================

@app.route(
    "/admin/dashboard",
    methods=["GET"]
)
def admin_dashboard():

    if not session.get("admin"):

        return redirect(
            url_for("admin")
        )

    conn = db()

    cur = conn.cursor(
        cursor_factory=
        psycopg2.extras.RealDictCursor
    )

    # -----------------------------------------------------
    # BASIC STATS
    # -----------------------------------------------------

    cur.execute(
        "SELECT COUNT(*) AS total FROM users"
    )

    total_users = cur.fetchone()["total"]

    # Real active users based on recent bot activity
    active_limit = (
        datetime.now()
        - timedelta(minutes=ACTIVE_MINUTES)
    ).strftime("%Y-%m-%d %H:%M:%S")

    cur.execute("""
        SELECT COUNT(*) AS total
        FROM users
        WHERE blocked=0
        AND last_seen IS NOT NULL
        AND last_seen >= %s
    """, (
        active_limit,
    ))

    active_users = cur.fetchone()["total"]

    inactive_users = max(
        total_users - active_users,
        0
    )

    cur.execute("""
        SELECT COALESCE(SUM(coins),0) AS total
        FROM users
    """)

    total_coins = cur.fetchone()["total"]

    cur.execute("""
        SELECT COALESCE(SUM(ads_watched),0) AS total
        FROM users
    """)

    total_ads = cur.fetchone()["total"]

    # -----------------------------------------------------
    # BADGE COUNTS
    # -----------------------------------------------------

    cur.execute("""
        SELECT COUNT(*) AS total
        FROM users
        WHERE badge='🏅 Badge 1'
    """)

    badge1_count = cur.fetchone()["total"]

    cur.execute("""
        SELECT COUNT(*) AS total
        FROM users
        WHERE badge='🥇 Badge 2'
    """)

    badge2_count = cur.fetchone()["total"]

    cur.execute("""
        SELECT COUNT(*) AS total
        FROM users
        WHERE badge='💎 Badge 3'
    """)

    badge3_count = cur.fetchone()["total"]

    cur.execute("""
        SELECT COUNT(*) AS total
        FROM users
        WHERE badge='👑 VIP Badge'
    """)

    vip_count = cur.fetchone()["total"]

    # -----------------------------------------------------
    # USERS
    # -----------------------------------------------------

    cur.execute("""
        SELECT
            user_id,
            username,
            profile_name,
            coins,
            ads_watched,
            coins_earned,
            badge,
            blocked,
            last_seen
        FROM users
        ORDER BY joined_at DESC
        LIMIT 100
    """)

    users = cur.fetchall()

    # -----------------------------------------------------
    # WITHDRAWALS
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # USERS HTML
    # -----------------------------------------------------

    users_html = ""

    for u in users:

        last_seen = u["last_seen"]

        is_active = False

        if last_seen:

            try:

                last_seen_dt = datetime.strptime(
                    last_seen,
                    "%Y-%m-%d %H:%M:%S"
                )

                is_active = (
                    datetime.now() - last_seen_dt
                    <= timedelta(minutes=ACTIVE_MINUTES)
                )

            except Exception:

                is_active = False

        if u["blocked"]:

            status = "🚫 Blocked"

        elif is_active:

            status = "🟢 Active"

        else:

            status = "⚫ Inactive"

        display_name = (
            u["profile_name"]
            or u["username"]
            or "N/A"
        )

        badge = (
            u["badge"]
            or "—"
        )

        users_html += f"""
        <tr>
        <td>{u['user_id']}</td>
        <td>{html.escape(str(display_name))}</td>
        <td>{u['coins']:,}</td>
        <td>{u['ads_watched']:,}</td>
        <td>{u['coins_earned']:,}</td>
        <td>{html.escape(str(badge))}</td>
        <td>{status}</td>
        </tr>
        """

    # -----------------------------------------------------
    # WITHDRAWAL HTML
    # -----------------------------------------------------

    withdrawal_html = ""

    for w in withdrawals:

        actions = ""

        if w["status"] == "pending":

            actions = f"""
            <a href="/admin/withdraw/{w['id']}/approve">
                ✅ Approve
            </a>

            |

            <a href="/admin/withdraw/{w['id']}/reject">
                ❌ Reject
            </a>
            """

        withdrawal_html += f"""
        <tr>
        <td>{w['id']}</td>
        <td>{w['user_id']}</td>
        <td>
            @{html.escape(w['username'] or 'N/A')}
        </td>
        <td>{html.escape(w['method'] or '')}</td>
        <td>{html.escape(w['number'] or '')}</td>
        <td>{w['coins']:,}</td>
        <td>{html.escape(w['status'])}</td>
        <td>{actions}</td>
        </tr>
        """

    # -----------------------------------------------------
    # ADMIN DASHBOARD
    # -----------------------------------------------------

    return f"""
    <!doctype html>

    <html>

    <head>

    <meta name="viewport"
          content="width=device-width,initial-scale=1">

    <title>Found Coins Admin</title>

    <style>

    body {{
        font-family: Arial;
        margin: 20px;
    }}

    table {{
        border-collapse: collapse;
        width: 100%;
        margin-top: 15px;
    }}

    th, td {{
        border: 1px solid #ddd;
        padding: 8px;
        text-align: left;
    }}

    th {{
        background: #f2f2f2;
    }}

    .stats {{
        display: grid;
        grid-template-columns:
            repeat(auto-fit,minmax(150px,1fr));
        gap: 10px;
    }}

    .box {{
        border: 1px solid #ddd;
        padding: 15px;
        border-radius: 8px;
    }}

    .badge-box {{
        border: 1px solid #ddd;
        padding: 15px;
        border-radius: 8px;
        margin-top: 10px;
    }}

    .active {{
        color: green;
        font-weight: bold;
    }}

    .inactive {{
        color: #666;
        font-weight: bold;
    }}

    </style>

    </head>

    <body>

    <h2>🛠️ Found Coins Admin Panel</h2>

    <p>
    <a href="/admin/logout">
        Logout
    </a>
    </p>

    <div class="stats">

        <div class="box">
            👥 Total Users<br>
            <b>{total_users:,}</b>
        </div>

        <div class="box">
            🟢 Active Users<br>
            <b>{active_users:,}</b>
        </div>

        <div class="box">
            ⚫ Inactive Users<br>
            <b>{inactive_users:,}</b>
        </div>

        <div class="box">
            🪙 Total Coins<br>
            <b>{total_coins:,}</b>
        </div>

        <div class="box">
            📺 Ads Watched<br>
            <b>{total_ads:,}</b>
        </div>

    </div>

    <h2>🏅 Active Badges</h2>

    <div class="stats">

        <div class="badge-box">
            🏅 Badge 1<br>
            <b>{badge1_count:,} Users</b>
        </div>

        <div class="badge-box">
            🥇 Badge 2<br>
            <b>{badge2_count:,} Users</b>
        </div>

        <div class="badge-box">
            💎 Badge 3<br>
            <b>{badge3_count:,} Users</b>
        </div>

        <div class="badge-box">
            👑 VIP Badge<br>
            <b>{vip_count:,} Users</b>
        </div>

    </div>

    <p>
        🟢 Active = last interaction within
        {ACTIVE_MINUTES} minutes.
    </p>

    <h2>👥 Users</h2>

    <table>

    <tr>
        <th>ID</th>
        <th>Name</th>
        <th>Coins</th>
        <th>Ads</th>
        <th>Earned</th>
        <th>Badge</th>
        <th>Status</th>
    </tr>

    {users_html}

    </table>

    <h2>💰 Withdrawals</h2>

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

    {withdrawal_html}

    </table>

    </body>
    </html>
    """


# =========================================================
# ADMIN WITHDRAW APPROVE
# =========================================================

@app.route(
    "/admin/withdraw/<int:withdrawal_id>/approve"
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
        UPDATE withdrawals
        SET status='approved'
        WHERE id=%s
        AND status='pending'
        RETURNING user_id
    """, (
        withdrawal_id,
    ))

    row = cur.fetchone()

    conn.commit()

    cur.close()
    conn.close()

    if row:

        send_message(
            row["user_id"],
            "✅ Withdrawal Approved!\n\n"
            "Your payment has been approved "
            "by the admin."
        )

    return redirect(
        url_for("admin_dashboard")
    )


# =========================================================
# ADMIN WITHDRAW REJECT
# =========================================================

@app.route(
    "/admin/withdraw/<int:withdrawal_id>/reject"
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

    try:

        cur.execute("""
            SELECT
                user_id,
                coins
            FROM withdrawals
            WHERE id=%s
            AND status='pending'
            FOR UPDATE
        """, (
            withdrawal_id,
        ))

        row = cur.fetchone()

        if not row:

            conn.rollback()

            cur.close()
            conn.close()

            return redirect(
                url_for("admin_dashboard")
            )

        cur.execute("""
            UPDATE withdrawals
            SET status='rejected'
            WHERE id=%s
            AND status='pending'
        """, (
            withdrawal_id,
        ))

        if cur.rowcount != 1:

            conn.rollback()

            cur.close()
            conn.close()

            return redirect(
                url_for("admin_dashboard")
            )

        cur.execute("""
            UPDATE users
            SET coins = coins + %s
            WHERE user_id=%s
        """, (
            row["coins"],
            row["user_id"]
        ))

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
            row["user_id"],
            row["coins"],
            "Withdrawal rejected - coins returned",
            now()
        ))

        conn.commit()

    except Exception:

        conn.rollback()

        raise

    finally:

        cur.close()
        conn.close()

    send_message(
        row["user_id"],
        "❌ Withdrawal Rejected.\n\n"
        f"{row['coins']:,} Coins "
        "have been returned to your balance."
    )

    return redirect(
        url_for("admin_dashboard")
    )


# =========================================================
# ADMIN LOGOUT
# =========================================================

@app.route(
    "/admin/logout"
)
def admin_logout():

    session.clear()

    return redirect(
        url_for("admin")
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
