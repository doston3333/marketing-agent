# Offline tests for what the agent treats as a good story (lib/sources.py scoring and the daily menu) and for
# the checks on a news post (agent_lib lint). Run: python3 -m unittest discover -s tests
import copy, json, os, sys, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import sources as S  # noqa: E402
import agent_lib as A  # noqa: E402

NOW = 1_800_000_000


def item(source, title, age_h=5, **kw):
    return {"source": source, "id": f"{source}/{title}", "url": "https://example.com/x", "title": title,
            "snippet": "", "ts": NOW - age_h * 3600, **kw}


def score(*items):
    c = {"items": list(items), "_tok": S.tokens(" ".join(i["title"] for i in items))}
    return S.score_cluster(c, [], NOW)


class Scoring(unittest.TestCase):
    def test_a_story_the_world_covers_beats_a_local_opportunity(self):
        world, sig = score(item("rss:techcrunch.com", "Apple signs $1 billion deal with Google to power Siri with Gemini AI"),
                           item("rss:theverge.com", "Apple will pay Google $1 billion a year for Gemini in Siri"),
                           item("rss:feeds.bloomberg.com", "Apple, Google strike AI deal for Siri"),
                           item("rss:techmeme.com", "Apple confirms a multi-year Gemini deal with Google for Siri AI"))
        local, lsig = score(item("tg:itpark_uz", "IT Park hackathon uchun ariza topshirish muddati 15-noyabrgacha, Toshkent",
                                 views=9000, eng=2.5))
        self.assertGreater(world, local + 0.2)
        self.assertEqual(sig["type"], "bigtech")
        self.assertEqual(sig["buzz"], 5)  # four outlets, and Techmeme counts twice
        self.assertEqual(lsig["type"], "opportunity")

    def test_a_story_that_is_not_about_ai_stays_in_the_background(self):
        ai, _ = score(item("rss:techcrunch.com", "AI startup Listen Labs raises $69 million at a $500 million valuation"))
        other, _ = score(item("hn", "Show HN: I rebuilt my bicycle wheel truing stand", points=900))
        self.assertGreater(ai, other)

    def test_a_big_round_outranks_a_tiny_one(self):
        big, sig = score(item("rss:siliconangle.com", "Mercor raises $750M at $20B valuation for AI agent evaluation"))
        small, _ = score(item("rss:siliconangle.com", "AI startup Trina raises $112,000 seed for team AI tools"))
        self.assertGreater(big, small)
        self.assertEqual(sig["type"], "money")

    def test_private_life_scandals_are_pushed_down(self):
        news, _ = score(item("rss:theverge.com", "Nvidia unveils a new AI chip for humanoid robots"))
        gossip, _ = score(item("rss:theverge.com", "Nvidia chief's affair scandal: what we know so far about the AI boss"))
        self.assertGreater(news, gossip * 2)

    def test_real_life_type(self):
        _, sig = score(item("rss:scmp.com", "Schools across the country use AI to mark homework for teachers"))
        self.assertEqual(sig["type"], "reallife")

    def test_amount(self):
        self.assertEqual(S.amount("raises $750M at $20B valuation"), 20e9)
        self.assertEqual(S.amount("valuation of $2.2trn or more"), 2.2e12)
        self.assertEqual(S.amount("a $112,000 seed"), 112000)
        self.assertEqual(S.amount("no money"), 0)
        self.assertEqual([S.money_weight(t) for t in ("$1.4 trillion IPO", "$625M deal", "$19.4m round", "£5k grant",
                                                      "raises funds", "a quiet day")], [1.0, 0.7, 0.4, 0.1, 0.3, 0.0])


def story(sid, title, sc, typ, tags=("ai",)):
    return {"id": sid, "status": "candidate", "score": sc, "tokens": [],
            "signals": {"type": typ, "relevance_tags": list(tags), "buzz": 1, "hn_points": 0, "age_h": 5},
            "items": [item("rss:techcrunch.com", title)]}


class Digest(unittest.TestCase):
    def bank(self):
        ss = [story(f"a{i}", f"Anthropic IPO headline number {i}", 0.9 - i * 0.01, "bigtech") for i in range(9)]
        ss += [story("o1", "OpenAI buys a travel startup", 0.5, "bigtech"),
               story("m1", "Mercor raises $750M at $20B valuation", 0.4, "money"),
               story("r1", "Schools use AI to mark homework", 0.3, "reallife"),
               story("x1", "Tashkent bakery opens a new shop", 0.95, "other", tags=()),
               story("l1", "IT Park opens AI lab in Tashkent", 0.6, "local", tags=("ai", "uz", "local"))]
        return {"stories": {s["id"]: s for s in ss}}

    def test_one_loud_name_cannot_fill_the_menu(self):
        d = S.digest(self.bank())
        big = [s["id"] for s in d["bigtech"]]
        self.assertEqual(big, ["a0", "a1", "o1"])
        self.assertEqual(d["bigtech"][0]["also"], 8)  # eight more Anthropic stories: a loud day for that name
        self.assertEqual([s["id"] for s in d["money"]], ["m1"])
        self.assertEqual([s["id"] for s in d["reallife"]], ["r1"])

    def test_a_bank_saved_under_the_old_rules_is_scored_again(self):
        b = {"stories": {"s1": {"id": "s1", "status": "candidate", "score": 0.9, "signals": {"relevance_tags": ["ai"]},
                                "tokens": ["openai", "acquire"],
                                "items": [item("rss:techcrunch.com", "OpenAI acquires an AI startup for $1 billion")]}}}
        self.assertEqual(S.digest(b)["bigtech"], [])  # no type yet
        S.rescore(b, [], NOW)
        self.assertEqual([s["id"] for s in S.digest(b)["bigtech"]], ["s1"])

    def test_only_ai_stories_and_no_local_ones(self):
        ids = {s["id"] for ss in S.digest(self.bank()).values() for s in ss}
        self.assertNotIn("x1", ids)
        self.assertNotIn("l1", ids)
        self.assertEqual([s["id"] for s in S.top(self.bank(), 4, types=("local",), ai=True)], ["l1"])


