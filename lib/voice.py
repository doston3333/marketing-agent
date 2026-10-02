# Voice memory for the AI Station marketing agent.
# - archive: every public @aistationuz post with views (data/archive.json), refreshed from t.me/s each run
# - examples(): the 3 most similar high-performing human posts for a topic + 1 recent (not just "the latest 5")
# - fingerprint(): measurable style targets computed from human-written posts
# - slop(): phrases the agent over-uses compared with the human archive (auto-banned list)
import json, re, statistics, time
from collections import Counter
import sources as S

CHANNEL = "aistationuz"
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐✅❌✨]")


def refresh_archive(archive, pages=8):
    """Merge the newest channel posts (text, date, views) into archive {"posts": {id: post}}."""
    posts = archive.setdefault("posts", {})
    for p in S.telegram_channel(CHANNEL, pages=pages):
        pid = p["id"].split("/")[-1]
        old = posts.get(pid, {})
        posts[pid] = {"id": int(pid), "ts": p["ts"], "text": p["text"], "views": p["views"], "photo": p["photo"],
                      "url": p["url"], "agent_post": old.get("agent_post"), "views_seen": time.time()}
    archive["updated"] = time.time()
    return archive


def _age_days(p, now):
    return (now - (p.get("ts") or now)) / 86400


def engagement(archive, now=None):
    """views relative to the channel median of mature posts (>=7 days old, views mostly settled)."""
    now = now or time.time()
    posts = list(archive.get("posts", {}).values())
    mature = [p["views"] for p in posts if p.get("views") and _age_days(p, now) >= 7]
    med = statistics.median(mature) if mature else 1
    out = {}
    for p in posts:
        if not p.get("views"):
            continue
        age = _age_days(p, now)
        # young posts have not collected their views yet: scale by a rough accumulation curve
        settle = min(1.0, 0.55 + 0.45 * age / 7) if age < 7 else 1.0
        out[str(p["id"])] = round(p["views"] / settle / med, 2)
    return out, med


def kind(text):
    t = text.lower()
    if re.search(r"muborak|bayram|tabriklay|qutlay|поздрав", t):
        return "greeting"
    if re.search(r"ariza|qabul|apply|applications|vakans|lavozim|набор|заявк|deadline|muddat", t):
        return "opportunity"
    if re.search(r"hamkor|partner|a.zosi|member|alliance|memorandum", t):
        return "partnership"
    if re.search(r"\(\d-qism\)|atama|qadam|maslahat|xato|tips|\d+ ta ", t):
        return "educational"
    if re.search(r"tashrif|tadbir|forum|week|summit|demo ?day|event|uchrashuv", t):
        return "event"
    return "other"


def examples(archive, topic, k=3, exclude_agent=True):
    """k posts most similar to the topic, weighted by engagement, plus the most recent post for drift control."""
    now = time.time()
    eng, _ = engagement(archive, now)
    tk = S.tokens(topic)
    posts = [p for p in archive.get("posts", {}).values() if p.get("text") and len(p["text"]) > 120
             and not (exclude_agent and p.get("agent_post")) and kind(p["text"]) != "greeting"]
    sims = {p["id"]: S._sim(tk, S.tokens(p["text"])) for p in posts}
    top_sim = max(sims.values() or [1]) or 1
    scored = []
    for p in posts:
        e = eng.get(str(p["id"]), 1.0)
        scored.append((sims[p["id"]] / top_sim * 0.65 + min(e, 3) / 3 * 0.35, p))
    picked = [p for _, p in sorted(scored, key=lambda x: -x[0])[:k]]
    recent = max(posts, key=lambda p: p.get("ts") or 0) if posts else None
    if recent and recent not in picked:
        picked.append(recent)
    return [{"id": p["id"], "views": p.get("views"), "eng": eng.get(str(p["id"])), "kind": kind(p["text"]),
             "text": p["text"]} for p in picked]


def _sentences(t):
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", t) if len(s.split()) >= 3]


def fingerprint(texts):
    """Measurable style of a set of posts."""
    sl, emo, q, ex, words, paras, hooks = [], [], 0, 0, 0, [], Counter()
    for t in texts:
        ss = _sentences(t)
        sl += [len(s.split()) for s in ss]
        w = len(t.split())
        words += w
        emo.append(len(EMOJI.findall(t)) / max(w, 1) * 100)
        q += t.count("?")
        ex += t.count("!")
        paras.append(len([p for p in t.split("\n") if p.strip()]))
        first = t.strip().split("\n")[0]
        hooks["emoji_start"] += bool(EMOJI.match(first))
        hooks["number_start"] += bool(re.match(r"\W*\d", first))
        hooks["question_start"] += first.rstrip().endswith("?")
    n = max(len(texts), 1)
    return {"posts": len(texts), "sentence_words_mean": round(statistics.mean(sl), 1) if sl else None,
            "sentence_words_p90": round(sorted(sl)[int(len(sl) * 0.9)] if sl else 0, 1),
            "emoji_per_100_words": round(statistics.mean(emo), 2) if emo else None,
            "questions_per_post": round(q / n, 2), "exclamations_per_post": round(ex / n, 2),
            "lines_per_post": round(statistics.mean(paras), 1) if paras else None,
            "hooks": {k: round(v / n, 2) for k, v in hooks.items()}}


def _ngrams(t, n):
    w = re.findall(r"[\w‘'’-]+", t.lower())
    return [" ".join(w[i:i + n]) for i in range(len(w) - n + 1)]


def slop(agent_texts, human_texts, min_count=3, ratio=5.0):
    """Phrases (2-4 grams) the agent uses far more than the humans did: candidates for the banned list."""
    out = []
    for n in (2, 3, 4):
        a = Counter(g for t in agent_texts for g in set(_ngrams(t, n)))
        h = Counter(g for t in human_texts for g in set(_ngrams(t, n)))
        na, nh = max(len(agent_texts), 1), max(len(human_texts), 1)
        for g, c in a.items():
            if c < min_count or re.search(r"\d", g) or g.startswith("#") or "#" in g:
                continue
            fa, fh = c / na, (h.get(g, 0) + 0.5) / nh
            if fa / fh >= ratio:
                out.append((g, c, round(fa / fh, 1)))
    out.sort(key=lambda x: -x[2])
    # drop n-grams contained in a longer flagged one
    keep = []
    for g, c, r in out:
        if not any(g in k[0] and g != k[0] for k in keep):
            keep.append((g, c, r))
    return keep[:40]
