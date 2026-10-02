# AI Station tender agent: collects tender listings from the sites in data/tender_sites.json, keeps a memory of
# what was already seen (data/tenders.json), pre-scores new lots against data/tender_keywords.json and delivers
# Claude's review to Telegram. The code does the mechanics; Claude decides what is worth bidding on.
#
# Telegram: TENDER_BOT_TOKEN (its own bot, so replies never reach the marketing agent). Without it the digest
# goes out through TELEGRAM_BOT_TOKEN to the marketing leads, send-only (that bot is never polled from here).
import datetime, hashlib, html, os, re, time
import requests
import agent_lib as A

STATE_FILE = "tenders.json"
SITES_FILE = "tender_sites.json"
KEYWORDS_FILE = "tender_keywords.json"
PROFILE_FILE = "tender_profile.md"
OWN_FILES = (STATE_FILE, SITES_FILE, KEYWORDS_FILE, PROFILE_FILE)  # the only data/ files this agent saves
PAGES_DIR = os.path.join(A.WORK, "tenders")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"


def esc(s, quote=False):
    return html.escape(str(s), quote=quote)


# ---------------------------------------------------------------- state
def blank_state():
    return {"items": {}, "runs": [], "feedback": [], "site_status": {}, "shown": [], "sent": [], "ingested": [],
            "bot": {"offset": 0, "chats": [], "names": {}}, "settings": {}}


def load_state():
    st = A.data_json(STATE_FILE, None) or blank_state()
    for k, v in blank_state().items():
        st.setdefault(k, v)
    return st


def save_state(st):
    prune(st)
    A.save_json(STATE_FILE, st)


def prune(st, now=None):
    """Forget lots whose deadline passed 14+ days ago, or that were first seen 60+ days ago without a deadline."""
    now = now or time.time()
    today = datetime.date.today().isoformat()
    cutoff = (datetime.date.today() - datetime.timedelta(days=14)).isoformat()
    for k in list(st["items"]):
        it = st["items"][k]
        d = it.get("deadline")
        if (d and d < cutoff) or (not d and now - it.get("first_seen", now) > 60 * 86400):
            del st["items"][k]
        elif d and d < today and it.get("status") == "new":
            it["status"] = "expired"
    st["runs"] = st["runs"][-60:]
    st["sent"] = st["sent"][-300:]
    st["ingested"] = st["ingested"][-500:]
    st["feedback"] = st["feedback"][-80:]


def sites():
    return A.data_json(SITES_FILE)["sites"]


def site_by_id(sid):
    for s in sites():
        if s["id"] == sid:
            return s
    raise SystemExit(f"unknown site {sid!r}; see `python3 tender.py sites`")


# ---------------------------------------------------------------- parsing helpers
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_DATE_RES = [
    (re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d\d)\b"), lambda m: (int(m[3]), int(m[2]), int(m[1]))),
    (re.compile(r"\b(\d{1,2})[\s-]([A-Za-z]{3})[a-z]*[\s,-]*(20\d\d)\b"), lambda m: (int(m[3]), MONTHS.get(m[2].lower()), int(m[1]))),
    (re.compile(r"\b([A-Za-z]{3})[a-z]* (\d{1,2}),? (20\d\d)\b"), lambda m: (int(m[3]), MONTHS.get(m[1].lower()), int(m[2]))),
]
DEADLINE_WORDS = re.compile(r"deadline|closing|close[sd]?|due|submission|tugash|muddat|истекает|срок|окончан|"
                            r"date limite|expires", re.I)


def parse_date(s):
    """First date in s as YYYY-MM-DD, or None."""
    if not s:
        return None
    s = str(s)
    best = None
    for rx, fn in _DATE_RES:
        m = rx.search(s)
        if m:
            try:
                y, mo, d = fn(m)
                if mo:
                    iso = datetime.date(y, mo, d).isoformat()
                    if best is None or m.start() < best[0]:
                        best = (m.start(), iso)
            except Exception:
                pass
    return best[1] if best else None


def all_dates(s):
    out = []
    for rx, fn in _DATE_RES:
        for m in rx.finditer(s or ""):
            try:
                y, mo, d = fn(m)
                if mo:
                    out.append((m.start(), datetime.date(y, mo, d).isoformat()))
            except Exception:
                pass
    return sorted(out)


