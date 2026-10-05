# AI Station marketing agent library v3 (Oct 2026).
# Standalone port of the Composio-workbench v2: state and knowledge live as local files in
# data/ (AIS_DATA_DIR), Telegram goes straight to the Bot API, secrets come from env vars.
#   TELEGRAM_BOT_TOKEN  token of @marketingagent67_bot (from @BotFather)
#   MINIMAX_API_KEY     MiniMax image-01 key
# Either may instead be put in data/secrets.json as telegram_bot_token / minimax_key (gitignored).
import base64, io, json, os, re, time, math, random, threading, datetime, html as _html
import requests
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("AIS_DATA_DIR", os.path.join(ROOT, "data"))
CHANNEL_ID = -1002832312156          # public @aistationuz channel. Posting happens ONLY via publish_telegram(),
                                     # only when settings.publish_telegram is on AND a lead pressed "📢 Kanalga joylash".
MAX_LEADS = 2
WORK = os.environ.get("AIS_WORK_DIR", os.path.join(ROOT, "work"))
TG_API = "https://api.telegram.org/bot{token}/{method}"
os.makedirs(WORK + "/out", exist_ok=True)
MM_URL = "https://api.minimax.io/v1/image_generation"
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf"
TG_LINKS = ("\n\n🌐 [Website](https://aistation.uz) | [Instagram](https://www.instagram.com/aistationuz) | "
            "[LinkedIn](https://www.linkedin.com/company/109457182) | [YouTube](https://www.youtube.com/@AI_Station_UZ)")
TG_END = "Learn. Build. Launch. Scale. 🚀"
SIGNOFF_NAMES = ["Aloqabank", "Agrobank", "UNDP", "Ministry of Economy", "Iqtisodiyot va moliya vazirligi",
                 "Founders Hub", "NexaGrid", "IT Park", "UBS", "University of Business and Science", "Talos Capital",
                 "Montfort", "Virtual Accelerate", "AloqaVentures", "United Ventures", "NVIDIA", "Notion", "HubSpot"]
GREETING = ("Salom! Men AI Station marketing agentiman. Har kuni ertalab shu yerga tayyor post g‘oyasi, "
            "rasm va matnlarni yuboraman. Tugmalar orqali tasdiqlang yoki qayta so‘rang; istalgan xabarga "
            "reply qilib tahrir yozsangiz, men uni qo‘llayman.")
esc = _html.escape


# ---------------------------------------------------------------- local storage
_LOCK = threading.RLock()


def _path(name):
    return os.path.join(DATA, name)


def drive_get(name):
    """Return the bytes stored under name in the data dir, or None. (Name kept from the Drive version.)"""
    p = _path(name)
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return f.read()


def drive_put(name, data):
    """Atomically write data (str or bytes) to name in the data dir."""
    if isinstance(data, str):
        data = data.encode()
    os.makedirs(DATA, exist_ok=True)
    p = _path(name)
    tmp = f"{p}.{os.getpid()}.tmp"
    with _LOCK:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
    return p


_SECRETS = {}


def secrets():
    if not _SECRETS:
        raw = drive_get("secrets.json")
        _SECRETS.update(json.loads(raw.decode()) if raw else {})
        for env, key in (("MINIMAX_API_KEY", "minimax_key"), ("TELEGRAM_BOT_TOKEN", "telegram_bot_token")):
            if os.environ.get(env):
                _SECRETS[key] = os.environ[env]
    return _SECRETS


# ---------------------------------------------------------------- state
DEFAULT_STATE = {"leads": [], "lead_names": {}, "offset": 0, "posts": {}, "history": [], "pending": [],
                 "handled": [], "learnings": [], "msg_map": {}, "channel_posts": [], "prefs": [], "edits": [],
                 "rejected": [], "settings": {}, "week_plan": {}, "tender_inbox": [], "shortlists": {}}


SL_RANK = {"open": 0, "picked": 1, "done": 2}  # shortlist status order (see send_shortlist)


def _blank():
    return json.loads(json.dumps(DEFAULT_STATE))


def _load_remote():
    raw = drive_get("state.json") or drive_get("state.snapshot.json")
    st = json.loads(raw.decode()) if raw else {}
    for k, v in _blank().items():
        st.setdefault(k, v)
    return st


def load_state():
    st = _load_remote()
    st["lead_chat_id"] = st["leads"][0] if st["leads"] else None
    return st


def _merge(remote, local):
    m = dict(remote)
    for k, v in local.items():
        if k not in DEFAULT_STATE:
            m[k] = v
    leads = list(remote.get("leads", []))
    for c in local.get("leads", []):
        if c not in leads and len(leads) < MAX_LEADS:
            leads.append(c)
    m["leads"] = leads
    m["lead_names"] = {**remote.get("lead_names", {}), **local.get("lead_names", {})}
    m["offset"] = max(remote.get("offset", 0), local.get("offset", 0))
    posts = dict(remote.get("posts", {}))
    for pid, p in local.get("posts", {}).items():
        if pid not in posts or p.get("updated", 0) >= posts[pid].get("updated", 0):
            posts[pid] = p
    m["posts"] = posts
    seen, hist = set(), []
    for h in remote.get("history", []) + local.get("history", []):
        if h.get("post_id") not in seen:
            seen.add(h.get("post_id"))
            hist.append(h)
    m["history"] = hist[-90:]
    handled = list(dict.fromkeys(remote.get("handled", []) + local.get("handled", [])))[-600:]
    m["handled"] = handled
    hs, seen, pend = set(handled), set(), []
    for a in remote.get("pending", []) + local.get("pending", []):
        if a.get("uid") not in hs and a.get("uid") not in seen:
            seen.add(a.get("uid"))
            pend.append(a)
    m["pending"] = pend
    m["learnings"] = list(dict.fromkeys(remote.get("learnings", []) + local.get("learnings", [])))
    prefs = {x["rule"]: x for x in remote.get("prefs", [])}
    for x in local.get("prefs", []):
        if x["rule"] not in prefs or x.get("count", 1) >= prefs[x["rule"]].get("count", 1):
            prefs[x["rule"]] = x
    m["prefs"] = list(prefs.values())
    for k, cap in (("edits", 60), ("rejected", 40)):
        seen_k, lst = set(), []
        for x in remote.get(k, []) + local.get(k, []):
            key = (x.get("post_id"), x.get("ts"))
            if key not in seen_k:
                seen_k.add(key)
                lst.append(x)
        m[k] = lst[-cap:]
    m["settings"] = {**remote.get("settings", {}), **local.get("settings", {})}
    m["week_plan"] = local.get("week_plan") or remote.get("week_plan") or {}
    sls = dict(remote.get("shortlists", {}))
    for k, v in local.get("shortlists", {}).items():
        r = sls.get(k)
        # a pick recorded by one run must not be undone by another run's older copy
        if not r or SL_RANK.get(v.get("status"), 0) >= SL_RANK.get(r.get("status"), 0):
            sls[k] = v
    m["shortlists"] = dict(sorted(sls.items())[-14:])
    tin = {x["uid"]: x for x in remote.get("tender_inbox", []) + local.get("tender_inbox", [])}
    m["tender_inbox"] = sorted(tin.values(), key=lambda x: x["uid"])[-200:]
    mm = {**remote.get("msg_map", {}), **local.get("msg_map", {})}
    m["msg_map"] = dict(list(mm.items())[-800:])
    cps = {c["message_id"]: c for c in remote.get("channel_posts", []) + local.get("channel_posts", [])}
    m["channel_posts"] = sorted(cps.values(), key=lambda c: c["message_id"])[-12:]
    m.pop("lead_chat_id", None)
    return m


