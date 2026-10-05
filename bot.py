import os,html,hashlib
from datetime import datetime,date,timedelta
import psycopg2,psycopg2.extras,requests
from flask import Flask,request,session,redirect

TOKEN=os.environ['BOT_TOKEN']
DBURL=os.environ['DATABASE_URL']
ADMIN_ID=7926491409
ADMIN_PASSWORD=os.environ.get('ADMIN_PASSWORD','')
ADS_TOKEN=os.environ.get('ADSGRAM_TOKEN','')
BLOCK_ID='49050'

MIN_W=MAX_W=10000
API=f'https://api.telegram.org/bot{TOKEN}'

app=Flask(__name__)
app.secret_key=os.environ.get(
    'FLASK_SECRET_KEY',
    os.environ.get('SECRET_KEY','found-coins-session')
)

def db():
    return psycopg2.connect(DBURL)

def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def today():
    return date.today().isoformat()

def q(sql,args=(),dictcur=False,fetch=False):
    c=db()
    cur=c.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
        if dictcur else None
    )
    cur.execute(sql,args)
    r=cur.fetchall() if fetch else cur.rowcount
    c.commit()
    cur.close()
    c.close()
    return r

def init():
    c=db()
    cur=c.cursor()

    tables=[
        """CREATE TABLE IF NOT EXISTS users(
        user_id BIGINT PRIMARY KEY,
        username TEXT,
        coins INTEGER DEFAULT 0,
        ads_watched INTEGER DEFAULT 0,
        coins_earned INTEGER DEFAULT 0,
        last_bonus TEXT,
        referred_by BIGINT,
        joined_at TEXT,
        blocked INTEGER DEFAULT 0)""",

        """CREATE TABLE IF NOT EXISTS history(
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT,
        amount INTEGER,
        reason TEXT,
        created_at TEXT)""",

        """CREATE TABLE IF NOT EXISTS withdrawals(
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT,
        username TEXT,
        method TEXT,
        number TEXT,
        coins INTEGER,
        status TEXT DEFAULT 'pending',
        created_at TEXT)""",

        """CREATE TABLE IF NOT EXISTS pending_ads(
        user_id BIGINT PRIMARY KEY,
        created_at TEXT)""",

        """CREATE TABLE IF NOT EXISTS bot_settings(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL)""",

        """CREATE TABLE IF NOT EXISTS daily_task_claims(
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        task_key TEXT NOT NULL,
        claim_date TEXT NOT NULL,
        reward INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(user_id,task_key,claim_date))""",

        """CREATE TABLE IF NOT EXISTS quiz_attempts(
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        question_key TEXT NOT NULL,
        attempt_date TEXT NOT NULL,
        correct INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        UNIQUE(user_id,attempt_date))""",

        """CREATE TABLE IF NOT EXISTS user_stats(
        user_id BIGINT PRIMARY KEY,
        task_completed INTEGER DEFAULT 0,
        daily_streak INTEGER DEFAULT 0,
        last_checkin TEXT)""",

        """CREATE TABLE IF NOT EXISTS shop_items(
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        price INTEGER NOT NULL,
        active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL)""",

        """CREATE TABLE IF NOT EXISTS shop_purchases(
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        item_id BIGINT NOT NULL,
        item_name TEXT NOT NULL,
        price INTEGER NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL,
        processed_at TEXT)"""
    ]

    for s in tables:
        cur.execute(s)

    for k,v in {
        'daily_bonus':5,
        'ad_reward':50,
        'referral_bonus':100,
        'task_reward':5
    }.items():
        cur.execute(
            "INSERT INTO bot_settings(key,value) "
            "VALUES(%s,%s) "
            "ON CONFLICT(key) DO NOTHING",
            (k,str(v))
        )

    cur.execute('SELECT COUNT(*) FROM shop_items')

    if cur.fetchone()[0]==0:
        for x in [
            ('🥉 Bronze Badge','Virtual badge',500),
            ('🥈 Silver Badge','Virtual badge',1000),
            ('🥇 Gold Badge','Virtual badge',2000),
            ('👑 VIP Badge','Special badge',5000)
        ]:
            cur.execute(
                'INSERT INTO shop_items'
                '(name,description,price,active,created_at) '
                'VALUES(%s,%s,%s,1,%s)',
                (*x,now())
            )

    c.commit()
    cur.close()
    c.close()

init()

def user(uid):
    r=q(
        'SELECT * FROM users WHERE user_id=%s',
        (uid,),
        True,
        True
    )
    return r[0] if r else None

def ensure(uid,uname=None):
    q(
        'INSERT INTO users(user_id,username,joined_at) '
        'VALUES(%s,%s,%s) '
        'ON CONFLICT(user_id) '
        'DO UPDATE SET username=EXCLUDED.username',
        (uid,uname,now())
    )

    q(
        'INSERT INTO user_stats(user_id) VALUES(%s) '
        'ON CONFLICT(user_id) DO NOTHING',
        (uid,)
    )

def hist(uid,amt,reason):
    q(
        'INSERT INTO history'
        '(user_id,amount,reason,created_at) '
        'VALUES(%s,%s,%s,%s)',
        (uid,amt,reason,now())
    )

def add(uid,amt,reason):
    r=q(
        'UPDATE users '
        'SET coins=coins+%s,'
        'coins_earned=CASE '
        'WHEN %s>0 THEN coins_earned+%s '
        'ELSE coins_earned END '
        'WHERE user_id=%s',
        (amt,amt,amt,uid)
    )

    if isinstance(r,int) and r>0:
        hist(uid,amt,reason)

def setting(k,d):
    r=q(
        'SELECT value FROM bot_settings WHERE key=%s',
        (k,),
        False,
        True
    )

    try:
        return int(r[0][0]) if r else d
    except:
        return d

def setsetting(k,v):
    q(
        'INSERT INTO bot_settings(key,value) '
        'VALUES(%s,%s) '
        'ON CONFLICT(key) '
        'DO UPDATE SET value=EXCLUDED.value',
        (k,str(max(0,int(v))))
    )

