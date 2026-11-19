# Проверки bot.py с поддельными Telegram и Supabase — без интернета и без ключей.
# Запуск из корня репозитория: python tests/test_bot.py (GitHub запускает их сам при каждом изменении).
import sys, types, json, os, time, hmac, hashlib, urllib.parse

calls = []
DB = {"data": None}


class Resp:
    def __init__(self, data, code=200):
        self._d, self.status_code, self.text, self.content = data, code, json.dumps(data), b"jpgbytes"

    def json(self):
        return self._d


def fake_get(url, **kw):
    if "supabase" in url:
        return Resp([{"data": json.loads(json.dumps(DB["data"]))}] if DB["data"] is not None else [])
    return Resp(None)


def fake_post(url, json=None, **kw):
    if "supabase" in url:
        DB["data"] = json["data"]
        return Resp(None, 201)
    method = url.rsplit("/", 1)[1]
    calls.append((method, json))
    if method == "sendMessage":
        return Resp({"ok": True, "result": {"message_id": 1000 + len(calls)}})
    if method == "getFile":
        return Resp({"ok": True, "result": {"file_path": "photos/x.jpg"}})
    return Resp({"ok": True, "result": True})


def fake_patch(url, json=None, **kw):
    DB["data"] = json["data"]
    return Resp(None, 204)


req = types.ModuleType("requests")
req.get, req.post, req.patch = fake_get, fake_post, fake_patch
sys.modules["requests"] = req
os.environ.update(BOT_TOKEN="1:x", SUPABASE_URL="https://x.supabase.co", SUPABASE_KEY="sb_secret_x", OWNER_IDS="1",
                  BOT_USERNAME="krc_bot", APP_URL="https://example.com/app")
BOT_PY = "bot.py" if os.path.exists("bot.py") else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot.py")
bot = types.ModuleType("bot")
exec(compile(open(BOT_PY, encoding="utf-8").read(), "bot.py", "exec"), bot.__dict__)

checks = []
mid = [100]


def check(name, cond, info=""):
    checks.append(("OK  " if cond else "FAIL") + " " + name + ("" if cond else "  ->  " + str(info)[:300]))


def U(uid):
    return {"id": uid, "first_name": f"N{uid}", "username": f"user{uid}"}


def dm(uid, text=None, **extra):
    mid[0] += 1
    m = {"message_id": mid[0], "chat": {"id": uid, "type": "private"}, "from": U(uid)}
    if text is not None:
        m["text"] = text
    m.update(extra)
    calls.clear()
    bot.load_state(); bot.handle(m); bot.save_state()
    return list(calls)


def tap(uid, data, msg_id=5):
    calls.clear()
    bot.load_state()
    bot.handle_cb({"id": "q", "from": U(uid), "data": data, "message": {"message_id": msg_id, "chat": {"id": uid}}})
    bot.save_state()
    return list(calls)


def vid(uid, f):
    return dm(uid, video={"file_id": f})


def texts(c):
    return " || ".join((p or {}).get("text") or (p or {}).get("caption") or "" for m, p in c
                       if m in ("sendMessage", "answerCallbackQuery", "sendVideo", "editMessageText"))


def btns(c):
    return [x["callback_data"] for m, p in c for row in ((p or {}).get("reply_markup") or {}).get("inline_keyboard", [])
            for x in row if "callback_data" in x]


def to(c, uid):
    return [p for m, p in c if (p or {}).get("chat_id") == uid]


def S():
    bot.load_state()
    return bot.S


