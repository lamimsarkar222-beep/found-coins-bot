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

# AdsGram Block ID from your dashboard
ADSGRAM_BLOCK_ID = "49050"
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

DAILY_TASK_REWARD = 10

MIN_WITHDRAW = 10000
MAX_WITHDRAW = 10000

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

    # NEW: daily task claims
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_task_claims (
            user_id BIGINT NOT NULL,
            task_date DATE NOT NULL,
            task_key TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            PRIMARY KEY (user_id, task_date, task_key)
        )
    """)

    # NEW: quiz attempts
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
            blocked
        )
        VALUES (%s, %s, 0, 0, 0, NULL, %s, %s, 0)

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
                    "text": "👥 Referral"
                },
                {
                    "text": "💰 Withdraw"
                }
            ],
            [
                {
                    "text": "🏆 Leaderboard"
                },
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


def daily_tasks_keyboard(user_id):

    bonus_done = task_claimed(
        user_id,
        "daily_bonus"
    )

    quiz_done = task_claimed(
        user_id,
        "daily_quiz"
    )

    checkin_done = task_claimed(
        user_id,
        "coin_checkin"
    )

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "✅ Daily Bonus — 10"
                        if bonus_done
                        else "🎁 Daily Bonus — 10",
                    "callback_data":
                        "task_bonus"
                }
            ],
            [
                {
                    "text":
                        "✅ Daily Quiz — 10"
                        if quiz_done
                        else "🎯 Daily Quiz — 10",
                    "callback_data":
                        "task_quiz"
                }
            ],
            [
                {
                    "text":
                        "✅ Coin Check-in — 10"
                        if checkin_done
                        else "🪙 Coin Check-in — 10",
                    "callback_data":
                        "task_checkin"
                }
            ]
        ]
    }


def show_daily_tasks(user_id, message_id=None):

    done = 0

    if task_claimed(user_id, "daily_bonus"):
        done += 1

    if task_claimed(user_id, "daily_quiz"):
        done += 1

    if task_claimed(user_id, "coin_checkin"):
        done += 1

    text = (
        "📋 Daily Tasks\n\n"
        "Complete all 3 tasks every day.\n\n"
        f"✅ Completed: {done}/3\n"
        f"🏆 Daily Task Reward: {done * DAILY_TASK_REWARD}/30 Points\n\n"
        "1️⃣ 🎁 Daily Bonus Claim — 10 Points\n"
        "2️⃣ 🎯 Daily Quiz — 10 Points\n"
        "3️⃣ 🪙 Coin Check-in — 10 Points\n\n"
        "Complete all 3 to earn 30 Points today."
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
# DAILY QUIZ
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
        "question": "১ টাকার কয়েনের গায়ে কোন দেশের নাম থাকে?",
        "options": [
            "বাংলাদেশ",
            "ভারত",
            "নেপাল",
            "ভুটান"
        ],
        "answer": 0
    },

    {
        "question": "কোনটি একটি মুদ্রার একক?",
        "options": [
            "টাকা",
            "মিটার",
            "লিটার",
            "কিলোগ্রাম"
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


def todays_quiz():

    index = today().toordinal() % len(
        QUIZ_QUESTIONS
    )

    return index, QUIZ_QUESTIONS[index]


def show_quiz(user_id, message_id=None):

    question_id, quiz = todays_quiz()

    keyboard = []

    for index, option in enumerate(
        quiz["options"]
    ):

        keyboard.append([
            {
                "text": option,
                "callback_data":
                    f"quiz:{question_id}:{index}"
            }
        ])

    text = (
        "🎯 Daily Quiz\n\n"
        f"{quiz['question']}\n\n"
        "সঠিক উত্তর নির্বাচন করো।\n"
        "সঠিক হলে ১০ পয়েন্ট পাবে।"
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


def handle_quiz_answer(
    user_id,
    callback_id,
    message_id,
    question_id,
    selected
):

    real_id, quiz = todays_quiz()

    if question_id != real_id:

        answer_callback(
            callback_id,
            "এই কুইজটি আর সক্রিয় নেই।"
        )

        return

    if task_claimed(
        user_id,
        "daily_quiz"
    ):

        answer_callback(
            callback_id,
            "আজকের কুইজ ইতিমধ্যে সম্পন্ন হয়েছে।"
        )

        show_daily_tasks(
            user_id,
            message_id
        )

        return

    correct = (
        selected == quiz["answer"]
    )

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
            1 if correct else 0,
            now()
        ))

        inserted = cur.rowcount == 1

        conn.commit()

    except Exception:

        conn.rollback()
        inserted = False

    finally:

        cur.close()
        conn.close()

    if not inserted:

        answer_callback(
            callback_id,
            "আজকের কুইজ ইতিমধ্যে দেওয়া হয়েছে।"
        )

        return

    if correct:

        claim_task(
            user_id,
            "daily_quiz",
            "Daily Quiz"
        )

        answer_callback(
            callback_id,
            "🎉 সঠিক! +10 Points"
        )

        send_message(
            user_id,
            "🎉 সঠিক উত্তর!\n\n"
            "🎯 Daily Quiz সম্পন্ন হয়েছে।\n"
            "+10 Points যোগ হয়েছে।"
        )

    else:

        answer_callback(
            callback_id,
            "❌ ভুল উত্তর।"
        )

        send_message(
            user_id,
            "❌ উত্তরটি সঠিক হয়নি।\n\n"
            "আজকের কুইজের জন্য পয়েন্ট পাওয়া যাবে না।"
        )

    show_daily_tasks(user_id)


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

        # AdsGram reward flow needs reward_url
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

        # Remove pending FIRST inside the same transaction.
        # This prevents double reward.
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

        # Daily Bonus
        if data == "task_bonus":

            if task_claimed(
                user_id,
                "daily_bonus"
            ):

                answer_callback(
                    callback_id,
                    "আজকের Daily Bonus ইতিমধ্যে নেওয়া হয়েছে।"
                )

                return

            # Claim the existing Daily Bonus first
            user = get_user(user_id)

            today_string = str(today())

            if user["last_bonus"] == today_string:

                claim_task(
                    user_id,
                    "daily_bonus",
                    "Daily Bonus Task"
                )

                answer_callback(
                    callback_id,
                    "Daily Bonus আগে নেওয়া হয়েছে। Task complete!"
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
                        "🎁 Bonus +20 এবং Task +10!"
                    )

                    send_message(
                        user_id,
                        "🎁 Daily Bonus Claimed!\n\n"
                        f"+{DAILY_BONUS} Coins\n"
                        "+10 Daily Task Points"
                    )

                else:

                    claim_task(
                        user_id,
                        "daily_bonus",
                        "Daily Bonus Task"
                    )

                    answer_callback(
                        callback_id,
                        "Task সম্পন্ন হয়েছে।"
                    )

            show_daily_tasks(
                user_id,
                message_id
            )

            return

        # Daily Quiz
        if data == "task_quiz":

            if task_claimed(
                user_id,
                "daily_quiz"
            ):

                answer_callback(
                    callback_id,
                    "আজকের Quiz ইতিমধ্যে সম্পন্ন।"
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

        # Coin Check-in
        if data == "task_checkin":

            if task_claimed(
                user_id,
                "coin_checkin"
            ):

                answer_callback(
                    callback_id,
                    "আজকের Check-in ইতিমধ্যে সম্পন্ন।"
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
                    "🪙 +10 Points!"
                )

                send_message(
                    user_id,
                    "🪙 Coin Check-in Complete!\n\n"
                    "+10 Points যোগ হয়েছে।"
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

        # Quiz answer
        if data.startswith("quiz:"):

            try:

                parts = data.split(":")

                question_id = int(
                    parts[1]
                )

                selected = int(
                    parts[2]
                )

                handle_quiz_answer(
                    user_id,
                    callback_id,
                    message_id,
                    question_id,
                    selected
                )

            except Exception:

                answer_callback(
                    callback_id,
                    "কুইজে সমস্যা হয়েছে। আবার চেষ্টা করুন।"
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

    user = get_user(user_id)

    if user and user["blocked"]:

        send_message(
            user_id,
            "🚫 Your account is currently blocked."
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

        # Pending reward is created BEFORE sending
        # the advertisement.
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

            # Automatically completes Daily Bonus task
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
                f"+{DAILY_TASK_REWARD} Daily Task Points added.",
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

        # Always return 200 so Telegram
        # does not repeatedly retry.
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

    # Stats
    cur.execute(
        "SELECT COUNT(*) AS total FROM users"
    )

    total_users = cur.fetchone()["total"]

    cur.execute("""
        SELECT COUNT(*) AS total
        FROM users
        WHERE blocked=0
    """)

    active_users = cur.fetchone()["total"]

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

    # Users
    cur.execute("""
        SELECT
            user_id,
            username,
            coins,
            ads_watched,
            coins_earned,
            blocked
        FROM users
        ORDER BY joined_at DESC
        LIMIT 100
    """)

    users = cur.fetchall()

    # Withdrawals
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

    users_html = ""

    for u in users:

        users_html += f"""
        <tr>
        <td>{u['user_id']}</td>
        <td>
            @{html.escape(u['username'] or 'N/A')}
        </td>
        <td>{u['coins']:,}</td>
        <td>{u['ads_watched']:,}</td>
        <td>{u['coins_earned']:,}</td>
        <td>{'Blocked' if u['blocked'] else 'Active'}</td>
        </tr>
        """

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
            🪙 Total Coins<br>
            <b>{total_coins:,}</b>
        </div>

        <div class="box">
            📺 Ads Watched<br>
            <b>{total_ads:,}</b>
        </div>

    </div>

    <h2>👥 Users</h2>

    <table>

    <tr>
        <th>ID</th>
        <th>Username</th>
        <th>Coins</th>
        <th>Ads</th>
        <th>Earned</th>
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