def tg(method,data):
    try:
        return requests.post(
            f'{API}/{method}',
            json=data,
            timeout=20
        ).json()
    except Exception as e:
        print('TG:',e)
        return None

def send(uid,text,markup=None,protect=False,parse=None):
    d={
        'chat_id':uid,
        'text':text
    }

    if markup:
        d['reply_markup']=markup

    if protect:
        d['protect_content']=True

    if parse:
        d['parse_mode']=parse

    return tg('sendMessage',d)

def answer(cid,text=''):
    return tg(
        'answerCallbackQuery',
        {
            'callback_query_id':cid,
            'text':text
        }
    )

def menu():
    return {
        'keyboard':[
            [
                {'text':'🪙 My Coins'},
                {'text':'📺 Watch Ad'}
            ],
            [
                {'text':'📋 Daily Tasks'},
                {'text':'👤 My Profile'}
            ],
            [
                {'text':'🎁 Daily Bonus'},
                {'text':'👥 Referral'}
            ],
            [
                {'text':'🛍️ Coin Shop'},
                {'text':'💰 Withdraw'}
            ],
            [
                {'text':'🏆 Leaderboard'},
                {'text':'📜 History'}
            ]
        ],
        'resize_keyboard':True
    }

# =========================
# ADSGRAM
# =========================

def ad(uid):
    if not ADS_TOKEN:
        return None

    try:
        r=requests.get(
            'https://api.adsgram.ai/advbot',
            params={
                'tgid':uid,
                'blockid':BLOCK_ID,
                'language':'en',
                'token':ADS_TOKEN
            },
            timeout=20
        )

        return r.json() if r.status_code==200 else None

    except:
        return None

def pending(uid):
    q(
        'INSERT INTO pending_ads(user_id,created_at) '
        'VALUES(%s,%s) '
        'ON CONFLICT(user_id) '
        'DO UPDATE SET created_at=EXCLUDED.created_at',
        (uid,now())
    )

def consume(uid):
    r=q(
        'SELECT created_at FROM pending_ads WHERE user_id=%s',
        (uid,),
        True,
        True
    )

    if not r:
        return False

    try:
        created=datetime.strptime(
            r[0]['created_at'],
            '%Y-%m-%d %H:%M:%S'
        )

        if datetime.now()-created>timedelta(hours=1):
            q(
                'DELETE FROM pending_ads WHERE user_id=%s',
                (uid,)
            )
            return False

    except:
        return False

    q(
        'DELETE FROM pending_ads WHERE user_id=%s',
        (uid,)
    )

    return True

# =========================
# DAILY TASKS
# =========================

TASKS={
    'bonus':'🎁 Daily Bonus',
    'quiz':'🎯 Daily Quiz',
    'checkin':'🪙 Coin Check-in',
    'knowledge':'🌍 Daily Knowledge',
    'world':'☀️ World Facts'
}

QUESTIONS=[
('q1','What is the capital of Bangladesh?',
 ['Dhaka','Sylhet','Rajshahi','Khulna'],0),

('q2','Which planet is called the Red Planet?',
 ['Mars','Venus','Jupiter','Mercury'],0),

('q3','How many days are in a leap year?',
 ['366','365','364','360'],0),

('q4','Which is the largest ocean?',
 ['Pacific','Atlantic','Indian','Arctic'],0),

('q5','How many continents are there?',
 ['7','5','6','8'],0),

('q6','What is H2O?',
 ['Water','Oxygen','Hydrogen','Salt'],0),

('q7','Which animal is the fastest on land?',
 ['Cheetah','Tiger','Horse','Lion'],0),

('q8','Which country is famous for Mount Fuji?',
 ['Japan','China','India','Nepal'],0),

('q9','Which country has the Taj Mahal?',
 ['India','Pakistan','Nepal','Sri Lanka'],0),

('q10','Which country is famous for the Eiffel Tower?',
 ['France','Italy','Spain','Germany'],0),

('q11','What is 10 + 15?',
 ['25','20','30','35'],0),

('q12','Which organ pumps blood?',
 ['Heart','Liver','Lung','Kidney'],0),

('q13','How many minutes are in an hour?',
 ['60','30','90','100'],0),

('q14','Which planet is closest to the Sun?',
 ['Mercury','Venus','Earth','Mars'],0),

('q15','Which is the largest mammal?',
 ['Blue whale','Elephant','Giraffe','Hippo'],0),

('q16','Which country is famous for kangaroos?',
 ['Australia','Brazil','Canada','Kenya'],0),

('q17','What is the freezing point of water?',
 ['0°C','10°C','32°C','100°C'],0),

('q18','Which instrument has black and white keys?',
 ['Piano','Flute','Drum','Guitar'],0),

('q19','Which country is famous for the pyramids of Giza?',
 ['Egypt','Greece','Turkey','Jordan'],0),

('q20','Which planet do we live on?',
 ['Earth','Mars','Venus','Jupiter'],0)
]

FACTS=[
'🌍 Bangladesh is in South Asia.',
'🌊 The Pacific Ocean is the largest ocean on Earth.',
'☀️ The Sun is a star.',
'🌙 The Moon is Earth’s natural satellite.',
'🐘 Elephants are the largest land animals.',
'🐝 Bees help pollinate many plants.',
'🌳 Trees absorb carbon dioxide.',
'💧 Water is essential for life.',
'🗺️ Asia is the largest continent.',
'🦒 Giraffes can reach leaves high in trees.'
]

def claimed(uid,k):
    return bool(
        q(
            'SELECT 1 FROM daily_task_claims '
            'WHERE user_id=%s AND task_key=%s AND claim_date=%s',
            (uid,k,today()),
            False,
            True
        )
    )

