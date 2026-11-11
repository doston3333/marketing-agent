# Offline tests for the picture brief and layout (art_prompt, art_warnings, compose). Run: python3 -m unittest discover -s tests
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import agent_lib as A  # noqa: E402
from PIL import Image  # noqa: E402

GOOD = ("Documentary photograph of a teenage girl seen from behind at a workbench at night, assembling a small "
        "cube-shaped satellite frame with gold solar panels; her dark hair is tied back and a soldering iron is in her "
        "right hand. The bench is crowded with wires and small tools. Warm desk-lamp light on her hands, cool blue "
        "window light on her shoulders. Shot on a 50mm lens at f/2, shallow depth of field, fine film grain.")


def post(ig, tg=None, li=None, **kw):
    return {"spec": {"instagram": {"art": "minimax", "image_prompt": ig, **kw},
                     "telegram": {"art": "minimax", "image_prompt": tg or ig + " Close detail of her hands."},
                     "linkedin": {"art": "minimax", "image_prompt": li or ig + " Wider view of the whole room."}}}


class ArtPrompt(unittest.TestCase):
    def test_brief_comes_first_then_framing_and_no_writing(self):
        for p in ("instagram", "telegram", "linkedin"):
            full = A.art_prompt("  " + GOOD + " ", p)
            self.assertTrue(full.startswith(GOOD))
            self.assertIn(A.FRAME[p], full)
            self.assertIn("No text, letters, numbers, logos", full)

    def test_dark_edge_matches_where_the_headline_sits(self):
        self.assertIn("lowest fifth", A.FRAME["instagram"])
        self.assertIn("lowest fifth", A.FRAME["linkedin"])
        self.assertIn("left fifth", A.FRAME["telegram"])

    def test_code_no_longer_forces_one_style_on_every_picture(self):
        full = A.art_prompt(GOOD, "instagram").lower()
        for forced in ("3d render", "editorial illustration", "minimal", "film grain, soft realistic light"):
            self.assertNotIn(forced, full.replace(GOOD.lower(), ""))


class Poster(unittest.TestCase):
    """By default the image model sets the headline into the picture; the code only adds the logo."""

    def test_headline_lines_and_highlight(self):
        self.assertEqual(A.headline_lines("Huquq uchun kod yozing: [[60 mln]] so‘m", "instagram"),
                         (["Huquq uchun kod", "yozing: 60 mln", "so‘m"], "60 mln"))
        self.assertEqual(A.headline_lines("UniSat 4.0: apply by [[15 Nov]]", "linkedin"),
                         (["UniSat 4.0: apply", "by 15 Nov"], "15 Nov"))
        self.assertEqual(A.headline_lines("Qisqa ibora", "telegram"), (["Qisqa ibora"], ""))

    def test_never_more_than_three_lines(self):
        lines, _ = A.headline_lines("Juda uzun sarlavha bu yerda yetti so‘zdan iborat", "telegram")
        self.assertLessEqual(len(lines), 3)
        self.assertEqual(" ".join(lines), "Juda uzun sarlavha bu yerda yetti so‘zdan iborat")

    def test_apostrophe_is_normalised_before_it_reaches_the_model(self):
        self.assertEqual(A.headline_lines("60 mln so`m", "instagram")[0], ["60 mln so‘m"])

    def test_prompt_carries_the_exact_headline_and_the_highlight(self):
        full = A.art_prompt(GOOD, "instagram", "Qizlar uchun bepul [[kosmik]] dastur")
        self.assertTrue(full.startswith(GOOD))
        self.assertIn('on 3 lines: "Qizlar uchun" / "bepul kosmik" / "dastur".', full)
        self.assertIn('The words "kosmik" are bright sky blue (#4FBFFD)', full)
        self.assertIn("Spell the headline exactly as written", full)
        self.assertIn("The top left corner stays plain and dark", full)   # the logo goes there
        self.assertNotIn("[[", full)
        self.assertNotIn(A.NO_TEXT.strip(), full)

    def test_telegram_headline_goes_on_the_left(self):
        self.assertIn("left half", A.art_prompt(GOOD, "telegram", "Qisqa ibora"))
        self.assertIn("lower third", A.art_prompt(GOOD, "linkedin", "Short phrase"))
        self.assertIn('on 1 line: "Qisqa ibora". Every word is pure white.', A.art_prompt(GOOD, "telegram", "Qisqa ibora"))

    def test_when_the_model_sets_the_headline(self):
        sp = {"art": "minimax", "image_prompt": GOOD, "headline": "Qisqa ibora"}
        self.assertTrue(A.text_in_art(sp))
        self.assertTrue(A.text_in_art({"image_prompt": GOOD, "headline": "Qisqa ibora"}))   # art defaults to minimax
        self.assertFalse(A.text_in_art({**sp, "text": "code"}))                              # the fallback
        self.assertFalse(A.text_in_art({**sp, "headline": ""}))
        self.assertFalse(A.text_in_art({"art": "photo", "photo": "https://example.com/a.jpg", "headline": "Qisqa ibora"}))
        self.assertFalse(A.text_in_art({"art": "card", "headline": "Qisqa ibora"}))