def save_state(state):
    """Merge with the latest copy on disk (another run may have written meanwhile) and save."""
    try:
        remote = _load_remote()
    except Exception as ex:
        print("save_state: could not read remote state, writing local copy:", ex)
        remote = _blank()
    m = _merge(remote, state)
    drive_put("state.json", json.dumps(m, ensure_ascii=False))
    state.clear()
    state.update(m)
    state["lead_chat_id"] = m["leads"][0] if m["leads"] else None
    return state


def tashkent_now():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5)


def new_post_id(state):
    base = tashkent_now().strftime("%Y%m%d")
    pid, n = base, 1
    while pid in state["posts"]:
        n += 1
        pid = f"{base}-{n}"
    return pid


def set_status(state, post_id, status):
    p = state["posts"].get(post_id)
    if p:
        p["status"] = status
        p["updated"] = time.time()


def add_learning(state, text, context="", explicit=False):
    """Record a preference distilled from a lead's edit. A rule becomes 'confirmed' when the lead states it
    as a standing rule (explicit) or when it is inferred from 2+ separate edits; until then it is a candidate."""
    import sources as S
    text = text.strip()
    if not text:
        return None
    tk = S.tokens(text)
    for p in state["prefs"]:
        if S._sim(tk, S.tokens(p["rule"])) >= 0.6:
            p["count"] = p.get("count", 1) + 1
            p["contexts"] = sorted(set(p.get("contexts", []) + ([context] if context else [])))
            p["last"] = f"{tashkent_now():%Y-%m-%d}"
            if explicit or p["count"] >= 2:
                p["status"] = "confirmed"
            return p
    p = {"rule": text, "count": 1, "contexts": [context] if context else [], "status": "confirmed" if explicit else "candidate",
         "first": f"{tashkent_now():%Y-%m-%d}", "last": f"{tashkent_now():%Y-%m-%d}"}
    state["prefs"].append(p)
    return p


def record_edit(state, post_id, old, new, instruction=""):
    """Keep (draft -> edited) caption pairs: contrastive examples for writing and judging."""
    for plat in ("instagram", "telegram", "linkedin"):
        a, b = (old.get("captions") or {}).get(plat), (new.get("captions") or {}).get(plat)
        if a and b and a.strip() != b.strip():
            state["edits"].append({"post_id": post_id, "platform": plat, "before": a, "after": b,
                                   "instruction": instruction[:300], "ts": time.time()})


def record_rejection(state, post_id, reason=""):
    p = state["posts"].get(post_id)
    if p:
        state["rejected"].append({"post_id": post_id, "topic": p.get("topic"), "kind": p.get("kind"),
                                  "reason": reason[:300], "telegram": (p.get("captions") or {}).get("telegram", "")[:600],
                                  "ts": time.time()})


# ---------------------------------------------------------------- Telegram
def tg(method, args, tries=3, files=None):
    """Call the Telegram Bot API. method is a Bot API name (sendMessage) or a legacy Composio slug
    (TELEGRAM_SEND_MESSAGE). files={"photo": path} sends multipart. Returns the response JSON."""
    if method.startswith("TELEGRAM_"):
        method = re.sub(r"_(\w)", lambda m: m.group(1).upper(), method[9:].lower())
    token = secrets().get("telegram_bot_token")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    url = TG_API.format(token=token, method=method)
    last = None
    for i in range(tries):
        try:
            if files:
                form = {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in args.items()}
                handles = {k: open(v, "rb") for k, v in files.items()}
                try:
                    r = requests.post(url, data=form, files=handles, timeout=120)
                finally:
                    for h in handles.values():
                        h.close()
            else:
                r = requests.post(url, json=args, timeout=60)
            data = r.json()
        except Exception as ex:
            data, last = None, str(ex)
        if isinstance(data, dict):
            if data.get("ok"):
                return data
            last = data.get("description") or data
            ra = (data.get("parameters") or {}).get("retry_after")
            if ra:
                time.sleep(int(ra) + 1)
                continue
        if re.search(r"can't parse entities|chat not found|bot was blocked|query is too old|wrong file|unauthorized", str(last), re.I):
            break
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"{method} failed: {last}")


UPDATE_TYPES = ["message", "callback_query", "channel_post", "my_chat_member", "poll_answer"]


def peek(state=None):
    """Look at the update queue without consuming anything new. Passing offset = last handled + 1
    only confirms updates that poll() already processed, so they stop showing up here."""
    args = {"timeout": 0, "limit": 100, "allowed_updates": UPDATE_TYPES}
    if state and state.get("offset"):
        args["offset"] = state["offset"] + 1
    d = tg("getUpdates", args)
    return d.get("result") or []


def _send_text(chat_id, text, html=False, markup=None, reply_to=None):
    out = []
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [""]
    for j, ch in enumerate(chunks):
        a = {"chat_id": chat_id, "text": ch, "disable_web_page_preview": True}
        if html:
            a["parse_mode"] = "HTML"
        if markup and j == len(chunks) - 1:
            a["reply_markup"] = markup
        if reply_to and j == 0:
            a["reply_to_message_id"] = reply_to
        try:
            d = tg("TELEGRAM_SEND_MESSAGE", a)
        except RuntimeError as ex:
            if html and "parse" in str(ex).lower():
                a.pop("parse_mode")
                a["text"] = re.sub(r"<[^>]+>", "", ch)
                d = tg("TELEGRAM_SEND_MESSAGE", a)
            else:
                raise
        out.append((d.get("result") or {}).get("message_id"))
    return out


def say(state, html_text, chat_ids=None, markup=None, reply_to=None):
    """Send an HTML message to the leads (or chat_ids). Returns message ids."""
    mids = []
    for cid in (chat_ids or state["leads"]):
        mids += _send_text(cid, html_text, html=True, markup=markup, reply_to=reply_to)
    return mids


def ack(cb_id, text=""):
    try:
        tg("TELEGRAM_ANSWER_CALLBACK_QUERY", {"callback_query_id": str(cb_id), "text": text[:190]}, tries=1)
    except Exception:
        pass  # callback ids expire after a while; the hourly handler is often later than that


def _name(u):
    return " ".join(x for x in [u.get("first_name"), u.get("last_name")] if x) or u.get("username") or str(u.get("id"))