def claim(uid,k):
    if k not in TASKS or claimed(uid,k):
        return False

    r=setting('task_reward',5)

    try:
        q(
            'INSERT INTO daily_task_claims'
            '(user_id,task_key,claim_date,reward,created_at) '
            'VALUES(%s,%s,%s,%s,%s)',
            (uid,k,today(),r,now())
        )

        q(
            'UPDATE user_stats '
            'SET task_completed=task_completed+1 '
            'WHERE user_id=%s',
            (uid,)
        )

        add(
            uid,
            r,
            f'Daily Task: {TASKS[k]}'
        )

        return True

    except:
        return False

def question(uid):
    used={
        x['question_key']
        for x in q(
            'SELECT question_key FROM quiz_attempts '
            'WHERE user_id=%s',
            (uid,),
            True,
            True
        )
    }

    available=[
        x for x in QUESTIONS
        if x[0] not in used
    ] or QUESTIONS

    i=int(
        hashlib.sha256(
            f'{uid}:{today()}'.encode()
        ).hexdigest(),
        16
    )%len(available)

    return available[i]

def tasktext(uid):
    r=setting('task_reward',5)

    return (
        '📋 Daily Tasks\n\n'
        +
        '\n'.join(
            ('✅' if claimed(uid,k) else '⬜')
            +
            f' {v} — {r} Coins'
            for k,v in TASKS.items()
        )
        +
        f'\n\n💰 Maximum: {r*5} Coins/day'
    )

def taskmark():
    return {
        'inline_keyboard':[
            [
                {
                    'text':'🎁 Daily Bonus',
                    'callback_data':'task:bonus'
                }
            ],
            [
                {
                    'text':'🎯 Daily Quiz',
                    'callback_data':'task:quiz'
                }
            ],
            [
                {
                    'text':'🪙 Coin Check-in',
                    'callback_data':'task:checkin'
                }
            ],
            [
                {
                    'text':'🌍 Daily Knowledge',
                    'callback_data':'task:knowledge'
                }
            ],
            [
                {
                    'text':'☀️ World Facts',
                    'callback_data':'task:world'
                }
            ]
        ]
    }

def profile(uid):
    u=user(uid)

    s=q(
        'SELECT task_completed,daily_streak '
        'FROM user_stats WHERE user_id=%s',
        (uid,),
        False,
        True
    )

    s=s[0] if s else {
        'task_completed':0,
        'daily_streak':0
    }

    return (
        f"👤 My Profile\n\n"
        f"🆔 {uid}\n"
        f"🪙 Coins: {u['coins']:,}\n"
        f"📺 Ads: {u['ads_watched']:,}\n"
        f"📋 Tasks: {s['task_completed']:,}\n"
        f"🔥 Streak: {s['daily_streak']:,}\n"
        f"📈 Earned: {u['coins_earned']:,}"
    )

# =========================
# SHOP
# =========================

def shop():
    return q(
        'SELECT * FROM shop_items '
        'WHERE active=1 ORDER BY id',
        (),
        True,
        True
    )

def shopmark():
    return {
        'inline_keyboard':[
            [
                {
                    'text':f"{x['name']} — {x['price']:,}",
                    'callback_data':f"shop:{x['id']}"
                }
            ]
            for x in shop()
        ]
    }

# =========================
# CALLBACKS
# =========================

def callback(u):
    c=u['callback_query']
    uid=c['from']['id']
    data=c.get('data','')

    ensure(
        uid,
        c['from'].get('username')
    )

    answer(c['id'])

    if user(uid)['blocked']:
        return

    if data.startswith('task:'):

        k=data.split(':',1)[1]

        if k=='bonus':

            if user(uid)['last_bonus']!=today():

                r=setting(
                    'daily_bonus',
                    5
                )

                q(
                    'UPDATE users '
                    'SET coins=coins+%s,'
                    'coins_earned=coins_earned+%s,'
                    'last_bonus=%s '
                    'WHERE user_id=%s',
                    (r,r,today(),uid)
                )

                hist(
                    uid,
                    r,
                    'Daily bonus'
                )

                claim(uid,'bonus')

                send(
                    uid,
                    f'🎁 Daily Bonus Claimed!\n\n+{r} Coins added.'
                )

            else:
                claim(uid,'bonus')
                send(
                    uid,
                    "⏳ Today's bonus is already claimed."
                )

            send(
                uid,
                tasktext(uid),
                taskmark()
            )

            return

        if k=='quiz':

            if claimed(uid,'quiz'):
                send(
                    uid,
                    "🎯 Today's quiz is already completed."
                )
                return

            z=question(uid)

            send(
                uid,
                '🎯 Daily Quiz\n\n'+z[1],
                {
                    'inline_keyboard':[
                        [
                            {
                                'text':a,
                                'callback_data':
                                f'quiz:{z[0]}:{i}'
                            }
                        ]
                        for i,a in enumerate(z[2])
                    ]
                }
            )

            return

        if k=='checkin':

            if claimed(uid,'checkin'):
                send(
                    uid,
                    "🪙 Today's check-in is already completed."
                )
                return

            s=q(
                'SELECT last_checkin,daily_streak '
                'FROM user_stats WHERE user_id=%s',
                (uid,),
                True,
                True
            )

            s=s[0] if s else {
                'last_checkin':None,
                'daily_streak':0
            }

            yesterday=(
                date.today()-timedelta(1)
            ).isoformat()

            st=(
                s['daily_streak']+1
                if s['last_checkin']==yesterday
                else 1
            )

            q(
                'UPDATE user_stats '
                'SET last_checkin=%s,daily_streak=%s '
                'WHERE user_id=%s',
                (today(),st,uid)
            )

            claim(uid,'checkin')

            send(
                uid,
                f'🪙 Check-in complete!\n🔥 Streak: {st} day(s)'
            )

            send(
                uid,
                tasktext(uid),
                taskmark()
            )

            return

        if k in ('knowledge','world'):

            if claimed(uid,k):
                send(
                    uid,
                    "⏳ Today's task is already completed."
                )
                return

            f=FACTS[
                int(
                    hashlib.sha256(
                        f'{uid}:{today()}:{k}'.encode()
                    ).hexdigest(),
                    16
                )%len(FACTS)
            ]

            claim(uid,k)

            send(
                uid,
                (
                    '🌍 Daily Knowledge'
                    if k=='knowledge'
                    else '☀️ World Facts'
                )
                +
                '\n\n'+f+
                '\n\n✅ Task completed.',
                taskmark()
            )

            return

    if data.startswith('quiz:'):

        _,key,ans=data.split(':')
        ans=int(ans)

        z=next(
            (x for x in QUESTIONS if x[0]==key),
            None
        )

        if (
            claimed(uid,'quiz')
            or
            q(
                'SELECT 1 FROM quiz_attempts '
                'WHERE user_id=%s AND attempt_date=%s',
                (uid,today()),
                False,
                True
            )
        ):
            send(
                uid,
                "🎯 Today's quiz is already completed."
            )
            return

        if not z:
            return

        ok=int(ans==z[3])

        q(
            'INSERT INTO quiz_attempts'
            '(user_id,question_key,attempt_date,correct,created_at) '
            'VALUES(%s,%s,%s,%s,%s)',
            (uid,key,today(),ok,now())
        )

        if ok:
            claim(uid,'quiz')

            send(
                uid,
                f"🎉 Correct! +{setting('task_reward',5)} Coins"
            )
        else:
            send(
                uid,
                f"❌ Wrong answer. "
                f"Correct answer: {z[2][z[3]]}"
            )

        send(
            uid,
            tasktext(uid),
            taskmark()
        )

        return

    if data.startswith('shop:'):

        iid=int(data.split(':')[1])

        r=q(
            'SELECT * FROM shop_items '
            'WHERE id=%s AND active=1',
            (iid,),
            True,
            True
        )

        item=r[0] if r else None

        if not item:
            send(uid,'❌ Item unavailable.')
            return

        if user(uid)['coins']<item['price']:
            send(uid,'❌ Not enough Coins.')
            return

        changed=q(
            'UPDATE users '
            'SET coins=coins-%s '
            'WHERE user_id=%s AND coins>=%s',
            (
                item['price'],
                uid,
                item['price']
            )
        )

        if changed!=1:
            send(
                uid,
                '❌ Balance changed. Please try again.'
            )
            return

        r=q(
            "INSERT INTO shop_purchases"
            "(user_id,item_id,item_name,price,status,created_at) "
            "VALUES(%s,%s,%s,%s,'pending',%s) "
            "RETURNING id",
            (
                uid,
                iid,
                item['name'],
                item['price'],
                now()
            ),
            True,
            True
        )

        pid=r[0]['id']

        hist(
            uid,
            -item['price'],
            f"Shop purchase pending: {item['name']}"
        )

        send(
            uid,
            f'🛍️ Purchase #{pid} submitted.\n'
            'Coins are reserved until admin decision.'
        )

        send(
            ADMIN_ID,
            f"🛍️ NEW SHOP PURCHASE #{pid}\n"
            f"User: {uid}\n"
            f"Item: {item['name']}\n"
            f"Price: {item['price']:,} Coins"
        )

        return