def deadline_from_text(text):
    """The date that follows a deadline word, else the latest date in the block."""
    dates = all_dates(text)
    if not dates:
        return None
    for m in DEADLINE_WORDS.finditer(text):
        after = [d for pos, d in dates if pos >= m.start()]
        if after:
            return after[0]
    return max(d for _, d in dates)


def dig(obj, path):
    """dig(obj, "response.docs") -> nested value; a "|" separated path returns the first non-empty one."""
    for alt in str(path).split("|"):
        cur = obj
        for part in alt.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                cur = None
                break
        if isinstance(cur, list) and cur and not isinstance(cur[0], (dict, list)):
            cur = ", ".join(str(x) for x in cur)
        if cur not in (None, "", []):
            return cur
    return None


def clean(s, n=None):
    s = re.sub(r"<[^>]+>", " ", str(s or ""))
    s = re.sub(r"\s+", " ", html.unescape(s)).strip()
    return s[:n] if n else s


def _hid(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def deadline_by_regex(cfg, text):
    """page_cfg["deadline_regex"]: a pattern whose first group is the deadline, for layouts where the nearest
    deadline word points at the wrong date."""
    if not cfg.get("deadline_regex"):
        return None
    m = re.search(cfg["deadline_regex"], text or "")
    return parse_date(m.group(1)) if m else None


def make_item(site, native_id, title, url, text="", deadline=None, infer_deadline=True, **extra):
    """infer_deadline: guess the deadline from dates in text when none was given (page cards). Data feeds pass
    False: their text holds other dates (published), and a wrong deadline would hide an open lot."""
    title = clean(title, 300)
    nid = str(native_id) if native_id not in (None, "") else _hid(url, title)
    if deadline:
        deadline = parse_date(deadline)
    elif infer_deadline:
        deadline = deadline_from_text(text)
    it = {"key": f"{site['id']}:{nid}", "site": site["id"], "id": nid, "title": title, "url": url,
          "text": clean(text, 700), "deadline": deadline}
    for k, v in extra.items():
        if v not in (None, "", []):
            it[k] = clean(v, 200) if isinstance(v, str) else v
    return it


# ---------------------------------------------------------------- extractors
def _fmt(template, row):
    def rep(m):
        v = dig(row, m.group(1))
        return "" if v is None else str(v)
    return re.sub(r"\{([^}]+)\}", rep, template)


def from_json(site, page_cfg, payloads):
    """Rows of captured JSON -> items, using page_cfg["items"] (path to the list) and ["fields"]."""
    f = page_cfg.get("fields", {})
    out = []
    for p in payloads:
        rows = dig(p, page_cfg["items"]) if page_cfg.get("items") else p
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            title = dig(row, f.get("title", "title|name"))
            if not title:
                continue
            url = _fmt(page_cfg["link"], row) if page_cfg.get("link") else page_cfg["url"]
            extra = {k: dig(row, f[k]) for k in ("buyer", "amount", "currency", "country", "region", "type",
                                                  "category", "published", "reference") if f.get(k)}
            if page_cfg.get("type") and not extra.get("type"):  # e.g. "Tanlov" for the whole page
                extra["type"] = page_cfg["type"]
            text = " | ".join(f"{k}: {clean(v, 160)}" for k, v in extra.items() if v and k != "buyer")
            if f.get("text"):
                text = clean(dig(row, f["text"]), 500) + (" | " + text if text else "")
            out.append(make_item(site, dig(row, f.get("id", "id")), title, url, text,
                                 deadline=dig(row, f["deadline"]) if f.get("deadline") else None,
                                 infer_deadline=False, **extra))
    return out


def from_links(site, page_cfg, res):
    """Anchors whose href matches page_cfg["pattern"]; the text of the surrounding card becomes the item text."""
    rx = re.compile(page_cfg["pattern"])
    ex = re.compile(page_cfg["exclude"]) if page_cfg.get("exclude") else None
    by_href = {}
    for l in res["links"]:
        href = l["href"].split("#")[0]
        if not rx.search(href) or (ex and ex.search(href + " " + l["card"])):
            continue
        cur = by_href.setdefault(href, {"texts": [], "card": ""})
        if l["text"]:
            cur["texts"].append(l["text"])
        if len(l["card"]) > len(cur["card"]):
            cur["card"] = l["card"]
    out = []
    for href, v in by_href.items():
        lines = [x.strip() for x in v["card"].split("\n") if len(x.strip()) > 12]
        cands = [t for t in v["texts"] if len(t) > 12 and not re.match(r"(view|batafsil|подробнее|details?|more)\b", t, re.I)]
        title = max(cands, key=len) if cands else (lines[0] if lines else href)
        m = re.search(page_cfg["id_from_url"], href) if page_cfg.get("id_from_url") else None
        out.append(make_item(site, m.group(1) if m else None, title, href, v["card"],
                             deadline=deadline_by_regex(page_cfg, v["card"])))
    return out


def from_text(site, page_cfg, res):
    """Split the page text into blocks: each block ends (or starts) at page_cfg["split"]."""
    text = res["text"]
    if page_cfg.get("start"):
        i = text.find(page_cfg["start"])
        text = text[i:] if i >= 0 else text
    blocks = re.split(page_cfg["split"], text)
    out = []
    for b in blocks:
        lines = [x.strip() for x in b.split("\n") if x.strip()]
        if not lines or len(b) < 40 or len(b) > 3000:
            continue
        cands = [x for x in lines if 4 < len(x) < 300 and not parse_date(x)
                 and not re.search(r"ref\.? no|deadline|published|view detail|batafsil|подробнее", x, re.I)]
        if not cands:
            continue
        title = next((x for x in cands if len(x) > 20), cands[0])
        m = re.search(page_cfg["id_regex"], b) if page_cfg.get("id_regex") else None
        out.append(make_item(site, m.group(1) if m else None, title, res["url"], b,
                             deadline=deadline_by_regex(page_cfg, b)))
    return out


def worldbank(site, page_cfg):
    params = {"format": "json", "rows": page_cfg.get("rows", 100), "os": 0, "srt": "submission_date",
              "order": "desc", "project_ctry_name_exact": page_cfg.get("country", "Uzbekistan"),
              "fl": "id,notice_type,noticedate,project_ctry_name,project_name,bid_description,bid_reference_no,"
                    "procurement_group,procurement_method_name,submission_deadline_date,contact_organization"}
    r = requests.get("https://search.worldbank.org/api/v2/procnotices", params=params, timeout=60,
                     headers={"User-Agent": UA})
    r.raise_for_status()
    out = []
    for n in r.json().get("procnotices", []):
        if re.search(page_cfg.get("skip_types", "Contract Award"), n.get("notice_type", ""), re.I):
            continue
        out.append(make_item(
            site, n["id"], n.get("bid_description") or n.get("project_name"),
            f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{n['id']}",
            f"{n.get('notice_type')} | {n.get('procurement_method_name')} | project: {n.get('project_name')}",
            deadline=(n.get("submission_deadline_date") or "")[:10] or None, infer_deadline=False,
            buyer=n.get("contact_organization"), country=n.get("project_ctry_name"), type=n.get("notice_type"),
            published=n.get("noticedate"), reference=n.get("bid_reference_no")))
    return out


def from_api(site, page_cfg):
    """A public JSON endpoint fetched without a browser (GET, or POST with page_cfg["post"])."""
    h = {"User-Agent": UA, "Accept": "application/json", **page_cfg.get("headers", {})}
    if page_cfg.get("post") is not None:
        r = requests.post(page_cfg["url"], json=page_cfg["post"], headers=h, timeout=60)
    else:
        r = requests.get(page_cfg["url"], params=page_cfg.get("params"), headers=h, timeout=60)
    r.raise_for_status()
    return from_json(site, page_cfg, [r.json()])


def collect_site(site, br=None, save_pages=True):
    """All items listed by one site. Returns (items, notes) where notes lists problems (blocked, login, errors)."""
    items, notes = [], []
    if site.get("login"):
        if br is None:
            notes.append("needs browser")
        else:
            res = br.login(site)
            if res.startswith("failed"):
                notes.append(f"login {res}")
    for i, page in enumerate(site["pages"]):
        page = {"url": page} if isinstance(page, str) else page
        mode = page.get("mode", site.get("mode", "links"))
        cfg = {**{k: v for k, v in site.items() if k not in ("pages",)}, **page}
        try:
            if mode == "worldbank":
                got = worldbank(site, cfg)
            elif mode == "api":
                got = from_api(site, cfg)
            else:
                res = br.fetch(site, cfg["url"], capture=cfg.get("capture"), scroll=cfg.get("scroll", 0),
                               wait_for=cfg.get("wait_for"), actions=cfg.get("actions"))
                if save_pages:
                    os.makedirs(PAGES_DIR, exist_ok=True)
                    with open(os.path.join(PAGES_DIR, f"{site['id']}-{i}.txt"), "w") as fh:
                        fh.write(f"URL: {res['url']}\nTITLE: {res['title']}\n\n{res['text']}")
                if res["error"]:
                    notes.append(f"page {i}: {res['error']}")
                if res["blocked"]:
                    notes.append(f"page {i}: blocked by an anti-bot check ({res['title']})")
                    continue
                if mode == "capture":
                    got = from_json(site, cfg, [c["json"] for c in res["captured"]])
                    if not res["captured"]:
                        notes.append(f"page {i}: data feed {cfg['capture']['match']!r} not seen")
                        continue
                elif mode == "text":
                    got = from_text(site, cfg, res)
                else:
                    got = from_links(site, cfg, res)
            if not got and not cfg.get("may_be_empty"):  # may_be_empty: a filtered feed that is often empty
                notes.append(f"page {i}: 0 items (layout changed? see work/tenders/{site['id']}-{i}.txt)")
            items += got
        except Exception as ex:
            notes.append(f"page {i}: {type(ex).__name__}: {str(ex)[:200]}")
    seen, uniq = set(), []
    for it in items:
        if it["key"] not in seen:
            seen.add(it["key"])
            uniq.append(it)
    return uniq, notes


# ---------------------------------------------------------------- scoring
def _norm(s):
    s = (s or "").lower()
    s = re.sub(r"[ʻʼ’‘`´']", "'", s)
    return s


def keywords():
    return A.data_json(KEYWORDS_FILE)


def _rx(words):
    parts = []
    for w in words:
        w = _norm(w)
        e = re.escape(w).replace("\\ ", r"[\s\-_]*")
        # words of 3 letters or fewer (ai, ии, ict, api) must stand alone; longer ones are stems
        parts.append(rf"(?<!\w){e}(?!\w)" if len(w) <= 3 else e)
    return re.compile("|".join(parts)) if parts else None


_KW = {}


def score(it):
    """(score, hits): strong terms 3, medium 1, local geography 1 on international sites, negative -2."""
    if not _KW:
        kw = keywords()
        for k in ("strong", "medium", "geo", "negative"):
            _KW[k] = _rx(kw.get(k, []))
        _KW["intl"] = set(kw.get("international_sites", []))
    # what is being bought, not who buys it: a school district buying coal is not an education tender
    hay = _norm(" ".join(str(it.get(k, "")) for k in ("title", "text", "category", "type")))
    hits, s = [], 0
    for k, w in (("strong", 3), ("medium", 1), ("negative", -2)):
        if _KW[k]:
            found = sorted(set(m.group(0).strip() for m in _KW[k].finditer(hay)))
            hits += [("-" if w < 0 else "") + f for f in found]
            s += w * len(found)
    if it["site"] in _KW["intl"] and _KW["geo"] and _KW["geo"].search(hay) and s > 0:
        s += 1
        hits.append("geo")
    return s, hits


# ---------------------------------------------------------------- merge
def merge(st, site, items, notes):
    """Record items; returns the keys that are new. A lot seen before keeps its review."""
    now = time.time()
    today = datetime.date.today().isoformat()
    new, expired = [], 0
    for it in items:
        if it.get("deadline") and it["deadline"] < today:  # closed already: never stored, so never "new" again
            expired += 1
            continue
        old = st["items"].get(it["key"])
        sc, hits = score(it)
        if old:
            old.update({k: it[k] for k in ("title", "url", "deadline") if it.get(k)})
            old["last_seen"] = now
            continue
        rec = {"site": it["site"], "title": it["title"][:200], "url": it["url"], "deadline": it.get("deadline"),
               "first_seen": now, "last_seen": now, "score": sc, "hits": hits, "status": "new"}
        if sc > 0:  # keep the full card only for lots that may matter
            rec["detail"] = {k: v for k, v in it.items() if k not in ("key", "site", "id", "title", "url", "deadline")}
        st["items"][it["key"]] = rec
        new.append(it["key"])
    st["site_status"][site["id"]] = {"at": now, "found": len(items), "closed": expired, "new": len(new), "notes": notes}
    return new


def open_items(st, min_score=None, include_seen=False):
    today = datetime.date.today().isoformat()
    out = []
    for k, it in st["items"].items():
        if it.get("status") != "new" and not include_seen:
            continue
        if it.get("deadline") and it["deadline"] < today:
            continue
        if min_score is not None and it.get("score", 0) < min_score:
            continue
        out.append({"key": k, **it})
    return sorted(out, key=lambda x: (-x.get("score", 0), x.get("deadline") or "9999"))


# ---------------------------------------------------------------- telegram (own bot)
def bot_token():
    return os.environ.get("TENDER_BOT_TOKEN") or (A.secrets().get("tender_bot_token"))


def tg(method, args, token=None):
    token = token or bot_token() or A.secrets().get("telegram_bot_token")
    if not token:
        raise RuntimeError("no TENDER_BOT_TOKEN (or TELEGRAM_BOT_TOKEN) set")
    last = None
    for i in range(3):
        try:
            d = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=args, timeout=60).json()
        except Exception as ex:
            d, last = None, str(ex)
        if isinstance(d, dict):
            if d.get("ok"):
                return d
            last = d.get("description")
            ra = (d.get("parameters") or {}).get("retry_after")
            if ra:
                time.sleep(int(ra) + 1)
                continue
            if re.search(r"can't parse entities|chat not found|blocked|unauthorized", str(last), re.I):
                break
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"{method} failed: {last}")


