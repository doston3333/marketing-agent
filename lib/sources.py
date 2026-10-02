# Story discovery for the AI Station marketing agent.
# Collects 150-400 items from a fixed source list (public Telegram channels via t.me/s, RSS, Google News
# RSS, Hacker News), groups duplicates into story clusters, scores them, and keeps a rolling story bank
# (data/story_bank.json) so a strong story from earlier in the week can beat a weak one from today.
# No LLM here: Claude reads the top candidates (`agent.py candidates`) and applies the editorial rubric.
import hashlib, html, json, math, os, re, statistics, time, datetime, email.utils
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; AIStationAgent/1.0)"}

# tier: 3 = core local tech/startup, 2 = general local or regional news, 1 = global/background
TELEGRAM = {
    "thetechkz": 3, "itpark_uz": 3, "startupchoyxona": 3, "yoshlarventures": 3, "startupgarage_uz": 3,
    "itparkventures": 3, "spotuz": 2, "kunuzen": 2, "kunuzofficial": 2, "gazetauz": 2, "daryo_uz": 2,
    "uzdaily": 2, "kursivuz": 2,
}
RSS = {
    "https://www.spot.uz/rss/": 3, "https://www.spot.uz/en/rss/": 3, "https://the-tech.kz/feed/": 3,
    "https://digitalbusiness.kz/feed/": 2, "https://kun.uz/news/rss": 2, "https://www.gazeta.uz/ru/rss/": 2,
    "https://daryo.uz/feed/": 2, "https://uz.kursiv.media/feed/": 2, "https://kz.kursiv.media/feed/": 1,
    "https://timesca.com/feed/": 2, "https://www.uzdaily.uz/en/rss": 2,
    "https://openai.com/news/rss.xml": 1,
    "https://blog.google/technology/ai/rss/": 1, "https://techcrunch.com/category/artificial-intelligence/feed/": 1,
}
GNEWS = [  # (query, locale) - when:3d keeps it fresh
    ("Uzbekistan (startup OR AI OR venture OR \"IT Park\") when:3d", "en"),
    ("\"Central Asia\" (startup OR venture OR funding) when:3d", "en"),
    ("Узбекистан (стартап OR \"искусственный интеллект\" OR венчур OR \"IT Park\") when:3d", "ru"),
    ("Казахстан OR Узбекистан стартап инвестиции when:3d", "ru"),
]
GNEWS_LOCALE = {"en": "hl=en-US&gl=US&ceid=US:en", "ru": "hl=ru&gl=RU&ceid=RU:ru"}

# Topic hints: what AI Station's audience cares about. Used for a cheap relevance prior and tagging.
KEYWORDS = {
    "ai": r"\b(ai|a\.i\.|llm|gpt|claude|gemini|openai|anthropic|nvidia|sun.?iy intellekt|искусственн\w+ интеллект|нейросет\w*|ии)\b",
    "startup": r"\b(startap\w*|startup\w*|стартап\w*|founder\w*|asoschi\w*|основател\w*|accelerat\w*|акселерат\w*|inkubator\w*)\b",
    "venture": r"\b(invest\w*|инвест\w*|venture|venchur\w*|венчур\w*|raund\w*|раунд\w*|funding|seed|pre-seed|grant\w*|грант\w*|vc)\b",
    "local": r"\b(uzbek\w*|o.zbek\w*|узбек\w*|tashkent|toshkent|ташкент\w*|it ?park|central asia|markaziy osiyo|центральн\w+ ази\w+|kazakh\w*|казах\w*|astana hub)\b",
    "policy": r"\b(prezident|president|президент\w*|farmon|qaror|указ\w*|постановлен\w*|ministry|vazirlik|министерств\w*|law|qonun|закон\w*)\b",
    "education": r"\b(talaba\w*|student\w*|студент\w*|universit\w*|университет\w*|ta.lim|education|образован\w*|kurs\w*|курс\w*|olimpiada|hackathon|хакатон\w*)\b",
    "opportunity": r"(ariza\w* (topshir|qabul)\w*|qabul (ochiq|boshlan)\w*|ro.yxatdan o.t\w*|tanlov\w*|bepul (dastur|kurs|o.qish)\w*|"
                   r"applications? (open|close|deadline)\w*|apply (now|by|before)|deadline|call for (applications|startups|proposals)|"
                   r"подач\w* заяв\w*|при[её]м заяв\w*|открыт набор|набор на|заявк\w* до|дедлайн|последний день|"
                   r"vakans\w*|ваканс\w*|we.re hiring|грант\w* (на|для|до)|grant program\w*|stipend\w*|стипенди\w*)",
    "uz": r"\b(uzbek\w*|o.zbek\w*|узбек\w*|tashkent|toshkent|ташкент\w*|it ?park|samarkand|samarqand|самарканд\w*)\b",
}
_KW = {k: re.compile(v, re.I) for k, v in KEYWORDS.items()}
STOP = set("""the a an and or of to in on for with at by from is are was were be been it its this that as into about after over new
va bilan uchun bu ham bir yil yilda haqida qilib dan ga da ni ning esa lekin ham emas bo'ladi bo‘ladi
и в на с по для из от к о что это как не а но за до при об во же или его их был была были будет году""".split())