# =========================
# NORMAL MESSAGES
# =========================

def message(u):

    m=u['message']
    s=m.get('from',{})
    uid=s.get('id')
    t=(m.get('text') or '').strip()

    if not uid:
        return

    ensure(
        uid,
        s.get('username')
    )

    U=user(uid)

    if U['blocked']:
        send(
            uid,
            '🚫 Your account is currently blocked.'
        )
        return

    if t.startswith('/start'):

        p=t.split()

        if len(p)>1:
            try:
                ref=int(p[1])
                r=user(ref)

                if (
                    ref!=uid
                    and r
                    and U['referred_by'] is None
                    and U['coins']==0
                    and U['ads_watched']==0
                ):
                    q(
                        'UPDATE users '
                        'SET referred_by=%s '
                        'WHERE user_id=%s',
                        (ref,uid)
                    )

                    bonus=setting(
                        'referral_bonus',
                        100
                    )

                    add(
                        ref,
                        bonus,
                        'Referral bonus'
                    )

                    send(
                        ref,
                        f'🎉 Referral Bonus! '
                        f'+{bonus} Coins'
                    )

            except:
                pass

        send(
            uid,
            '🎁 Welcome to Found Coins!\n\n'
            '🪙 Earn Coins through tasks, quizzes, '
            'check-ins, referrals and optional ads.\n\n'
            '💰 10,000 Coins = ৳100',
            menu()
        )

        return

    if t=='🪙 My Coins':

        send(
            uid,
            f"🪙 Your Coins\n\n"
            f"💰 Balance: {U['coins']:,}\n"
            f"💵 Value: ৳{U['coins']/100:.2f}\n"
            f"📺 Ads Watched: {U['ads_watched']:,}\n"
            f"📈 Total Earned: {U['coins_earned']:,}",
            menu()
        )

        return

    if t=='📺 Watch Ad':

        a=ad(uid)

        if not a:
            send(
                uid,
                '📺 No ad is available right now.\n\n'
                'Please try again later.',
                menu()
            )
            return

        pending(uid)

        k=[]

        if a.get('click_url'):
            k.append([
                {
                    'text':a.get(
                        'button_name',
                        '▶️ Open Ad'
                    ),
                    'url':a['click_url']
                }
            ])

        if a.get('reward_url'):
            k.append([
                {
                    'text':a.get(
                        'button_reward_name',
                        '🎁 Continue'
                    ),
                    'url':a['reward_url']
                }
            ])

        cap=(
            a.get('text_html')
            or
            a.get('text')
            or
            'Sponsored advertisement'
        )

        mark={
            'inline_keyboard':k
        }

        if a.get('image_url'):

            z=tg(
                'sendPhoto',
                {
                    'chat_id':uid,
                    'photo':a['image_url'],
                    'caption':cap,
                    'parse_mode':'HTML',
                    'reply_markup':mark,
                    'protect_content':True
                }
            )

            if not z or not z.get('ok'):
                send(
                    uid,
                    cap,
                    mark,
                    True,
                    'HTML'
                )

        else:
            send(
                uid,
                cap,
                mark,
                True,
                'HTML'
            )

        return

    if t=='🎁 Daily Bonus':

        if U['last_bonus']==today():
            send(
                uid,
                "⏳ You already claimed today's bonus."
            )
            return

        r=setting(
            'daily_bonus',
            5
        )

        q(
            'UPDATE users '
            'SET coins=coins+%s,'
            'coins_earned=coins_earned+%s,'
            'last_bonus=%s '
            'WHERE user_id=%s',
            (r,r,today(),uid)
        )

        hist(
            uid,
            r,
            'Daily bonus'
        )

        claim(
            uid,
            'bonus'
        )

        send(
            uid,
            f'🎁 Daily Bonus Claimed!\n\n'
            f'+{r} Coins added.',
            menu()
        )

        return

    if t=='📋 Daily Tasks':
        send(
            uid,
            tasktext(uid),
            taskmark()
        )
        return

    if t=='👤 My Profile':
        send(
            uid,
            profile(uid),
            menu()
        )
        return

    if t=='🛍️ Coin Shop':

        xs=shop()

        if xs:
            text=(
                '🛍️ Coin Shop\n\n'
                +
                '\n'.join(
                    f"• {x['name']} — "
                    f"{x['price']:,} Coins\n"
                    f"  {x['description']}"
                    for x in xs
                )
            )

            send(
                uid,
                text,
                shopmark()
            )

        else:
            send(
                uid,
                '🛍️ Coin Shop\n\n'
                'No items available.',
                menu()
            )

        return

    if t=='👥 Referral':

        bonus=setting(
            'referral_bonus',
            100
        )

        send(
            uid,
            f'👥 Referral Program\n\n'
            f'Earn {bonus} Coins per successful referral.\n\n'
            f'🔗 https://t.me/FoundCoinsBot?start={uid}'
        )

        return

    if t=='🏆 Leaderboard':

        xs=q(
            'SELECT username,user_id,coins '
            'FROM users '
            'WHERE blocked=0 '
            'ORDER BY coins DESC '
            'LIMIT 10',
            (),
            True,
            True
        )

        text='🏆 Top 10 Leaderboard\n\n'

        if xs:
            text+='\n'.join(
                f"{i}. "
                f"@{x['username'] if x['username'] else x['user_id']} "
                f"— {x['coins']:,}"
                for i,x in enumerate(xs,1)
            )
        else:
            text+='No users yet.'

        send(uid,text)
        return

    if t=='📜 History':

        xs=q(
            'SELECT amount,reason,created_at '
            'FROM history '
            'WHERE user_id=%s '
            'ORDER BY id DESC '
            'LIMIT 10',
            (uid,),
            True,
            True
        )

        if xs:
            text='📜 Your Recent History\n\n'

            for x in xs:
                sign='+' if x['amount']>=0 else ''

                text+=(
                    f"{sign}{x['amount']} — "
                    f"{x['reason']}\n"
                    f"{x['created_at']}\n\n"
                )
        else:
            text='📜 Your Recent History\n\nNo history yet.'

        send(uid,text)
        return

    if t=='💰 Withdraw':

        if U['coins']<MIN_W:

            send(
                uid,
                f'💰 Withdrawal\n\n'
                f'Your Coins: {U["coins"]:,}\n'
                f'Minimum: {MIN_W:,} Coins\n'
                f'Value: ৳100\n\n'
                f'❌ Need {MIN_W-U["coins"]:,} more Coins.'
            )

            return

        send(
            uid,
            '💰 Withdrawal\n\n'
            'Send:\n\n'
            'bKash: 01XXXXXXXXX\n'
            'Nagad: 01XXXXXXXXX'
        )

        return

    if t.lower().startswith(
        ('bkash:','nagad:')
    ):

        p=t.split(':',1)

        num=p[1].strip() if len(p)>1 else ''

        method=(
            'bKash'
            if p[0].lower()=='bkash'
            else 'Nagad'
        )

        if (
            len(num)!=11
            or
            not num.startswith('01')
            or
            not num.isdigit()
        ):
            send(
                uid,
                '❌ Invalid payment number.'
            )
            return

        if U['coins']!=MAX_W:

            send(
                uid,
                f'❌ Withdrawal is available '
                f'at exactly {MAX_W:,} Coins.\n'
                f'Your balance: {U["coins"]:,}'
            )

            return

        if q(
            "SELECT id FROM withdrawals "
            "WHERE user_id=%s AND status='pending'",
            (uid,),
            False,
            True
        ):
            send(
                uid,
                '⏳ You already have a pending withdrawal.'
            )
            return

        q(
            "INSERT INTO withdrawals"
            "(user_id,username,method,number,coins,status,created_at) "
            "VALUES(%s,%s,%s,%s,%s,'pending',%s)",
            (
                uid,
                U['username'],
                method,
                num,
                MAX_W,
                now()
            )
        )

        q(
            'UPDATE users SET coins=0 '
            'WHERE user_id=%s',
            (uid,)
        )

        hist(
            uid,
            -MAX_W,
            'Withdrawal requested'
        )

        send(
            ADMIN_ID,
            f'💰 NEW WITHDRAWAL\n\n'
            f'User: @{U["username"] or "N/A"}\n'
            f'ID: {uid}\n'
            f'Method: {method}\n'
            f'Number: {num}\n'
            f'Coins: {MAX_W:,}\n'
            f'Amount: ৳100'
        )

        send(
            uid,
            '⏳ Payment Processing...\n\n'
            'Your withdrawal request has been submitted.'
        )

        return

    send(
        uid,
        'Please choose an option from the menu.',
        menu()
    )