GREETING = ("Salom! Men AI Station tender agentiman. Har ish kuni ertalab tender saytlarini ko‘rib chiqaman va "
            "AI Station uchun mos lotlarni shu yerga yuboraman. Javob yozib fikr bildiring (masalan: "
            "“2 - qiziq emas, bu uskunalar xaridi”), keyingi safar hisobga olaman.")


def recipients(st):
    if os.environ.get("TENDER_CHAT_IDS"):
        return [int(x) for x in re.split(r"[,\s]+", os.environ["TENDER_CHAT_IDS"]) if x]
    if bot_token():
        return st["bot"]["chats"]
    return A.load_state().get("leads", [])  # fallback: marketing leads via the marketing bot (send-only)


def inbox_from_marketing(st):
    """Replies the marketing agent set aside for us (replies to a digest, or "tender: ..."): it is the only
    reader of the shared bot, so it parks them in its state.json under tender_inbox."""
    done = set(st["ingested"])
    feedback = []
    for x in A.load_state().get("tender_inbox", []):
        if x["uid"] in done:
            continue
        fb = {"ts": x["ts"], "from": x.get("from"), "chat_id": x.get("chat_id"), "text": x["text"],
              "reply_to": x.get("reply_to", "")}
        st["feedback"].append(fb)
        st["ingested"].append(x["uid"])
        feedback.append(fb)
    return {"via": "marketing bot", "feedback": feedback, "chats": recipients(st)}