TENDER_ACK = "📑 Tender agentiga yetkazildi, keyingi tender sharhida hisobga olinadi."
TENDER_PREFIX = re.compile(r"\s*#?(tender|тендер)\b", re.I)


def _tender_messages():
    """chat:message ids of the tender agent's digests (it sends them through this bot, see tenders.py)."""
    sent = data_json("tenders.json", {}).get("sent", [])
    return {f"{x['chat']}:{x['mid']}" for x in sent}


def poll(state):
    """Read new Telegram updates. Registers up to MAX_LEADS private chats (greets them) and returns
    actions: {"type": "registered"|"button"|"text", ...}. Saves state when anything changed."""
    args = {"timeout": 0, "limit": 100, "allowed_updates": UPDATE_TYPES}
    if state.get("offset"):
        args["offset"] = state["offset"] + 1
    d = tg("TELEGRAM_GET_UPDATES", args)
    ups = d.get("result") or []
    acts, changed = [], False
    for u in ups:
        uid = u["update_id"]
        state["offset"] = max(state.get("offset", 0), uid)
        changed = True
        if "my_chat_member" in u:
            mc = u["my_chat_member"]
            ch = mc.get("chat", {})
            if ch.get("type") == "channel" and mc.get("new_chat_member", {}).get("status") == "administrator" \
                    and ch.get("id") != CHANNEL_ID:
                lst = state.setdefault("settings", {}).setdefault("admin_channels", [])
                if ch["id"] not in [c["id"] for c in lst]:
                    lst.append({"id": ch["id"], "title": ch.get("title"), "by": _name(mc.get("from", {}))})
            continue
        if "channel_post" in u:
            cp = u["channel_post"]
            txt = cp.get("text") or cp.get("caption")
            if txt and cp.get("chat", {}).get("id") == CHANNEL_ID:
                state["channel_posts"].append({"message_id": cp["message_id"], "date": cp.get("date"), "text": txt[:1500]})
            continue
        if "poll_answer" in u:
            act = _poll_pick(state, uid, u["poll_answer"])
            if act:
                acts.append(act)
            continue
        if "callback_query" in u:
            cq = u["callback_query"]
            cid = ((cq.get("message") or {}).get("chat") or {}).get("id") or cq["from"]["id"]
            if cid not in state["leads"]:
                ack(cq["id"], "Ruxsat yo‘q")
                continue
            action, _, pid = (cq.get("data") or "").partition("|")
            acts.append({"uid": uid, "type": "button", "action": action, "post_id": pid or None,
                         "cb_id": cq["id"], "chat_id": cid, "from": _name(cq["from"])})
            continue
        msg = u.get("message")
        if not msg or msg.get("chat", {}).get("type") != "private":
            continue
        cid, text = msg["chat"]["id"], (msg.get("text") or msg.get("caption") or "").strip()
        photo = (msg.get("photo") or [{}])[-1].get("file_id")
        if cid not in state["leads"]:
            if len(state["leads"]) < MAX_LEADS:
                state["leads"].append(cid)
                state["lead_names"][str(cid)] = _name(msg.get("from", {}))
                try:
                    _send_text(cid, GREETING)
                except Exception as ex:
                    print("greeting failed:", ex)
                acts.append({"uid": uid, "type": "registered", "chat_id": cid, "from": _name(msg.get("from", {}))})
            else:
                try:
                    _send_text(cid, "Bu bot faqat AI Station marketing jamoasi uchun.")
                except Exception:
                    pass
            continue
        if text.startswith("/start") or (not text and not photo):
            continue
        rt = msg.get("reply_to_message") or {}
        # replies to a tender digest, or "tender: ...", belong to the tender agent, not to a post edit
        if (rt and f"{cid}:{rt.get('message_id')}" in _tender_messages()) or TENDER_PREFIX.match(text):
            state["tender_inbox"].append({"uid": uid, "ts": time.time(), "chat_id": cid,
                                          "from": _name(msg.get("from", {})), "text": text[:1000],
                                          "reply_to": (rt.get("text") or "")[:600]})
            try:
                _send_text(cid, TENDER_ACK, reply_to=msg["message_id"])
            except Exception as ex:
                print("tender ack failed:", ex)
            continue
        is_idea = bool(photo) or bool(msg.get("forward_origin") or msg.get("forward_date")) or \
            bool(re.match(r"\s*(g.?oya|idea|#idea|#g.?oya)\b", text, re.I))
        if is_idea and not rt:
            acts.append({"uid": uid, "type": "idea", "text": text, "photo": f"tg:{photo}" if photo else None,
                         "chat_id": cid, "message_id": msg["message_id"], "from": _name(msg.get("from", {}))})
            continue
        pid = state["msg_map"].get(f"{cid}:{rt.get('message_id')}") if rt else None
        act = {"uid": uid, "type": "text", "text": text, "post_id": pid, "chat_id": cid,
               "message_id": msg["message_id"], "from": _name(msg.get("from", {}))}
        # the lead selected part of a message and replied to just that part (Telegram "quote")
        quote = ((msg.get("quote") or {}).get("text") or "").strip()
        if quote:
            act["quote"] = quote
        if pid:
            act["platform"] = _which_caption(state["posts"].get(pid), quote or (rt.get("text") or rt.get("caption") or ""))
        acts.append(act)
    state["lead_chat_id"] = state["leads"][0] if state["leads"] else None
    if changed:
        save_state(state)
    return acts


def _norm(t):
    return re.sub(r"\s+", " ", re.sub(r"\*\*", "", t or "")).strip()


def _which_caption(post, text):
    """Which platform caption a replied-to (or quoted) text belongs to: instagram / telegram / linkedin, or None."""
    t = _norm(text)
    if not post or len(t) < 3:
        return None
    caps = post.get("captions") or {}
    for p in ("instagram", "telegram", "linkedin"):
        if t in _norm(caps.get(p)):
            return p
    for p in ("instagram", "telegram", "linkedin"):  # the reply is to a long caption: match its opening
        if _norm(caps.get(p))[:120] and _norm(caps.get(p))[:120] in t:
            return p
    return None