def init_for(uid, bad=False):
    pairs = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": "x"})}
    secret = hmac.new(b"WebAppData", b"1:x", hashlib.sha256).digest()
    h = hmac.new(secret, "\n".join(f"{k}={v}" for k, v in sorted(pairs.items())).encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(dict(pairs, hash="0" * 64 if bad else h))


def api(req_):
    bot.load_state()
    res = bot.app_api(req_)
    if res.get("ok"):
        bot.save_state()
    return res


# ---------- старт, пилоты ----------
bot.load_state()
check("own state row created on first load", bot.DB_OK and DB["data"] is not None)
c = dm(101, "/start")
check("start shows help and menu", "Khinkali Racing Championship" in texts(c) and "m:pilot" in btns(c))
tap(101, "m:pilot"); c = dm(101, "Ivan")
check("pilot registered", S()["pilots"]["101"]["nick"] == "Ivan" and S()["pilots"]["101"]["pid"] == 1)
tap(102, "m:pilot"); c = dm(102, "ivan")
check("duplicate nick rejected", "занят" in texts(c) and "102" not in S()["pilots"])
c = dm(102, "A"); check("too short nick rejected", "от 2 до 24" in texts(c))
dm(102, "Petya")
for i in range(103, 125):
    dm(i, "/start"); tap(i, "m:pilot"); dm(i, f"Pilot{i}")
check("many pilots", len(S()["pilots"]) == 24)
c = dm(101, "/cancel"); check("cancel", "Отменено" in texts(c))
c = dm(101, "просто текст"); check("unknown text hint", "/menu" in texts(c))
mid[0] += 1; calls.clear(); bot.load_state()
bot.handle({"message_id": mid[0], "chat": {"id": -100, "type": "supergroup"}, "from": U(101), "text": "/start@krc_bot"})
check("group command points to DM", "t.me/krc_bot" in texts(calls))

# ---------- роли ----------
dm(2, "/start"); dm(11, "/start"); dm(12, "/start"); dm(13, "/start")
c = dm(2, "/admin"); check("non-org has no admin menu", "st:new" not in btns(c))
c = tap(2, "st:new"); check("non-org cannot press org buttons", "для организаторов" in texts(c))
tap(1, "og:list"); tap(1, "og:add"); c = dm(1, "@user2")
check("owner adds organizer", 2 in S()["orgs"] and any("организатором" in p.get("text", "") for p in to(c, 2)))
c = dm(2, "/admin"); check("organizer menu", "st:new" in btns(c) and "og:list" not in btns(c))
c = tap(2, "og:add"); check("only owner manages organizers", not any(p.get("text", "").startswith("Напиши @ник нового") for p in to(c, 2)))

# ---------- клубы ----------
tap(101, "cl:new"); dm(101, "Khinkali")
cid = next(iter(S()["clubs"]))
check("club created", S()["clubs"][cid]["leader"] == 101)
tap(102, "cl:new"); c = dm(102, "khinkali"); check("duplicate club name rejected", "не занятое" in texts(c))
dm(102, "/cancel")
c = tap(101, "cl:me"); check("leader adds himself", 101 in S()["clubs"][cid]["members"])
for i in (103, 104, 105, 106, 107):
    tap(101, "cl:add"); c = dm(101, f"@user{i}")
    check(f"invite sent {i}", any(f"cl:ok:{cid}" == x for x in btns(to(c, i) and [("x", p) for p in to(c, i)])))
    tap(i, f"cl:ok:{cid}")
check("club has 6", len(S()["clubs"][cid]["members"]) == 6)
tap(101, "cl:add"); c = dm(101, "@user108"); check("7th member refused", "уже 6" in texts(c))
c = tap(108, f"cl:ok:{cid}"); check("7th cannot join via old invite", len(S()["clubs"][cid]["members"]) == 6)
tap(101, "cl:add"); c = dm(101, "@nobody"); check("unknown user", "Не нашёл" in texts(c))
c = tap(108, "cl:add"); check("non-leader cannot add", "не капитан" in texts(c))
tap(110, "cl:new"); dm(110, "Chacha Drift")
cid2 = next(k for k, v in S()["clubs"].items() if v["name"] == "Chacha Drift")
for i in (110, 111, 112):
    tap(110, "cl:add"); dm(110, f"@user{i}"); tap(i, f"cl:ok:{cid2}")
check("second club", len(S()["clubs"][cid2]["members"]) == 3)
c = tap(101, "m:club"); check("club menu shows members", "Khinkali" in texts(c) and f"cl:rm:103" in btns(c))

# ---------- этап 1: топ-16, квалификация, 3 судьи ----------
tap(2, "st:new")
check("draft defaults", S()["users"]["2"]["draft"]["size"] == 32)
tap(2, "sd:size"); tap(2, "sd:size")
check("size cycles to 16", S()["users"]["2"]["draft"]["size"] == 16)
tap(2, "sd:title"); dm(2, "Этап 1 · Ebisu"); tap(2, "sd:date"); dm(2, "20 ноября, 19:00")
c = tap(2, "sd:ok")
st1 = "1"
check("stage created", S()["stages"][st1]["title"] == "Этап 1 · Ebisu" and S()["stages"][st1]["state"] == "draft"
      and S()["stages"][st1]["qual"] and S()["stages"][st1]["jn"] == 3)
tap(2, f"tze:{st1}:q"); dm(2, "Соло-заезд на Ebisu, судейские зоны видны"); c = vid(2, "tzq_video")
check("TZ qual with video", S()["stages"][st1]["tz"]["q"] == {"text": "Соло-заезд на Ebisu, судейские зоны видны", "video": "tzq_video"})
tap(2, f"tze:{st1}:b"); dm(2, "Парные: первый ведёт старший посев"); c = dm(2, "/skip")
check("TZ battle without video", S()["stages"][st1]["tz"]["b"]["text"].startswith("Парные") and "video" not in S()["stages"][st1]["tz"]["b"])
for j in (11, 12, 13):
    tap(2, f"sj:{st1}:add"); c = dm(2, f"@user{j}")
check("three judges", S()["stages"][st1]["judges"] == [11, 12, 13])
c = tap(2, f"sa:{st1}:open")
check("registration opened and announced", S()["stages"][st1]["state"] == "reg" and len([p for m, p in c if m == "sendMessage"]) >= 24
      and f"rg:{st1}" in btns(c))
c = tap(103, f"rg:{st1}")
check("register sends TZ", 103 in S()["stages"][st1]["reg"] and any(m == "sendVideo" and p["video"] == "tzq_video" for m, p in c))
c = tap(103, f"rg:{st1}"); check("register twice", "уже записан" in texts(c))
c = tap(101, f"rgc:{st1}"); check("leader registers whole club", all(u in S()["stages"][st1]["reg"] for u in S()["clubs"][cid]["members"]))
for i in list(range(108, 125)) + [102]:
    tap(i, f"rg:{st1}")
check("registered 24", len(S()["stages"][st1]["reg"]) == 24)
c = tap(124, f"ul:{st1}"); check("unregister", 124 not in S()["stages"][st1]["reg"])
c = tap(2, f"sa:{st1}:qual"); check("qualification opened", S()["stages"][st1]["state"] == "qual")
c = vid(124, "x"); check("not registered cannot submit", "некуда сдать" in texts(c))
c = vid(101, "q101"); check("qual video asks score", "Сколько очков" in texts(c))
c = dm(101, "abc"); check("score must be number", "Напиши число" in texts(c))
c = dm(101, "199,5")
check("qual sent to judges", S()["stages"][st1]["qruns"]["101"]["score"] == 199.5
      and len([p for m, p in c if m == "sendVideo" and p["video"] == "q101"]) == 3 and f"jq:{st1}:101:ok" in btns(c))
c = tap(124, f"jq:{st1}:101:ok"); check("non-judge cannot confirm", "не судья" in texts(c))
c = tap(11, f"jq:{st1}:101:ok")
check("judge confirms qual", S()["stages"][st1]["qruns"]["101"]["st"] == "ok" and any("засчитана" in p.get("text", "") for p in to(c, 101)))
c = tap(12, f"jq:{st1}:101:ok"); check("already checked", "уже проверена" in texts(c))
c = vid(101, "q101b"); check("cannot resubmit confirmed", "уже засчитана" in texts(c))
vid(102, "q102"); dm(102, "150"); tap(12, f"jq:{st1}:102:fix"); c = dm(12, "145.5")
check("judge fixes score", S()["stages"][st1]["qruns"]["102"]["score"] == 145.5 and S()["stages"][st1]["qruns"]["102"]["st"] == "ok")
vid(103, "q103"); dm(103, "190"); c = tap(13, f"jq:{st1}:103:no")
check("judge rejects", S()["stages"][st1]["qruns"]["103"]["st"] == "no" and any("не по ТЗ" in p.get("text", "") for p in to(c, 103)))
vid(103, "q103b"); dm(103, "188"); tap(11, f"jq:{st1}:103:ok")
check("resubmit after reject", S()["stages"][st1]["qruns"]["103"]["score"] == 188 and S()["stages"][st1]["qruns"]["103"]["st"] == "ok")
score = 180
for i in [x for x in S()["stages"][st1]["reg"] if x not in (101, 102, 103)]:
    vid(i, f"q{i}"); dm(i, str(score)); tap(11, f"jq:{st1}:{i}:ok"); score -= 1
ok = [u for u, r in S()["stages"][st1]["qruns"].items() if r["st"] == "ok"]
check("all qualified checked", len(ok) == 23)
c = tap(2, f"sa:{st1}:qbuild")
s1 = S()["stages"][st1]
check("bracket built from qualification", s1["state"] == "run" and s1["bsize"] == 16 and len(s1["seeds"]) == 16
      and s1["seeds"][0] == 101 and s1["seeds"][1] == 103 and len(s1["matches"]) == 16 and s1["third"] == "B3")
check("losers of qualification told", any("не прошёл" in p.get("text", "") for m, p in c))
check("pairs announced", any("Твоя пара" in p.get("text", "") for m, p in c))
m1 = bot.mget(s1, "U1-1")
A, B = m1["pa"], m1["pb"]
check("top seed meets last", A == 101 and B == s1["seeds"][15])

# ---------- парный заезд: 2 видео, судьи, OMT ----------
c = vid(A, "a1"); check("first video asks who leads", "up:lead:a" in btns(c))
c = tap(A, "up:lead:a"); check("asks second video", "второе видео" in texts(c))
c = vid(A, "a2"); check("confirm before sending", "up:send" in btns(c) and "Видео 2 — ведёт" in texts(c))
c = tap(A, "up:send")
s1 = S()["stages"][st1]; m1 = bot.mget(s1, "U1-1")
check("sent to 3 judges", len([p for m, p in c if m == "sendMediaGroup"]) == 3 and m1["tries"][0]["v"] == ["a1", "a2"]
      and any("сдал ваш заезд" in p.get("text", "") for p in to(c, B)))
c = vid(B, "b1"); check("opponent second submission not duplicated", "уже у судей" in texts(c) and not any(m == "sendMediaGroup" for m, p in c))
c = tap(A, f"wd:{st1}:U1-1:0"); check("withdraw before votes", not S()["stages"][st1]["matches"][0]["tries"])
vid(B, "b1"); tap(B, "up:lead:a"); vid(B, "b2"); c = tap(B, "up:send")
s1 = S()["stages"][st1]; m1 = bot.mget(s1, "U1-1")
check("leader mapping by choice", m1["tries"][0]["v"] == ["b1", "b2"] and m1["tries"][0]["by"] == B)
c = tap(124, f"jv:{st1}:U1-1:0:a"); check("non-judge cannot vote", "не судья" in texts(c))
c = tap(11, f"jv:{st1}:U1-1:0:a"); check("vote hidden until decision", "Ждём остальных" in texts(c) and not bot.mget(S()["stages"][st1], "U1-1")["done"])
c = tap(11, f"jv:{st1}:U1-1:0:b"); check("cannot vote twice", "уже проголосовал" in texts(c))
c = tap(B, f"wd:{st1}:U1-1:0"); check("no withdraw after vote", "нельзя" in texts(c))
tap(12, f"jv:{st1}:U1-1:0:b"); c = tap(13, f"jv:{st1}:U1-1:0:omt")
m1 = bot.mget(S()["stages"][st1], "U1-1")
check("1-1-1 split -> OMT", m1["omt"] == 1 and not m1["done"] and m1["tries"][0]["res"] == "omt"
      and any("OMT" in p.get("text", "") for p in to(c, A)))
vid(A, "a3"); tap(A, "up:lead:a"); vid(A, "a4"); tap(A, "up:send")
tap(11, f"jv:{st1}:U1-1:1:omt"); c = tap(12, f"jv:{st1}:U1-1:1:omt")
m1 = bot.mget(S()["stages"][st1], "U1-1")
check("majority OMT", m1["omt"] == 2 and m1["tries"][1]["res"] == "omt")
vid(A, "a5"); tap(A, "up:lead:a"); c = vid(A, "a6"); c = tap(A, "up:send")
check("OMT button hidden after limit", not any(x.endswith(":omt") for x in btns(c)))
c = tap(11, f"jv:{st1}:U1-1:2:omt"); check("OMT vote refused after limit", "Лимит OMT" in texts(c))
tap(11, f"jv:{st1}:U1-1:2:a"); c = tap(12, f"jv:{st1}:U1-1:2:a")
s1 = S()["stages"][st1]; m1 = bot.mget(s1, "U1-1")
check("2 of 3 decides without third", m1["done"] and m1["w"] == A and m1["l"] == B
      and any("Победа" in p.get("text", "") for p in to(c, A)) and any("Поражение" in p.get("text", "") for p in to(c, B)))
c = tap(13, f"jv:{st1}:U1-1:2:b"); check("late vote ignored", "уже есть" in texts(c))
check("winner moved to next round", bot.mget(s1, "U2-1")["pa"] == A)
# зритель смотрит видео пары
c = dm(150, "/start w_1_U1-1")
check("watch battle videos", len([p for m, p in c if m == "sendMediaGroup"]) == 3 and "Решение" in texts(c))
c = dm(150, "/start tz_1_q"); check("TZ deep link", any(m == "sendVideo" for m, p in c))
# напоминание судье
bot.load_state(); s1 = bot.S["stages"][st1]; m2 = bot.mget(s1, "U1-2"); bot.save_state()
vid(m2["pa"], "c1"); tap(m2["pa"], "up:lead:a"); vid(m2["pa"], "c2"); tap(m2["pa"], "up:send")
bot.load_state(); bot.mget(bot.S["stages"][st1], "U1-2")["tries"][0]["t"] -= 3600; bot.save_state()
calls.clear(); bot.load_state(); ch = bot.remind_judges(time.time()); bot.save_state()
check("judges reminded once", ch and len([p for m, p in calls if "Ждём твой голос" in p.get("text", "")]) == 3)
calls.clear(); bot.load_state(); bot.remind_judges(time.time()); check("reminder only once", not calls)

# ---------- ручные результаты, сброс, итоги ----------
c = tap(124, f"mr:{st1}:U1-2:a"); check("non-org cannot set result", "для организаторов" in texts(c))
c = tap(2, f"mr:{st1}:U1-2:b")
check("org technical win", bot.mget(S()["stages"][st1], "U1-2")["done"] and bot.mget(S()["stages"][st1], "U1-2")["manual"])
c = tap(11, f"jv:{st1}:U1-2:0:a"); check("judging after manual result refused", "уже есть" in texts(c))


def run_all(sid, side="a"):
    for _ in range(300):
        bot.load_state()
        st = bot.S["stages"][sid]
        ms = [m for m in st["matches"] if m["open"] and not m["done"]]
        if not ms:
            break
        for m in ms:
            bot.set_result(st, m, side)
        bot.save_state()


run_all(st1)
s1 = S()["stages"][st1]
pl = s1["places"]
pts = s1["pts"]
fin, b3 = bot.mget(s1, "U4-1"), bot.mget(s1, "B3")
check("stage finished", s1["state"] == "done" and len(pl) == 16)
check("places 1-4", pl[str(fin["w"])] == 1 and pl[str(fin["l"])] == 2 and pl[str(b3["w"])] == 3 and pl[str(b3["l"])] == 4)
check("points table", pts[str(fin["w"])] == 100 and pts[str(fin["l"])] == 88 and pts[str(b3["w"])] == 76 and pts[str(b3["l"])] == 64
      and sorted(pts.values()).count(48) == 4 and sorted(pts.values()).count(32) == 8)
tot = bot.season_points()
check("club points = sum of members", bot.club_points(cid) == sum(tot.get(str(u), 0) for u in S()["clubs"][cid]["members"]))

# ---------- переходы между клубами ----------
bot.load_state()
mover = next(u for u in S()["clubs"][cid]["members"] if bot.season_points().get(str(u), 0) > 0)
mp = bot.season_points()[str(mover)]
before1, before2 = bot.club_points(cid), bot.club_points(cid2)
tap(110, "cl:add"); dm(110, f"@user{mover}")
c = tap(mover, f"cl:ok:{cid2}")
check("transfer with points needs organizer", "одобрении" in texts(c) and mover in S()["clubs"][cid]["members"]
      and any(f"tr:{mover}:{cid2}:ok" == x for x in btns(c)))
c = tap(2, f"tr:{mover}:{cid2}:ok")
bot.load_state()
check("points move with pilot", mover in bot.S["clubs"][cid2]["members"] and bot.club_points(cid) == before1 - mp
      and bot.club_points(cid2) == before2 + mp and bot.season_points()[str(mover)] == mp)
c = tap(111, "cl:leave")
bot.load_state()
check("leaving club keeps solo points", 111 not in bot.S["clubs"][cid2]["members"] and bot.S["pilots"]["111"]["club"] is None)
tap(110, f"cl:rm:112")
check("captain removes pilot", 112 not in S()["clubs"][cid2]["members"])

# ---------- этап 2: без квалификации, лузер-сетка, 1 судья, неполная сетка ----------
tap(2, "st:new"); tap(2, "sd:qual"); tap(2, "sd:de"); tap(2, "sd:jn"); tap(2, "sd:ok")
st2 = "2"
check("stage 2 settings", S()["stages"][st2]["de"] and not S()["stages"][st2]["qual"] and S()["stages"][st2]["jn"] == 1
      and S()["stages"][st2]["size"] == 32)
tap(2, f"sj:{st2}:add"); dm(2, "@user11")
tap(2, f"sa:{st2}:open")
for i in range(101, 121):
    tap(i, f"rg:{st2}")
tap(2, f"sa:{st2}:bp")
s2 = S()["stages"][st2]
check("bracket by season points with byes", s2["state"] == "run" and s2["bsize"] == 32 and len(s2["seeds"]) == 20
      and s2["seeds"][0] == fin["w"] and s2["de_on"] and s2["final"] == "GF")
openm = [m for m in s2["matches"] if m["open"] and not m["done"]]
m = openm[0]
vid(m["pa"], "d1"); tap(m["pa"], "up:lead:b"); vid(m["pa"], "d2"); c = tap(m["pa"], "up:send")
check("lead b mapping", bot.mget(S()["stages"][st2], m["id"])["tries"][0]["v"] == ["d2", "d1"])
c = tap(11, f"jv:{st2}:{m['id']}:0:b")
check("single judge decides", bot.mget(S()["stages"][st2], m["id"])["w"] == m["pb"])
# сброс результата
c = tap(2, f"mr:{st2}:{m['id']}:reset")
m_after = bot.mget(S()["stages"][st2], m["id"])
check("reset before next battle", not m_after["done"] and m_after["tries"][0]["res"] == "reset")
tap(2, f"mr:{st2}:{m['id']}:a")
nxt = next(x for x in S()["stages"][st2]["matches"] if x["a"].get("m") == m["id"] or x["b"].get("m") == m["id"])
run_all(st2)
s2 = S()["stages"][st2]
check("double elimination finished", s2["state"] == "done" and len(s2["places"]) == 20)
vals = sorted(s2["places"].values())
check("unique top-4 in DE", vals[:4] == [1, 2, 3, 4])
gf, lf = bot.mget(s2, "GF"), bot.mget(s2, s2["third"])
check("DE podium", s2["places"][str(gf["w"])] == 1 and s2["places"][str(gf["l"])] == 2 and s2["places"][str(lf["l"])] == 3)
check("DE points buckets", all(s2["pts"][u] == bot.place_points(p) for u, p in s2["places"].items()))
bot.load_state()
st2o = bot.S["stages"][st2]
mm = bot.mget(st2o, "U1-1")
check("reset refused when next battle done", bot.reset_result(st2o, mm) != "ok")

# ---------- этап 3: правки сетки из приложения ----------
tap(2, "st:new"); tap(2, "sd:qual"); tap(2, "sd:size"); tap(2, "sd:size"); tap(2, "sd:ok")
st3 = "3"
tap(2, f"sa:{st3}:open")
for i in range(101, 109):
    tap(i, f"rg:{st3}")
tap(2, f"sa:{st3}:br")
s3 = S()["stages"][st3]
check("random bracket of 8", s3["bsize"] == 8 and len(s3["seeds"]) == 8)
pa, pb = S()["pilots"][str(s3["seeds"][0])]["pid"], S()["pilots"][str(s3["seeds"][1])]["pid"]
check("app api rejects fake init", not api({"init": init_for(2, bad=True), "act": "me"})["ok"])
r = api({"init": init_for(124), "act": "me"}); check("app me for pilot", r["ok"] and not r["org"] and r["pid"])
r = api({"init": init_for(2), "act": "me"}); check("app me for organizer", r["ok"] and r["org"])
r = api({"init": init_for(124), "act": "swap", "sid": st3, "a": pa, "b": pb}); check("pilot cannot edit bracket", not r["ok"])
r = api({"init": init_for(2), "act": "swap", "sid": st3, "a": pa, "b": pb})
s3 = S()["stages"][st3]
check("organizer swaps seeds", r["ok"] and S()["pilots"][str(s3["seeds"][0])]["pid"] == pb)
r = api({"init": init_for(2), "act": "win", "sid": st3, "mid": "U1-1", "side": "a"})
check("organizer sets result in app", r["ok"] and bot.mget(S()["stages"][st3], "U1-1")["done"])
r = api({"init": init_for(2), "act": "swap", "sid": st3, "a": pa, "b": pb}); check("no swap after first result", not r["ok"])
r = api({"init": init_for(2), "act": "reset", "sid": st3, "mid": "U1-1"}); check("organizer resets in app", r["ok"])
# ограничение мест без квалификации
tap(2, "st:new"); tap(2, "sd:qual"); tap(2, "sd:size"); tap(2, "sd:size"); tap(2, "sd:ok")
st4 = "4"
tap(2, f"sa:{st4}:open")
for i in range(101, 117):
    tap(i, f"rg:{st4}")
c = tap(117, f"rg:{st4}"); check("no more places than bracket", "Мест больше нет" in texts(c))
c = tap(2, f"sa:{st4}:del"); check("cannot delete started registration", st4 in S()["stages"])

# ---------- регламент и публичные данные ----------
tap(2, "ct:rules:text"); dm(2, "Колёса: 265/35, развал — по ТЗ")
tap(2, "ct:rules:photo"); dm(2, photo=[{"file_id": "s"}, {"file_id": "rules_big"}]); dm(2, "/done")
bot.load_state()
check("rules saved", bot.S["content"]["rules"]["text"].startswith("Колёса") and bot.S["content"]["rules"]["photos"][0]["f"] == "rules_big")
key = bot.S["content"]["rules"]["photos"][0]["k"]
check("rules image served", bot.image_bytes(key) == b"jpgbytes")
d = json.loads(json.dumps(bot.public_data(), ensure_ascii=False))
check("public data stages", [s["id"] for s in d["stages"]] == ["1", "2", "3", "4"] and d["stages"][0]["matches"][0]["tries"])
check("public data votes visible", any(t["votes"] for m in d["stages"][0]["matches"] for t in m["tries"]))
check("public standings sorted", d["pilots"][0]["pts"] >= d["pilots"][-1]["pts"] and d["pilots"][0]["pts"] > 0)
check("public clubs", {c_["name"] for c_ in d["clubs"]} == {"Khinkali", "Chacha Drift"} and d["club_max"] == 6)
check("no telegram usernames in public data", "user101" not in json.dumps(d))
check("rules in public data", d["content"]["rules"]["photos"] == [key])
c = dm(150, "/start tz_9_q"); check("unknown stage deep link safe", True)

# ---------- организатор удаляет клуб ----------
c = tap(124, "oc:list"); check("non-org cannot list clubs for deletion", "для организаторов" in texts(c))
c = tap(2, "oc:list"); check("org sees clubs to delete", f"oc:ask:{cid2}" in btns(c))
c = tap(2, f"oc:ask:{cid2}"); check("delete asks confirmation", f"oc:del:{cid2}" in btns(c) and cid2 in S()["clubs"])
members2 = list(S()["clubs"][cid2]["members"])
pts_before = bot.season_points()
c = tap(2, f"oc:del:{cid2}")
bot.load_state()
check("club deleted, pilots solo, points kept", cid2 not in bot.S["clubs"] and all(bot.S["pilots"][str(x)]["club"] is None for x in members2)
      and bot.season_points() == pts_before and all(any("удалил клуб" in p.get("text", "") for p in to(c, x)) for x in members2))
c = tap(2, f"oc:del:{cid2}"); check("deleting twice is safe", "Клубы" in texts(c))
check("log kept", len(S()["log"]) > 5)

# ---------- дедлайн, протесты, повтор видео ----------
bot.load_state(); bot.S["stages"]["3"]["state"] = "done"; bot.save_state()  # пилоты этапа 3 едут теперь в этапе 5
tap(2, "st:new"); tap(2, "sd:qual"); tap(2, "sd:size"); tap(2, "sd:size"); tap(2, "sd:dl"); tap(2, "sd:pw")
c = tap(2, "sd:ok")
st5 = "5"
check("stage deadline and protest settings", S()["stages"][st5]["dl"] == 30 and S()["stages"][st5]["pw"] == 10 and "Дедлайн" in texts(c))
for j in (11, 12, 13):
    tap(2, f"sj:{st5}:add"); dm(2, f"@user{j}")
tap(2, f"sa:{st5}:open")
for i in (101, 102, 103, 104):
    tap(i, f"rg:{st5}")
bot.load_state(); bot.S["stages"]["3"]["state"] = "run"; bot.save_state()
c = tap(2, f"sa:{st5}:br"); check("two stages cannot run at once", "два этапа" in texts(c) and S()["stages"][st5]["state"] == "reg")
bot.load_state(); bot.S["stages"]["3"]["state"] = "done"; bot.save_state()
c = tap(2, f"sa:{st5}:br")
check("pair message shows deadline", any("На заезд 30 мин" in p.get("text", "") for m, p in c))
bot.load_state(); s5 = bot.S["stages"][st5]; m5 = bot.mget(s5, "U1-1"); A5, B5 = m5["pa"], m5["pb"]
m5["open"] -= 16 * 60; bot.save_state()
calls.clear(); bot.load_state(); bot.deadlines(time.time()); bot.save_state()
check("half-time reminder to pilots", len([p for m, p in calls if "осталось" in p.get("text", "")]) == 2)
bot.load_state(); bot.mget(bot.S["stages"][st5], "U1-1")["open"] -= 20 * 60; bot.save_state()
calls.clear(); bot.load_state(); bot.deadlines(time.time()); bot.save_state()
check("deadline -> organizers choose", any(f"mx:{st5}:U1-1" == x for x in btns(calls)) and any(f"mr:{st5}:U1-1:a" == x for x in btns(calls)))
calls.clear(); bot.load_state(); bot.deadlines(time.time()); check("deadline alert once", not calls)
c = tap(2, f"mx:{st5}:U1-1"); check("organizer extends deadline", bot.mget(S()["stages"][st5], "U1-1")["ext"] == 30)
c = dm(A5, video={"file_id": "e1", "file_unique_id": "E1"}); tap(A5, "up:lead:a")
c = dm(A5, video={"file_id": "e1b", "file_unique_id": "E1"}); check("same video twice in one upload refused", "то же самое видео" in texts(c))
dm(A5, video={"file_id": "e2", "file_unique_id": "E2"}); tap(A5, "up:send")
m52 = bot.mget(S()["stages"][st5], "U1-2")
c = dm(m52["pa"], video={"file_id": "x", "file_unique_id": "E2"}); check("video reused from another pair refused", "уже сдавали" in texts(c))
tap(11, f"jv:{st5}:U1-1:0:a"); c = tap(12, f"jv:{st5}:U1-1:0:a")
check("loser gets protest button, winner not", any(f"pr:{st5}:U1-1" == x for x in btns([("x", p) for p in to(c, B5)]))
      and not any(x.startswith("pr:") for x in btns([("x", p) for p in to(c, A5)])))
c = tap(A5, f"pr:{st5}:U1-1"); check("winner cannot protest", "только проигравший" in texts(c))
tap(B5, f"pr:{st5}:U1-1"); c = dm(B5, "Соперник срезал зону")
check("protest sent to judges", any(f"pv:{st5}:U1-1:omt" == x for x in btns(c)) and S()["stages"][st5]["matches"][0]["protest"]["why"] == "Соперник срезал зону")
c = tap(B5, f"pr:{st5}:U1-1"); check("one protest at a time", "уже подан" in texts(c))
tap(11, f"pv:{st5}:U1-1:omt"); c = tap(12, f"pv:{st5}:U1-1:omt")
m5 = bot.mget(S()["stages"][st5], "U1-1")
check("protest accepted -> OMT rerun", not m5["done"] and m5["omt"] == 1 and m5["protests"] and not m5.get("protest")
      and any("Протест принят" in p.get("text", "") for p in to(c, A5)))
dm(B5, video={"file_id": "e3", "file_unique_id": "E3"}); tap(B5, "up:lead:a"); dm(B5, video={"file_id": "e4", "file_unique_id": "E4"}); tap(B5, "up:send")
tap(11, f"jv:{st5}:U1-1:1:a"); tap(13, f"jv:{st5}:U1-1:1:a")
tap(B5, f"pr:{st5}:U1-1"); dm(B5, "Всё равно не согласен")
tap(11, f"pv:{st5}:U1-1:keep"); c = tap(13, f"pv:{st5}:U1-1:omt")
check("split protest waits for the third judge", not S()["stages"][st5]["matches"][0]["protest"]["res"])
c = tap(12, f"pv:{st5}:U1-1:keep")
m5 = bot.mget(S()["stages"][st5], "U1-1")
check("protest rejected -> decision stays", m5["done"] and m5["w"] == A5 and m5["protest"]["res"] == "keep")
bot.load_state(); m52 = bot.mget(bot.S["stages"][st5], "U1-2"); bot.set_result(bot.S["stages"][st5], m52, "a"); bot.save_state()
c = tap(m52["l"], f"pr:{st5}:U1-2"); check("no protest after technical result", "вышло" in texts(c))
d5 = [x for x in bot.public_data()["stages"] if x["id"] == st5][0]
check("public data: deadline and protest", d5["dl"] == 30 and d5["matches"][0]["protest"]["res"] == "keep")

# ---------- новости, очки, сезоны ----------
tap(2, "nw:add"); c = dm(2, "Этап 6 переносится на субботу")
check("news published and sent to pilots", S()["news"][0]["text"].startswith("Этап 6") and len([p for m, p in c if "переносится" in p.get("text", "")]) >= 20)
check("news in public data", bot.public_data()["news"][0]["text"].startswith("Этап 6"))
tap(2, "nw:rm:0"); check("news removed", not S()["news"])
tap(2, "pt:edit"); c = dm(2, "1 2 3"); check("points need 8 numbers", "ровно 8" in texts(c))
c = dm(2, "50 40 30 20 10 5 2 1")
bot.load_state(); s1 = bot.S["stages"]["1"]
check("points table changed and season recomputed", bot.S["points"][0] == [1, 50] and s1["pts"][str(bot.mget(s1, "U4-1")["w"])] == 50)
c = tap(2, "ss"); check("organizer cannot end season", "ss:end" not in btns(c))
c = tap(1, "ss:end"); check("season end blocked while stages run", "заверши все идущие" in texts(c))
bot.load_state()
for sid in ("3", "4", "5"):
    bot.S["stages"][sid]["state"] = "done"
bot.save_state()
tap(1, "ss:name"); dm(1, "Осень 2026")
c = tap(1, "ss:end"); check("season end asks confirmation", "ss:yes" in btns(c))
champ = bot.public_data()["pilots"][0]["nick"]
c = tap(1, "ss:yes")
bot.load_state()
check("season archived", bot.S["seasons"][-1]["name"] == "Осень 2026" and bot.S["seasons"][-1]["pilots"][0][0] == champ
      and bot.S["season"]["n"] == 2 and any("завершён" in p.get("text", "") for m, p in c))
check("new season starts from zero", not bot.season_points() and all(p["pts"] == 0 for p in bot.public_data()["pilots"]))
tap(2, "st:new"); tap(2, "sd:ok")
check("new stages belong to new season", bot.S["stages"] and S()["stages"]["6"]["season"] == 2)

# ---------- тестовый этап, заявки, ДСК, отстранение, анкета, следующая пара, судьи, выгрузка, зрители ----------
pts_before = bot.season_points()
tap(2, "st:new"); tap(2, "sd:qual"); tap(2, "sd:size"); tap(2, "sd:size"); tap(2, "sd:test"); tap(2, "sd:appr")
c = tap(2, "sd:ok")
st7 = max(S()["stages"], key=int)
check("test stage with approval", S()["stages"][st7]["test"] and S()["stages"][st7]["appr"] and "тест" in texts(c))
tap(2, f"sj:{st7}:add"); dm(2, "@user11")
tap(2, f"sa:{st7}:open")
c = tap(101, f"rg:{st7}")
check("registration goes to approval", 101 in S()["stages"][st7]["pend"] and 101 not in S()["stages"][st7]["reg"]
      and any(f"ap:{st7}:101:ok" == x for x in btns(c)))
c = tap(101, f"rg:{st7}"); check("pending twice", "уже у организаторов" in texts(c))
c = tap(2, f"ap:{st7}:101:ok"); check("organizer approves", 101 in S()["stages"][st7]["reg"] and any("принята" in p.get("text", "") for p in to(c, 101)))
c = tap(2, f"ap:{st7}:101:ok"); check("approve twice safe", "уже рассмотрена" in texts(c))
tap(102, f"rg:{st7}"); c = tap(2, f"ap:{st7}:102:no")
check("organizer rejects", 102 not in S()["stages"][st7]["reg"] and any("отклонили" in p.get("text", "") for p in to(c, 102)))
for i in (103, 104, 105, 106):
    tap(i, f"rg:{st7}"); tap(2, f"ap:{st7}:{i}:ok")
tap(107, f"rg:{st7}"); c = tap(2, f"sa:{st7}:pend"); check("pending list in stage menu", f"ap:{st7}:107:ok" in btns(c))
tap(107, f"ul:{st7}"); check("withdraw pending", 107 not in S()["stages"][st7]["pend"])
tap(2, f"sa:{st7}:sched"); dm(2, "19:00 — топ-8\n20:00 — финалы")
check("schedule saved", S()["stages"][st7]["sched"].startswith("19:00"))
tap(2, f"sa:{st7}:dsql"); tap(2, f"sa:{st7}:dsq:106"); c = dm(2, "Не та машина")
check("DSQ in registration removes pilot", 106 not in S()["stages"][st7]["reg"] and S()["stages"][st7]["dsq"]["106"] == "Не та машина"
      and any("дисквалифицирован" in p.get("text", "") for p in to(c, 106)))
c = tap(2, f"sa:{st7}:br")
s7 = S()["stages"][st7]
check("bracket of test stage", s7["state"] == "run" and len(s7["seeds"]) == 4)
check("next pair alert", any("ваша пара следующая" in p.get("text", "") for m, p in c))
opened = [m for m in s7["matches"] if m["open"] and not m["done"]]
victim = opened[0]["pa"]
tap(2, f"sa:{st7}:dsq:{victim}"); c = dm(2, "Неспортивное поведение")
mv = next(m for m in S()["stages"][st7]["matches"] if victim in (m["pa"], m["pb"]) and not m["bye"])
check("DSQ during bracket = technical loss", mv["done"] and mv["l"] == victim)
# анкета пилота
c = tap(103, "m:card"); check("card menu", "m:num" in btns(c))
tap(103, "m:num"); c = dm(103, "1000"); check("number range", "от 1 до 999" in texts(c))
c = dm(103, "#77"); check("number saved", S()["pilots"]["103"]["num"] == 77)
tap(104, "m:num"); c = dm(104, "77"); check("number unique", "занят" in texts(c))
dm(104, "/cancel")
tap(103, "m:car"); dm(103, "Nissan Silvia S15")
tap(103, "m:pphoto"); c = dm(103, photo=[{"file_id": "s"}, {"file_id": "car103"}])
bot.load_state(); key = bot.S["pilots"]["103"]["photo"]["k"]
check("car and photo saved", bot.S["pilots"]["103"]["car"] == "Nissan Silvia S15" and bot.image_bytes(key) == b"jpgbytes")
# прогоняем тестовый этап до конца
run_all(st7)
s7 = S()["stages"][st7]
check("test stage finished without points", s7["state"] == "done" and all(v == 0 for v in s7["pts"].values())
      and bot.season_points() == pts_before)
c = tap(2, f"sa:{st7}:csv"); check("CSV export sent", any(m == "sendDocument" for m, p in c))
csv_text = bot.stage_csv(S()["stages"][st7]).decode("utf-8-sig")
check("CSV has places and pairs", "Место;Пилот;Номер;Клуб;Очки" in csv_text and "Пара;Раунд" in csv_text and "#" not in csv_text.split("\n")[3][:0] + "")
c = tap(2, "js"); check("judge stats", "Статистика судей" in texts(c) and "N11" in texts(c))
# голос зрителей за пару этапа (на тестовом этапе нет, на обычном — есть)
bot.load_state(); real = bot.S["stages"]["5"]; real["done_t"] = int(time.time()); bot.save_state()
pair = next(m for m in real["matches"] if not m["bye"] and m["done"])
r = api({"init": init_for(150), "act": "fan", "sid": st7, "mid": pair["id"]}); check("no fan vote on test stage", not r["ok"])
r = api({"init": init_for(150), "act": "fan", "sid": "5", "mid": pair["id"]}); check("fan vote", r["ok"])
r = api({"init": init_for(151), "act": "fan", "sid": "5", "mid": pair["id"]})
r = api({"init": init_for(150), "act": "me"}); check("my fan vote remembered", r["fans"].get("5") == pair["id"])
d5 = [x for x in bot.public_data()["stages"] if x["id"] == "5"][0]
check("fan votes public", d5["fans"][pair["id"]] == 2 and d5["fan_open"] and d5["fan_best"] is None)
bot.load_state(); bot.S["stages"]["5"]["done_t"] -= 49 * 3600; bot.save_state()
d = bot.public_data(); d5 = [x for x in d["stages"] if x["id"] == "5"][0]
winners = {p_["nick"] for p_ in d["pilots"] if p_["fan"]}
check("fan prize after voting closes", d5["fan_best"] == pair["id"] and not d5["fan_open"]
      and winners == {bot.nick(pair["pa"]), bot.nick(pair["pb"])})
r = api({"init": init_for(152), "act": "fan", "sid": "5", "mid": pair["id"]}); check("fan vote closed", not r["ok"])
# отстранение от чемпионата
tap(2, "bn:add"); dm(2, "Nobody"); c = dm(2, "Pilot108")
c = dm(2, "Накрутка")
check("pilot banned", "108" in S()["banned"] and any("отстранён" in p.get("text", "") for p in to(c, 108)))
tap(2, "st:new"); tap(2, "sd:qual"); tap(2, "sd:ok"); st8 = max(S()["stages"], key=int); tap(2, f"sa:{st8}:open")
c = tap(108, f"rg:{st8}"); check("banned pilot cannot register", "отстранён" in texts(c) and 108 not in S()["stages"][st8]["reg"])
d = bot.public_data(); check("ban visible in app data", next(p_ for p_ in d["pilots"] if p_["nick"] == "Pilot108")["banned"] == "Накрутка")
c = tap(2, "bn:rm:108"); check("pilot returned", "108" not in S()["banned"])
d = bot.public_data(); p103 = next(p_ for p_ in d["pilots"] if p_["nick"] == "Pilot103")
check("pilot card in app data", p103["num"] == 77 and p103["car"] == "Nissan Silvia S15" and p103["photo"])
print("\n".join(checks))
failed = sum(x.startswith("FAIL") for x in checks)
print("FAILED:", failed, "of", len(checks))
if failed:
    sys.exit(1)
