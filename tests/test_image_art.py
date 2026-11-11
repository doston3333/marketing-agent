# Offline tests for the picture brief and layout (art_prompt, art_warnings, compose). Run: python3 -m unittest discover -s tests
import os, sys, unittest

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

    def test_card_without_art_still_renders(self):
        img = A.compose("instagram", {"headline": "Bir [[qisqa]] ibora"}, 7, None)
        self.assertEqual(img.size, A.SIZES["instagram"])


if __name__ == "__main__":
    unittest.main()