def send_shortlist(state, date, ideas, intro=""):
    """Send today's idea shortlist (2-10 ideas) to every lead: a numbered HTML list with sources, then a
    one-choice Telegram poll. The first vote picks the idea; the hourly handler then builds the post."""
    if not state["leads"]:
        raise RuntimeError("No lead registered: ask the leads to press Start in @marketingagent67_bot")
    if not 2 <= len(ideas) <= 10:
        raise ValueError(f"a shortlist needs 2-10 ideas, got {len(ideas)}")
    for i, x in enumerate(ideas):
        if not x.get("title"):
            raise ValueError(f"idea {i + 1} has no title")
    lines = [f"<b>🗳 Bugungi post uchun {len(ideas)} ta g‘oya</b>" + (f"\n{esc(intro)}" if intro else "")]
    for i, x in enumerate(ideas, 1):
        src = x.get("url")
        tail = f' · <a href="{esc(src)}">manba</a>' if src else ""
        lines.append(f"<b>{i}. {esc(x['title'])}</b>\n{esc(x.get('why', ''))}{tail}")
    lines.append("👇 Pastdagi so‘rovnomada bittasini tanlang. Post tanlovdan keyin 1 soat ichida tayyor bo‘ladi.")
    polls = {}
    for cid in state["leads"]:
        say(state, "\n\n".join(lines), [cid])
        opts = [{"text": f"{i}. {x['title']}"[:100]} for i, x in enumerate(ideas, 1)]
        d = tg("sendPoll", {"chat_id": cid, "question": f"Bugun ({date}) qaysi g‘oya bo‘yicha post qilamiz?"[:300],
                            "options": opts, "is_anonymous": False, "allows_multiple_answers": False})
        res = d.get("result") or {}
        polls[res["poll"]["id"]] = {"chat_id": cid, "message_id": res.get("message_id")}
    state["shortlists"][date] = {"ideas": ideas, "polls": polls, "status": "open", "sent_at": time.time()}
    save_state(state)
    return polls


def _poll_pick(state, uid, pa):
    """A lead voted in a shortlist poll. The first vote picks the idea (status 'picked'), closes the other
    leads' polls and tells everyone; later or retracted votes are ignored."""
    for date, sl in state.get("shortlists", {}).items():
        if pa.get("poll_id") not in sl.get("polls", {}):
            continue
        opts = pa.get("option_ids") or []
        who = _name(pa.get("user") or {})
        if not opts:
            return None
        if sl.get("status") != "open":
            chosen = sl.get("picked", {})
            try:
                _send_text(sl["polls"][pa["poll_id"]]["chat_id"],
                           f"ℹ️ Bu so‘rovnomada {chosen.get('by', 'jamoa')} allaqachon {chosen.get('index', 0) + 1}-g‘oyani tanlagan.")
            except Exception:
                pass
            return None
        i = opts[0]
        if i >= len(sl["ideas"]):
            return None
        act = _pick(state, date, sl, i, who, keep_poll=pa["poll_id"])
        act.update({"uid": uid, "chat_id": (pa.get("user") or {}).get("id")})
        return act
    return None


AUTOPICK_HOUR = 14  # Tashkent hour; override with `agent.py settings autopick_hour 15` (or "off")


def _pick(state, date, sl, i, who, keep_poll=None, auto=False):
    """Mark idea i of a shortlist as picked, close the polls still open and tell the leads."""
    sl["status"] = "picked"
    sl["picked"] = {"index": i, "by": who, "at": time.time(), "auto": auto}
    for pid, pinfo in sl.get("polls", {}).items():
        if pid != keep_poll:
            try:
                tg("stopPoll", {"chat_id": pinfo["chat_id"], "message_id": pinfo["message_id"]}, tries=1)
            except Exception:
                pass
    title = esc(sl["ideas"][i]["title"])
    msg = (f"⏰ Soat {_autopick_hour(state)}:00 gacha ovoz bo‘lmadi, shuning uchun eng yaxshi deb bilgan "
           f"{i + 1}-g‘oyani tanladim: {title}\nPost tayyorlanmoqda, 1 soat ichida yuboraman." if auto else
           f"🗳 <b>{esc(who)}</b> {i + 1}-g‘oyani tanladi: {title}\nPost tayyorlanmoqda, 1 soat ichida yuboraman.")
    try:
        say(state, msg)
    except Exception as ex:
        print("pick notice failed:", ex)
    return {"type": "pick", "shortlist": date, "index": i, "idea": sl["ideas"][i], "from": who}


def _autopick_hour(state):
    h = (state.get("settings") or {}).get("autopick_hour", AUTOPICK_HOUR)
    try:
        return int(h)
    except (TypeError, ValueError):
        return None  # "off"


def autopick_due(state):
    """Today's shortlist if nobody voted and the auto-pick hour has passed, else None."""
    h, now = _autopick_hour(state), tashkent_now()
    date = now.strftime("%Y%m%d")
    sl = state.get("shortlists", {}).get(date)
    if h is None or not sl or sl.get("status") != "open" or now.hour < h:
        return None
    return date


def autopick(state):
    """Pick the first-ranked idea of today's shortlist when nobody voted by the auto-pick hour."""
    date = autopick_due(state)
    if not date:
        return None
    act = _pick(state, date, state["shortlists"][date], 0, "auto", auto=True)
    act["uid"] = f"autopick-{date}"
    return act


def take_actions(state):
    """For the hourly handler: queued actions from the daily run + fresh ones, marked as handled."""
    acts = list(state.get("pending", [])) + poll(state)
    auto = autopick(state)  # after poll(): a vote that just arrived wins over the auto-pick
    if auto:
        acts.append(auto)
    acts = [a for a in acts if a.get("uid") not in set(state.get("handled", []))]
    state["handled"] += [a["uid"] for a in acts]
    state["pending"] = []
    save_state(state)
    # newest button per post wins
    out, seen = [], set()
    for a in reversed(acts):
        if a["type"] == "button":
            key = (a.get("post_id"), "publish" if a.get("action") in ("pub", "hold") else "review")
            if key in seen:
                ack(a["cb_id"], "Eskirgan tugma")
                continue
            seen.add(key)
        out.append(a)
    return list(reversed(out))


# ---------------------------------------------------------------- rendering
BG = (13, 13, 33)
BLUE = (122, 122, 251)
CYAN = (79, 191, 253)
WHITE = (255, 255, 255)
LOGO_B64 = "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"
SIZES = {"instagram": (1080, 1350), "telegram": (1280, 720), "linkedin": (1200, 1200)}
STYLE = {
    "instagram": "Vivid cinematic 3D render with dramatic lighting and depth. Keep the main subject in the upper half and the lower third calm and dark.",
    "telegram": "Wide editorial illustration with clean bold shapes. Keep the main subject on the right side and the left half calm and dark.",
    "linkedin": "Refined, minimal, premium business image with generous negative space. Keep the subject in the upper right and the lower half calm and dark.",
}
COMMON = (" Colour palette: deep indigo night (#0D0D21) with electric blue (#7A7AFB) and cyan (#4FBFFD) light accents."
          " Matte surfaces with subtle film grain, soft realistic light, not glossy plastic."
          " Absolutely no text, letters, numbers, logos, watermarks, flags or real people."
          " Avoid AI cliches: no robots, humanoids, glowing brains, circuit-board faces, handshake holograms or floating screens.")
_FONTS = {}


def _font_path():
    for p in (os.path.join(ROOT, "assets", "Montserrat.ttf"), WORK + "/Montserrat.ttf"):
        if os.path.exists(p) and os.path.getsize(p) > 100000:
            return p
    r = requests.get(FONT_URL, timeout=40)
    r.raise_for_status()
    with open(p, "wb") as f:
        f.write(r.content)
    return p


