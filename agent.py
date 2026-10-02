#!/usr/bin/env python3
"""Command line for the AI Station marketing agent. Every command loads state from data/ and saves it back.

Telegram + state
  check                       env + token sanity check (getMe), no side effects
  peek                        read-only: count of waiting Telegram updates and pending actions
  start                       daily run: poll, queue lead actions for the hourly handler, store lead ideas, print leads
  actions                     hourly run: consume queued + new actions, print them as JSON
  ack CB_ID TEXT              answer a button press
  say HTML [--chat ID] [--publish-buttons POST_ID]   message the leads (or one chat)
  status POST_ID STATUS [--reason TEXT]               approved / rejected / sent (rejected keeps the reason)
  publish POST_ID             post the approved Telegram version to @aistationuz (only after a lead pressed 📢)
  settings [KEY VALUE]        show or set settings, e.g. settings publish_telegram on

Finding what to post
  collect                     pull ~700 items from Telegram channels, RSS, Google News, HN into the story bank
  candidates [--n 20]         top story clusters with signals (velocity, engagement, novelty, tags)
  story ID [STATUS]           show a story, or mark it posted / rejected / evergreen
  today                       today's slot from the weekly plan + matching opportunities, ideas and stories
  week                        the plan for the next 7 days (Monday run sends it to the leads)
  idea add TEXT [--pillar P] [--photo REF] | idea list | idea done ID

Writing
  knowledge                   guide, team rules, style targets, over-used phrases, edits, recent topics, performance
  examples "TOPIC" [--k 3]    the team's most similar high-performing posts (voice examples for this topic)
  learn TEXT [--context C] [--explicit]   store a preference from a lead's edit (confirmed when repeated/explicit)
  show POST_ID                print a stored post as JSON (to edit and resend)
  lint FILE                   lint a post JSON file; exit 1 if there are problems (prints style warnings too)

Images + sending
  render FILE [--id ID] [--n 2]   make background candidates + previews to look at; pin one with spec.<p>.art_path
  send FILE [--id ID] [--note TEXT] [--history] [--no-minimax]

Memory (survives fresh sessions without git push)
  save                        commit data/ to the repo's default branch via the GitHub API (GITHUB_TOKEN)
  restore                     unpack the newest pinned data/ bundle from the private storage channel
  backup                      upload data/ as a new pinned bundle to the storage channel

Learning loop
  metrics                     refresh the @aistationuz archive (views), link agent posts, update performance.json
  style                       recompute the house-style fingerprint and the over-used phrase list
"""
import argparse, datetime, json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import agent_lib as A  # noqa: E402
import sources as S  # noqa: E402
import voice as V  # noqa: E402

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TENDER_FILES = ("tenders.json", "tender_sites.json", "tender_keywords.json", "tender_profile.md")  # tender.py saves these