def _txt(s, keep_lines=False):
    s = re.sub(r"<br\s*/?>", "\n", s or "")
    s = html.unescape(re.sub(r"<[^>]+>", "" if keep_lines else " ", s))
    if keep_lines:
        return "\n".join(re.sub(r"[ \t]+", " ", l).strip() for l in s.split("\n")).strip()
    return re.sub(r"\s+", " ", s).strip()


def _get(url, timeout=20):
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r


def _ts(s):
    if not s:
        return None
    try:
        return email.utils.parsedate_to_datetime(s).timestamp()
    except Exception:
        pass
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _views(v):
    v = (v or "").strip().upper().replace(",", ".")
    m = re.match(r"([\d.]+)\s*([KM]?)", v)
    if not m:
        return None
    n = float(m.group(1))
    return int(n * {"K": 1e3, "M": 1e6}.get(m.group(2), 1))


# ---------------------------------------------------------------- collectors
def telegram_channel(name, pages=1):
    """Public channel posts from https://t.me/s/<name>: text, date, views, link. No login needed."""
    out, before = [], None
    for _ in range(pages):
        r = _get(f"https://t.me/s/{name}" + (f"?before={before}" if before else "")).text
        ids = []
        for m in re.finditer(r'data-post="([^"/]+)/(\d+)"(.*?)(?=data-post="|\Z)', r, re.S):
            pid, b = int(m.group(2)), m.group(3)
            ids.append(pid)
            t = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', b, re.S)
            v = re.search(r'tgme_widget_message_views">([^<]+)<', b)
            d = re.search(r'datetime="([^"]+)"', b)
            text = _txt(t.group(1), keep_lines=True) if t else ""
            if not text:
                continue
            links = re.findall(r'href="(https?://(?!t\.me)[^"]+)"', t.group(1)) if t else []
            out.append({"source": f"tg:{name}", "id": f"tg:{name}/{pid}", "url": f"https://t.me/{name}/{pid}",
                        "title": text[:160], "text": text[:1500], "ts": _ts(d.group(1)) if d else None,
                        "views": _views(v.group(1)) if v else None, "links": links[:3],
                        "photo": bool(re.search(r"tgme_widget_message_photo", b))})
        if not ids:
            break
        before = min(ids)
    return out


def rss(url):
    root = ET.fromstring(_get(url).content)
    out = []
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    for it in items[:60]:
        def g(*tags):
            for t in tags:
                e = it.find(t)
                if e is not None:
                    return e.get("href") if (e.text is None and e.get("href")) else e.text
            return None
        title = _txt(g("title", "{http://www.w3.org/2005/Atom}title"))
        link = g("link", "{http://www.w3.org/2005/Atom}link") or ""
        desc = _txt(g("description", "{http://www.w3.org/2005/Atom}summary", "{http://www.w3.org/2005/Atom}content"))
        ts = _ts(g("pubDate", "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated"))
        src = g("source")
        if title:
            out.append({"source": "rss:" + re.sub(r"^https?://(www\.)?", "", url).split("/")[0],
                        "id": link or title, "url": link.strip(), "title": title, "text": desc[:1200], "ts": ts,
                        "outlet": _txt(src) if src else None})
    return out