def F(weight, size):
    k = (weight, size)
    if k not in _FONTS:
        f = ImageFont.truetype(_font_path(), size)
        f.set_variation_by_axes([weight])
        _FONTS[k] = f
    return _FONTS[k]


def clean(s):
    return (s or "").replace("ʻ", "‘").replace("ʼ", "’").replace("`", "‘").replace("—", "-").replace("–", "-").strip()


def mix(c, a):
    return tuple(int(b * (1 - a) + x * a) for b, x in zip(BG, c)) + (255,)


def _glow(img, center, radius, color, alpha):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, y = center
    d.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color + (alpha,))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius * 0.55)))


def _network(img, seed, box):
    rnd = random.Random(seed)
    x0, y0, x1, y1 = box
    pts = [(rnd.uniform(x0, x1), rnd.uniform(y0, y1)) for _ in range(26)]
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i, p in enumerate(pts):
        for q in pts[i + 1:]:
            dist = math.dist(p, q)
            if dist < 230:
                d.line([p, q], fill=BLUE + (int(85 * (1 - dist / 230)),), width=2)
    for p in pts:
        r = rnd.choice([3, 4, 5, 7])
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=rnd.choice([BLUE, CYAN, WHITE]) + (rnd.randint(110, 220),))
    img.alpha_composite(layer)


def _template_bg(platform, seed):
    W, H = SIZES[platform]
    img = Image.new("RGBA", (W, H), BG + (255,))
    _glow(img, (W - 120, int(H * 0.2)), int(min(W, H) * 0.4), BLUE, 100)
    _glow(img, (60, H - 160), int(min(W, H) * 0.33), CYAN, 50)
    box = {"instagram": (470, 220, W - 50, 620), "telegram": (W * 0.55, 80, W - 40, H - 80),
           "linkedin": (520, 200, W - 60, 560)}[platform]
    _network(img, seed, box)
    return img


def _cover(art, W, H):
    s = max(W / art.width, H / art.height)
    art = art.resize((int(art.width * s + 0.5), int(art.height * s + 0.5)), Image.LANCZOS)
    x, y = (art.width - W) // 2, (art.height - H) // 2
    return art.crop((x, y, x + W, y + H)).convert("RGBA")


def _scrim(img, kind):
    W, H = img.size
    if kind == "bottom":
        top, full = 0.30, 0.66
        col = Image.new("L", (1, H))
        for y in range(H):
            t = y / H
            v = 0 if t < top else (min(1, (t - top) / (full - top)) ** 1.35) * 245
            if y < H * 0.2:
                v = max(v, (1 - y / (H * 0.2)) * 150)
            col.putpixel((0, y), int(v))
        a = col.resize((W, H))
    else:  # left
        row = Image.new("L", (W, 1))
        for x in range(W):
            t = x / W
            row.putpixel((x, 0), int(245 if t < 0.42 else max(0, 245 * (1 - (t - 0.42) / 0.33))))
        a = row.resize((W, H))
    layer = Image.new("RGBA", (W, H), BG + (0,))
    layer.putalpha(a)
    img.alpha_composite(layer)


def _tokens(headline):
    out, hl = [], False
    for part in headline.replace("[[", "\x00[[\x00").replace("]]", "\x00]]\x00").split("\x00"):
        if part == "[[":
            hl = True
        elif part == "]]":
            hl = False
        else:
            out += [(w, hl) for w in part.split()]
    return out


def _wrap_tokens(toks, font, maxw, d):
    lines, cur = [], []
    for t in toks:
        trial = " ".join(w for w, _ in cur + [t])
        if cur and d.textlength(trial, font=font) > maxw:
            lines.append(cur)
            cur = [t]
        else:
            cur.append(t)
    return lines + ([cur] if cur else [])


def _wrap(text, font, maxw, d):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if cur and d.textlength(t, font=font) > maxw:
            lines.append(cur)
            cur = w
        else:
            cur = t
    return lines + ([cur] if cur else [])


def _logo(width):
    lg = Image.open(io.BytesIO(base64.b64decode(LOGO_B64))).convert("RGBA")
    return lg.resize((width, int(lg.height * width / lg.width)), Image.LANCZOS)


def compose(platform, sp, seed=7, art=None):
    """Draw the AI Station layer for one platform: the logo and one short phrase (sp["headline"]), nothing else.
    Other spec fields (tag, subline, stat, stat_label) are not drawn; carousels have their own slide layout."""
    W, H = SIZES[platform]
    if art is not None:
        img = _cover(art, W, H)
        _scrim(img, "left" if platform == "telegram" else "bottom")
    else:
        img = _template_bg(platform, seed)
    d = ImageDraw.Draw(img)
    cfg = {"instagram": dict(M=80, logo=220, maxw=W - 160, hs=(108, 64), hmax=3),
           "telegram": dict(M=64, logo=190, maxw=int(W * 0.52), hs=(86, 52), hmax=3),
           "linkedin": dict(M=88, logo=210, maxw=W - 176, hs=(100, 60), hmax=3)}[platform]
    M, maxw = cfg["M"], cfg["maxw"]
    img.alpha_composite(_logo(cfg["logo"]), (M, int(M * 1.05)))
    toks = _tokens(clean(sp.get("headline") or ""))
    for size in range(cfg["hs"][0], cfg["hs"][1] - 1, -4):
        hf = F(800, size)
        lines = _wrap_tokens(toks, hf, maxw, d)
        if len(lines) <= cfg["hmax"]:
            break
    lh = int(size * 1.14)
    y = H - int(M * 1.35) - len(lines) * lh
    if platform == "telegram":
        y = (H - len(lines) * lh) // 2 + int(M * 0.6)
    for line in lines:
        x = M
        for i, (w, hl) in enumerate(line):
            gap = i < len(line) - 1 and not re.match(r"[,.:;!?)]", line[i + 1][0])
            t = w + (" " if gap else "")
            d.text((x, y), t, font=hf, fill=(CYAN if hl else WHITE) + (255,))
            x += d.textlength(t, font=hf)
        y += lh
    return img.convert("RGB")


def minimax(prompt, platform):
    key = secrets().get("minimax_key")
    if not key:
        raise RuntimeError("no MiniMax key in secrets.json")
    body = {"model": "image-01", "prompt": prompt.strip() + " " + STYLE[platform] + COMMON,
            "response_format": "base64", "n": 1, "prompt_optimizer": False}
    if platform == "instagram":
        body.update(width=1024, height=1280)
    else:
        body["aspect_ratio"] = "16:9" if platform == "telegram" else "1:1"
    last = None
    for i in range(2):
        try:
            r = requests.post(MM_URL, headers={"Authorization": "Bearer " + key}, json=body, timeout=120)
            j = r.json()
            br = j.get("base_resp") or {}
            if br.get("status_code") == 0 and (j.get("data") or {}).get("image_base64"):
                return Image.open(io.BytesIO(base64.b64decode(j["data"]["image_base64"][0]))).convert("RGB")
            last = f"{br.get('status_code')} {br.get('status_msg')}"
            if br.get("status_code") in (1004, 1008, 2013, 1026, 1027):  # auth / balance / params / sensitive content
                break
        except Exception as ex:
            last = str(ex)
        time.sleep(3)
    raise RuntimeError(last)