def _post(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _posted_topics(state):
    return [h.get("topic", "") for h in state["history"][-40:]] + \
        [r.get("topic", "") for r in state.get("rejected", [])[-20:]]


def _brief_story(s):
    return {"id": s["id"], "score": s["score"], "headline": S.headline(s), "signals": s["signals"],
            "status": s["status"],
            "items": [{"source": i["source"], "title": i["title"][:200], "url": i["url"], "views": i.get("views"),
                       "eng": i.get("eng"), "age_h": round((time.time() - i["ts"]) / 3600, 1) if i.get("ts") else None,
                       "snippet": (i.get("snippet") or "")[:240]} for i in s["items"][:5]]}


def cmd_today(state, day=None):
    plan = A.data_json("content_plan.json")
    now = A.tashkent_now()
    day = day or DAYS[now.weekday()]
    slot = dict(plan["week"][day])
    wp = state.get("week_plan", {}).get(now.strftime("%Y%m%d"))
    bank = A.data_json("story_bank.json", {})
    stories = S.top(bank, 60)
    opps = [s for s in stories if "opportunity" in s["signals"]["relevance_tags"]][:6]
    ideas = [i for i in A.data_json("ideas.json", {"ideas": []})["ideas"] if i.get("status") == "open"]
    lead_ideas = [i for i in ideas if i.get("source") == "lead"]
    pillar_ideas = [i for i in ideas if i.get("pillar") == slot["pillar"] and i.get("source") != "lead"][:4]
    print(json.dumps({"day": day, "date": now.strftime("%Y-%m-%d"), "slot": slot, "planned_this_week": wp,
                      "rules": plan.get("always"),
                      "lead_ideas_first": lead_ideas,
                      "opportunities_in_bank": [_brief_story(s) for s in opps],
                      "ideas_for_this_pillar": pillar_ideas,
                      "top_stories": [_brief_story(s) for s in stories[:12]],
                      "bank_updated_h_ago": round((time.time() - bank.get("updated", 0)) / 3600, 1) if bank else None},
                     ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser(description="AI Station marketing agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("check", "peek", "start", "actions", "knowledge", "collect", "today", "week", "metrics", "style",
              "backup", "restore", "save"):
        sub.add_parser(c)
    p = sub.add_parser("show"); p.add_argument("post_id")
    p = sub.add_parser("lint"); p.add_argument("file")
    p = sub.add_parser("render"); p.add_argument("file"); p.add_argument("--id", default="preview"); p.add_argument("--n", type=int, default=2)
    p = sub.add_parser("send"); p.add_argument("file"); p.add_argument("--id"); p.add_argument("--note")
    p.add_argument("--history", action="store_true"); p.add_argument("--no-minimax", action="store_true")
    p = sub.add_parser("ack"); p.add_argument("cb_id"); p.add_argument("text", nargs="?", default="")
    p = sub.add_parser("status"); p.add_argument("post_id"); p.add_argument("status"); p.add_argument("--reason", default="")
    p = sub.add_parser("say"); p.add_argument("html"); p.add_argument("--chat", type=int); p.add_argument("--publish-buttons")
    p = sub.add_parser("learn"); p.add_argument("text"); p.add_argument("--context", default=""); p.add_argument("--explicit", action="store_true")
    p = sub.add_parser("publish"); p.add_argument("post_id")
    p = sub.add_parser("settings"); p.add_argument("key", nargs="?"); p.add_argument("value", nargs="?")
    p = sub.add_parser("candidates"); p.add_argument("--n", type=int, default=20)
    p = sub.add_parser("story"); p.add_argument("id"); p.add_argument("status", nargs="?")
    p = sub.add_parser("examples"); p.add_argument("topic"); p.add_argument("--k", type=int, default=3)
    p = sub.add_parser("idea"); p.add_argument("op", choices=["add", "list", "done"]); p.add_argument("text", nargs="?")
    p.add_argument("--pillar", default=""); p.add_argument("--photo"); p.add_argument("--source", default="lead")
    a = ap.parse_args()

    if a.cmd == "check":
        s = A.secrets()
        print("TELEGRAM_BOT_TOKEN:", "set" if s.get("telegram_bot_token") else "MISSING")
        print("MINIMAX_API_KEY:", "set" if s.get("minimax_key") else "MISSING")
        if s.get("telegram_bot_token"):
            me = A.tg("getMe", {})["result"]
            print("bot:", "@" + me.get("username", "?"))
        st = A.load_state()
        print("leads:", st["leads"], "offset:", st["offset"], "posts:", len(st["posts"]),
              "settings:", st.get("settings"))
        return
    if a.cmd == "save":
        import ghstore
        try:
            print(ghstore.save(f"agent: state {A.tashkent_now():%Y-%m-%d %H:%M}", exclude=TENDER_FILES))
        except Exception as ex:
            import storage
            print("GitHub save failed:", ex)
            res = storage.backup()
            print("fallback:", res)
            if res.startswith("no storage chat"):
                sys.exit(1)
        return
    if a.cmd in ("backup", "restore"):
        import storage
        print(storage.backup() if a.cmd == "backup" else storage.restore())
        return
    if a.cmd == "peek":
        st = A.load_state()
        print("UPDATES", len(A.peek(st)), "PENDING", len(st["pending"]))
        return
    if a.cmd == "collect":
        st = A.load_state()
        items, errors = S.collect()
        bank = S.update_bank(A.data_json("story_bank.json", {}), items, _posted_topics(st))
        A.save_json("story_bank.json", bank)
        print(f"collected {len(items)} items -> {len(bank['stories'])} stories in the bank; source errors: {errors or 'none'}")
        return
    if a.cmd == "candidates":
        bank = A.data_json("story_bank.json", {})
        print(json.dumps([_brief_story(s) for s in S.top(bank, a.n)], ensure_ascii=False, indent=1))
        return
    if a.cmd == "story":
        bank = A.data_json("story_bank.json", {})
        s = bank.get("stories", {}).get(a.id)
        if not s:
            sys.exit(f"no story {a.id}")
        if a.status:
            s["status"] = a.status
            A.save_json("story_bank.json", bank)
        print(json.dumps(_brief_story(s), ensure_ascii=False, indent=1))
        return
    if a.cmd == "examples":
        arch = A.data_json("archive.json", {})
        if not arch.get("posts"):
            arch = V.refresh_archive(arch)
            A.save_json("archive.json", arch)
        for e in V.examples(arch, a.topic, a.k):
            print(f"--- post {e['id']} · {e['kind']} · {e['views']} views ({e['eng']}x channel median)\n{e['text']}\n")
        return
    if a.cmd == "style":
        st = A.load_state()
        arch = A.data_json("archive.json", {})
        human = [p["text"] for p in arch.get("posts", {}).values() if not p.get("agent_post") and V.kind(p["text"]) != "greeting"]
        by_topic = {}  # one post per topic, so a re-sent or edited post is not counted several times
        for p in sorted(st["posts"].values(), key=lambda p: p.get("updated", 0)):
            by_topic[p.get("topic") or id(p)] = p
        agent = []
        for p in by_topic.values():
            agent.append(p["captions"]["instagram"] + "\n" + p["captions"]["telegram"])
        style = {"telegram_human": V.fingerprint(human), "agent_drafts": V.fingerprint(agent), "updated": time.time()}
        A.save_json("style.json", style)
        sl = V.slop(agent, human, min_count=3) if len(agent) >= 8 else []
        A.save_json("slop.json", {"phrases": sl, "agent_texts": len(agent), "human_texts": len(human), "updated": time.time()})
        print(json.dumps(style, ensure_ascii=False, indent=1))
        print("over-used phrases:", sl[:15] or f"(needs 8+ distinct agent posts; have {len(agent)})")
        return
    if a.cmd == "lint":
        post = _post(a.file)
        probs = A.lint(post)
        print(json.dumps(probs, ensure_ascii=False, indent=1))
        for w in A.lint_warnings(post):
            print("warn:", w)
        sys.exit(1 if probs else 0)
    if a.cmd == "render":
        import visuals
        post = _post(a.file)
        out = visuals.render_candidates(post["spec"], a.id, n=a.n)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        print("Look at each preview (Read the .jpg), then set spec.<platform>.art_path to the chosen art_path.")
        return

    state = A.load_state()
    if a.cmd == "start":
        acts = A.poll(state)
        queued = [x for x in acts if x["type"] in ("button", "text")]
        ideas = [x for x in acts if x["type"] == "idea"]
        if ideas:
            bank = A.data_json("ideas.json", {"ideas": []})
            for x in ideas:
                bank["ideas"].append({"id": f"l{x['uid']}", "text": x["text"], "photo": x.get("photo"), "pillar": "",
                                      "source": "lead", "from": x["from"], "status": "open", "ts": time.time()})
            A.save_json("ideas.json", bank)
            A.say(state, f"💡 G‘oya qabul qilindi ({len(ideas)} ta). Keyingi postlarda foydalanaman.")
        if queued:
            state["pending"] += queued
            A.save_state(state)
        print(json.dumps({"leads": state["leads"], "lead_names": state["lead_names"],
                          "registered": [x for x in acts if x["type"] == "registered"],
                          "ideas_stored": len(ideas), "queued_for_hourly": len(queued)}, ensure_ascii=False, indent=1))
    elif a.cmd == "actions":
        acts = A.take_actions(state)
        ideas = [x for x in acts if x["type"] == "idea"]
        if ideas:
            bank = A.data_json("ideas.json", {"ideas": []})
            for x in ideas:
                bank["ideas"].append({"id": f"l{x['uid']}", "text": x["text"], "photo": x.get("photo"), "pillar": "",
                                      "source": "lead", "from": x["from"], "status": "open", "ts": time.time()})
            A.save_json("ideas.json", bank)
        print(json.dumps(acts, ensure_ascii=False, indent=1))
    elif a.cmd == "knowledge":
        A.load_knowledge(state)
    elif a.cmd == "today":
        cmd_today(state)
    elif a.cmd == "week":
        plan = A.data_json("content_plan.json")
        start = A.tashkent_now()
        for i in range(7):
            d = start + datetime.timedelta(days=i)
            day = DAYS[d.weekday()]
            slot = plan["week"][day]
            print(f"{d:%a %d %b}: {slot['series']} ({slot['pillar']}, {slot['format']}) - {slot['brief']}")
            if state.get("week_plan", {}).get(d.strftime("%Y%m%d")):
                print("   planned:", state["week_plan"][d.strftime("%Y%m%d")])
    elif a.cmd == "show":
        post = state["posts"].get(a.post_id)
        if not post:
            sys.exit(f"no post {a.post_id}; known: {', '.join(sorted(state['posts']))}")
        print(json.dumps(post, ensure_ascii=False, indent=1))
    elif a.cmd == "send":
        post = _post(a.file)
        probs = A.lint(post)
        if probs:
            sys.exit("lint failed, not sending:\n" + "\n".join(probs))
        for w in A.lint_warnings(post):
            print("warn:", w)
        pid = a.id or A.new_post_id(state)
        post.pop("file_ids", None)
        post["status"] = "sent"
        A.send_package(state, pid, post, note=a.note, use_minimax=not a.no_minimax)
        if a.history:
            state["history"].append({"date": pid[:8], "post_id": pid, "topic": post.get("topic", ""),
                                     "source": (post.get("sources") or [""])[0], "kind": post.get("kind"),
                                     "pillar": post.get("pillar"), "series": post.get("series")})
            A.save_state(state)
        if post.get("story_id"):
            bank = A.data_json("story_bank.json", {})
            if post["story_id"] in bank.get("stories", {}):
                bank["stories"][post["story_id"]]["status"] = "posted"
                A.save_json("story_bank.json", bank)
        if post.get("idea_id"):
            ib = A.data_json("ideas.json", {"ideas": []})
            for i in ib["ideas"]:
                if i["id"] == post["idea_id"]:
                    i["status"] = "used"
            A.save_json("ideas.json", ib)
        print("POST_ID", pid)
    elif a.cmd == "ack":
        A.ack(a.cb_id, a.text)
    elif a.cmd == "status":
        if a.status == "rejected":
            A.record_rejection(state, a.post_id, a.reason)
        A.set_status(state, a.post_id, a.status)
        A.save_state(state)
    elif a.cmd == "say":
        markup = A.publish_markup(a.publish_buttons) if a.publish_buttons else None
        print(A.say(state, a.html, [a.chat] if a.chat else None, markup=markup))
    elif a.cmd == "publish":
        res = A.publish_telegram(state, a.post_id)
        A.save_state(state)
        print(json.dumps(res, ensure_ascii=False))
    elif a.cmd == "settings":
        if a.key:
            v = {"on": True, "off": False, "true": True, "false": False}.get(str(a.value).lower(), a.value)
            state["settings"][a.key] = v
            A.save_state(state)
        print(json.dumps(state.get("settings", {}), ensure_ascii=False))
    elif a.cmd == "learn":
        p = A.add_learning(state, a.text, a.context, a.explicit)
        A.save_state(state)
        print(json.dumps(p, ensure_ascii=False))
    elif a.cmd == "idea":
        ib = A.data_json("ideas.json", {"ideas": []})
        if a.op == "add":
            ib["ideas"].append({"id": f"i{int(time.time())}", "text": a.text, "photo": a.photo, "pillar": a.pillar,
                                "source": a.source, "status": "open", "ts": time.time()})
        elif a.op == "done":
            for i in ib["ideas"]:
                if i["id"] == a.text:
                    i["status"] = "used"
        if a.op != "list":
            A.save_json("ideas.json", ib)
        print(json.dumps([i for i in ib["ideas"] if i.get("status") == "open"], ensure_ascii=False, indent=1))
    elif a.cmd == "metrics":
        arch = V.refresh_archive(A.data_json("archive.json", {}))
        eng, med = V.engagement(arch)
        linked = 0
        for pid, post in state["posts"].items():
            tgc = (post.get("captions") or {}).get("telegram", "")
            pub = (post.get("published") or {}).get("telegram") or {}
            match = str(pub.get("message_id")) if pub.get("message_id") else None
            if not match and tgc:
                tk = S.tokens(tgc)
                best = max(arch["posts"].values(), key=lambda x: S._sim(tk, S.tokens(x["text"])), default=None)
                if best and S._sim(tk, S.tokens(best["text"])) >= 0.55:
                    match = str(best["id"])
            if match and match in arch["posts"]:
                arch["posts"][match]["agent_post"] = pid
                post.setdefault("published", {}).setdefault("telegram", {})["message_id"] = int(match)
                post["metrics"] = {"views": arch["posts"][match]["views"], "eng": eng.get(match), "at": time.time()}
                linked += 1
        A.save_json("archive.json", arch)
        rows = []
        for x in arch["posts"].values():
            if str(x["id"]) in eng:
                ap = state["posts"].get(x.get("agent_post") or "", {})
                rows.append({"kind": ap.get("kind") or V.kind(x["text"]), "series": ap.get("series") or "-",
                             "by": "agent" if x.get("agent_post") else "team", "photo": x.get("photo"),
                             "eng": eng[str(x["id"])]})
        summ = {}
        for key in ("kind", "series", "by", "photo"):
            g = {}
            for r in rows:
                g.setdefault(str(r[key]), []).append(r["eng"])
            summ[key] = {k: {"posts": len(v), "median_eng": round(sorted(v)[len(v) // 2], 2)} for k, v in g.items()}
        A.save_json("performance.json", {"channel_median_views": med, "summary": summ, "updated": time.time()})
        A.save_state(state)
        print(f"archive {len(arch['posts'])} posts, channel median {med} views, agent posts linked: {linked}")
        print(json.dumps(summ, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