def inbox(st, max_chats=5):
    """New replies from the team. With the shared marketing bot (default) they come from the marketing agent's
    tender_inbox; with an own TENDER_BOT_TOKEN this polls that bot, registering people who pressed Start."""
    if not bot_token():
        return inbox_from_marketing(st)
    b = st["bot"]
    args = {"timeout": 0, "limit": 100, "allowed_updates": ["message"]}
    if b.get("offset"):
        args["offset"] = b["offset"] + 1
    ups = tg("getUpdates", args).get("result") or []
    registered, feedback = [], []
    for u in ups:
        b["offset"] = max(b.get("offset", 0), u["update_id"])
        msg = u.get("message") or {}
        if (msg.get("chat") or {}).get("type") not in ("private", "group", "supergroup"):
            continue
        cid = msg["chat"]["id"]
        frm = msg.get("from") or {}
        name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x) or frm.get("username") or str(cid)
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if cid not in b["chats"]:
            if len(b["chats"]) >= max_chats:
                continue
            b["chats"].append(cid)
            b["names"][str(cid)] = msg["chat"].get("title") or name
            registered.append({"chat_id": cid, "name": b["names"][str(cid)]})
            try:
                tg("sendMessage", {"chat_id": cid, "text": GREETING})
            except Exception as ex:
                print("greeting failed:", ex)
            if text.startswith("/start"):
                continue
        if not text or text.startswith("/start"):
            continue
        rt = (msg.get("reply_to_message") or {}).get("text") or ""
        fb = {"ts": time.time(), "from": name, "chat_id": cid, "text": text[:1000], "reply_to": rt[:600]}
        st["feedback"].append(fb)
        feedback.append(fb)
    return {"registered": registered, "feedback": feedback, "chats": b["chats"]}