def update(u):

    if 'callback_query' in u:
        callback(u)

    elif 'message' in u:
        message(u)

# =========================
# FLASK
# =========================

@app.get('/')
def home():
    return 'Found Coins Bot is running!',200

@app.get('/health')
def health():

    try:
        q('SELECT 1')
        return 'OK',200
    except:
        return 'DB ERROR',500

@app.post('/webhook')
def webhook():

    try:
        update(request.get_json(silent=True) or {})
        return 'OK',200

    except Exception as e:
        print('Webhook:',e)
        return 'OK',200

@app.get('/reward')
def reward():

    try:
        uid=int(
            request.args.get(
                'userid',
                '0'
            )
        )
    except:
        uid=0

    if not uid or not consume(uid):
        return 'No valid pending ad',400

    r=setting(
        'ad_reward',
        50
    )

    q(
        'UPDATE users '
        'SET coins=coins+%s,'
        'coins_earned=coins_earned+%s,'
        'ads_watched=ads_watched+1 '
        'WHERE user_id=%s',
        (r,r,uid)
    )

    hist(
        uid,
        r,
        'AdsGram ad reward'
    )

    send(
        uid,
        f'🎉 Ad Completed!\n\n'
        f'+{r} Coins added.'
    )

    return 'Reward granted',200

