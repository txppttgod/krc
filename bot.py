# Khinkali Racing Championship — бот чемпионата по дрифту в CarX Drift Racing 3.
# Всё управление — в личке бота кнопками: пилоты, клубы, этапы, судьи, ТЗ, регламент.
# Публичная часть — мини-приложение (index.html): сетки, таблицы, клубы, регламент. Данные оно берёт с /data.
import os
import sys
import json
import time
import hmac
import hashlib
import random
import threading
import requests
from urllib.parse import parse_qsl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(line_buffering=True)

TOKEN = os.environ["BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
APP_URL = os.environ.get("APP_URL", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").lstrip("@")
OWNER_IDS = {int(x) for x in os.environ.get("OWNER_IDS", "").replace(" ", "").split(",") if x.isdigit()}
SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY", "")
STATE_ID = os.environ.get("STATE_ID", "krc")  # своя строка в таблице bot_state — клубный бот её не трогает
TITLE = "Khinkali Racing Championship"

CLUB_MAX = 6          # пилотов в клубе
SIZES = [16, 32, 64]  # размеры сетки
# Кто может менять картинку «Регламент авто» в приложении (только один человек)
CAR_ADMIN = int(os.environ.get("CAR_ADMIN", "5607731610") or 0)
REG_MAX = 64          # больше на этап не записываем
OMT_MAX = 2           # сколько OMT подряд можно в одной паре, дальше судьи обязаны выбрать
JUDGE_REMIND_MIN = 20
DL_OPTS = [0, 30, 60, 120]   # дедлайн на пару, мин (0 — без дедлайна), выбирает организатор
PW_OPTS = [0, 10, 30]        # окно для протеста после решения судей, мин (0 — без протестов)
FAN_HOURS = 48               # сколько часов после этапа зрители голосуют за пару этапа
# Очки за итоговое место на этапе (по образцу Formula Drift): [до какого места, очки]
POINTS = [[1, 100], [2, 88], [3, 76], [4, 64], [8, 48], [16, 32], [32, 16], [64, 8]]

# ---------- База (Supabase, та же таблица bot_state, что у клубного бота, своя строка) ----------
SB_HEADERS = {"apikey": SB_KEY, "Content-Type": "application/json"}
if SB_KEY.startswith("eyJ"):
    SB_HEADERS["Authorization"] = f"Bearer {SB_KEY}"
DB_OK = False
LOCK = threading.RLock()


def fresh():
    return {"users": {}, "pilots": {}, "clubs": {}, "orgs": [], "stages": {}, "points": POINTS, "log": [],
            "content": {"rules": {"text": "", "photos": []}, "about": {"text": "", "photos": []},
                        "car": {"text": "", "photos": []}},
            "seq": {"pid": 0, "club": 0, "stage": 0, "img": 0},
            "news": [], "season": {"n": 1, "name": "Сезон 1"}, "seasons": [], "vids": [], "banned": {}}


S = fresh()


def db_load():
    if not SB_URL or not SB_KEY:
        print("Не заданы SUPABASE_URL / SUPABASE_KEY — данные не сохраняются!")
        return False, None
    try:
        r = requests.get(f"{SB_URL}/rest/v1/bot_state?id=eq.{STATE_ID}&select=data", headers=SB_HEADERS, timeout=15)
        rows = r.json()
        if not isinstance(rows, list):
            print(f"Ошибка загрузки из базы: HTTP {r.status_code} {str(rows)[:300]}")
            return False, None
        if not rows:  # первый запуск: создаём свою строку
            r2 = requests.post(f"{SB_URL}/rest/v1/bot_state", headers=dict(SB_HEADERS, Prefer="resolution=merge-duplicates"),
                               json={"id": STATE_ID, "data": fresh()}, timeout=15)
            if r2.status_code >= 300:
                print(f"Не смог создать строку {STATE_ID}: HTTP {r2.status_code} {r2.text[:300]}")
                return False, None
            return True, fresh()
        return True, rows[0]["data"] or fresh()
    except Exception as e:
        print("Ошибка загрузки из базы:", e)
        return False, None


def load_state():
    global DB_OK, S
    ok, d = db_load()
    DB_OK = ok
    if ok:
        base = fresh()
        base.update(d)
        S = base
        S["content"].setdefault("car", {"text": "", "photos": []})


def save_state():
    if not DB_OK:
        return
    try:
        r = requests.patch(f"{SB_URL}/rest/v1/bot_state?id=eq.{STATE_ID}", headers=SB_HEADERS, json={"data": S}, timeout=15)
        if r.status_code >= 300:
            print(f"Ошибка сохранения: HTTP {r.status_code} {r.text[:300]}")
    except Exception as e:
        print("Ошибка сохранения:", e)


def next_id(kind):
    S["seq"][kind] = S["seq"].get(kind, 0) + 1
    return S["seq"][kind]


def log(who, text):
    S["log"].append({"t": int(time.time()), "who": nick(who) if who else "бот", "text": text})
    del S["log"][:-200]


# ---------- Телеграм ----------
def api(method, payload):
    try:
        return requests.post(f"{API}/{method}", json=payload, timeout=15).json()
    except Exception as e:
        print("Ошибка API:", e)
        return {}


def kb(rows):
    return {"inline_keyboard": [r for r in rows if r]}


def b(text, data):
    return {"text": text, "callback_data": data}


def app_row():
    return [{"text": "📱 Приложение чемпионата", "web_app": {"url": APP_URL}}] if APP_URL else None


def send(chat, text, markup=None):
    p = {"chat_id": chat, "text": text[:4096]}
    if markup:
        p["reply_markup"] = markup
    res = api("sendMessage", p).get("result")
    return res.get("message_id", 0) if isinstance(res, dict) else 0


def edit(chat, mid, text, markup=None):
    if not mid:
        return send(chat, text, markup)
    api("editMessageText", {"chat_id": chat, "message_id": mid, "text": text[:4096],
                            "reply_markup": markup or {"inline_keyboard": []}})


def answer(cq, text="", alert=False):
    api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": text[:200], "show_alert": alert})


# ---------- Люди, роли ----------
def remember(u):
    if not u or u.get("is_bot"):
        return
    x = S["users"].setdefault(str(u["id"]), {})
    x["name"] = u.get("first_name", "") or x.get("name", "")
    x["un"] = (u.get("username") or "").lower()


def user(uid):
    return S["users"].setdefault(str(uid), {})


def user_by_un(text):
    un = (text or "").strip().lstrip("@").lower()
    return next((int(k) for k, v in S["users"].items() if un and v.get("un") == un), None)


def is_owner(uid):
    return uid in OWNER_IDS


def is_org(uid):
    return is_owner(uid) or uid in S["orgs"]


def pilot(uid):
    return S["pilots"].get(str(uid))


def nick(uid):
    p = pilot(uid)
    return p["nick"] if p else (S["users"].get(str(uid), {}).get("name") or "?")


def mention(uid):
    un = S["users"].get(str(uid), {}).get("un")
    return f"{nick(uid)} (@{un})" if un else nick(uid)


def club_of(uid):
    p = pilot(uid)
    return S["clubs"].get(p["club"]) if p and p.get("club") else None


def uid_by_pid(pid):
    return next((int(k) for k, p in S["pilots"].items() if str(p["pid"]) == str(pid)), None)


def orgs_dm(text, markup=None):
    for uid in OWNER_IDS | set(S["orgs"]):
        send(uid, text, markup)


# ---------- Очки и таблицы ----------
def place_points(place):
    return next((pts for mx, pts in S["points"] if place <= mx), 0)


def cur_season():
    return S["season"]["n"]


def season_points():
    tot = {}
    for st in S["stages"].values():
        if st["state"] == "done" and st.get("season", 1) == cur_season() and not st.get("test"):
            for u, p in st.get("pts", {}).items():
                tot[u] = tot.get(u, 0) + p
    return tot


def club_points(cid, tot=None):
    # В клубный зачёт идут очки всех нынешних пилотов клуба: ушёл пилот — его очки ушли вместе с ним
    tot = season_points() if tot is None else tot
    return sum(tot.get(str(u), 0) for u in S["clubs"][cid]["members"])


# ---------- Сетка ----------
def seed_order(size):
    order = [1]
    while len(order) < size:
        m = len(order) * 2 + 1
        order = [x for s in order for x in (s, m - s)]
    return order


def mget(st, mid):
    return next((m for m in st["matches"] if m["id"] == mid), None)


def new_match(st, mid, a, bb, name):
    m = {"id": mid, "name": name, "a": a, "b": bb, "pa": None, "pb": None, "w": None, "l": None,
         "done": False, "bye": False, "open": 0, "tries": [], "omt": 0}
    st["matches"].append(m)
    return m


def round_name(players, de):
    base = "Финал" if players == 2 else "Полуфинал" if players == 4 else f"Топ-{players}"
    return base + (" · верхняя сетка" if de else "")


def build_bracket(st, seeds):
    n = len(seeds)
    size = 2
    while size < n:
        size *= 2
    k = size.bit_length() - 1
    slots = [seeds[i - 1] if i <= n else None for i in seed_order(size)]
    de = bool(st["de"]) and size >= 4
    st.update(matches=[], seeds=seeds, bsize=size, de_on=de)
    W = lambda m: {"m": m["id"], "t": "w"}
    L = lambda m: {"m": m["id"], "t": "l"}
    up = [[new_match(st, f"U1-{i + 1}", {"p": slots[2 * i]}, {"p": slots[2 * i + 1]}, round_name(size, de))
           for i in range(size // 2)]]
    for r in range(2, k + 1):
        prev = up[-1]
        up.append([new_match(st, f"U{r}-{i + 1}", W(prev[2 * i]), W(prev[2 * i + 1]), round_name(size >> (r - 1), de))
                   for i in range(len(prev) // 2)])
    if de:
        lr = 1
        low = [new_match(st, f"L1-{i + 1}", L(up[0][2 * i]), L(up[0][2 * i + 1]), "Нижняя сетка · раунд 1")
               for i in range(len(up[0]) // 2)]
        for j in range(2, k + 1):
            lr += 1
            uj = up[j - 1]
            low = [new_match(st, f"L{lr}-{i + 1}", W(low[i]), L(uj[len(uj) - 1 - i]), f"Нижняя сетка · раунд {lr}")
                   for i in range(len(low))]
            if j < k:
                lr += 1
                low = [new_match(st, f"L{lr}-{i + 1}", W(low[2 * i]), W(low[2 * i + 1]), f"Нижняя сетка · раунд {lr}")
                       for i in range(len(low) // 2)]
        low[0]["name"] = "Финал нижней сетки (за 3-е место)"
        new_match(st, "GF", W(up[-1][0]), W(low[0]), "Финал")
        st["final"], st["third"] = "GF", low[0]["id"]
    else:
        up[-1][0]["name"] = "Финал"
        st["final"], st["third"] = up[-1][0]["id"], None
        if k >= 2:
            new_match(st, "B3", L(up[-2][0]), L(up[-2][1]), "Заезд за 3-е место")
            st["third"] = "B3"


def slot(st, s):
    if "p" in s:
        return True, s["p"]
    m = mget(st, s["m"])
    if not m or not m["done"]:
        return False, None
    return True, m["w"] if s["t"] == "w" else m["l"]


def advance(st):
    opened, changed = [], True
    while changed:
        changed = False
        for m in st["matches"]:
            if m["done"]:
                continue
            ra, pa = slot(st, m["a"])
            rb, pb = slot(st, m["b"])
            m["pa"], m["pb"] = pa, pb
            if ra and rb and (pa is None or pb is None):
                m.update(done=True, w=pa if pb is None else pb, l=None, bye=True)
                changed = True
            elif ra and rb and not m["open"]:
                m["open"] = int(time.time())
                opened.append(m)
    for m in opened:
        notify_open(st, m)
    for m in opened:  # дисквалифицированный в новой паре — сразу тех. поражение
        if not m["done"] and (str(m["pa"]) in st.get("dsq", {}) or str(m["pb"]) in st.get("dsq", {})):
            set_result(st, m, "b" if str(m["pa"]) in st["dsq"] else "a")
            return
    notify_next(st)
    if st["state"] == "run" and st["matches"] and all(m["done"] for m in st["matches"]):
        finish_stage(st)


def stage_places(st):
    order = []
    fin = mget(st, st["final"])
    order += [[fin["w"]], [fin["l"]]]
    th = mget(st, st["third"]) if st.get("third") else None
    rnd = lambda m: int(m["id"][1:].split("-")[0])
    if th and st.get("de_on"):
        order.append([th["l"]])
        for r in sorted({rnd(m) for m in st["matches"] if m["id"].startswith("L")}, reverse=True):
            order.append([m["l"] for m in st["matches"] if m["id"].startswith(f"L{r}-") and m["id"] != th["id"]])
    elif th:
        order += [[th["w"]], [th["l"]]]
        for r in sorted({rnd(m) for m in st["matches"] if m["id"].startswith("U")}, reverse=True)[2:]:
            order.append([m["l"] for m in st["matches"] if m["id"].startswith(f"U{r}-")])
    places, n = {}, 0
    for grp in order:
        grp = [u for u in grp if u is not None and str(u) not in places]
        for u in grp:
            places[str(u)] = n + 1
        n += len(grp)
    return places


def finish_stage(st):
    st["state"] = "done"
    st["done_t"] = int(time.time())
    st["places"] = stage_places(st)
    st["pts"] = {u: 0 if u in st.get("dsq", {}) or st.get("test") else place_points(p) for u, p in st["places"].items()}
    top = sorted(st["places"].items(), key=lambda x: x[1])[:3]
    medals = ["🥇", "🥈", "🥉"]
    head = f"🏁 {st['title']} завершён!\n\n" + "\n".join(f"{medals[i]} {nick(int(u))}" for i, (u, _) in enumerate(top))
    for u, place in st["places"].items():
        send(int(u), head + f"\n\nТвоё место: {place}, очки в сезон: +{st['pts'][u]}")
    orgs_dm(head + ("\n\n🧪 Тестовый этап — очки не начислялись." if st.get("test") else "\n\nОчки начислены, таблицы в приложении обновлены.")
            + f"\nЗрители {FAN_HOURS} ч голосуют в приложении за пару этапа.")
    log(None, f"{st['title']} завершён")


# ---------- Этапы ----------
def stage_list(states=None):
    return [st for st in sorted(S["stages"].values(), key=lambda x: x["n"]) if not states or st["state"] in states]


def new_stage(d):
    sid = str(next_id("stage"))
    st = {"id": sid, "n": int(sid), "title": d["title"], "date": d.get("date", ""), "size": d["size"], "de": d["de"],
          "qual": d["qual"], "jn": d["jn"], "judges": [], "state": "draft", "reg": [], "qruns": {}, "seeds": [],
          "matches": [], "tz": {}, "places": {}, "pts": {}, "created": int(time.time()), "dl": d.get("dl", 0),
          "pw": d.get("pw", 0), "season": cur_season(), "test": d.get("test", False), "appr": d.get("appr", False),
          "pend": [], "dsq": {}, "sched": "", "fans": {}}
    S["stages"][sid] = st
    return st


STATE_RU = {"draft": "черновик", "reg": "регистрация", "qual": "квалификация", "run": "идут парные заезды",
            "done": "завершён"}


def stage_desc(st):
    fmt = f"Топ-{st['size']} · {'с лузер-сеткой' if st['de'] else 'на вылет'} · " \
          f"{'с квалификацией' if st['qual'] else 'без квалификации'} · судей: {st['jn']}\n" \
          f"Дедлайн на пару: {str(st.get('dl', 0)) + ' мин' if st.get('dl') else 'нет'} · " \
          f"протесты: {'в течение ' + str(st.get('pw', 0)) + ' мин' if st.get('pw') else 'нет'}"
    return (f"{'🧪' if st.get('test') else '🏁'} {st['title']}" + (" (тест, без очков)" if st.get("test") else "")
            + (f" · {st['date']}" if st.get("date") else "") + f"\n{fmt}\nСтатус: {STATE_RU[st['state']]}"
            + f"\nЗаписано: {len(st['reg'])}" + (f"\nСудьи: {', '.join(nick(j) for j in st['judges'])}" if st["judges"] else
                                                   "\nСудьи: не назначены"))


def active_for(uid):
    # Этап, в котором пилот сейчас что-то сдаёт: квалификация или открытая пара
    for st in stage_list(["qual", "run"]):
        if st["state"] == "qual" and uid in st["reg"]:
            return st, None
        if st["state"] == "run":
            m = next((m for m in st["matches"] if m["open"] and not m["done"] and uid in (m["pa"], m["pb"])), None)
            if m:
                return st, m
    return None, None


def send_tz(uid, st, kind):
    tz = st["tz"].get(kind)
    what = "квалификации" if kind == "q" else "парных заездов"
    if not tz:
        send(uid, f"ТЗ {what} для «{st['title']}» пока нет.")
        return
    head = f"📋 ТЗ {what} · {st['title']}\n\n"
    if tz.get("video"):
        api("sendVideo", {"chat_id": uid, "video": tz["video"], "caption": (head + tz.get("text", ""))[:1024]})
        if len(head + tz.get("text", "")) > 1024:
            send(uid, tz.get("text", ""))
    else:
        send(uid, head + tz.get("text", ""))


def notify_open(st, m):
    for me, opp in ((m["pa"], m["pb"]), (m["pb"], m["pa"])):
        send(me, f"⚔️ {st['title']} · {m['name']}\nТвоя пара: {nick(me)} vs {nick(opp)}\n\n"
                 "Катайте по ТЗ и пришлите боту 2 видео: одно, где ведёт один пилот, и второе, где ведёт другой. "
                 "Сдать может любой из вас — второй раз сдавать не нужно."
                 + (f"\n\n⏳ На заезд {st['dl']} мин." if st.get("dl") else ""),
             kb([[b("📋 ТЗ парных заездов", f"tz:{st['id']}:b")]]))


# ---------- Квалификация по видео ----------
def qual_submit(uid, st, file_id, score):
    run = {"f": file_id, "score": score, "st": "wait", "t": int(time.time()), "jm": {}}
    st["qruns"][str(uid)] = run
    judges = [j for j in st["judges"] if j != uid]
    if not judges:
        orgs_dm(f"⚠️ {st['title']}: {nick(uid)} сдал квалификацию, а судей нет. Назначь судей в меню этапа.")
    for j in judges:
        api("sendVideo", {"chat_id": j, "video": file_id,
                          "caption": f"⏱ Квалификация · {st['title']}\nПилот: {nick(uid)}\nЗаявлено очков: {score:g}"})
        run["jm"][str(j)] = send(j, f"Квалификация {nick(uid)}: {score:g} очков. Проверь видео по ТЗ.",
                                 kb([[b("✅ Засчитать", f"jq:{st['id']}:{uid}:ok"), b("✏️ Исправить счёт", f"jq:{st['id']}:{uid}:fix")],
                                     [b("❌ Не по ТЗ", f"jq:{st['id']}:{uid}:no")]]))
    send(uid, f"✅ Квалификация отправлена судьям: {score:g} очков. Бот напишет, когда её проверят.")


def qual_close_msgs(st, uid, text):
    run = st["qruns"][str(uid)]
    for j, mid in run.get("jm", {}).items():
        edit(int(j), mid, text)


def qual_judge(cq, sid, uid, act):
    j = cq["from"]["id"]
    st = S["stages"].get(sid)
    run = st["qruns"].get(str(uid)) if st else None
    if not st or j not in st["judges"] or j == uid:
        return answer(cq, "Ты не судья этого этапа.", True)
    if not run or run["st"] != "wait" or st["state"] != "qual":
        return answer(cq, "Эта квалификация уже проверена.", True)
    if act == "ok":
        run.update(st="ok", judge=j)
        qual_close_msgs(st, uid, f"✅ Квалификация {nick(uid)}: {run['score']:g} — засчитал {nick(j)}")
        send(uid, f"✅ Квалификация засчитана: {run['score']:g} очков.")
    elif act == "no":
        run.update(st="no", judge=j)
        qual_close_msgs(st, uid, f"❌ Квалификация {nick(uid)} отклонена ({nick(j)})")
        send(uid, "❌ Судья отклонил квалификацию: заезд не по ТЗ. Можешь прислать новое видео, пока квалификация открыта.")
    elif act == "fix":
        user(j)["aw"] = f"qfix:{sid}:{uid}"
        send(j, f"Напиши правильный счёт для {nick(uid)} (число):")
    answer(cq)


# ---------- Парные заезды: сдача видео и судейство ----------
def side_name(m, s):
    return nick(m["pa"] if s == "a" else m["pb"])


def try_open(m):
    t = m["tries"][-1] if m["tries"] else None
    return t if t and t["res"] is None else None


def upload_video(uid, st, m, file_id, vu=None):
    if try_open(m):
        send(uid, "Этот заезд уже у судей — ждём решения. Второй раз сдавать не нужно.")
        return
    up = user(uid).get("up")
    if not up or up.get("mid") != m["id"] or up.get("sid") != st["id"]:
        up = user(uid)["up"] = {"sid": st["id"], "mid": m["id"], "v": []}
    if len(up["v"]) >= 2:
        send(uid, "Два видео уже есть. Отправь их судьям или начни заново.", up_kb(up))
        return
    if any(v.get("u") == (vu or file_id) for v in up["v"]):
        send(uid, "Это то же самое видео. Пришли второй заезд — где ведёт другой пилот.")
        return
    up["v"].append({"f": file_id, "lead": None, "u": vu or file_id})
    if len(up["v"]) == 1:
        send(uid, f"🎥 Видео 1 получено. Кто в нём ведёт?",
             kb([[b(f"Ведёт {side_name(m, 'a')}", "up:lead:a"), b(f"Ведёт {side_name(m, 'b')}", "up:lead:b")]]))
    elif up["v"][0]["lead"]:
        up["v"][1]["lead"] = "b" if up["v"][0]["lead"] == "a" else "a"
        send(uid, up_text(m, up), up_kb(up))


def up_text(m, up):
    return ("Готово к отправке:\n" + "\n".join(f"Видео {i + 1} — ведёт {side_name(m, v['lead'])}" for i, v in enumerate(up["v"]))
            + "\n\nОтправить судьям?")


def up_kb(up):
    return kb([[b("✅ Отправить судьям", "up:send"), b("🔁 Заново", "up:redo")]])


def upload_cb(cq, act, arg):
    uid = cq["from"]["id"]
    up = user(uid).get("up")
    st = S["stages"].get(up["sid"]) if up else None
    m = mget(st, up["mid"]) if st else None
    if not m or m["done"] or st["state"] != "run":
        user(uid).pop("up", None)
        return answer(cq, "Эта пара уже не ждёт видео.", True)
    if act == "lead":
        up["v"][0]["lead"] = arg
        if len(up["v"]) == 2:
            up["v"][1]["lead"] = "b" if arg == "a" else "a"
            send(uid, up_text(m, up), up_kb(up))
        else:
            other = "b" if arg == "a" else "a"
            send(uid, f"Принято. Теперь пришли второе видео — где ведёт {side_name(m, other)}.")
        return answer(cq)
    if act == "redo":
        user(uid).pop("up", None)
        send(uid, "Начнём заново: пришли первое видео.")
        return answer(cq)
    if act == "send":
        if try_open(m):
            user(uid).pop("up", None)
            return answer(cq, "Соперник уже сдал этот заезд — он у судей.", True)
        if len(up["v"]) < 2 or not all(v["lead"] for v in up["v"]):
            return answer(cq, "Нужно два видео и отметка, кто где ведёт.", True)
        if any(used_video(v.get("u")) for v in up["v"]):
            user(uid).pop("up", None)
            return answer(cq, "Одно из видео уже сдавали в чемпионате — нужен новый заезд.", True)
        if not st.get("test"):
            mark_videos(*(v.get("u") for v in up["v"]))
        vids = {v["lead"]: v["f"] for v in up["v"]}
        t = {"by": uid, "v": [vids["a"], vids["b"]], "votes": {}, "res": None, "t": int(time.time()), "jm": {}, "rem": 0}
        m["tries"].append(t)
        user(uid).pop("up", None)
        to_judges(st, m, t)
        opp = m["pb"] if uid == m["pa"] else m["pa"]
        send(opp, f"🎥 {nick(uid)} сдал ваш заезд ({m['name']}) — он у судей.")
        send(uid, "✅ Заезд у судей. Пока никто не проголосовал, его можно отозвать.",
             kb([[b("↩️ Отозвать", f"wd:{st['id']}:{m['id']}:{len(m['tries']) - 1}")]]))
        return answer(cq, "Отправлено")


def eligible(st, m):
    return [j for j in st["judges"] if j not in (m["pa"], m["pb"])]


def to_judges(st, m, t):
    ti = len(m["tries"]) - 1
    js = eligible(st, m)
    if not js:
        orgs_dm(f"⚠️ {st['title']} · {m['name']}: заезд сдан, а судей нет (или они сами в этой паре). "
                "Назначь судей или поставь результат вручную.")
    for j in js:
        api("sendMediaGroup", {"chat_id": j, "media": [
            {"type": "video", "media": t["v"][0], "caption": f"Заезд 1 — ведёт {side_name(m, 'a')}"},
            {"type": "video", "media": t["v"][1], "caption": f"Заезд 2 — ведёт {side_name(m, 'b')}"}]})
        t["jm"][str(j)] = send(j, vote_text(st, m, ti), vote_kb(st, m, ti))


def vote_text(st, m, ti):
    return (f"⚖️ {st['title']} · {m['name']}\n{side_name(m, 'a')} vs {side_name(m, 'b')}"
            + (f" · попытка {ti + 1} (было OMT: {m['omt']})" if m["omt"] else "") + "\n\nКто выиграл пару?")


def vote_kb(st, m, ti):
    row = [b(f"🏁 {side_name(m, 'a')}", f"jv:{st['id']}:{m['id']}:{ti}:a"),
           b(f"🏁 {side_name(m, 'b')}", f"jv:{st['id']}:{m['id']}:{ti}:b")]
    return kb([row, [b("🔁 OMT — перезаезд", f"jv:{st['id']}:{m['id']}:{ti}:omt")] if m["omt"] < OMT_MAX else None])


def choice_name(m, c):
    return "OMT" if c == "omt" else side_name(m, c)


def decide(st, m, t):
    js = eligible(st, m)
    need = 1 if st["jn"] == 1 else min(2, max(1, len(js)))
    votes = [t["votes"].get(str(j)) for j in js if t["votes"].get(str(j))]
    for c in ("a", "b", "omt"):
        if votes.count(c) >= need:
            return c
    if len(votes) >= min(len(js), max(st["jn"], need)) and votes:
        return "omt"  # голоса разошлись — перезаезд
    return None


def judge_vote(cq, sid, mid, ti, c):
    j = cq["from"]["id"]
    st = S["stages"].get(sid)
    m = mget(st, mid) if st else None
    if not m or j not in st["judges"]:
        return answer(cq, "Ты не судья этого этапа.", True)
    if j in (m["pa"], m["pb"]):
        return answer(cq, "Свою пару судить нельзя.", True)
    t = m["tries"][int(ti)] if int(ti) < len(m["tries"]) else None
    if not t or t["res"] is not None or m["done"]:
        return answer(cq, "По этой паре решение уже есть.", True)
    if str(j) in t["votes"]:
        return answer(cq, "Ты уже проголосовал.", True)
    if c == "omt" and m["omt"] >= OMT_MAX:
        return answer(cq, "Лимит OMT исчерпан — выбери победителя.", True)
    t["votes"][str(j)] = c
    res = decide(st, m, t)
    answer(cq, f"Голос: {choice_name(m, c)}")
    if not res:
        edit(j, cq["message"]["message_id"], vote_text(st, m, int(ti)) + f"\n\nТвой голос: {choice_name(m, c)} ✔ Ждём остальных судей.")
        return
    t["res"] = res
    votes_txt = "\n".join(f"• {nick(int(x))}: {choice_name(m, v)}" for x, v in t["votes"].items())
    for jj, mmid in t["jm"].items():
        edit(int(jj), mmid, vote_text(st, m, int(ti)) + f"\n\nРешение: {choice_name(m, res)}\n{votes_txt}")
    if res == "omt":
        m["omt"] += 1
        restart_deadline(m)
        for p in (m["pa"], m["pb"]):
            send(p, f"🔁 OMT! {m['name']}: судьи назначили перезаезд.\n{votes_txt}\n\nКатайте снова и пришлите новые 2 видео.")
        return
    win, lose = (m["pa"], m["pb"]) if res == "a" else (m["pb"], m["pa"])
    m.update(done=True, w=win, l=lose, dec_t=int(time.time()))
    for p in (win, lose):
        pw = st.get("pw") if p == lose else 0
        send(p, f"{'🏆 Победа' if p == win else '❌ Поражение'} · {st['title']} · {m['name']}\n"
                f"{nick(win)} побеждает {nick(lose)}\n{votes_txt}"
             + (f"\n\nНе согласен? Протест можно подать в течение {pw} мин." if pw else ""),
             kb([[b("📣 Подать протест", f"pr:{st['id']}:{m['id']}")]]) if pw else None)
    log(None, f"{st['title']} · {m['name']}: {nick(win)} > {nick(lose)}")
    advance(st)
    notify_next(st)


def withdraw(cq, sid, mid, ti):
    uid = cq["from"]["id"]
    st = S["stages"].get(sid)
    m = mget(st, mid) if st else None
    t = m["tries"][int(ti)] if m and int(ti) < len(m["tries"]) else None
    if not t or t["by"] != uid or t["res"] is not None or m["done"]:
        return answer(cq, "Отозвать уже нельзя.", True)
    if t["votes"]:
        return answer(cq, "Судьи уже голосуют — отозвать нельзя.", True)
    m["tries"].pop(int(ti))
    for jj, mmid in t["jm"].items():
        edit(int(jj), mmid, f"↩️ {m['name']}: пилот отозвал видео, ждите новые.")
    send(uid, "Отозвано. Пришли видео заново.")
    answer(cq)


# ---------- Дедлайн на пару ----------
def restart_deadline(m):
    m["open"] = int(time.time())
    m["ext"] = 0
    m.pop("dlh", None)
    m.pop("dlw", None)


def deadlines(now):
    changed = False
    for st in stage_list(["run"]):
        if not st.get("dl"):
            continue
        for m in st["matches"]:
            if not m["open"] or m["done"] or try_open(m):
                continue
            limit = (st["dl"] + m.get("ext", 0)) * 60
            if not m.get("dlh") and now - m["open"] > limit / 2:
                m["dlh"] = 1
                changed = True
                left = max(1, int((m["open"] + limit - now) // 60))
                for x in (m["pa"], m["pb"]):
                    send(x, f"⏳ {st['title']} · {m['name']}: на заезд осталось ~{left} мин. Пришлите 2 видео боту.")
            if not m.get("dlw") and now - m["open"] > limit:
                m["dlw"] = 1
                changed = True
                orgs_dm(f"⌛ {st['title']} · {m['name']}: {nick(m['pa'])} vs {nick(m['pb'])} — время на заезд вышло, видео нет.",
                        kb([[b(f"Тех. победа {nick(m['pa'])}", f"mr:{st['id']}:{m['id']}:a"),
                             b(f"Тех. победа {nick(m['pb'])}", f"mr:{st['id']}:{m['id']}:b")],
                            [b("➕ Продлить на 30 мин", f"mx:{st['id']}:{m['id']}")]]))
    return changed


# ---------- Протесты: решают те же судьи, все голоса равны ----------
def protest_start(cq, sid, mid):
    uid = cq["from"]["id"]
    st = S["stages"].get(sid)
    m = mget(st, mid) if st else None
    if not m or not m["done"] or m["l"] != uid:
        return answer(cq, "Протест может подать только проигравший в этой паре.", True)
    if not st.get("pw") or time.time() - m.get("dec_t", 0) > st["pw"] * 60:
        return answer(cq, "Время на протест вышло.", True)
    if m.get("protest"):
        return answer(cq, "Протест по этой паре уже подан.", True)
    user(uid)["aw"] = f"protest:{sid}:{mid}"
    answer(cq)
    send(uid, "Опиши коротко, с чем не согласен — судьи пересмотрят пару:")


def protest_submit(uid, st, m, why):
    if not m["done"] or m["l"] != uid or m.get("protest"):
        return send(uid, "Протест по этой паре уже нельзя подать.")
    pr = m["protest"] = {"by": uid, "why": why[:500], "t": int(time.time()), "votes": {}, "res": None, "jm": {}}
    js = eligible(st, m)
    if not js:
        orgs_dm(f"📣 Протест {nick(uid)} · {st['title']} · {m['name']}: «{pr['why']}». Судей нет — реши вручную.")
    for j in js:
        pr["jm"][str(j)] = send(j, f"📣 Протест · {st['title']} · {m['name']}\n{nick(uid)}: «{pr['why']}»\n"
                                   f"Решение было: победил {nick(m['w'])}. Видео: /start w_{st['id']}_{m['id']}",
                                kb([[b("✅ Оставить решение", f"pv:{st['id']}:{m['id']}:keep"),
                                     b("🔁 OMT — перезаезд", f"pv:{st['id']}:{m['id']}:omt")]]))
    send(uid, "📣 Протест отправлен судьям. Бот напишет решение.")
    send(m["w"], f"📣 {nick(uid)} подал протест по паре {m['name']}. Судьи пересмотрят её.")


def protest_vote(cq, sid, mid, c):
    j = cq["from"]["id"]
    st = S["stages"].get(sid)
    m = mget(st, mid) if st else None
    pr = m.get("protest") if m else None
    if not pr or j not in eligible(st, m):
        return answer(cq, "Ты не судья этой пары.", True)
    if pr["res"] or str(j) in pr["votes"]:
        return answer(cq, "Голос уже учтён или протест решён.", True)
    pr["votes"][str(j)] = c
    js = eligible(st, m)
    need = 1 if st["jn"] == 1 else min(2, len(js))
    votes = list(pr["votes"].values())
    res = "omt" if votes.count("omt") >= need else "keep" if votes.count("keep") >= need else \
        ("keep" if len(votes) >= len(js) else None)  # голоса разошлись — решение остаётся
    answer(cq, "Голос учтён")
    if not res:
        return
    pr["res"] = res
    txt = "\n".join(f"• {nick(int(x))}: {'OMT' if v == 'omt' else 'оставить'}" for x, v in pr["votes"].items())
    for jj, mmid in pr["jm"].items():
        edit(int(jj), mmid, f"📣 Протест · {m['name']}: {'принят — OMT' if res == 'omt' else 'отклонён'}\n{txt}")
    if res == "keep":
        for x in (m["pa"], m["pb"]):
            send(x, f"📣 Протест по паре {m['name']} отклонён — решение остаётся.\n{txt}")
        return
    if reset_result(st, m) != "ok":
        orgs_dm(f"📣 Протест по {st['title']} · {m['name']} принят (OMT), но следующая пара уже ехала — реши вручную.")
        return
    m["omt"] += 1
    m.setdefault("protests", []).append(m.pop("protest"))  # после перезаезда можно будет подать новый протест
    restart_deadline(m)
    for x in (m["pa"], m["pb"]):
        send(x, f"📣 Протест принят: {m['name']} — OMT, перезаезд. Пришлите новые 2 видео.\n{txt}")


# ---------- Новости, очки, сезоны ----------
def post_news(uid, text):
    S["news"].insert(0, {"t": int(time.time()), "text": text[:2000]})
    del S["news"][30:]
    for x in list(S["pilots"]):
        send(int(x), f"📰 {TITLE}\n\n{text[:2000]}", kb([app_row()]))
    log(uid, "новость опубликована")


def recompute_points():
    for st in S["stages"].values():
        if st["state"] == "done" and st.get("season", 1) == cur_season():
            st["pts"] = {u: place_points(pl) for u, pl in st["places"].items()}


def points_text():
    prev, rows = 0, []
    for mx, pts in S["points"]:
        rows.append(f"{prev + 1}{'–' + str(mx) if mx > prev + 1 else ''} место — {pts}")
        prev = mx
    return "\n".join(rows)


def finish_season(uid):
    tot = season_points()
    top = sorted(((nick(int(u)), (club_of(int(u)) or {}).get("name", ""), p) for u, p in tot.items()), key=lambda x: -x[2])[:10]
    clubs = sorted(((c["name"], club_points(cid, tot)) for cid, c in S["clubs"].items()), key=lambda x: -x[1])[:5]
    S["seasons"].append({"n": cur_season(), "name": S["season"]["name"], "pilots": [list(x) for x in top],
                         "clubs": [list(x) for x in clubs], "end": int(time.time())})
    n = cur_season() + 1
    S["season"] = {"n": n, "name": f"Сезон {n}"}
    log(uid, f"завершён сезон, начался «Сезон {n}»")
    champ = f"\n🏆 Чемпион: {top[0][0]} — {top[0][2]} очк." if top else ""
    club = f"\n👥 Лучший клуб: {clubs[0][0]} — {clubs[0][1]} очк." if clubs and clubs[0][1] else ""
    for x in list(S["pilots"]):
        send(int(x), f"🏁 {S['seasons'][-1]['name']} завершён!{champ}{club}\n\nНачинается {S['season']['name']} — очки с нуля.")


# ---------- Следующая пара, дисквалификация, отстранение ----------
def notify_next(st):
    queue = [m for m in st["matches"] if m["open"] and not m["done"] and not m["tries"]]
    if len(queue) > 1 and not queue[1].get("nx"):
        m = queue[1]
        m["nx"] = 1
        for x in (m["pa"], m["pb"]):
            send(x, f"🔜 {st['title']}: ваша пара следующая — {m['name']}, {nick(m['pa'])} vs {nick(m['pb'])}. Готовьтесь!")


def disqualify(st, uid, why, who=None):
    st.setdefault("dsq", {})[str(uid)] = why[:200]
    log(who, f"{st['title']}: дисквалифицирован {nick(uid)} — {why}")
    send(uid, f"⛔ Ты дисквалифицирован с этапа {st['title']}. Причина: {why}")
    if uid in st.get("pend", []):
        st["pend"].remove(uid)
    if st["state"] in ("reg", "qual"):
        if uid in st["reg"]:
            st["reg"].remove(uid)
        st["qruns"].pop(str(uid), None)
        return
    if st["state"] == "run":
        m = next((m for m in st["matches"] if m["open"] and not m["done"] and uid in (m["pa"], m["pb"])), None)
        if m:
            set_result(st, m, "b" if m["pa"] == uid else "a", who)
    if st["state"] == "done" and str(uid) in st["pts"]:
        st["pts"][str(uid)] = 0


def find_pilot_uid(text):
    t = (text or "").strip()
    uid = user_by_un(t) if t.startswith("@") else None
    if uid and pilot(uid):
        return uid
    return next((int(k) for k, p in S["pilots"].items() if p["nick"].lower() == t.lstrip("@").lower()), None)


def ban(uid, why, who=None):
    S["banned"][str(uid)] = why[:200]
    log(who, f"отстранён {nick(uid)} — {why}")
    send(uid, f"⛔ Ты отстранён от {TITLE}. Причина: {why}")
    for st in stage_list(["reg", "qual", "run"]):
        if uid in st["reg"] or uid in st.get("pend", []) or uid in st.get("seeds", []):
            disqualify(st, uid, why, who)


# ---------- Статистика судей, выгрузка, зрительский приз ----------
def judge_stats():
    out = {}
    for st in S["stages"].values():
        for m in st["matches"]:
            for t in m["tries"]:
                if t["res"] is None:
                    continue
                for j, v in t["votes"].items():
                    x = out.setdefault(j, {"n": 0, "omt": 0, "pr": 0, "pr_ok": 0})
                    x["n"] += 1
                    x["omt"] += v == "omt"
            for pr in ([m["protest"]] if m.get("protest") else []) + m.get("protests", []):
                last = next((t for t in reversed(m["tries"]) if t["res"] in ("a", "b", "reset")), None)
                for j in (last or {}).get("votes", {}):
                    x = out.setdefault(j, {"n": 0, "omt": 0, "pr": 0, "pr_ok": 0})
                    x["pr"] += 1
                    x["pr_ok"] += pr.get("res") == "omt"
    return out


def judge_stats_text():
    st = judge_stats()
    if not st:
        return "📊 Судьи ещё ничего не судили."
    rows = [f"• {nick(int(j))}: голосов {x['n']}, OMT {x['omt']} ({round(x['omt'] / x['n'] * 100) if x['n'] else 0}%), "
            f"протестов по его парам {x['pr']}, принято {x['pr_ok']}" for j, x in sorted(st.items(), key=lambda kv: -kv[1]["n"])]
    return "📊 Статистика судей (все этапы)\n\n" + "\n".join(rows)


def stage_csv(st):
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([st["title"] + (" (тест)" if st.get("test") else ""), st.get("date", "")])
    w.writerow([])
    w.writerow(["Место", "Пилот", "Номер", "Клуб", "Очки", "Отметка"])
    for u, place in sorted(st.get("places", {}).items(), key=lambda x: x[1]):
        p = pilot(int(u)) or {}
        w.writerow([place, nick(int(u)), p.get("num", ""), (club_of(int(u)) or {}).get("name", "соло"), st["pts"].get(u, 0),
                    "DSQ: " + st["dsq"][u] if u in st.get("dsq", {}) else ""])
    w.writerow([])
    w.writerow(["Пара", "Раунд", "Пилот A", "Пилот B", "Победитель", "OMT", "Голоса судей"])
    for m in st["matches"]:
        if m["bye"]:
            continue
        votes = " | ".join(", ".join(f"{nick(int(j))}: {choice_name(m, v)}" for j, v in t["votes"].items())
                           for t in m["tries"] if t["res"])
        w.writerow([m["id"], m["name"], nick(m["pa"]) if m["pa"] else "", nick(m["pb"]) if m["pb"] else "",
                    nick(m["w"]) if m["w"] else "", m["omt"], votes + (" (вручную)" if m.get("manual") else "")])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def send_file(chat, name, data, caption=""):
    try:
        requests.post(f"{API}/sendDocument", data={"chat_id": chat, "caption": caption}, files={"document": (name, data)}, timeout=30)
    except Exception as e:
        print("Ошибка отправки файла:", e)


def fan_open(st):
    return st["state"] == "done" and not st.get("test") and time.time() - st.get("done_t", 0) < FAN_HOURS * 3600


def fan_best(st):
    cnt = {}
    for mid in st.get("fans", {}).values():
        cnt[mid] = cnt.get(mid, 0) + 1
    return max(cnt, key=lambda k: (cnt[k], -[m["id"] for m in st["matches"]].index(k))) if cnt else None


def fan_vote(uid, sid, mid):
    st = S["stages"].get(str(sid))
    if not st or not fan_open(st):
        return {"ok": False, "error": "Голосование за пару этапа закрыто"}
    m = mget(st, mid)
    if not m or m["bye"] or not m["done"] or m["pa"] is None or m["pb"] is None:
        return {"ok": False, "error": "За эту пару голосовать нельзя"}
    st.setdefault("fans", {})[str(uid)] = mid
    return {"ok": True}


# ---------- Ручные правки организатора ----------
def can_reset(st, m):
    deps = [x for x in st["matches"] if (x["a"].get("m") == m["id"] or x["b"].get("m") == m["id"])]
    return all(not x["done"] or x["bye"] for x in deps) and all(not x["tries"] for x in deps)


def set_result(st, m, side, who=None):
    if m["done"] or not m["open"] or m["pa"] is None or m["pb"] is None:
        return "Эта пара ещё не началась или уже решена."
    win, lose = (m["pa"], m["pb"]) if side == "a" else (m["pb"], m["pa"])
    m.update(done=True, w=win, l=lose, manual=True)
    log(who, f"{st['title']} · {m['name']}: вручную {nick(win)} > {nick(lose)}")
    for p in (win, lose):
        send(p, f"⚖️ Организатор поставил результат · {m['name']}: побеждает {nick(win)}.")
    advance(st)
    return "ok"


def reset_result(st, m, who=None):
    if not m["done"] or m["bye"]:
        return "Сбрасывать нечего."
    if not can_reset(st, m):
        return "Следующая пара уже ехала — сначала сбрось её."
    for x in st["matches"]:  # следующие пары, где были автопроходы из-за этого результата, тоже откатываем
        if (x["a"].get("m") == m["id"] or x["b"].get("m") == m["id"]) and x["done"] and x["bye"]:
            x.update(done=False, w=None, l=None, bye=False, open=0)
    m.update(done=False, w=None, l=None, manual=False)
    if m["tries"] and m["tries"][-1]["res"] in ("a", "b"):
        m["tries"][-1]["res"] = "reset"
    if st["state"] == "done":
        st.update(state="run", places={}, pts={})
    log(who, f"{st['title']} · {m['name']}: результат сброшен")
    advance(st)
    return "ok"


def swap_seeds(st, a, bb, who=None):
    if st["state"] != "run" or any(m["tries"] or (m["done"] and not m["bye"]) for m in st["matches"]):
        return "Менять пилотов можно только до первого заезда."
    seeds = st["seeds"]
    if a not in seeds or bb not in seeds:
        return "Этих пилотов нет в сетке."
    i, k = seeds.index(a), seeds.index(bb)
    seeds[i], seeds[k] = seeds[k], seeds[i]
    build_bracket(st, seeds)
    advance(st)
    log(who, f"{st['title']}: поменял местами {nick(a)} и {nick(bb)}")
    return "ok"


def start_bracket(st, seeds, who=None):
    if len(seeds) < 2:
        return "Для сетки нужно хотя бы 2 пилота."
    st["state"] = "run"
    build_bracket(st, seeds[:st["size"]])
    out = [u for u in st["reg"] if u not in st["seeds"]]
    for u in out:
        send(u, f"{st['title']}: в сетку топ-{st['size']} ты не прошёл. Спасибо за участие!")
    log(who, f"{st['title']}: построена сетка на {len(st['seeds'])}")
    advance(st)
    return "ok"


# ---------- Публичные данные для приложения ----------
def pub_pilot(uid):
    if uid is None:
        return None
    p = pilot(uid) or {}
    c = club_of(uid)
    return {"id": p.get("pid"), "nick": nick(uid), "club": c["name"] if c else "", "num": p.get("num")}


def fan_badges(uid):
    out = []
    for st in stage_list(["done"]):
        best = fan_best(st) if not fan_open(st) else None
        m = mget(st, best) if best else None
        if m and uid in (m["pa"], m["pb"]):
            out.append(st["title"])
    return out


def public_data():
    tot = season_points()
    stages = []
    for st in stage_list():
        seeds = {u: i + 1 for i, u in enumerate(st.get("seeds", []))}
        matches = []
        for m in st["matches"]:
            tries = [{"res": t["res"], "votes": [[nick(int(j)), v] for j, v in t["votes"].items()]} for t in m["tries"]
                     if t["res"] is not None]
            matches.append({"id": m["id"], "name": m["name"], "a": pub_pilot(m["pa"]), "b": pub_pilot(m["pb"]),
                            "sa": seeds.get(m["pa"]), "sb": seeds.get(m["pb"]),
                            "w": "a" if m["w"] is not None and m["w"] == m["pa"] else "b" if m["w"] is not None else None,
                            "done": m["done"], "bye": m["bye"], "live": bool(m["open"] and not m["done"]),
                            "judging": bool(try_open(m)) and not m["done"], "omt": m["omt"], "tries": tries, "manual": m.get("manual", False),
                            "until": (m["open"] + (st.get("dl", 0) + m.get("ext", 0)) * 60) if st.get("dl") and m["open"] and not m["done"] else None,
                            "protest": {"by": nick(m["protest"]["by"]), "why": m["protest"]["why"], "res": m["protest"]["res"],
                                        "votes": [[nick(int(j)), v] for j, v in m["protest"]["votes"].items()] if m["protest"]["res"] else []}
                            if m.get("protest") else None})
        qual = sorted(([nick(int(u)), r["score"], r["st"]] for u, r in st["qruns"].items()), key=lambda x: (x[2] != "ok", -x[1]))
        stages.append({"id": st["id"], "n": st["n"], "title": st["title"], "date": st.get("date", ""), "state": st["state"],
                       "size": st["size"], "de": st["de"], "qual": st["qual"], "jn": st["jn"], "dl": st.get("dl", 0),
                       "pw": st.get("pw", 0), "season": st.get("season", 1), "test": st.get("test", False),
                       "appr": st.get("appr", False), "sched": st.get("sched", ""),
                       "dsq": [[nick(int(u)), why] for u, why in st.get("dsq", {}).items()],
                       "fans": {mid: sum(1 for v in st.get("fans", {}).values() if v == mid) for mid in set(st.get("fans", {}).values())},
                       "fan_open": fan_open(st), "fan_until": st.get("done_t", 0) + FAN_HOURS * 3600 if st.get("done_t") else None,
                       "fan_best": fan_best(st) if st["state"] == "done" and not fan_open(st) else None,
                       "judges": [nick(j) for j in st["judges"]], "reg": [pub_pilot(u) for u in st["reg"]],
                       "qruns": qual, "matches": matches, "final": st.get("final"), "third": st.get("third"),
                       "bsize": st.get("bsize"),
                       "places": sorted(([pub_pilot(int(u)), p, st["pts"].get(u, 0)] for u, p in st["places"].items()),
                                        key=lambda x: x[1]),
                       "tz": {k: {"text": v.get("text", ""), "video": bool(v.get("video"))} for k, v in st["tz"].items()}})
    pilots = []
    cur_done = [st for st in stage_list(["done"]) if st.get("season", 1) == cur_season() and not st.get("test")]
    for u, p in S["pilots"].items():
        c = club_of(int(u))
        per = {st["id"]: st["pts"].get(u, 0) for st in cur_done if u in st["pts"]}
        w = l = 0
        for st in S["stages"].values():
            if st.get("season", 1) != cur_season() or st.get("test"):
                continue
            for m in st["matches"]:
                if m["done"] and not m["bye"]:
                    w += m["w"] == int(u)
                    l += m["l"] == int(u)
        hist = [[st["title"], st["places"][u], st["pts"].get(u, 0)] for st in cur_done if u in st["places"]]
        pilots.append({"id": p["pid"], "nick": p["nick"], "club": c["name"] if c else "", "pts": tot.get(u, 0), "stages": per,
                       "best": min((h[1] for h in hist), default=None), "w": w, "l": l, "hist": hist, "num": p.get("num"),
                       "car": p.get("car", ""), "photo": p.get("photo", {}).get("k"), "fan": fan_badges(int(u)),
                       "banned": S["banned"].get(u)})
    pilots.sort(key=lambda x: (-x["pts"], x["best"] or 999, x["nick"].lower()))
    clubs = [{"id": c["id"], "name": c["name"], "leader": nick(c["leader"]), "members": [pub_pilot(u) for u in c["members"]],
              "pts": club_points(cid, tot)} for cid, c in S["clubs"].items()]
    clubs.sort(key=lambda x: (-x["pts"], x["name"].lower()))
    content = {k: {"text": v.get("text", ""), "photos": [ph["k"] for ph in v.get("photos", [])]} for k, v in S["content"].items()}
    return {"title": TITLE, "bot": BOT_USERNAME, "stages": stages, "pilots": pilots, "clubs": clubs, "content": content,
            "points": S["points"], "club_max": CLUB_MAX, "season": S["season"], "seasons": S["seasons"][::-1],
            "news": S["news"][:10]}


# ---------- Меню ----------
HELP = ("🏁 Khinkali Racing Championship — чемпионат по дрифту в CarX Drift Racing 3.\n\n"
        "• Стань пилотом (укажи ник в игре) и записывайся на этапы.\n"
        "• Капитан создаёт клуб и добавляет до 6 пилотов. Очки пилотов идут и в личный, и в клубный зачёт.\n"
        "• Квалификация — видео соло-заезда по ТЗ и твой счёт; судьи проверяют.\n"
        "• Парные заезды — 2 видео (ведёт один, потом другой). Сдаёт любой из пары. Судьи решают: победа или OMT.\n"
        "• Сетки, таблицы и регламент — в приложении.\n\n"
        "/menu — главное меню · /club — клуб · /stages — этапы · /cancel — отменить ввод · /myid — твой ID")


def home(uid, chat=None, mid=None):
    p = pilot(uid)
    c = club_of(uid)
    open_st = stage_list(["reg", "qual", "run"])
    text = (f"🏁 {TITLE}\n\n" + (f"Пилот: {p['nick']} · {('клуб ' + c['name']) if c else 'соло'}" if p else "Ты ещё не пилот.")
            + ("\n\nСейчас: " + "; ".join(f"{s['title']} — {STATE_RU[s['state']]}" for s in open_st) if open_st else ""))
    rows = [[b("✏️ Мой ник" if p else "🏎 Стать пилотом", "m:pilot"), b("🏁 Этапы", "m:stages")],
            [b("🪪 Анкета: номер, машина, фото", "m:card")] if p else None,
            [b("👥 Клуб", "m:club"), b("❓ Как это работает", "m:help")], app_row(),
            [b("⚙️ Организатор", "m:org")] if is_org(uid) else None]
    if mid:
        edit(chat, mid, text, kb(rows))
    else:
        send(uid, text, kb(rows))


def stages_menu(uid):
    sts = stage_list(["reg", "qual", "run", "done"])
    if not sts:
        send(uid, "Этапов пока нет — организаторы скоро их объявят.", kb([app_row()]))
        return
    rows = []
    for st in sts[-8:]:
        if st["state"] == "reg":
            rows.append([b(("🚪 Выйти: " if uid in st["reg"] else "✋ Участвую: ") + st["title"],
                           f"{'ul' if uid in st['reg'] else 'rg'}:{st['id']}")])
        if st["tz"]:
            rows.append([b(f"📋 ТЗ · {st['title']}", f"tzm:{st['id']}")])
    c = next((x for x in S["clubs"].values() if x["leader"] == uid), None)
    if c:
        rows += [[b(f"👥 Записать весь клуб: {st['title']}", f"rgc:{st['id']}")] for st in sts if st["state"] == "reg"]
    send(uid, "🏁 Этапы:\n\n" + "\n\n".join(stage_desc(st) for st in sts[-4:]), kb(rows + [app_row()]))


def register(uid, sid, quiet=False):
    st = S["stages"].get(sid)
    if not pilot(uid):
        return "Сначала стань пилотом: /menu → «Стать пилотом»."
    if not st or st["state"] != "reg":
        return "Регистрация на этот этап закрыта."
    if str(uid) in S["banned"]:
        return f"Ты отстранён от чемпионата: {S['banned'][str(uid)]}"
    if uid in st["reg"]:
        return "Ты уже записан."
    if len(st["reg"]) >= (REG_MAX if st["qual"] else st["size"]):
        return "Мест больше нет."
    if st.get("appr") and not quiet == "approved":
        if uid in st["pend"]:
            return "Заявка уже у организаторов."
        st["pend"].append(uid)
        orgs_dm(f"📝 Заявка на {st['title']}: {mention(uid)}" + (f" · клуб {club_of(uid)['name']}" if club_of(uid) else " · соло"),
                kb([[b("✅ Принять", f"ap:{st['id']}:{uid}:ok"), b("❌ Отклонить", f"ap:{st['id']}:{uid}:no")]]))
        return "📝 Заявка отправлена организаторам — бот напишет, когда её рассмотрят."
    st["reg"].append(uid)
    if not quiet:
        for k in ("q", "b"):
            if st["tz"].get(k):
                send_tz(uid, st, k)
    return f"✅ Ты записан на {st['title']}."


def card_menu(uid):
    p = pilot(uid)
    if not p:
        return send(uid, "Сначала стань пилотом.")
    send(uid, f"🪪 Анкета пилота {p['nick']}\nНомер: {('#' + str(p['num'])) if p.get('num') else '—'}\n"
              f"Машина: {p.get('car') or '—'}\nФото: {'есть' if p.get('photo') else 'нет'}\n\nВсё это видно в приложении.",
         kb([[b("🔢 Номер", "m:num"), b("🚗 Машина", "m:car")], [b("📸 Фото машины", "m:pphoto")]]))


def club_menu(uid):
    c = club_of(uid)
    own = next((x for x in S["clubs"].values() if x["leader"] == uid), None)
    if own:
        tot = season_points()
        lines = [f"• {nick(u)} — {tot.get(str(u), 0)} очк." for u in own["members"]]
        rows = [[b("➕ Добавить пилота", "cl:add"), b("✏️ Название", "cl:ren")]]
        rows += [[b(f"✖️ {nick(x)}", f"cl:rm:{x}") for x in own["members"][i:i + 2]] for i in range(0, len(own["members"]), 2)]
        if uid not in own["members"]:
            rows.append([b("➕ Добавить себя", "cl:me")])
        send(uid, f"👥 Клуб {own['name']} (ты капитан) · {len(own['members'])}/{CLUB_MAX}\n" + ("\n".join(lines) or "Пока пусто")
             + f"\n\nОчки клуба: {club_points(own['id'], tot)}", kb(rows))
        return
    if c:
        send(uid, f"👥 Ты в клубе {c['name']} (капитан {nick(c['leader'])}).", kb([[b("🚪 Выйти из клуба", "cl:leave")]]))
        return
    send(uid, "Ты не в клубе — выступаешь соло. Клуб создаёт капитан и сам добавляет до 6 пилотов.",
         kb([[b("➕ Создать клуб", "cl:new")]]))


def join_club(uid, cid, by=None):
    c = S["clubs"].get(cid)
    p = pilot(uid)
    if not c or not p:
        return "Клуба уже нет."
    if uid in c["members"]:
        return "Ты уже в этом клубе."
    if len(c["members"]) >= CLUB_MAX:
        return f"В клубе уже {CLUB_MAX} пилотов."
    old = S["clubs"].get(p.get("club"))
    if old:
        old["members"] = [u for u in old["members"] if u != uid]
        send(old["leader"], f"👥 {nick(uid)} ушёл из клуба {old['name']} в {c['name']}.")
    c["members"].append(uid)
    p["club"] = cid
    log(by, f"{nick(uid)} → клуб {c['name']}" + (f" (из {old['name']})" if old else ""))
    send(c["leader"], f"👥 {nick(uid)} теперь в клубе {c['name']}.")
    return f"✅ Ты в клубе {c['name']}."


def org_menu(uid):
    sts = stage_list()
    rows = [[b(f"{'📝' if st['state'] == 'draft' else '🏁'} {st['title']} · {STATE_RU[st['state']]}", f"sm:{st['id']}")]
            for st in sts[-10:]]
    rows += [[b("➕ Новый этап", "st:new"), b("👥 Клубы", "oc:list")], [b("📜 Регламент", "ct:rules"), b("ℹ️ О чемпионате", "ct:about")],
             [b("📰 Новости", "nw"), b("🔢 Очки", "pt")], [b("⛔ Отстранения", "bn"), b("📊 Судьи", "js")],
             [b("👔 Организаторы", "og:list"), b("🏆 Сезон", "ss")] if is_owner(uid) else None,
             [b("🚗 Фото регламента авто", "rc")] if uid == CAR_ADMIN else None, app_row()]
    send(uid, f"⚙️ Организатор · {S['season']['name']}: этапы, регламент, судьи.", kb(rows))


def draft_text(d):
    return (("Изменение этапа" if d.get("edit") else "Новый этап") + f"\n\nНазвание: {d['title']}\nДата: {d.get('date') or '—'}"
            + f"\nСетка: топ-{d['size']}\nЛузер-сетка: {'да' if d['de'] else 'нет'}\nКвалификация: {'да' if d['qual'] else 'нет'}"
            + f"\nСудей: {d['jn']}\nДедлайн на пару: {str(d.get('dl', 0)) + ' мин' if d.get('dl') else 'нет'}"
            + f"\nПротесты: {'в течение ' + str(d.get('pw', 0)) + ' мин после решения' if d.get('pw') else 'нет'}"
            + f"\nЗаявки: {'одобряет организатор' if d.get('appr') else 'сразу в списке'}"
            + ("\n🧪 Тестовый этап — очки не начисляются." if d.get("test") else "") + "\nЗаезд за 3-е место есть всегда.")


def draft_kb(d):
    return kb([[b(f"Сетка: топ-{d['size']}", "sd:size"), b(f"Лузер-сетка: {'да' if d['de'] else 'нет'}", "sd:de")],
               [b(f"Квалификация: {'да' if d['qual'] else 'нет'}", "sd:qual"), b(f"Судей: {d['jn']}", "sd:jn")],
               [b(f"Дедлайн: {str(d.get('dl', 0)) + ' мин' if d.get('dl') else 'нет'}", "sd:dl"),
                b(f"Протесты: {str(d.get('pw', 0)) + ' мин' if d.get('pw') else 'нет'}", "sd:pw")],
               [b(f"Заявки: {'с одобрением' if d.get('appr') else 'сразу'}", "sd:appr"),
                b(f"Тест: {'да' if d.get('test') else 'нет'}", "sd:test")],
               [b("✏️ Название", "sd:title"), b("📅 Дата", "sd:date")],
               [b("✅ Сохранить" if d.get("edit") else "✅ Создать", "sd:ok"), b("✖️ Отмена", "sd:no")]])


def stage_menu(uid, st):
    s, sid = st["state"], st["id"]
    rows = []
    if s in ("draft", "reg"):
        rows.append([b("⚙️ Настройки", f"sa:{sid}:edit"), b("👨‍⚖️ Судьи", f"sj:{sid}")])
        rows.append([b("📋 ТЗ квалификации", f"tze:{sid}:q") if st["qual"] else None, b("📋 ТЗ парных", f"tze:{sid}:b")])
    if s == "draft":
        rows.append([b("▶️ Открыть регистрацию", f"sa:{sid}:open"), b("🗑 Удалить", f"sa:{sid}:del")])
    if s == "reg":
        rows.append([b(f"👥 Участники ({len(st['reg'])})", f"sa:{sid}:list")])
        rows.append([b("⏱ Начать квалификацию", f"sa:{sid}:qual")] if st["qual"] else
                    [b("🏗 Сетка по очкам сезона", f"sa:{sid}:bp"), b("🎲 Сетка жребием", f"sa:{sid}:br")])
    if s == "qual":
        ok = sum(1 for r in st["qruns"].values() if r["st"] == "ok")
        rows.append([b("👨‍⚖️ Судьи", f"sj:{sid}"), b(f"⏱ Сдали {len(st['qruns'])}, засчитано {ok}", f"sm:{sid}")])
        rows.append([b("🏗 Закрыть квалификацию и построить сетку", f"sa:{sid}:qbuild")])
    if s == "run":
        rows.append([b("👨‍⚖️ Судьи", f"sj:{sid}"), b("🛠 Результат вручную", f"sa:{sid}:man")])
    if st.get("pend"):
        rows.append([b(f"📝 Заявки ({len(st['pend'])})", f"sa:{sid}:pend")])
    if s in ("draft", "reg", "qual", "run"):
        rows.append([b("🕒 Расписание", f"sa:{sid}:sched"), b("⛔ Дисквалификация", f"sa:{sid}:dsql")] if s != "draft" else
                    [b("🕒 Расписание", f"sa:{sid}:sched")])
    if s in ("run", "done"):
        rows.append([b("⬇️ Итоги таблицей", f"sa:{sid}:csv")])
    rows.append([b("⬅️ Назад", "m:org")])
    send(uid, stage_desc(st) + "\n\nВ приложении сетку можно править: поставить или сбросить результат, поменять пилотов.",
         kb(rows))


# ---------- Ввод текста ----------
def on_text(msg, uid, aw, text):
    u = user(uid)
    u.pop("aw", None)
    if aw == "num" and pilot(uid):
        t = text.strip().lstrip("#")
        if not t.isdigit() or not 1 <= int(t) <= 999:
            u["aw"] = aw
            return send(uid, "Номер — число от 1 до 999:")
        if any(x.get("num") == int(t) and k != str(uid) for k, x in S["pilots"].items()):
            u["aw"] = aw
            return send(uid, f"Номер #{t} уже занят. Выбери другой:")
        pilot(uid)["num"] = int(t)
        return card_menu(uid)
    if aw == "car" and pilot(uid):
        pilot(uid)["car"] = text.strip()[:40]
        return card_menu(uid)
    if aw.startswith("dsq:") and is_org(uid):
        _, sid, x = aw.split(":")
        st = S["stages"].get(sid)
        if st:
            disqualify(st, int(x), text.strip(), uid)
            return send(uid, f"⛔ {nick(int(x))} дисквалифицирован с {st['title']}.")
        return
    if aw == "ban" and is_org(uid):
        x = find_pilot_uid(text)
        if not x:
            u["aw"] = aw
            return send(uid, "Не нашёл пилота. Напиши его ник в игре или @ник:")
        u["aw"] = f"banwhy:{x}"
        return send(uid, f"Причина отстранения {nick(x)}:")
    if aw.startswith("banwhy:") and is_org(uid):
        x = int(aw.split(":")[1])
        ban(x, text.strip(), uid)
        return send(uid, f"⛔ {nick(x)} отстранён от чемпионата.")
    if aw.startswith("sched:") and is_org(uid):
        st = S["stages"].get(aw.split(":")[1])
        if st:
            st["sched"] = text.strip()[:1500]
            return send(uid, "🕒 Расписание сохранено — оно в приложении на странице этапа.")
        return
    if aw.startswith("protest:"):
        _, sid, mid = aw.split(":")
        st = S["stages"].get(sid)
        m = mget(st, mid) if st else None
        return protest_submit(uid, st, m, text.strip()) if m else None
    if aw == "news" and is_org(uid):
        post_news(uid, text.strip())
        return send(uid, "✅ Новость опубликована.")
    if aw == "points" and is_org(uid):
        try:
            vals = [int(x) for x in text.replace(",", " ").split()]
        except ValueError:
            vals = []
        if len(vals) != 8 or any(v < 0 for v in vals):
            u["aw"] = aw
            return send(uid, "Нужно ровно 8 целых чисел через пробел. Ещё раз:")
        S["points"] = [[mx, v] for mx, v in zip([1, 2, 3, 4, 8, 16, 32, 64], vals)]
        recompute_points()
        log(uid, "изменена таблица очков")
        return send(uid, "✅ Очки обновлены, таблицы сезона пересчитаны:\n" + points_text())
    if aw == "season_name" and is_owner(uid):
        S["season"]["name"] = text.strip()[:40]
        return send(uid, f"✅ Сезон: {S['season']['name']}")
    if aw == "nick":
        t = text.strip()
        if not 2 <= len(t) <= 24:
            u["aw"] = aw
            return send(uid, "Ник — от 2 до 24 символов. Напиши ещё раз:")
        if any(p["nick"].lower() == t.lower() and k != str(uid) for k, p in S["pilots"].items()):
            u["aw"] = aw
            return send(uid, "Такой ник уже занят. Напиши другой:")
        p = S["pilots"].get(str(uid))
        if p:
            p["nick"] = t
        else:
            S["pilots"][str(uid)] = {"pid": next_id("pid"), "nick": t, "club": None, "t": int(time.time())}
        send(uid, f"✅ Ник: {t}")
        return home(uid)
    if aw in ("club_name", "club_rename"):
        t = text.strip()
        if not 2 <= len(t) <= 30 or any(c["name"].lower() == t.lower() for c in S["clubs"].values()):
            u["aw"] = aw
            return send(uid, "Название — от 2 до 30 символов и не занятое. Напиши ещё раз:")
        if aw == "club_rename":
            own = next((x for x in S["clubs"].values() if x["leader"] == uid), None)
            if own:
                own["name"] = t
        else:
            cid = str(next_id("club"))
            S["clubs"][cid] = {"id": cid, "name": t, "leader": uid, "members": [], "t": int(time.time())}
            log(uid, f"создан клуб {t}")
        send(uid, f"✅ Клуб: {t}")
        return club_menu(uid)
    if aw == "club_add":
        own = next((x for x in S["clubs"].values() if x["leader"] == uid), None)
        target = user_by_un(text)
        if not own:
            return
        if not target or not pilot(target):
            return send(uid, "Не нашёл такого пилота. Пусть он откроет бота, нажмёт Start и укажет ник — потом добавь его снова.")
        if len(own["members"]) >= CLUB_MAX:
            return send(uid, f"В клубе уже {CLUB_MAX} пилотов.")
        send(target, f"👥 Клуб {own['name']} (капитан {nick(uid)}) зовёт тебя в состав. Вступить?",
             kb([[b("✅ Вступить", f"cl:ok:{own['id']}"), b("❌ Нет", f"cl:no:{own['id']}")]]))
        return send(uid, f"Приглашение отправлено {nick(target)}.")
    if aw in ("draft_title", "draft_date"):
        d = u.get("draft")
        if d:
            d["title" if aw == "draft_title" else "date"] = text.strip()[:60]
            return send(uid, draft_text(d), draft_kb(d))
    if aw.startswith("judge_add:"):
        st = S["stages"].get(aw.split(":")[1])
        target = user_by_un(text)
        if not st:
            return
        if not target:
            return send(uid, "Не нашёл. Судья должен сначала открыть бота и нажать Start.")
        if target not in st["judges"]:
            st["judges"].append(target)
            send(target, f"👨‍⚖️ Тебя назначили судьёй: {st['title']}. Заезды будут приходить сюда, в личку.")
            log(uid, f"{st['title']}: судья {nick(target)}")
        return judges_menu(uid, st)
    if aw == "org_add" and is_owner(uid):
        target = user_by_un(text)
        if not target:
            return send(uid, "Не нашёл. Пусть сначала откроет бота и нажмёт Start.")
        if target not in S["orgs"]:
            S["orgs"].append(target)
            send(target, f"⚙️ Тебя назначили организатором {TITLE}. Меню: /menu → «Организатор».")
        return send(uid, f"✅ {mention(target)} — организатор.")
    if aw.startswith("tz_text:"):
        _, sid, k = aw.split(":")
        st = S["stages"].get(sid)
        if st:
            st["tz"].setdefault(k, {})["text"] = text.strip()[:3500]
            u["aw"] = f"tz_video:{sid}:{k}"
            return send(uid, "Текст сохранён. Теперь пришли видео ТЗ одним сообщением (или /skip — без видео).")
    if aw.startswith("tz_video:") and text.strip() == "/skip":
        return send(uid, "✅ ТЗ сохранено без видео.")
    if aw.startswith("ct_text:"):
        page = aw.split(":")[1]
        S["content"][page]["text"] = text.strip()[:6000]
        send(uid, "✅ Текст сохранён — он уже в приложении.")
        return content_menu(uid, page)
    if aw == "car_photo":
        u["aw"] = aw
        return send(uid, "Жду фото регламента авто (картинкой или файлом). Передумал — /cancel.")
    if aw.startswith("ct_photo:"):
        if text.strip() in ("/done", "/skip"):
            return content_menu(uid, aw.split(":")[1])
        u["aw"] = aw
        return send(uid, "Жду фото. Когда закончишь — /done.")
    if aw.startswith("qscore:"):
        st = S["stages"].get(aw.split(":")[1])
        up = u.pop("qf", None)
        try:
            score = float(text.replace(",", ".").strip())
        except ValueError:
            u["aw"], u["qf"] = aw, up
            return send(uid, "Напиши число — сколько очков в этом заезде:")
        if st and up and st["state"] == "qual":
            qfu = u.pop("qfu", None) or up
            if not st.get("test"):
                mark_videos(qfu)
            return qual_submit(uid, st, up, score)
        return send(uid, "Квалификация уже закрыта.")
    if aw.startswith("qfix:"):
        _, sid, pu = aw.split(":")
        st = S["stages"].get(sid)
        run = st["qruns"].get(pu) if st else None
        try:
            score = float(text.replace(",", ".").strip())
        except ValueError:
            u["aw"] = aw
            return send(uid, "Нужно число:")
        if run and run["st"] == "wait":
            run.update(score=score, st="ok", judge=uid, fixed=True)
            qual_close_msgs(st, int(pu), f"✏️ Квалификация {nick(int(pu))}: исправлено на {score:g} ({nick(uid)})")
            send(int(pu), f"✅ Квалификация засчитана, судья исправил счёт: {score:g}.")
        return
    send(uid, "Не понял. /menu — главное меню.")


def judges_menu(uid, st):
    rows = [[b(f"✖️ {nick(j)}", f"sj:{st['id']}:rm:{j}")] for j in st["judges"]]
    rows += [[b("➕ Добавить судью", f"sj:{st['id']}:add")], [b("⬅️ К этапу", f"sm:{st['id']}")]]
    send(uid, f"👨‍⚖️ Судьи · {st['title']} (нужно: {st['jn']})\n"
              + ("\n".join(f"• {mention(j)}" for j in st["judges"]) or "Пока никого."), kb(rows))


def car_menu(uid):
    has = bool(S["content"]["car"]["photos"])
    rows = [[b("📤 Заменить фото", "rc:new")]]
    if has:
        rows.append([b("↩️ Вернуть картинку из PDF", "rc:reset")])
    rows.append([b("⬅️ Назад", "m:org")])
    send(uid, "🚗 Регламент авто — главная картинка на странице «Регламент» в приложении.\n\n"
              + ("Сейчас стоит фото, которое ты прислал." if has else "Сейчас стоит картинка из PDF регламента.")
              + "\n\nЛучше присылать файлом (без сжатия) — так таблица будет чётче.", kb(rows))


def car_photo_id(msg):
    if msg.get("photo"):
        return msg["photo"][-1]["file_id"]
    d = msg.get("document") or {}
    if (d.get("mime_type") or "").startswith("image/"):
        return d.get("file_id")
    return None


def content_menu(uid, page):
    c = S["content"][page]
    name = "Регламент" if page == "rules" else "О чемпионате"
    rows = [[b("✏️ Текст", f"ct:{page}:text"), b("🖼 Добавить фото", f"ct:{page}:photo")]]
    rows += [[b(f"🗑 Фото {i + 1}", f"ct:{page}:rm:{i}") for i in range(len(c["photos"]))][:8]]
    rows.append([b("⬅️ Назад", "m:org")])
    send(uid, f"📜 {name}\n\n{(c['text'] or 'Текста пока нет.')[:1500]}\n\nФото: {len(c['photos'])}", kb(rows))


# ---------- Сообщения ----------
def video_of(msg):
    v = msg.get("video") or {}
    d = msg.get("document") or {}
    if not v and (d.get("mime_type") or "").startswith("video/"):
        v = d
    return v.get("file_id"), v.get("file_unique_id") or v.get("file_id")


def used_video(vu):
    return vu in S["vids"]


def mark_videos(*vus):
    S["vids"].extend(x for x in vus if x and x not in S["vids"])
    del S["vids"][:-3000]


def handle(msg):
    chat = msg.get("chat", {})
    frm = msg.get("from")
    if not frm or chat.get("type") != "private":
        if BOT_USERNAME and (msg.get("text") or "").startswith("/") and BOT_USERNAME.lower() in (msg.get("text") or "").lower():
            send(chat["id"], f"Всё про чемпионат — в личке: https://t.me/{BOT_USERNAME}")
        return
    uid = frm["id"]
    remember(frm)
    u = user(uid)
    text = msg.get("text") or ""
    vid, vuid = video_of(msg)
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower() if text.startswith("/") else ""
    if cmd == "/cancel":
        u.pop("aw", None)
        u.pop("up", None)
        u.pop("qf", None)
        return send(uid, "Отменено.")
    aw = u.get("aw", "")
    # фото и видео, которых ждём от организатора
    if aw == "pphoto" and msg.get("photo") and pilot(uid):
        u.pop("aw", None)
        pilot(uid)["photo"] = {"k": f"p{next_id('img')}", "f": msg["photo"][-1]["file_id"]}
        send(uid, "📸 Фото машины сохранено — оно в твоём профиле в приложении.")
        return card_menu(uid)
    if aw == "car_photo" and uid == CAR_ADMIN and car_photo_id(msg):
        u.pop("aw", None)
        S["content"]["car"]["photos"] = [{"k": f"c{next_id('img')}", "f": car_photo_id(msg)}]
        send(uid, "✅ Фото регламента авто обновлено — в приложении уже новое.")
        return car_menu(uid)
    if aw.startswith("ct_photo:") and msg.get("photo"):
        page = aw.split(":")[1]
        S["content"][page]["photos"].append({"k": str(next_id("img")), "f": msg["photo"][-1]["file_id"]})
        return send(uid, "🖼 Фото добавлено. Можно ещё, или /done.")
    if aw.startswith("tz_video:") and vid:
        _, sid, k = aw.split(":")
        u.pop("aw", None)
        st = S["stages"].get(sid)
        if st:
            st["tz"].setdefault(k, {})["video"] = vid
        return send(uid, "✅ ТЗ сохранено с видео.")
    if vid:
        st, m = active_for(uid)
        if st and used_video(vuid):
            return send(uid, "⚠️ Это видео уже сдавали в чемпионате. Нужен новый заезд.")
        if not st:
            return send(uid, "Сейчас видео некуда сдать: нет открытой квалификации или пары с тобой.")
        if m is None:  # квалификация
            run = st["qruns"].get(str(uid))
            if run and run["st"] in ("ok", "wait"):
                return send(uid, "Твоя квалификация уже " + ("засчитана." if run["st"] == "ok" else "у судей."))
            u["qf"], u["qfu"], u["aw"] = vid, vuid, f"qscore:{st['id']}"
            return send(uid, "🎥 Видео квалификации получено. Сколько очков в этом заезде? Напиши число:")
        return upload_video(uid, st, m, vid, vuid)
    if cmd == "/start" and arg:
        return deep_start(uid, arg.strip())
    if cmd in ("/start", "/menu"):
        if cmd == "/start" and not pilot(uid):
            send(uid, HELP)
        return home(uid)
    if cmd == "/help":
        return send(uid, HELP)
    if cmd == "/myid":
        return send(uid, f"Твой Telegram ID: {uid}")
    if cmd == "/club":
        return club_menu(uid)
    if cmd == "/stages":
        return stages_menu(uid)
    if cmd == "/carphoto" and uid == CAR_ADMIN:
        return car_menu(uid)
    if cmd == "/admin" and is_org(uid):
        return org_menu(uid)
    if aw and text:
        return on_text(msg, uid, aw, text)
    send(uid, "Меню — /menu, помощь — /help.")


def deep_start(uid, arg):
    parts = arg.split("_")
    if parts[0] == "tz" and len(parts) == 3 and parts[1] in S["stages"]:
        return send_tz(uid, S["stages"][parts[1]], parts[2])
    if parts[0] == "rg" and len(parts) == 2:
        return send(uid, register(uid, parts[1]))
    if parts[0] == "w" and len(parts) == 3 and parts[1] in S["stages"]:
        st = S["stages"][parts[1]]
        m = mget(st, parts[2])
        done = [t for t in (m["tries"] if m else []) if t["res"] in ("a", "b", "omt")]
        if not done:
            return send(uid, "Видео этой пары пока нет.")
        for i, t in enumerate(done):
            api("sendMediaGroup", {"chat_id": uid, "media": [
                {"type": "video", "media": t["v"][0], "caption": f"{m['name']} · попытка {i + 1}: ведёт {side_name(m, 'a')}"},
                {"type": "video", "media": t["v"][1], "caption": f"ведёт {side_name(m, 'b')}"}]})
            send(uid, f"Решение: {choice_name(m, t['res'])}\n" + "\n".join(f"• {nick(int(j))}: {choice_name(m, v)}"
                                                                          for j, v in t["votes"].items()))
        return
    if parts[0] == "club":
        return club_menu(uid)
    home(uid)


# ---------- Кнопки ----------
def handle_cb(cq):
    uid = cq["from"]["id"]
    remember(cq["from"])
    data = cq.get("data", "")
    p = data.split(":")
    msg = cq.get("message") or {}
    k = p[0]
    if k == "m":
        answer(cq)
        if p[1] == "pilot":
            user(uid)["aw"] = "nick"
            return send(uid, "Напиши свой ник в игре (как в CarX):")
        if p[1] == "card":
            return card_menu(uid)
        if p[1] in ("num", "car", "pphoto") and pilot(uid):
            user(uid)["aw"] = p[1]
            return send(uid, {"num": "Напиши свой гоночный номер (1–999):", "car": "Какая у тебя машина? (до 40 символов)",
                              "pphoto": "Пришли фото своей машины (лучше в ливрее) одним фото."}[p[1]])
        return {"stages": stages_menu, "club": club_menu, "help": lambda x: send(x, HELP),
                "org": lambda x: org_menu(x) if is_org(x) else None}.get(p[1], home)(uid)
    if k in ("rg", "ul"):
        st = S["stages"].get(p[1])
        if k == "rg":
            return answer(cq, register(uid, p[1]), True)
        if st and st["state"] == "reg" and uid in st.get("pend", []):
            st["pend"].remove(uid)
            return answer(cq, "Заявка отозвана.", True)
        if st and st["state"] == "reg" and uid in st["reg"]:
            st["reg"].remove(uid)
            return answer(cq, "Ты выписан с этапа.", True)
        return answer(cq, "Выписаться уже нельзя.", True)
    if k == "rgc":
        c = next((x for x in S["clubs"].values() if x["leader"] == uid), None)
        if not c:
            return answer(cq, "Ты не капитан клуба.", True)
        res = [register(u, p[1], quiet=False) for u in c["members"]]
        return answer(cq, f"Записано из клуба: {sum(r.startswith('✅') for r in res)}", True)
    if k == "tz":
        st = S["stages"].get(p[1])
        answer(cq)
        return send_tz(uid, st, p[2]) if st else None
    if k == "tzm":
        st = S["stages"].get(p[1])
        answer(cq)
        for kind in ("q", "b"):
            if st and st["tz"].get(kind):
                send_tz(uid, st, kind)
        return
    if k == "cl":
        return club_cb(cq, uid, p)
    if k == "up":
        return upload_cb(cq, p[1], p[2] if len(p) > 2 else "")
    if k == "wd":
        return withdraw(cq, p[1], p[2], p[3])
    if k == "jv":
        return judge_vote(cq, p[1], p[2], p[3], p[4])
    if k == "jq":
        return qual_judge(cq, p[1], int(p[2]), p[3])
    if k == "pr":
        return protest_start(cq, p[1], p[2])
    if k == "pv":
        return protest_vote(cq, p[1], p[2], p[3])
    if k == "rc":
        if uid != CAR_ADMIN:
            return answer(cq, "Менять это фото может только владелец.", True)
        answer(cq)
        if len(p) > 1 and p[1] == "new":
            user(uid)["aw"] = "car_photo"
            return send(uid, "Пришли новое фото регламента авто — картинкой или файлом. Отмена — /cancel.")
        if len(p) > 1 and p[1] == "reset":
            S["content"]["car"]["photos"] = []
            send(uid, "↩️ Вернул картинку из PDF регламента.")
        return car_menu(uid)
    if not is_org(uid):
        return answer(cq, "Это для организаторов.", True)
    return org_cb(cq, uid, p)


def club_cb(cq, uid, p):
    own = next((x for x in S["clubs"].values() if x["leader"] == uid), None)
    act = p[1]
    if act == "new":
        if own:
            return answer(cq, "У тебя уже есть клуб.", True)
        user(uid)["aw"] = "club_name"
        answer(cq)
        return send(uid, "Название клуба:")
    if act in ("add", "ren", "rm", "me") and not own:
        return answer(cq, "Ты не капитан клуба.", True)
    if act == "add":
        user(uid)["aw"] = "club_add"
        answer(cq)
        return send(uid, "Напиши @ник пилота в Телеграме (он должен уже быть пилотом в боте):")
    if act == "ren":
        user(uid)["aw"] = "club_rename"
        answer(cq)
        return send(uid, "Новое название клуба:")
    if act == "me":
        if not pilot(uid):
            return answer(cq, "Сначала стань пилотом.", True)
        res = join_club(uid, own["id"], uid)
        answer(cq, res, True)
        return club_menu(uid)
    if act == "rm":
        x = int(p[2])
        if x in own["members"]:
            own["members"].remove(x)
            if pilot(x):
                pilot(x)["club"] = None
            send(x, f"👥 Капитан убрал тебя из клуба {own['name']}. Твои очки остаются у тебя.")
            log(uid, f"{nick(x)} убран из клуба {own['name']}")
        answer(cq)
        return club_menu(uid)
    if act == "leave":
        c = club_of(uid)
        if c:
            c["members"] = [x for x in c["members"] if x != uid]
            pilot(uid)["club"] = None
            send(c["leader"], f"👥 {nick(uid)} вышел из клуба {c['name']}.")
            log(uid, f"вышел из клуба {c['name']}")
        return answer(cq, "Ты вышел из клуба. Очки остаются у тебя, у клуба они пропадают.", True)
    if act in ("ok", "no"):
        c = S["clubs"].get(p[2])
        if not c:
            return answer(cq, "Клуба уже нет.", True)
        if act == "no":
            send(c["leader"], f"👥 {nick(uid)} отказался вступать в {c['name']}.")
            return answer(cq, "Отказано.")
        pts = season_points().get(str(uid), 0)
        if club_of(uid) and pts:
            orgs_dm(f"🔁 Переход: {nick(uid)} из {club_of(uid)['name']} в {c['name']}. Его {pts} очк. уйдут в {c['name']}.",
                    kb([[b("✅ Разрешить", f"tr:{uid}:{c['id']}:ok"), b("❌ Отказать", f"tr:{uid}:{c['id']}:no")]]))
            return answer(cq, "Переход посреди сезона — на одобрении у организаторов.", True)
        return answer(cq, join_club(uid, p[2], uid), True)
    answer(cq)


def org_cb(cq, uid, p):
    k = p[0]
    u = user(uid)
    if k == "tr":
        x, cid = int(p[1]), p[2]
        if p[3] == "ok":
            res = join_club(x, cid, uid)
            send(x, res)
            return answer(cq, res, True)
        send(x, "❌ Организатор не разрешил переход в другой клуб посреди сезона.")
        return answer(cq, "Отказано.")
    if k == "st":
        u["draft"] = {"title": f"Этап {S['seq'].get('stage', 0) + 1}", "date": "", "size": 32, "de": False, "qual": True, "jn": 3, "dl": 0, "pw": 0, "test": False, "appr": False}
        answer(cq)
        return send(uid, draft_text(u["draft"]), draft_kb(u["draft"]))
    if k == "sd":
        d = u.get("draft")
        if not d:
            return answer(cq, "Черновик потерялся — начни заново.", True)
        act = p[1]
        if act == "size":
            d["size"] = SIZES[(SIZES.index(d["size"]) + 1) % len(SIZES)]
        elif act in ("de", "qual"):
            d[act] = not d[act]
        elif act == "jn":
            d["jn"] = 1 if d["jn"] == 3 else 3
        elif act == "dl":
            d["dl"] = DL_OPTS[(DL_OPTS.index(d.get("dl", 0)) + 1) % len(DL_OPTS)]
        elif act == "pw":
            d["pw"] = PW_OPTS[(PW_OPTS.index(d.get("pw", 0)) + 1) % len(PW_OPTS)]
        elif act in ("test", "appr"):
            d[act] = not d.get(act)
        elif act in ("title", "date"):
            u["aw"] = f"draft_{act}"
            answer(cq)
            return send(uid, "Название этапа:" if act == "title" else "Дата и время (как написать — свободно):")
        elif act == "no":
            u.pop("draft", None)
            answer(cq)
            return org_menu(uid)
        elif act == "ok":
            u.pop("draft", None)
            if d.get("edit"):
                st = S["stages"].get(d["edit"])
                if not st or st["state"] not in ("draft", "reg"):
                    return answer(cq, "Этап уже начался — настройки менять нельзя.", True)
                st.update({x: d[x] for x in ("title", "date", "size", "de", "qual", "jn", "dl", "pw", "test", "appr")})
                log(uid, f"{st['title']}: изменены настройки")
            else:
                st = new_stage(d)
                log(uid, f"создан {st['title']}")
            answer(cq, "Сохранено")
            return stage_menu(uid, st)
        answer(cq)
        return edit(uid, cq["message"]["message_id"], draft_text(d), draft_kb(d))
    if k == "sm":
        st = S["stages"].get(p[1])
        answer(cq)
        return stage_menu(uid, st) if st else None
    if k == "sj":
        st = S["stages"].get(p[1])
        if not st:
            return answer(cq, "Этапа нет.", True)
        if len(p) > 2 and p[2] == "add":
            u["aw"] = f"judge_add:{st['id']}"
            answer(cq)
            return send(uid, "Напиши @ник судьи в Телеграме (он должен уже открыть бота):")
        if len(p) > 3 and p[2] == "rm":
            j = int(p[3])
            if j in st["judges"]:
                st["judges"].remove(j)
                send(j, f"👨‍⚖️ Ты больше не судья: {st['title']}.")
                log(uid, f"{st['title']}: снят судья {nick(j)}")
        answer(cq)
        return judges_menu(uid, st)
    if k == "tze":
        u["aw"] = f"tz_text:{p[1]}:{p[2]}"
        answer(cq)
        return send(uid, f"Пришли текст ТЗ {'квалификации' if p[2] == 'q' else 'парных заездов'} (потом попрошу видео):")
    if k == "ct":
        page = p[1]
        answer(cq)
        if page not in ("rules", "about"):
            return
        if len(p) == 2:
            return content_menu(uid, page)
        if p[2] == "text":
            u["aw"] = f"ct_text:{page}"
            return send(uid, "Пришли новый текст одним сообщением:")
        if p[2] == "photo":
            u["aw"] = f"ct_photo:{page}"
            return send(uid, "Пришли фото (можно несколько). Когда закончишь — /done.")
        if p[2] == "rm":
            ph = S["content"][page]["photos"]
            if int(p[3]) < len(ph):
                ph.pop(int(p[3]))
            return content_menu(uid, page)
    if k == "og" and is_owner(uid):
        answer(cq)
        if p[1] == "add":
            u["aw"] = "org_add"
            return send(uid, "Напиши @ник нового организатора (он должен уже открыть бота):")
        if p[1] == "rm":
            x = int(p[2])
            if x in S["orgs"]:
                S["orgs"].remove(x)
                send(x, "⚙️ Ты больше не организатор.")
        rows = [[b(f"✖️ {nick(x)}", f"og:rm:{x}")] for x in S["orgs"]] + [[b("➕ Добавить", "og:add")]]
        return send(uid, "👔 Организаторы (владельцы — всегда):\n" + ("\n".join(f"• {mention(x)}" for x in S["orgs"]) or "Пока никого."),
                    kb(rows))
    if k == "ap":
        st = S["stages"].get(p[1])
        x = int(p[2])
        if not st or x not in st.get("pend", []):
            return answer(cq, "Заявка уже рассмотрена.", True)
        st["pend"].remove(x)
        if p[3] == "ok":
            res = register(x, st["id"], quiet="approved")
            send(x, ("✅ Заявка принята! " if res.startswith("✅") else "") + res)
            if res.startswith("✅"):
                for kind in ("q", "b"):
                    if st["tz"].get(kind):
                        send_tz(x, st, kind)
            return answer(cq, res, True)
        send(x, f"❌ Заявку на {st['title']} отклонили.")
        return answer(cq, "Отклонено", True)
    if k == "bn":
        answer(cq)
        if len(p) > 1 and p[1] == "add":
            u["aw"] = "ban"
            return send(uid, "Кого отстранить? Ник в игре или @ник:")
        if len(p) > 2 and p[1] == "rm":
            if S["banned"].pop(p[2], None) is not None:
                send(int(p[2]), f"✅ Тебя вернули в {TITLE}.")
                log(uid, f"возвращён {nick(int(p[2]))}")
        rows = [[b(f"✅ Вернуть {nick(int(x))}", f"bn:rm:{x}")] for x in S["banned"]]
        return send(uid, "⛔ Отстранены от чемпионата:\n" + ("\n".join(f"• {nick(int(x))} — {w}" for x, w in S["banned"].items()) or "никого"),
                    kb(rows + [[b("➕ Отстранить пилота", "bn:add")], [b("⬅️ Назад", "m:org")]]))
    if k == "js":
        answer(cq)
        return send(uid, judge_stats_text(), kb([[b("⬅️ Назад", "m:org")]]))
    if k == "mx":
        st = S["stages"].get(p[1])
        m = mget(st, p[2]) if st else None
        if not m or m["done"]:
            return answer(cq, "Пара уже решена.", True)
        m["ext"] = m.get("ext", 0) + 30
        m.pop("dlw", None)
        for x in (m["pa"], m["pb"]):
            send(x, f"⏳ {m['name']}: организатор продлил время на заезд на 30 мин.")
        return answer(cq, "Продлено на 30 мин", True)
    if k == "nw":
        answer(cq)
        if len(p) > 2 and p[1] == "rm" and int(p[2]) < len(S["news"]):
            S["news"].pop(int(p[2]))
        if len(p) > 1 and p[1] == "add":
            u["aw"] = "news"
            return send(uid, "Напиши новость одним сообщением — она появится в приложении и придёт в личку всем пилотам:")
        rows = [[b(f"🗑 {n['text'][:30]}", f"nw:rm:{i}")] for i, n in enumerate(S["news"][:8])]
        return send(uid, "📰 Новости", kb([[b("✍️ Написать новость", "nw:add")]] + rows + [[b("⬅️ Назад", "m:org")]]))
    if k == "pt":
        answer(cq)
        if len(p) > 1 and p[1] == "edit":
            u["aw"] = "points"
            return send(uid, "Напиши 8 чисел через пробел — очки за места: 1, 2, 3, 4, 5–8, 9–16, 17–32, 33–64.\n"
                             "Например: 100 88 76 64 48 32 16 8")
        return send(uid, "🔢 Очки за место на этапе:\n" + points_text(), kb([[b("✏️ Изменить", "pt:edit")], [b("⬅️ Назад", "m:org")]]))
    if k == "ss" and is_owner(uid):
        answer(cq)
        if len(p) > 1 and p[1] == "name":
            u["aw"] = "season_name"
            return send(uid, "Название сезона:")
        if len(p) > 1 and p[1] == "end":
            if stage_list(["reg", "qual", "run"]):
                return send(uid, "Сначала заверши все идущие этапы.")
            return send(uid, f"Завершить «{S['season']['name']}»? Итоги уйдут в архив, новый сезон начнётся с нуля.",
                        kb([[b("🏁 Да, завершить", "ss:yes"), b("✖️ Нет", "ss")]]))
        if len(p) > 1 and p[1] == "yes":
            if stage_list(["reg", "qual", "run"]):
                return send(uid, "Сначала заверши все идущие этапы.")
            finish_season(uid)
        return send(uid, f"🏆 Сейчас: {S['season']['name']}. Прошлых сезонов: {len(S['seasons'])}.",
                    kb([[b("✏️ Название", "ss:name"), b("🏁 Завершить сезон", "ss:end")], [b("⬅️ Назад", "m:org")]]))
    if k == "oc":
        answer(cq)
        c = S["clubs"].get(p[2]) if len(p) > 2 else None
        if p[1] == "ask" and c:
            return send(uid, f"Удалить клуб {c['name']}? Пилоты станут соло, их очки останутся у них.",
                        kb([[b("🗑 Да, удалить", f"oc:del:{c['id']}"), b("✖️ Нет", "oc:list")]]))
        if p[1] == "del" and c:
            S["clubs"].pop(c["id"], None)
            for x in c["members"]:
                if pilot(x):
                    pilot(x)["club"] = None
                send(x, f"👥 Организатор удалил клуб {c['name']}. Ты теперь выступаешь соло, твои очки остаются у тебя.")
            if c["leader"] not in c["members"]:
                send(c["leader"], f"👥 Организатор удалил твой клуб {c['name']}.")
            log(uid, f"удалён клуб {c['name']}")
            send(uid, f"✅ Клуб {c['name']} удалён.")
        rows = [[b(f"🗑 {x['name']} ({len(x['members'])}/{CLUB_MAX})", f"oc:ask:{x['id']}")] for x in S["clubs"].values()]
        return send(uid, "👥 Клубы — нажми, чтобы удалить:" if rows else "Клубов пока нет.", kb(rows + [[b("⬅️ Назад", "m:org")]]))
    if k == "sa":
        return stage_action(cq, uid, S["stages"].get(p[1]), p[2], p[3:] if len(p) > 3 else [])
    if k == "mr":
        st = S["stages"].get(p[1])
        m = mget(st, p[2]) if st else None
        if not m:
            return answer(cq, "Пары нет.", True)
        res = reset_result(st, m, uid) if p[3] == "reset" else set_result(st, m, p[3], uid)
        return answer(cq, "Готово" if res == "ok" else res, True)
    answer(cq)


def stage_action(cq, uid, st, act, extra):
    if not st:
        return answer(cq, "Этапа нет.", True)
    s = st["state"]
    if act == "edit":
        if s not in ("draft", "reg"):
            return answer(cq, "Этап уже начался.", True)
        user(uid)["draft"] = {x: st.get(x, 0) for x in ("title", "date", "size", "de", "qual", "jn", "dl", "pw", "test", "appr")} | {"edit": st["id"]}
        answer(cq)
        return send(uid, draft_text(user(uid)["draft"]), draft_kb(user(uid)["draft"]))
    if act == "del" and s == "draft":
        S["stages"].pop(st["id"], None)
        log(uid, f"удалён {st['title']}")
        answer(cq, "Удалено")
        return org_menu(uid)
    if act == "open" and s == "draft":
        st["state"] = "reg"
        log(uid, f"{st['title']}: открыта регистрация")
        for x in list(S["pilots"]):
            send(int(x), f"🏁 Открыта регистрация: {st['title']}" + (f" · {st['date']}" if st.get("date") else "")
                 + f"\nТоп-{st['size']}, {'с квалификацией' if st['qual'] else 'без квалификации'}.",
                 kb([[b("✋ Участвую", f"rg:{st['id']}")]]))
        answer(cq, "Регистрация открыта")
        return stage_menu(uid, st)
    if act == "pend":
        answer(cq)
        rows = [[b(f"✅ {nick(x)}", f"ap:{st['id']}:{x}:ok"), b("❌", f"ap:{st['id']}:{x}:no")] for x in st.get("pend", [])[:30]]
        return send(uid, f"📝 Заявки · {st['title']}: {len(st.get('pend', []))}", kb(rows + [[b("⬅️ К этапу", f"sm:{st['id']}")]]))
    if act == "sched":
        user(uid)["aw"] = f"sched:{st['id']}"
        answer(cq)
        return send(uid, "Напиши расписание этапа одним сообщением, например:\n19:00 — квалификация\n20:00 — топ-16\n21:00 — финалы")
    if act == "dsql":
        answer(cq)
        who = st["seeds"] if s == "run" else st["reg"]
        rows = [[b(f"⛔ {nick(x)}", f"sa:{st['id']}:dsq:{x}")] for x in who if str(x) not in st.get("dsq", {})][:40]
        return send(uid, "⛔ Кого дисквалифицировать с этапа?", kb(rows + [[b("⬅️ К этапу", f"sm:{st['id']}")]]))
    if act == "dsq":
        user(uid)["aw"] = f"dsq:{st['id']}:{extra[0]}"
        answer(cq)
        return send(uid, f"Причина дисквалификации {nick(int(extra[0]))}:")
    if act == "csv":
        answer(cq)
        return send_file(uid, f"KRC_{st['title']}.csv".replace(" ", "_"), stage_csv(st), f"Итоги: {st['title']}")
    if act == "list":
        answer(cq)
        rows = [[b(f"✖️ {nick(x)}", f"sa:{st['id']}:kick:{x}") for x in st["reg"][i:i + 2]] for i in range(0, len(st["reg"]), 2)]
        return send(uid, f"👥 {st['title']}: {len(st['reg'])}\n" + "\n".join(f"{i + 1}. {nick(x)}" for i, x in enumerate(st["reg"])),
                    kb(rows[:40] + [[b("⬅️ К этапу", f"sm:{st['id']}")]]))
    if act == "kick" and s in ("reg", "qual"):
        x = int(extra[0])
        if x in st["reg"]:
            st["reg"].remove(x)
            st["qruns"].pop(str(x), None)
            send(x, f"Организатор снял тебя с этапа {st['title']}.")
            log(uid, f"{st['title']}: снят {nick(x)}")
        return answer(cq, "Снят", True)
    busy = [x for x in stage_list(["qual", "run"]) if x["id"] != st["id"]]
    if act in ("qual", "bp", "br") and busy:  # видео пилота бот привязывает к идущему этапу — двух сразу быть не должно
        return answer(cq, f"Сначала заверши «{busy[0]['title']}»: два этапа одновременно идти не могут.", True)
    if act == "qual" and s == "reg":
        st["state"] = "qual"
        log(uid, f"{st['title']}: квалификация")
        for x in st["reg"]:
            send(x, f"⏱ {st['title']}: квалификация открыта! Пришли сюда видео соло-заезда по ТЗ, потом бот спросит счёт.")
            if st["tz"].get("q"):
                send_tz(x, st, "q")
        if not st["judges"]:
            send(uid, "⚠️ Судей пока нет — назначь, иначе квалификацию некому проверять.")
        answer(cq, "Квалификация открыта")
        return stage_menu(uid, st)
    if act in ("bp", "br", "qbuild"):
        if act == "qbuild" and s == "qual":
            ok = sorted(((u, r) for u, r in st["qruns"].items() if r["st"] == "ok"), key=lambda x: (-x[1]["score"], x[1]["t"]))
            seeds = [int(u) for u, _ in ok]
        elif act in ("bp", "br") and s == "reg":
            tot = season_points()
            seeds = st["reg"][:]
            random.shuffle(seeds)
            if act == "bp":
                seeds.sort(key=lambda x: -tot.get(str(x), 0))
        else:
            return answer(cq, "Сейчас так нельзя.", True)
        res = start_bracket(st, seeds, uid)
        answer(cq, "Сетка построена" if res == "ok" else res, True)
        return stage_menu(uid, st)
    if act == "man" and s == "run":
        rows = []
        for m in st["matches"]:
            if m["open"] and not m["done"]:
                rows.append([b(f"{m['id']}: {nick(m['pa'])}", f"mr:{st['id']}:{m['id']}:a"),
                             b(f"{nick(m['pb'])}", f"mr:{st['id']}:{m['id']}:b")])
        answer(cq)
        return send(uid, "🛠 Тех. победа: нажми на победителя пары." if rows else "Сейчас нет открытых пар.",
                    kb(rows[:30] + [[b("⬅️ К этапу", f"sm:{st['id']}")]]))
    answer(cq, "Сейчас так нельзя.", True)


# ---------- Мини-приложение: проверка подписи и правки сетки ----------
def webapp_user(init):
    try:
        pairs = dict(parse_qsl(init or "", keep_blank_values=True))
        got = pairs.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        if not got or not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), got):
            return None
        if time.time() - int(pairs.get("auth_date", 0)) > 7 * 86400:
            return None
        return json.loads(pairs.get("user", "{}")) or None
    except Exception:
        return None


def app_api(req):
    u = webapp_user(req.get("init"))
    if not u:
        return {"ok": False, "error": "Открой приложение в Телеграме"}
    uid = u["id"]
    if req.get("act") == "me":
        p = pilot(uid)
        mine = {sid: st["fans"][str(uid)] for sid, st in S["stages"].items() if str(uid) in st.get("fans", {})}
        return {"ok": True, "org": is_org(uid), "pid": p["pid"] if p else None, "fans": mine}
    if req.get("act") == "fan":
        return fan_vote(uid, req.get("sid"), req.get("mid"))
    if not is_org(uid):
        return {"ok": False, "error": "Править сетку могут только организаторы"}
    st = S["stages"].get(str(req.get("sid")))
    if not st:
        return {"ok": False, "error": "Этапа нет"}
    act = req.get("act")
    if act in ("win", "reset"):
        m = mget(st, req.get("mid"))
        if not m:
            return {"ok": False, "error": "Пары нет"}
        res = set_result(st, m, req.get("side"), uid) if act == "win" else reset_result(st, m, uid)
    elif act == "swap":
        res = swap_seeds(st, uid_by_pid(req.get("a")), uid_by_pid(req.get("b")), uid)
    else:
        res = "неизвестное действие"
    return {"ok": res == "ok", "error": None if res == "ok" else res}


# ---------- Напоминания судьям и самопинг ----------
_auto = {"ping": 0}


def remind_judges(now):
    changed = False
    for st in stage_list(["run"]):
        for m in st["matches"]:
            t = try_open(m)
            if t and not t.get("rem") and now - t["t"] > JUDGE_REMIND_MIN * 60:
                t["rem"] = 1
                changed = True
                for j in eligible(st, m):
                    if str(j) not in t["votes"]:
                        send(j, f"⏰ Ждём твой голос: {st['title']} · {m['name']} ({side_name(m, 'a')} vs {side_name(m, 'b')}).")
    return changed


def scheduler_loop():
    while True:
        try:
            # Самопинг (чтобы бесплатный Render не засыпал) включается только явно: SELF_PING=1.
            # Бесплатные часы Render общие на все сервисы — второй неспящий бот съест их у клубного.
            url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/") if os.environ.get("SELF_PING") == "1" else ""
            if url and time.time() - _auto["ping"] > 600:
                _auto["ping"] = time.time()
                try:
                    requests.get(url + "/", timeout=20)
                except Exception:
                    pass
            with LOCK:
                load_state()
                if DB_OK and (remind_judges(time.time()) | deadlines(time.time())):
                    save_state()
        except Exception as e:
            print("Ошибка планировщика:", repr(e))
        time.sleep(60)


# ---------- Картинки регламента для приложения ----------
_img = {}


def image_bytes(key):
    if key in _img:
        return _img[key]
    f = next((ph["f"] for c in S["content"].values() for ph in c.get("photos", []) if ph["k"] == key), None) or \
        next((p["photo"]["f"] for p in S["pilots"].values() if p.get("photo", {}).get("k") == key), None)
    if not f:
        return None
    path = api("getFile", {"file_id": f}).get("result", {}).get("file_path")
    if not path:
        return None
    r = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{path}", timeout=30)
    if r.status_code == 200:
        _img[key] = r.content
        return r.content
    return None


# ---------- Сервер ----------
class Handler(BaseHTTPRequestHandler):
    def reply_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.startswith("/api"):
            with LOCK:
                try:
                    load_state()
                    res = app_api(json.loads(body or b"{}"))
                    if res.get("ok"):
                        save_state()
                except Exception as e:
                    print("Ошибка /api:", repr(e))
                    res = {"ok": False, "error": "ошибка бота"}
            return self.reply_json(res)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
        with LOCK:
            try:
                update = json.loads(body or b"{}")
                load_state()
                if "message" in update:
                    handle(update["message"])
                elif "callback_query" in update:
                    handle_cb(update["callback_query"])
            except Exception as e:
                print("Ошибка:", repr(e))
            save_state()

    def do_GET(self):
        if self.path.startswith("/data"):
            with LOCK:
                load_state()
                data = public_data()
            return self.reply_json(data)
        if self.path.startswith("/img/"):
            with LOCK:
                load_state()
                try:
                    data = image_bytes(self.path.split("/")[2].split("?")[0])
                except Exception as e:
                    print("Ошибка картинки:", repr(e))
                    data = None
            self.send_response(200 if data else 404)
            self.send_header("Access-Control-Allow-Origin", "*")
            if data:
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            if data:
                self.wfile.write(data)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"KRC bot is running")

    def log_message(self, *args):
        pass


MENU = [("menu", "Главное меню"), ("stages", "Этапы и регистрация"), ("club", "Мой клуб"),
        ("help", "Как это работает"), ("cancel", "Отменить ввод"), ("myid", "Мой Telegram ID")]


def setup():
    global BOT_USERNAME
    if not BOT_USERNAME:
        BOT_USERNAME = api("getMe", {}).get("result", {}).get("username", "")
    url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if url:
        res = api("setWebhook", {"url": url + "/", "allowed_updates": ["message", "callback_query"]})
        print("Вебхук:", "настроен" if res.get("ok") else res)
    api("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in MENU]})
    if APP_URL:  # кнопка «Чемпионат» слева от поля ввода — открывает приложение
        api("setChatMenuButton", {"menu_button": {"type": "web_app", "text": "Чемпионат", "web_app": {"url": APP_URL}}})
    if not OWNER_IDS:
        print("Внимание: не задан OWNER_IDS — владельцев нет, меню организатора никому не доступно.")
    print("База:", "подключена" if DB_OK else "НЕ подключена")


if __name__ == "__main__":
    load_state()
    setup()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), Handler).serve_forever()