def send_html(st, text):
    mids = []
    for cid in recipients(st):
        for chunk in split_message(text):
            a = {"chat_id": cid, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True}
            try:
                d = tg("sendMessage", a)
            except RuntimeError as ex:
                if "parse" not in str(ex).lower():
                    raise
                a.pop("parse_mode")
                a["text"] = re.sub(r"<[^>]+>", "", chunk)
                d = tg("sendMessage", a)
            mids.append(d["result"]["message_id"])
            # so the marketing agent can tell replies to this message apart from post edits
            st["sent"].append({"chat": cid, "mid": d["result"]["message_id"], "ts": time.time()})
    return mids


def split_message(text, limit=3900):
    """Split at blank lines so no tender block (or HTML tag) is cut in half."""
    out, cur = [], ""
    for block in text.split("\n\n"):
        if cur and len(cur) + len(block) + 2 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{block}" if cur else block
        while len(cur) > limit:
            out.append(cur[:limit])
            cur = cur[limit:]
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------- digest
VERDICTS = {"bid": "🟢 Ishtirok etishni tavsiya qilaman", "watch": "🟡 Kuzatib boring"}


def days_left(deadline):
    if not deadline:
        return None
    try:
        return (datetime.date.fromisoformat(deadline) - (A.tashkent_now().date())).days
    except Exception:
        return None