def render_all(spec, post_id, use_minimax=True):
    """Render the three platform images. Returns {platform: {"path", "minimax", "error"}}."""
    seed = spec.get("seed", 7)

    import visuals

    def one(p):
        sp = dict(spec[p])
        if not use_minimax and (sp.get("art") or "minimax") == "minimax" and not sp.get("art_path"):
            sp["art"] = "card"
        art, src, err = visuals.background(p, sp, seed)
        img = compose(p, sp, seed, art)
        path = f"{WORK}/out/{post_id}_{p}_{int(time.time())}.jpg"
        img.save(path, "JPEG", quality=92)
        return p, {"path": path, "minimax": src in ("minimax", "pinned"), "source": src, "error": err}

    _font_path()
    if use_minimax:
        try:
            secrets()  # load before threads
        except Exception as ex:
            print("secrets load failed:", ex)
    with ThreadPoolExecutor(3) as ex:
        return dict(ex.map(one, ["instagram", "telegram", "linkedin"]))


def _send_photo(chat_id, photo, caption):
    """photo is a local file path (uploaded as multipart) or a Telegram file_id."""
    args = {"chat_id": chat_id, "caption": caption}
    if os.path.exists(str(photo)):
        d = tg("sendPhoto", args, files={"photo": photo})
    else:
        d = tg("sendPhoto", {**args, "photo": photo})
    res = d.get("result") or {}
    sizes = res.get("photo") or []
    return res.get("message_id"), (sizes[-1]["file_id"] if sizes else None)


def tg_caption(text):
    t = text.rstrip()
    if TG_END not in t:
        t += "\n\n" + TG_END
    return t + TG_LINKS


def send_package(state, post_id, post, note=None, use_minimax=True):
    """Render the 3 images, send idea + images + captions + buttons to every lead, save state."""
    if not state["leads"]:
        raise RuntimeError("No lead registered: ask the leads to press Start in @marketingagent67_bot")
    spec, caps = post["spec"], post["captions"]
    import visuals
    old = state["posts"].get(post_id)
    if old:
        record_edit(state, post_id, old, post, note or "")
    imgs = render_all(spec, post_id, use_minimax)
    post["images"] = {p: {"minimax": v["minimax"], "source": v.get("source"), "error": v["error"]} for p, v in imgs.items()}
    for p, v in imgs.items():
        if v["error"]:
            print(f"Image error ({p}): {v['error']} -> brand template used")
    slides = visuals.render_carousel(post, post_id, {p: v["path"] for p, v in imgs.items()}) if post.get("carousel") else {}
    so = post.get("signoff")
    def _host(u):
        return re.sub(r"^https?://(www\.)?", "", u).split("/")[0]
    src = " · ".join(f'<a href="{esc(u)}">{esc(_host(u))}</a>' for u in post.get("sources", []))
    header = (f"{esc(note) + chr(10) + chr(10) if note else ''}<b>📝 Post #{esc(post_id)}</b>\n\n"
              f"<b>G‘oya:</b> {esc(post.get('idea', ''))}\n<b>Nega hozir:</b> {esc(post.get('why', ''))}\n"
              f"<b>Manba:</b> {src or '-'}\n"
              + (f"⚠️ <b>Sign-off kerak:</b> {esc(so if isinstance(so, str) else ', '.join(so))}" if so else "✅ Sign-off shart emas"))
    kb = {"inline_keyboard": [[{"text": "✅ Tasdiqlash", "callback_data": f"ok|{post_id}"},
                               {"text": "🎨 Rasmni qayta", "callback_data": f"img|{post_id}"}],
                              [{"text": "✍️ Matnni qayta", "callback_data": f"cap|{post_id}"},
                               {"text": "💡 Boshqa g‘oya", "callback_data": f"new|{post_id}"}]]}
    labels = [("instagram", "📸 Instagram"), ("telegram", "✈️ Telegram"), ("linkedin", "💼 LinkedIn")]
    texts = {"instagram": caps["instagram"], "telegram": tg_caption(caps["telegram"]), "linkedin": caps["linkedin"]}
    file_ids, sent = {}, 0
    for cid in state["leads"]:
        mids = say(state, header, [cid])
        for p, label in labels:
            photo = file_ids.get(p) or imgs[p]["path"]
            try:
                mid, fid = _send_photo(cid, photo, f"{label} · #{post_id}")
            except RuntimeError:
                mid, fid = _send_photo(cid, imgs[p]["path"], f"{label} · #{post_id}")  # retry with a fresh upload
            file_ids[p] = fid or file_ids.get(p)
            if slides.get(p):
                media = [{"type": "photo", "media": f"attach://s{i}"} for i in range(len(slides[p][:10]))]
                media[0]["caption"] = f"{label} carousel · #{post_id}"
                try:
                    d = tg("sendMediaGroup", {"chat_id": cid, "media": media},
                           files={f"s{i}": x for i, x in enumerate(slides[p][:10])})
                    mids += [m.get("message_id") for m in d.get("result") or []]
                except RuntimeError as ex:
                    print("carousel send failed:", ex)
                if p == "linkedin" and slides.get("linkedin_pdf"):
                    try:
                        d = tg("sendDocument", {"chat_id": cid, "caption": f"LinkedIn PDF carousel · #{post_id}"},
                               files={"document": slides["linkedin_pdf"]})
                        mids.append((d.get("result") or {}).get("message_id"))
                    except RuntimeError as ex:
                        print("pdf send failed:", ex)
            mids += [mid] + _send_text(cid, texts[p])
        mids += say(state, "👇 Qaror? Tahrir kerak bo‘lsa, istalgan xabarga <b>reply</b> qilib yozing.", [cid], markup=kb)
        for m in mids:
            if m:
                state["msg_map"][f"{cid}:{m}"] = post_id
        sent += 1
    post.setdefault("status", "sent")
    post["file_ids"] = file_ids
    post["sent_at"] = tashkent_now().isoformat(timespec="minutes")
    post["updated"] = time.time()
    state["posts"][post_id] = post
    save_state(state)
    mm = sum(v["minimax"] for v in imgs.values())
    print(f"Sent #{post_id} to {sent} lead(s); MiniMax images: {mm}/3")
    return post