def google_news(query, locale):
    url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&{GNEWS_LOCALE[locale]}"
    items = rss(url)
    for it in items:
        it["source"] = "gnews:" + (it.get("outlet") or "?")
        it["title"] = re.sub(r"\s+-\s+[^-]+$", "", it["title"])  # strip " - Outlet"
    return items


def hacker_news(min_points=150, hours=72):
    since = int(time.time() - hours * 3600)
    r = _get("https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=50"
             f"&numericFilters=points>{min_points},created_at_i>{since}").json()
    return [{"source": "hn", "id": f"hn:{h['objectID']}", "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
             "title": h.get("title") or "", "text": "", "ts": h.get("created_at_i"), "points": h.get("points"),
             "comments": h.get("num_comments")} for h in r.get("hits", []) if h.get("title")]


def collect(hours=96, tg_pages=1):
    """Fetch every source in parallel. Returns (items, errors)."""
    jobs = [("tg", n, lambda n=n: telegram_channel(n, tg_pages)) for n in TELEGRAM]
    jobs += [("rss", u, lambda u=u: rss(u)) for u in RSS]
    jobs += [("gnews", q, lambda q=q, l=l: google_news(q, l)) for q, l in GNEWS]
    jobs += [("hn", "hn", hacker_news)]
    items, errors = [], {}

    def run(j):
        kind, key, fn = j
        try:
            return key, fn(), None
        except Exception as ex:
            return key, [], str(ex)[:120]

    with ThreadPoolExecutor(12) as ex:
        for key, res, err in ex.map(run, jobs):
            if err:
                errors[key] = err
            items += res
    cutoff = time.time() - hours * 3600
    items = [i for i in items if not i.get("ts") or i["ts"] >= cutoff]
    return items, errors


# ---------------------------------------------------------------- clustering + scoring
def tokens(s):
    s = (s or "").lower().replace("‘", "'").replace("ʻ", "'").replace("’", "'")
    toks = re.findall(r"[\w$%'.]+", s)
    out = set()
    for t in toks:
        t = t.strip(".'")
        if len(t) < 3 and not re.search(r"\d", t):
            continue
        if t in STOP:
            continue
        out.add(t[:7])  # crude stemming that works across uz/ru/en suffixes
    return out