# =========================
# ADMIN
# =========================

def ok():
    return session.get('admin') is True

def E(x):
    return html.escape(
        str(x if x is not None else '')
    )

def page(body):

    return f'''
<!doctype html>
<meta name="viewport"
content="width=device-width,initial-scale=1">
<title>Found Coins Admin</title>

<style>
body{{
font-family:Arial;
background:#f3f4f6;
padding:12px
}}

.box{{
background:#fff;
padding:15px;
margin:10px auto;
border-radius:12px;
overflow:auto
}}

table{{
width:100%;
border-collapse:collapse;
min-width:760px
}}

td,th{{
padding:8px;
border-bottom:1px solid #ddd;
text-align:left
}}

input,button{{
padding:8px;
margin:3px
}}

a{{
margin:3px
}}
</style>

<div class="box">
{body}
</div>
'''

@app.route(
    '/admin',
    methods=['GET','POST']
)
def admin():

    if ok():
        return redirect(
            '/admin/dashboard'
        )

    err=''

    if request.method=='POST':

        if (
            ADMIN_PASSWORD
            and
            request.form.get('password','')
            ==
            ADMIN_PASSWORD
        ):

            session['admin']=True

            return redirect(
                '/admin/dashboard'
            )

        err='❌ Invalid password'

    return page(
        f'''
        <h1>🔐 Found Coins Admin</h1>
        <p>{err}</p>

        <form method="post">
        <input
        type="password"
        name="password"
        placeholder="Admin password"
        required>

        <button>Login</button>
        </form>
        '''
    )

@app.get('/admin/logout')
def logout():

    session.clear()

    return redirect('/admin')

def wrow(x):

    a=''

    if x['status']=='pending':

        a=(
            f"<form method='post' "
            f"action='/admin/withdrawal/"
            f"{x['id']}/approve'>"
            f"<button>Approve</button></form>"

            f"<form method='post' "
            f"action='/admin/withdrawal/"
            f"{x['id']}/reject'>"
            f"<button>Reject</button></form>"
        )

    return (
        f"<tr>"
        f"<td>#{x['id']}</td>"
        f"<td>{x['user_id']}</td>"
        f"<td>{E(x['method'])}</td>"
        f"<td>{E(x['number'])}</td>"
        f"<td>{x['coins']:,}</td>"
        f"<td>{x['status']}</td>"
        f"<td>{a}</td>"
        f"</tr>"
    )

def prow(x):

    a=''

    if x['status']=='pending':

        a=(
            f"<form method='post' "
            f"action='/admin/shop/purchase/"
            f"{x['id']}/approve'>"
            f"<button>Approve</button></form>"

            f"<form method='post' "
            f"action='/admin/shop/purchase/"
            f"{x['id']}/reject'>"
            f"<button>Reject</button></form>"
        )

    return (
        f"<tr>"
        f"<td>#{x['id']}</td>"
        f"<td>{x['user_id']}</td>"
        f"<td>{E(x['item_name'])}</td>"
        f"<td>{x['price']:,}</td>"
        f"<td>{x['status']}</td>"
        f"<td>{a}</td>"
        f"</tr>"
    )

@app.get('/admin/dashboard')
def dashboard():

    if not ok():
        return redirect('/admin')

    users=q(
        'SELECT * FROM users '
        'ORDER BY joined_at DESC LIMIT 200',
        (),
        True,
        True
    )

    ws=q(
        'SELECT * FROM withdrawals '
        'ORDER BY id DESC LIMIT 100',
        (),
        True,
        True
    )

    ps=q(
        'SELECT * FROM shop_purchases '
        'ORDER BY id DESC LIMIT 100',
        (),
        True,
        True
    )

    ur=''.join(
        f"""
        <tr>
        <td>{x['user_id']}</td>
        <td>@{E(x['username'] or 'N/A')}</td>
        <td>{x['coins']:,}</td>
        <td>{x['ads_watched']}</td>

        <td>
        <form method='post'
        action='/admin/user/{x["user_id"]}/coins'>

        <input
        name='coins'
        type='number'
        min='0'
        value='{x["coins"]}'>

        <button>✏️ Edit</button>
        </form>
        </td>

        <td>
        <a href='/admin/user/{x["user_id"]}'>
        Profile
        </a>
        </td>

        <td>
        <form method='post'
        action='/admin/user/{x["user_id"]}/block'>

        <button>
        {'Unblock' if x['blocked'] else 'Block'}
        </button>

        </form>
        </td>

        </tr>
        """
        for x in users
    )

    wr=''.join(
        wrow(x)
        for x in ws
    )

    sr=''.join(
        prow(x)
        for x in ps
    )

    return page(
        f"""
        <h1>🛠️ Found Coins Admin</h1>

        <p>
        <a href='/admin/shop'>
        🛍️ Coin Shop
        </a>

        <a href='/admin/logout'>
        Logout
        </a>
        </p>

        <p>
        👥 Users: {len(users)}
        |
        💰 Pending withdrawals:
        {sum(x['status']=='pending' for x in ws)}
        |
        🛍️ Pending purchases:
        {sum(x['status']=='pending' for x in ps)}
        </p>

        <div class='box'>

        <h2>⚙️ Rewards</h2>

        <form method='post'
        action='/admin/settings'>

        Daily Bonus

        <input
        name='daily_bonus'
        type='number'
        min='0'
        value='{setting("daily_bonus",5)}'>

        Ad

        <input
        name='ad_reward'
        type='number'
        min='0'
        value='{setting("ad_reward",50)}'>

        Referral

        <input
        name='referral_bonus'
        type='number'
        min='0'
        value='{setting("referral_bonus",100)}'>

        Task

        <input
        name='task_reward'
        type='number'
        min='0'
        value='{setting("task_reward",5)}'>

        <button>💾 Save</button>

        </form>

        </div>

        <div class='box'>

        <h2>👤 Users</h2>

        <table>

        <tr>
        <th>ID</th>
        <th>User</th>
        <th>Coins</th>
        <th>Ads</th>
        <th>Edit</th>
        <th>Profile</th>
        <th>Block</th>
        </tr>

        {ur}

        </table>

        </div>

        <div class='box'>

        <h2>💰 Withdrawals</h2>

        <table>

        <tr>
        <th>ID</th>
        <th>User</th>
        <th>Method</th>
        <th>Number</th>
        <th>Coins</th>
        <th>Status</th>
        <th>Actions</th>
        </tr>

        {wr}

        </table>

        </div>

        <div class='box'>

        <h2>🛍️ Purchases</h2>

        <table>

        <tr>
        <th>ID</th>
        <th>User</th>
        <th>Item</th>
        <th>Price</th>
        <th>Status</th>
        <th>Actions</th>
        </tr>

        {sr}

        </table>

        </div>
        """
    )