def digest_html(st, reviews, stats):
    date = A.tashkent_now().strftime("%d.%m.%Y")
    picked = [r for r in reviews if r.get("verdict") in VERDICTS]
    lines = [f"📑 <b>Tenderlar sharhi - {date}</b>",
             f"Ko‘rildi: {stats['new']} ta yangi lot, {stats['sites_ok']}/{stats['sites']} sayt. "
             f"Mos keldi: {len(picked)} ta."]
    n = 0
    for verdict, head in VERDICTS.items():
        group = sorted([r for r in picked if r["verdict"] == verdict], key=lambda r: -r.get("fit", 0))
        if not group:
            continue
        lines.append(f"<b>{head}</b>")
        for r in group:
            n += 1
            it = st["items"].get(r.get("key") or "web:" + _hid(r.get("url", "")), {})
            dl = parse_date(r.get("deadline")) or it.get("deadline")
            left = days_left(dl)
            when = f"⏰ {dl}" + (f" ({left} kun qoldi)" if left is not None and left >= 0 else "") if dl else "⏰ muddat ko‘rsatilmagan"
            money = f"💰 {esc(str(r['amount']))} · " if r.get("amount") else ""
            block = [f"{n}. <b>{esc(r.get('title_uz') or it.get('title') or r.get('title') or r.get('key'))}</b>"]
            if r.get("buyer"):
                block.append(f"🏢 {esc(r['buyer'])}")
            block.append(f"{money}{when} · {esc(site_name(it.get('site') or r.get('site') or 'web'))}")
            if r.get("summary"):
                block.append(esc(r["summary"]))
            if r.get("why"):
                block.append(f"<i>Nega:</i> {esc(r['why'])}")
            if r.get("next_step"):
                block.append(f"<i>Keyingi qadam:</i> {esc(r['next_step'])}")
            url = r.get("url") or it.get("url")
            if url:
                block.append(f"🔗 <a href=\"{esc(url, quote=True)}\">E‘lon</a>")
            lines.append("\n".join(block))
    if not picked:
        lines.append("Bugun AI Station profiliga mos yangi tender topilmadi.")
    if stats.get("problems"):
        lines.append("⚠️ <i>Ko‘rib bo‘lmadi:</i> " + esc("; ".join(stats["problems"]))[:700])
    lines.append("<i>Fikr bildirish uchun shu xabarga reply qiling (masalan: “2 - qiziq emas”) yoki "
                 "“tender: ...” deb yozing.</i>")
    return "\n\n".join(lines)


def site_name(sid):
    try:
        return site_by_id(sid)["name"]
    except SystemExit:
        return sid