def _sim(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / min(len(a), len(b)) * 0.5 + inter / len(a | b) * 0.5


def tier(source):
    if source.startswith("tg:"):
        return TELEGRAM.get(source[3:], 1)
    if source.startswith("rss:"):
        for u, t in RSS.items():
            if source[4:] in u:
                return t
        return 1
    if source.startswith("gnews:"):
        return 2
    return 1


def tags(text):
    return sorted(k for k, rx in _KW.items() if rx.search(text or ""))


def channel_medians(items):
    by = {}
    for i in items:
        if i["source"].startswith("tg:") and i.get("views"):
            by.setdefault(i["source"], []).append(i["views"])
    return {k: statistics.median(v) for k, v in by.items() if len(v) >= 3}


def cluster(items, threshold=0.42):
    """Greedy single-pass clustering on title+lead tokens. Returns list of clusters (dicts)."""
    med = channel_medians(items)
    clusters = []
    for it in sorted(items, key=lambda i: -(i.get("ts") or 0)):
        tk = tokens(it["title"] + " " + it.get("text", "")[:300])
        if len(tk) < 3:
            continue
        best, bs = None, 0
        for c in clusters:
            s = _sim(tk, c["_tok"])
            if s > bs:
                best, bs = c, s
        it = {k: v for k, v in it.items() if k != "text"} | {"snippet": it.get("text", "")[:400]}
        if it["source"] in med and it.get("views"):
            it["eng"] = round(it["views"] / med[it["source"]], 2)
        if best and bs >= threshold:
            best["items"].append(it)
            best["_tok"] |= tk
        else:
            clusters.append({"items": [it], "_tok": set(tk)})
    return clusters


def score_cluster(c, posted_tokens, now=None):
    now = now or time.time()
    srcs = {i["source"] for i in c["items"]}
    velocity = sum(tier(s) for s in srcs)
    eng = max([i.get("eng", 0) for i in c["items"]] + [0])
    hn = max([i.get("points", 0) or 0 for i in c["items"]] + [0])
    first = min([i["ts"] for i in c["items"] if i.get("ts")] or [now])
    age_h = max(0.0, (now - first) / 3600)
    fresh = 0.5 ** (age_h / 36)
    text = " ".join(i["title"] + " " + i.get("snippet", "") for i in c["items"])
    tg = tags(text)
    rel = len({"ai", "startup", "venture", "local", "education", "opportunity", "policy"} & set(tg))
    nov = 1 - max([_sim(c["_tok"], p) for p in posted_tokens] + [0])
    score = (0.30 * min(rel, 4) / 4 + 0.25 * min(velocity, 9) / 9 + 0.15 * min(eng, 3) / 3
             + 0.10 * min(hn, 600) / 600 + 0.20 * fresh) * (0.4 + 0.6 * nov)
    if "uz" in tg:
        score += 0.10  # Uzbekistan first, the rest of Central Asia second
    if "opportunity" in tg and "local" in tg:
        score += 0.15  # proven best performer on @aistationuz: things the audience can act on
    return round(score, 3), {"velocity": velocity, "sources": len(srcs), "eng_ratio": eng, "hn_points": hn,
                             "age_h": round(age_h, 1), "relevance_tags": tg, "novelty": round(nov, 2)}


def _cid(c):
    best = max(c["items"], key=lambda i: (tier(i["source"]), len(i["title"])))
    return "s" + hashlib.sha1(" ".join(sorted(tokens(best["title"]))).encode()).hexdigest()[:8]


def update_bank(bank, items, posted_topics, keep_days=7):
    """Merge freshly collected items into the story bank. bank = {"stories": {id: story}, "updated": ts}."""
    posted_tokens = [tokens(t) for t in posted_topics]
    stories = bank.setdefault("stories", {})
    # attach to existing stories first so a story keeps its id (and status) across days
    fresh = []
    for it in items:
        tk = tokens(it["title"] + " " + it.get("text", "")[:300])
        best, bs = None, 0
        for sid, s in stories.items():
            sim = _sim(tk, set(s["tokens"]))
            if sim > bs:
                best, bs = s, sim
        if best and bs >= 0.5:
            if it["id"] not in {x["id"] for x in best["items"]}:
                best["items"].append({k: v for k, v in it.items() if k != "text"} | {"snippet": it.get("text", "")[:400]})
        else:
            fresh.append(it)
    for c in cluster(fresh):
        sid = _cid(c)
        if sid in stories:
            stories[sid]["items"] += c["items"]
            continue
        stories[sid] = {"id": sid, "status": "candidate", "first_seen": time.time(), "tokens": sorted(c["_tok"]),
                        "items": c["items"]}
    now = time.time()
    for sid, s in list(stories.items()):
        s["items"] = sorted({i["id"]: i for i in s["items"]}.values(), key=lambda i: -(i.get("ts") or 0))[:8]
        c = {"items": s["items"], "_tok": set(s["tokens"])}
        s["score"], s["signals"] = score_cluster(c, posted_tokens, now)
        if s["status"] == "candidate" and now - s["first_seen"] > keep_days * 86400:
            del stories[sid]
    keep = sorted(stories.values(), key=lambda s: (s["status"] != "candidate", -s["score"]))
    bank["stories"] = {s["id"]: s for s in keep[:200] if s["status"] != "candidate" or s["score"] >= 0.25}
    for s in bank["stories"].values():
        s["items"] = s["items"][:8]
        for i in s["items"]:
            i["snippet"] = (i.get("snippet") or "")[:240]
    bank["updated"] = now
    return bank


def headline(s):
    best = max(s["items"], key=lambda i: (tier(i["source"]), -abs(len(i["title"]) - 90)))
    return best["title"]


def top(bank, n=25, statuses=("candidate",)):
    ss = [s for s in bank.get("stories", {}).values() if s["status"] in statuses]
    return sorted(ss, key=lambda s: -s["score"])[:n]


def og_image(url):
    """The article's lead photo (og:image / twitter:image), for a real-photo background."""
    try:
        h = _get(url, timeout=15).text[:200000]
    except Exception:
        return None
    m = re.search(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)', h, re.I) \
        or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)', h, re.I)
    return html.unescape(m.group(1)) if m else None