@app.post('/admin/settings')
def settings():

    if not ok():
        return redirect('/admin')

    for k in (
        'daily_bonus',
        'ad_reward',
        'referral_bonus',
        'task_reward'
    ):

        try:
            setsetting(
                k,
                int(
                    request.form.get(
                        k,
                        '0'
                    )
                )
            )
        except:
            pass

    return redirect(
        '/admin/dashboard'
    )

@app.get('/admin/user/<int:uid>')
def userpage(uid):

    if not ok():
        return redirect('/admin')

    u=user(uid)

    if not u:
        return page(
            '<h2>User not found.</h2>'
        )

    h=q(
        'SELECT * FROM history '
        'WHERE user_id=%s '
        'ORDER BY id DESC LIMIT 50',
        (uid,),
        True,
        True
    )

    rows=''.join(
        f"""
        <tr>
        <td>{x['amount']}</td>
        <td>{E(x['reason'])}</td>
        <td>{x['created_at']}</td>
        </tr>
        """
        for x in h
    )

    return page(
        f"""
        <h1>👤 User Profile</h1>

        <p>
        <a href='/admin/dashboard'>
        ← Dashboard
        </a>
        </p>

        <p>
        ID: {uid}<br>
        Username: @{E(u['username'] or 'N/A')}<br>
        Coins: {u['coins']:,}<br>
        Ads: {u['ads_watched']}<br>
        Total Earned: {u['coins_earned']:,}<br>
        Status:
        {'Blocked' if u['blocked'] else 'Active'}
        </p>

        <form method='post'
        action='/admin/user/{uid}/coins'>

        <input
        name='coins'
        type='number'
        min='0'
        value='{u['coins']}'>

        <button>
        💾 Save Coins
        </button>

        </form>

        <h3>📜 History</h3>

        <table>

        <tr>
        <th>Amount</th>
        <th>Reason</th>
        <th>Date</th>
        </tr>

        {rows}

        </table>
        """
    )

@app.post('/admin/user/<int:uid>/coins')
def editcoins(uid):

    if not ok():
        return redirect('/admin')

    try:
        n=max(
            0,
            int(
                request.form.get(
                    'coins',
                    '0'
                )
            )
        )
    except:
        n=0

    u=user(uid)

    if u:

        q(
            'UPDATE users SET coins=%s '
            'WHERE user_id=%s',
            (n,uid)
        )

        hist(
            uid,
            n-u['coins'],
            'Admin edited coin balance'
        )

        send(
            uid,
            f'🛠️ Admin updated your balance.\n'
            f'🪙 New balance: {n:,}'
        )

    return redirect(
        f'/admin/user/{uid}'
    )

@app.post('/admin/user/<int:uid>/block')
def block(uid):

    if not ok():
        return redirect('/admin')

    q(
        'UPDATE users '
        'SET blocked=CASE '
        'WHEN blocked=1 THEN 0 '
        'ELSE 1 END '
        'WHERE user_id=%s',
        (uid,)
    )

    return redirect(
        '/admin/dashboard'
    )

@app.post('/admin/withdrawal/<int:wid>/<action>')
def withdraw(wid,action):

    if (
        not ok()
        or
        action not in (
            'approve',
            'reject'
        )
    ):
        return redirect(
            '/admin/dashboard'
        )

    r=q(
        'SELECT * FROM withdrawals '
        'WHERE id=%s',
        (wid,),
        True,
        True
    )

    if (
        not r
        or
        r[0]['status']!='pending'
    ):
        return redirect(
            '/admin/dashboard'
        )

    w=r[0]

    if action=='approve':

        q(
            "UPDATE withdrawals "
            "SET status='approved' "
            "WHERE id=%s",
            (wid,)
        )

        msg='✅ Your withdrawal was approved.'

    else:

        q(
            "UPDATE withdrawals "
            "SET status='rejected' "
            "WHERE id=%s",
            (wid,)
        )

        q(
            'UPDATE users '
            'SET coins=coins+%s '
            'WHERE user_id=%s',
            (
                w['coins'],
                w['user_id']
            )
        )

        hist(
            w['user_id'],
            w['coins'],
            'Withdrawal rejected/refunded'
        )

        msg=(
            f"❌ Withdrawal rejected.\n"
            f"+{w['coins']:,} Coins refunded."
        )

    send(
        w['user_id'],
        msg
    )

    return redirect(
        '/admin/dashboard'
    )