BODY = ("Startap yaqinda $69 million investitsiya jalb qildi va umumiy bahosi $500 milliondan oshdi. "
        "Investorlar orasida Sequoia Capital va Ribbit Capital bor. ") * 6
SUBSCRIBE = "Sun’iy intellekt bo‘yicha so‘nggi yangiliklarni bilish uchun sahifamizga obuna bo‘ling ✔️"


def post(tg=None, ig=None, li=None):
    tg = tg or f"🚀 **Listen Labs $500 millionga baholandi**\n\n{BODY}\n\n{SUBSCRIBE}"
    ig = ig or (f"🚀 Listen Labs $500 millionga baholandi\n\n{BODY}\n\n{SUBSCRIBE}\n.\n"
                "#aistation #aistationuz #ai #startap #suniyintellekt")
    li = li or ("Listen Labs raised $69 million at a $500 million valuation. " + "It runs customer interviews with AI. " * 22
                + "\n#AIStation #AI #Startups")
    sp = {"art": "minimax", "image_prompt": "x", "headline": "Listen Labs: $500 mln baho"}
    return {"kind": "news", "pillar": "ventures", "series": "AI startap", "format": "single", "sources": ["https://e.com"],
            "captions": {"telegram": tg, "instagram": ig, "linkedin": li},
            "spec": {"instagram": dict(sp), "telegram": dict(sp), "linkedin": dict(sp, headline="Listen Labs hits $500M")}}


class NewsPostChecks(unittest.TestCase):
    def test_a_post_built_like_the_gold_posts_passes(self):
        p = post()
        self.assertEqual(A.lint(p), [])
        self.assertEqual(A.news_warnings(p), [])

    def test_no_fixed_tagline_is_added_any_more(self):
        self.assertNotIn(A.TG_END, A.tg_caption("Matn"))
        self.assertTrue(A.tg_caption("Matn").startswith("Matn\n\n🌐"))

    def test_a_long_news_post_is_allowed_and_a_stub_is_not(self):
        long_tg = f"🚀 **Sarlavha $1 mlrd**\n\n{'so‘z ' * 170}\n\n{SUBSCRIBE}"
        self.assertEqual(A.lint(post(tg=long_tg)), [])
        self.assertTrue(any("telegram: 2" in x for x in A.lint(post(tg=f"**S**\n\n{'so‘z ' * 220}"))))
        self.assertTrue(any(x.startswith("telegram:") for x in A.lint(post(tg="🚀 **Qisqa** post $5"))))

    def test_warnings_for_a_thin_post_without_headline_number_or_closing(self):
        w = A.news_warnings(post(tg="Listen Labs haqida qisqa xabar. " * 14))
        self.assertTrue(any("100-190" in x for x in w))
        self.assertTrue(any("line 1 is the headline" in x for x in w))
        self.assertTrue(any("no number" in x for x in w))
        self.assertTrue(any("telegram: no closing" in x for x in w))

    def test_two_closings_are_flagged(self):
        both = f"🚀 **S $1**\n\n{BODY}\n\nFikringizni izohlarda qoldiring 👇\n\n{SUBSCRIBE}"
        self.assertTrue(any("several closings" in x for x in A.news_warnings(post(tg=both))))
        self.assertEqual(A.closings("✔️ AI Station Studioda biz startuperlarga yordam beramiz."), ["studio"])

    def test_world_news_about_a_global_brand_needs_no_signoff(self):
        tg = f"🚀 **NVIDIA yangi chip chiqardi: $1 mlrd**\n\n{BODY}\n\n{SUBSCRIBE}"
        self.assertEqual(A.lint(post(tg=tg)), [])
        ours = tg.replace("yangi chip chiqardi", "bilan hamkorlik: AIS Ventures alliance a’zosi")
        self.assertTrue(any("signoff" in x and "NVIDIA" in x for x in A.lint(post(tg=ours))))

    def test_signoff_names_match_whole_words_only(self):
        tg = f"🚀 **AI hubs: $1 mlrd**\n\n{BODY} Startup hubs va clubs ko‘paymoqda.\n\n{SUBSCRIBE}"
        self.assertEqual(A.lint(post(tg=tg)), [])
        self.assertTrue(any("IT Park" in x for x in A.lint(post(tg=tg.replace("Startup hubs", "IT Park")))))


class Plan(unittest.TestCase):
    def test_every_day_is_a_news_day_with_a_known_type(self):
        plan = json.load(open(os.path.join(ROOT, "data", "content_plan.json"), encoding="utf-8"))
        for day, slot in plan["week"].items():
            if day != "Sun":
                self.assertIn(slot["series"], plan["types"], day)
            self.assertEqual(slot["format"], "single")
            self.assertIn(slot["pillar"], A.PILLARS)
            self.assertIn(slot["kind"], A.KINDS)


if __name__ == "__main__":
    unittest.main()