def md_to_html(text):
    """Telegram caption markdown (**bold**, [text](url)) -> Telegram HTML."""
    t = esc(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', t)
    return t


def publish_telegram(state, post_id):
    """Post the approved Telegram version to the public channel. Call only after a lead pressed the publish button."""
    if not state.get("settings", {}).get("publish_telegram"):
        raise RuntimeError("publishing to the channel is off (agent.py settings publish_telegram on)")
    post = state["posts"][post_id]
    if post.get("status") != "approved":
        raise RuntimeError(f"post {post_id} is not approved")
    if (post.get("published") or {}).get("telegram"):
        return post["published"]["telegram"]
    cap = md_to_html(tg_caption(post["captions"]["telegram"]))
    photo = (post.get("file_ids") or {}).get("telegram")
    if len(cap) <= 1024:
        d = tg("sendPhoto", {"chat_id": CHANNEL_ID, "photo": photo, "caption": cap, "parse_mode": "HTML"})
        mid = d["result"]["message_id"]
    else:
        d = tg("sendPhoto", {"chat_id": CHANNEL_ID, "photo": photo})
        mid = d["result"]["message_id"]
        tg("sendMessage", {"chat_id": CHANNEL_ID, "text": cap, "parse_mode": "HTML", "disable_web_page_preview": True})
    post.setdefault("published", {})["telegram"] = {"message_id": mid, "at": tashkent_now().isoformat(timespec="minutes"),
                                                   "url": f"https://t.me/aistationuz/{mid}"}
    post["updated"] = time.time()
    return post["published"]["telegram"]


def publish_markup(post_id):
    return {"inline_keyboard": [[{"text": "📢 Kanalga joylash", "callback_data": f"pub|{post_id}"},
                                 {"text": "⏸ Keyinroq", "callback_data": f"hold|{post_id}"}]]}


def data_json(name, default=None):
    raw = drive_get(name)
    return json.loads(raw.decode()) if raw else (default if default is not None else {})


def save_json(name, obj):
    drive_put(name, json.dumps(obj, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- knowledge + lint
def load_knowledge(state=None):
    """Everything the writer needs before drafting: guide, rules learned from the leads, measured style targets,
    phrases the agent over-uses, contrastive edits, rejected ideas, recent topics and performance."""
    state = state or load_state()
    kb = drive_get("knowledge.md")
    print(kb.decode() if kb else "(knowledge.md missing)")
    conf = [p for p in state.get("prefs", []) if p.get("status") == "confirmed"]
    cand = [p for p in state.get("prefs", []) if p.get("status") != "confirmed"]
    print("\n## TEAM RULES (confirmed; these override the guide)")
    print("\n".join(f"- {p['rule']}" + (f" [{', '.join(p['contexts'])}]" if p.get("contexts") else "") for p in conf)
          or "- none yet")
    for x in state.get("learnings", []):
        print(f"- {x}")
    if cand:
        print("\n## CANDIDATE RULES (seen once; follow when it fits, they become rules when repeated)")
        print("\n".join(f"- {p['rule']}" for p in cand[-12:]))
    style = data_json("style.json")
    if style:
        print("\n## MEASURED HOUSE STYLE (from the team's own @aistationuz posts; aim for these numbers on Telegram)")
        print(json.dumps(style.get("telegram_human", style), ensure_ascii=False))
    sl = data_json("slop.json", {}).get("phrases", [])
    if sl:
        print("\n## PHRASES THE AGENT OVER-USES (auto-detected vs the human posts; do not use)")
        print(", ".join(f"'{x[0]}'" for x in sl[:30]))
    ed = state.get("edits", [])[-4:]
    if ed:
        print("\n## RECENT EDITS BY THE LEADS (draft -> what they wanted; learn the pattern)")
        for e in ed:
            print(f"--- {e['platform']} #{e['post_id']} ({e.get('instruction') or 'edited'})\nBEFORE: {e['before'][:400]}\nAFTER:  {e['after'][:400]}")
    rj = state.get("rejected", [])[-5:]
    if rj:
        print("\n## REJECTED IDEAS (do not pitch similar)")
        print("\n".join(f"- {r.get('topic')} ({r.get('reason') or 'no reason given'})" for r in rj))
    cutoff = (tashkent_now() - datetime.timedelta(days=21)).strftime("%Y%m%d")
    print("\n## TOPICS COVERED RECENTLY (last 21 days, do not repeat)")
    print("\n".join(f"- {h['date']}: {h['topic']} ({h.get('source', '')})" for h in state["history"] if h["date"] >= cutoff) or "- none")
    perf = data_json("performance.json")
    if perf.get("summary"):
        print("\n## WHAT PERFORMS ON @aistationuz (views vs channel median; >1 = above normal)")
        print(json.dumps(perf["summary"], ensure_ascii=False))
    print("\n## APPROVED CAPTIONS (what good looks like)")
    ap = [p for p in state["posts"].values() if p.get("status") == "approved"][-2:]
    for p in ap:
        print(f"--- {p.get('topic')}\n[IG]\n{p['captions']['instagram']}\n[TG]\n{p['captions']['telegram']}\n[LI]\n{p['captions']['linkedin']}")
    if not ap:
        print("- none yet")
    print("\nFor voice examples on today's topic run: python3 agent.py examples \"<topic>\"")


CLICHES = ["game-changer", "game changer", "fast-paced", "revolutionize", "revolutionise", "unlock the", "unleash",
           "delve", "landscape", "harness the", "cutting-edge", "seamless", "elevate", "buckle up", "dive in",
           "dive into", "we're thrilled", "we are thrilled", "exciting news", "in today's", "tapestry", "testament to",
           "paradigm", "synergy", "next level", "the future is here", "stay tuned",
           "tez o‘zgaruvchan", "tez sur'at", "inqilobiy", "kelajak shu yerda", "kelajak bugun", "yangi davr boshlan",
           "imkoniyatlar eshigini", "o‘yin qoidalarini", "hayratlanarli", "ajoyib imkoniyat"]
SLOP = []  # filled from data/slop.json at import (phrases the agent over-uses vs the human archive)
FORBIDDEN = ["applications open", "apply now", "qabul ochiq", "qabul boshlandi", "ro‘yxatdan o‘ting",
             "ariza topshiring", "kursimizga yoziling", "o‘quvchilarimiz", "talabalarimiz", "bitiruvchilarimiz"]


def _words(t):
    return len(re.findall(r"\w[\w‘’'-]*", t))


KINDS = ["news", "opportunity", "educational", "story", "case", "roundup", "event", "announcement"]
PILLARS = ["academy", "studio", "corporate", "ventures", "news"]


def lint_warnings(post):
    """Soft checks against the measured house style (printed, never blocking)."""
    W = []
    style = data_json("style.json").get("telegram_human") or {}
    tgc = (post.get("captions") or {}).get("telegram", "")
    if style and tgc:
        sents = [x for x in re.split(r"(?<=[.!?])\s+|\n+", tgc) if len(x.split()) >= 3]
        if sents:
            mean = sum(len(x.split()) for x in sents) / len(sents)
            if style.get("sentence_words_mean") and mean > style["sentence_words_mean"] * 1.5:
                W.append(f"telegram: sentences average {mean:.0f} words; house style is {style['sentence_words_mean']}")
    for k, v in (post.get("captions") or {}).items():
        if re.search(r"\b(not (just|only)|emas,? balki)\b", v, re.I):
            W.append(f"{k}: 'not just X but Y' construction reads as AI")
        if len(re.findall(r"\b\w+, \w+,? (and|va) \w+\b", v)) >= 2:
            W.append(f"{k}: several lists of three; vary the rhythm")
    return W


def lint(post):
    """Mechanical checks. Returns a list of problems; [] means pass."""
    P = []
    if post.get("kind") not in KINDS:
        P.append(f"kind must be one of {KINDS}")
    if post.get("pillar") not in PILLARS:
        P.append(f"pillar must be one of {PILLARS}")
    caps, spec = post.get("captions", {}), post.get("spec", {})
    for k in ("instagram", "telegram", "linkedin"):
        if not caps.get(k):
            P.append(f"caption {k} missing")
        if k not in spec:
            P.append(f"spec {k} missing")
    if P:
        return P
    allt = {**{f"caption.{k}": v for k, v in caps.items()},
            **{f"spec.{p}.{f}": str(spec[p].get(f) or "") for p in spec if isinstance(spec[p], dict)
               for f in ("headline", "subline", "tag", "stat", "stat_label", "image_prompt")}}
    for k, v in allt.items():
        if "—" in v or "–" in v:
            P.append(f"{k}: em/en dash, use a hyphen")
        low = v.lower().replace("'", "‘").replace("ʻ", "‘")
        for c in CLICHES:
            if c in low:
                P.append(f"{k}: cliché '{c}'")
        external_opp = post.get("kind") == "opportunity" and not re.search(r"academy|akademiya|ais academy", low)
        for c in ([] if external_opp else FORBIDDEN):
            if c in low:
                P.append(f"{k}: forbidden phrase '{c}' (no enrollment/applications claims)")
        for c in SLOP:
            if c in low:
                P.append(f"{k}: over-used agent phrase '{c}' (see slop.json)")
    uz = {"caption.instagram": caps["instagram"], "caption.telegram": caps["telegram"]}
    for p in ("instagram", "telegram"):
        for f in ("headline", "subline", "tag"):
            uz[f"spec.{p}.{f}"] = str(spec[p].get(f) or "")
    for k, v in uz.items():
        if re.search(r"\b[oOgG]['`’]", v):
            P.append(f"{k}: write o‘ / g‘ with the ‘ character, not ' ` or ’")
        if re.search(r"\b\d+\s+ta\s+\w+lar(?:ni|ga|da|dan|ning)?\b", v):
            P.append(f"{k}: plural after a number (use singular: '5 ta startap')")
    ig = caps["instagram"]
    body = re.split(r"\n\s*\.\s*\n", ig)[0]
    tags = re.findall(r"#\w+", ig)
    if not re.search(r"\n\s*\.\s*\n", ig):
        P.append("instagram: missing '.' separator line before hashtags")
    if not (5 <= len(tags) <= 8):
        P.append(f"instagram: {len(tags)} hashtags (want 5-8)")
    for t in ("#aistation", "#aistationuz"):
        if t not in [x.lower() for x in tags]:
            P.append(f"instagram: missing {t}")
    if not (80 <= _words(re.sub(r"#\w+", "", body)) <= 150):
        P.append(f"instagram: body {_words(body)} words (want 80-150)")
    if "](" in ig or "http" in ig:
        P.append("instagram: links do not work on Instagram, remove them")
    if "**" in ig or "__" in ig:
        P.append("instagram: no **markdown** (Instagram shows the asterisks)")
    if "**" in caps["linkedin"]:
        P.append("linkedin: no **markdown** (LinkedIn shows the asterisks)")
    car = post.get("carousel") or {}
    for plat, slides in car.items():
        if not (3 <= len(slides) <= 9):
            P.append(f"carousel.{plat}: {len(slides)} slides (want 3-9 plus the cover)")
        for i, sl in enumerate(slides):
            if len(str(sl.get("title", "")).replace("[[", "").replace("]]", "").split()) > 10:
                P.append(f"carousel.{plat}[{i}]: title over 10 words")
            if len(str(sl.get("body", "")).split()) > 45:
                P.append(f"carousel.{plat}[{i}]: body over 45 words")
            if plat == "instagram" and re.search(r"\b[oOgG]['`’]", str(sl.get("title", "")) + str(sl.get("body", ""))):
                P.append(f"carousel.{plat}[{i}]: write o‘ / g‘ with the ‘ character")
    for p in ("instagram", "telegram", "linkedin"):
        sp = spec[p]
        if sp.get("art") == "photo" and not sp.get("photo"):
            P.append(f"spec.{p}: art=photo needs a photo (article URL, image URL or tg:<file_id>)")
    tgc = caps["telegram"]
    if re.search(r"(^|\s)#\w", tgc):
        P.append("telegram: no hashtags")
    if not tgc.rstrip().endswith(TG_END):
        P.append(f"telegram: must end with '{TG_END}'")
    n = _words(tgc.replace(TG_END, ""))
    if not (60 <= n <= 120):
        P.append(f"telegram: {n} words (want 60-120)")
    if "@" in tgc:
        P.append("telegram: no profile links/handles")
    li = caps["linkedin"]
    lt = re.findall(r"#\w+", li)
    if not (3 <= len(lt) <= 5):
        P.append(f"linkedin: {len(lt)} hashtags (want 3-5)")
    n = _words(re.sub(r"#\w+", "", li))
    if not (120 <= n <= 200):
        P.append(f"linkedin: {n} words (want 120-200)")
    for p, mx in (("instagram", 7), ("telegram", 6), ("linkedin", 8)):
        h = str(spec[p].get("headline") or "")
        hw = len(h.replace("[[", "").replace("]]", "").split())
        if not hw:
            P.append(f"spec.{p}.headline: missing (the one short phrase on the image)")
        if hw > mx:
            P.append(f"spec.{p}.headline: {hw} words (max {mx}: one short phrase)")
        hl = re.findall(r"\[\[(.+?)\]\]", h)
        if h.count("[[") != h.count("]]") or len(hl) > 1:
            P.append(f"spec.{p}.headline: at most one [[highlight]]")
        elif sum(len(x.split()) for x in hl) > 3:
            P.append(f"spec.{p}.headline: highlight max 3 words")
        for f in ("tag", "subline", "stat", "stat_label"):
            if spec[p].get(f):
                P.append(f"spec.{p}.{f}: not drawn any more (image = logo + one phrase); remove it")
        if (spec[p].get("art") or "minimax") == "minimax" and not spec[p].get("image_prompt"):
            P.append(f"spec.{p}: image_prompt missing (or set art to photo/card)")
    if not post.get("sources"):
        P.append("sources missing")
    txt = " ".join(caps.values()).lower()
    named = [n for n in SIGNOFF_NAMES if n.lower() in txt]
    if named and not post.get("signoff"):
        P.append(f"signoff should be set: {', '.join(named)}")
    return P


try:
    SLOP = [x[0] for x in data_json("slop.json", {}).get("phrases", []) if x[1] >= 3]
except Exception:
    SLOP = []