# =========================
# SHOP ADMIN
# =========================

@app.get('/admin/shop')
def shopadmin():

    if not ok():
        return redirect('/admin')

    xs=q(
        'SELECT * FROM shop_items '
        'ORDER BY id',
        (),
        True,
        True
    )

    rows=''.join(
        f"""
        <tr>
        <td>{x['id']}</td>
        <td>{E(x['name'])}</td>
        <td>{E(x['description'])}</td>
        <td>{x['price']:,}</td>
        <td>{'ON' if x['active'] else 'OFF'}</td>

        <td>

        <a href='/admin/shop/{x["id"]}/edit'>
        ✏️ Edit
        </a>

        <form
        style='display:inline'
        method='post'
        action='/admin/shop/{x["id"]}/delete'>

        <button>
        🗑️ Delete
        </button>

        </form>

        </td>

        </tr>
        """
        for x in xs
    )

    return page(
        f"""
        <h1>🛍️ Coin Shop</h1>

        <p>
        <a href='/admin/dashboard'>
        ← Dashboard
        </a>
        </p>

        <form
        method='post'
        action='/admin/shop/add'>

        <input
        name='name'
        placeholder='Name'
        required>

        <input
        name='description'
        placeholder='Description'>

        <input
        name='price'
        type='number'
        min='0'
        placeholder='Price'
        required>

        <button>
        ➕ Add
        </button>

        </form>

        <table>

        <tr>
        <th>ID</th>
        <th>Name</th>
        <th>Description</th>
        <th>Price</th>
        <th>Status</th>
        <th>Actions</th>
        </tr>

        {rows}

        </table>
        """
    )

@app.post('/admin/shop/add')
def shopadd():

    if not ok():
        return redirect('/admin')

    try:
        p=max(
            0,
            int(
                request.form.get(
                    'price',
                    '0'
                )
            )
        )
    except:
        p=0

    n=request.form.get(
        'name',
        ''
    ).strip()

    d=request.form.get(
        'description',
        ''
    ).strip()

    if n:

        q(
            'INSERT INTO shop_items'
            '(name,description,price,active,created_at) '
            'VALUES(%s,%s,%s,1,%s)',
            (
                n,
                d,
                p,
                now()
            )
        )

    return redirect(
        '/admin/shop'
    )

@app.route(
    '/admin/shop/<int:iid>/edit',
    methods=['GET','POST']
)
def shopedit(iid):

    if not ok():
        return redirect('/admin')

    r=q(
        'SELECT * FROM shop_items '
        'WHERE id=%s',
        (iid,),
        True,
        True
    )

    if not r:
        return page(
            'Item not found'
        )

    x=r[0]

    if request.method=='POST':

        try:
            p=max(
                0,
                int(
                    request.form.get(
                        'price',
                        '0'
                    )
                )
            )
        except:
            p=0

        q(
            'UPDATE shop_items '
            'SET name=%s,'
            'description=%s,'
            'price=%s,'
            'active=%s '
            'WHERE id=%s',
            (
                request.form.get(
                    'name',
                    ''
                ).strip(),

                request.form.get(
                    'description',
                    ''
                ).strip(),

                p,

                1 if request.form.get(
                    'active'
                ) else 0,

                iid
            )
        )

        return redirect(
            '/admin/shop'
        )

    return page(
        f"""
        <h1>✏️ Edit Shop Item</h1>

        <form method='post'>

        <input
        name='name'
        value='{E(x["name"])}'
        required>

        <br>

        <input
        name='description'
        value='{E(x["description"])}'>

        <br>

        <input
        name='price'
        type='number'
        min='0'
        value='{x["price"]}'>

        <br>

        <label>

        <input
        type='checkbox'
        name='active'
        {'checked' if x['active'] else ''}>

        Active

        </label>

        <br>

        <button>
        💾 Save
        </button>

        </form>

        <p>
        <a href='/admin/shop'>
        ← Shop
        </a>
        </p>
        """
    )

@app.post('/admin/shop/<int:iid>/delete')
def shopdelete(iid):

    if not ok():
        return redirect('/admin')

    # Safe delete: hide the item.
    # Old purchase/history data stays preserved.
    q(
        'UPDATE shop_items '
        'SET active=0 '
        'WHERE id=%s',
        (iid,)
    )

    return redirect(
        '/admin/shop'
    )

@app.post('/admin/shop/purchase/<int:pid>/<action>')
def purchase(pid,action):

    if (
        not ok()
        or
        action not in (
            'approve',
            'reject'
        )
    ):
        return redirect(
            '/admin/dashboard'
        )

    r=q(
        'SELECT * FROM shop_purchases '
        'WHERE id=%s',
        (pid,),
        True,
        True
    )

    if (
        not r
        or
        r[0]['status']!='pending'
    ):
        return redirect(
            '/admin/dashboard'
        )

    p=r[0]

    if action=='approve':

        q(
            "UPDATE shop_purchases "
            "SET status='approved',"
            "processed_at=%s "
            "WHERE id=%s",
            (now(),pid)
        )

        msg=(
            f"✅ Purchase approved.\n\n"
            f"Item: {p['item_name']}"
        )

    else:

        q(
            "UPDATE shop_purchases "
            "SET status='rejected',"
            "processed_at=%s "
            "WHERE id=%s",
            (now(),pid)
        )

        q(
            'UPDATE users '
            'SET coins=coins+%s '
            'WHERE user_id=%s',
            (
                p['price'],
                p['user_id']
            )
        )

        hist(
            p['user_id'],
            p['price'],
            f"Shop purchase rejected/refunded: "
            f"{p['item_name']}"
        )

        msg=(
            f"❌ Purchase rejected.\n"
            f"+{p['price']:,} Coins refunded."
        )

    send(
        p['user_id'],
        msg
    )

    return redirect(
        '/admin/dashboard'
    )

if __name__=='__main__':

    app.run(
        host='0.0.0.0',
        port=int(
            os.environ.get(
                'PORT',
                10000
            )
        )
    )