class Background(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
        import visuals
        self.V = visuals
        self.img = Image.new("RGB", (80, 100), (255, 0, 0))

    def test_headline_is_sent_to_the_model_by_default(self):
        sp = {"art": "minimax", "image_prompt": GOOD, "headline": "Bir [[qisqa]] ibora"}
        with mock.patch.object(A, "minimax", return_value=self.img) as m:
            art, src, err, baked = self.V.background("instagram", sp, 7)
        m.assert_called_once_with(GOOD, "instagram", "Bir [[qisqa]] ibora")
        self.assertEqual((src, err, baked), ("minimax", None, True))

    def test_text_code_keeps_the_headline_out_of_the_picture(self):
        sp = {"art": "minimax", "image_prompt": GOOD, "headline": "Bir ibora", "text": "code"}
        with mock.patch.object(A, "minimax", return_value=self.img) as m:
            art, src, err, baked = self.V.background("telegram", sp, 7, candidate=1)
        self.assertIsNone(m.call_args.args[2])
        self.assertTrue(m.call_args.args[0].startswith(GOOD + " Another take"))
        self.assertFalse(baked)

    def test_pinned_picture_remembers_whether_it_carries_the_headline(self):
        import tempfile
        d = tempfile.mkdtemp()
        for name, want in (("instagram_0_t.png", True), ("instagram_0.png", False)):   # the second: art made before this change
            path = os.path.join(d, name)
            self.img.save(path)
            art, src, err, baked = self.V.background("instagram", {"art": "minimax", "image_prompt": GOOD,
                                                                   "headline": "Bir ibora", "art_path": path}, 7)
            self.assertEqual((src, baked), ("pinned", want), name)

    def test_failed_generation_falls_back_to_a_card_with_the_headline_drawn(self):
        sp = {"art": "minimax", "image_prompt": GOOD, "headline": "Bir ibora"}
        with mock.patch.object(A, "minimax", side_effect=RuntimeError("402 no credit")):
            art, src, err, baked = self.V.background("linkedin", sp, 7)
        self.assertEqual((art, src, baked), (None, "card", False))
        self.assertIn("402 no credit", err)


class ArtWarnings(unittest.TestCase):
    def test_good_briefs_pass(self):
        self.assertEqual(A.art_warnings(post(GOOD)), [])

    def test_thin_prompt(self):
        w = A.art_warnings(post("A girl building a satellite at night, cinematic.", GOOD, GOOD + " Wide."))
        self.assertEqual(len(w), 1)
        self.assertIn("spec.instagram.image_prompt: 8 words", w[0])

    def test_stock_symbol(self):
        w = A.art_warnings(post(GOOD + " A brass gavel rests on a circuit board beside two robots.",
                                GOOD + " Close detail of her hands.", GOOD + " Wider view of the whole room."))
        self.assertEqual(len(w), 1)
        self.assertIn("stock symbol (gavel, robot, circuit board)", w[0])

    def test_symbol_inside_another_word_is_fine(self):
        self.assertEqual(A.art_warnings(post(GOOD + " Students brainstorm around a table.")), [])

    def test_same_prompt_on_two_platforms(self):
        w = A.art_warnings(post(GOOD, GOOD))
        self.assertEqual(w, ["spec.telegram.image_prompt: same as instagram; write a different shot for each platform"])

    def test_photo_and_card_are_not_checked(self):
        p = post(GOOD)
        p["spec"]["telegram"] = {"art": "photo", "photo": "https://example.com/a.jpg", "image_prompt": "x"}
        p["spec"]["linkedin"] = {"art": "card"}
        self.assertEqual(A.art_warnings(p), [])

    def test_lint_does_not_treat_the_brief_as_copy(self):
        # "landscape" is a banned word in captions but a normal word in a brief for the image model.
        p = post(GOOD + " Behind her a wide mountain landscape at dusk.")
        p.update(kind="news", pillar="news", sources=["https://example.com"],
                 captions={"instagram": "Bugun yangilik.", "telegram": "Bugun yangilik.", "linkedin": "The AI landscape."})
        for k in p["spec"]:
            p["spec"][k]["headline"] = "Bir ibora"
        problems = A.lint(p)
        self.assertFalse([x for x in problems if "image_prompt" in x], problems)
        self.assertIn("caption.linkedin: cliché 'landscape'", problems)


class Compose(unittest.TestCase):
    """The picture has its own window; the headline area is the plain brand background."""

    def setUp(self):
        self.red = Image.new("RGB", (1000, 800), (255, 0, 0))

    def _is_bg(self, px):
        return all(abs(a - b) <= 6 for a, b in zip(px, A.BG))

    def _is_red(self, px):
        return px[0] > 200 and px[1] < 60 and px[2] < 60

    def test_instagram_and_linkedin_picture_on_top_headline_area_clean(self):
        for p in ("instagram", "linkedin"):
            W, H = A.SIZES[p]
            img = A.compose(p, {"headline": ""}, 7, self.red)
            self.assertEqual(img.size, (W, H))
            self.assertTrue(self._is_red(img.getpixel((W - 60, int(H * 0.35)))), p)   # picture clear in the upper part
            self.assertTrue(self._is_bg(img.getpixel((W // 2, int(H * 0.70)))), p)    # gone where the headline starts
            self.assertTrue(self._is_bg(img.getpixel((W // 2, H - 10))), p)

    def test_telegram_picture_on_the_right_headline_area_clean(self):
        W, H = A.SIZES["telegram"]
        img = A.compose("telegram", {"headline": ""}, 7, self.red)
        self.assertTrue(self._is_bg(img.getpixel((int(W * 0.2), H // 2))))
        self.assertTrue(self._is_bg(img.getpixel((int(W * 0.39), H - 20))))
        self.assertTrue(self._is_red(img.getpixel((int(W * 0.85), H // 2))))

    def test_picture_is_not_stretched(self):
        # A tall picture is cover-cropped into the window, never squeezed: the window is filled edge to edge.
        tall = Image.new("RGB", (400, 1600), (255, 0, 0))
        W, H = A.SIZES["linkedin"]
        img = A.compose("linkedin", {"headline": ""}, 7, tall)
        self.assertTrue(self._is_red(img.getpixel((5, int(H * 0.3)))))
        self.assertTrue(self._is_red(img.getpixel((W - 5, int(H * 0.3)))))

    def test_picture_with_the_headline_fills_the_canvas_and_gets_only_the_logo(self):
        for p in ("instagram", "telegram", "linkedin"):
            W, H = A.SIZES[p]
            img = A.compose(p, {"headline": "Bu ibora chizilmasligi kerak"}, 7, self.red, baked=True)
            self.assertEqual(img.size, (W, H))
            for xy in ((W // 2, H - 10), (int(W * 0.1), int(H * 0.6)), (int(W * 0.1), H - 40), (W - 10, 10), (W // 2, H // 2)):
                self.assertTrue(self._is_red(img.getpixel(xy)), (p, xy))   # no fade, no headline drawn by the code
            box = img.crop((0, 0, int(W * 0.3), int(H * 0.14)))
            self.assertTrue(any(min(px) > 200 for px in box.getdata()), p)  # the white logo is there

    def test_baked_without_a_picture_is_a_normal_card(self):
        img = A.compose("instagram", {"headline": "Bir ibora"}, 7, None, baked=True)
        self.assertEqual(img.size, A.SIZES["instagram"])

    def test_card_without_art_still_renders(self):
        img = A.compose("instagram", {"headline": "Bir [[qisqa]] ibora"}, 7, None)
        self.assertEqual(img.size, A.SIZES["instagram"])


if __name__ == "__main__":
    unittest.main()
