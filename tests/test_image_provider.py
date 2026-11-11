# Offline tests for the concept-art providers (MiniMax / OpenRouter). Run: python3 -m unittest discover -s tests
import base64, io, os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import agent_lib as A  # noqa: E402
from PIL import Image  # noqa: E402


def _png_b64(w=40, h=50):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


FLUX_PARAMS = {
    "aspect_ratio": {"type": "enum", "values": ["16:9", "3:2", "5:4", "1:1", "4:5", "3:4", "2:3"]},
    "resolution": {"type": "enum", "values": ["768", "1K", "1.5K", "2K"]},
    "output_format": {"type": "enum", "values": ["jpeg", "png"]},
}


class Resp:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload
        self.content = b"x"
        self.text = str(payload)

    def json(self):
        return self._p


class Base(unittest.TestCase):
    def setUp(self):
        A._SECRETS.clear()
        A._OR_PARAMS.clear()
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for k in ("MINIMAX_API_KEY", "OPENROUTER_API_KEY", "IMAGE_PROVIDER"):
            os.environ.pop(k, None)
        self.addCleanup(self.env.stop)
        self.addCleanup(A._SECRETS.clear)


class ProviderChoice(Base):
    def test_none_without_keys(self):
        self.assertIsNone(A.image_provider())

    def test_openrouter_when_only_its_key(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
        self.assertEqual(A.image_provider(), "openrouter")

    def test_minimax_wins_when_both_keys(self):
        os.environ.update(MINIMAX_API_KEY="mm", OPENROUTER_API_KEY="sk-or-test")
        self.assertEqual(A.image_provider(), "minimax")

    def test_env_override(self):
        os.environ.update(MINIMAX_API_KEY="mm", OPENROUTER_API_KEY="sk-or-test", IMAGE_PROVIDER="openrouter")
        self.assertEqual(A.image_provider(), "openrouter")

    def test_minimax_dispatches_to_openrouter(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
        with mock.patch.object(A, "openrouter_image", return_value="IMG") as o:
            self.assertEqual(A.minimax("a paper boat", "telegram"), "IMG")
        o.assert_called_once_with("a paper boat", "telegram")

    def test_no_key_raises(self):
        with self.assertRaises(RuntimeError):
            A.minimax("a paper boat", "telegram")


class OpenRouterImage(Base):
    def setUp(self):
        super().setUp()
        os.environ["OPENROUTER_API_KEY"] = "sk-or-test"

    def test_request_and_decode(self):
        with mock.patch.object(A, "_or_params", return_value=FLUX_PARAMS), \
                mock.patch.object(A.requests, "post", return_value=Resp(200, {"data": [{"b64_json": _png_b64()}]})) as post:
            img = A.openrouter_image("a paper boat", "instagram")
        self.assertEqual(img.size, (40, 50))
        self.assertEqual(img.mode, "RGB")
        url = post.call_args.args[0]
        body = post.call_args.kwargs["json"]
        self.assertEqual(url, A.OR_URL)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer sk-or-test")
        self.assertEqual(body["model"], A.OR_MODEL)
        self.assertEqual(body["aspect_ratio"], "5:4")  # the shape of the picture window, not of the whole canvas
        self.assertEqual(body["resolution"], "2K")
        self.assertEqual(body["prompt"], A.art_prompt("a paper boat", "instagram"))

    def test_size_falls_back_to_what_the_model_offers(self):
        params = dict(FLUX_PARAMS, resolution={"type": "enum", "values": ["768", "1K", "1.5K"]})
        with mock.patch.object(A, "_or_params", return_value=params), \
                mock.patch.object(A.requests, "post", return_value=Resp(200, {"data": [{"b64_json": _png_b64()}]})) as post:
            A.openrouter_image("x", "linkedin")
        self.assertEqual(post.call_args.kwargs["json"]["resolution"], "1.5K")
        self.assertEqual(post.call_args.kwargs["json"]["aspect_ratio"], "3:2")

    def test_aspect_fallback_for_limited_model(self):
        params = {"aspect_ratio": {"type": "enum", "values": ["4:3", "16:9", "4:5", "auto"]}}
        with mock.patch.object(A, "_or_params", return_value=params), \
                mock.patch.object(A.requests, "post", return_value=Resp(200, {"data": [{"b64_json": _png_b64()}]})) as post:
            A.openrouter_image("x", "instagram")
            ig = post.call_args.kwargs["json"]
            A.openrouter_image("x", "telegram")
            tg = post.call_args.kwargs["json"]
            A.openrouter_image("x", "linkedin")
            li = post.call_args.kwargs["json"]
        self.assertEqual(ig["aspect_ratio"], "4:3")
        self.assertEqual(tg["aspect_ratio"], "4:5")
        self.assertEqual(li["aspect_ratio"], "16:9")
        self.assertNotIn("resolution", ig)

    def test_unknown_params_send_preferred_aspect_only(self):
        with mock.patch.object(A, "_or_params", return_value={}), \
                mock.patch.object(A.requests, "post", return_value=Resp(200, {"data": [{"b64_json": _png_b64()}]})) as post:
            A.openrouter_image("x", "telegram")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["aspect_ratio"], "1:1")
        self.assertNotIn("resolution", body)

    def test_no_credit_fails_fast(self):
        err = Resp(402, {"error": {"message": "Insufficient credits"}})
        with mock.patch.object(A, "_or_params", return_value=FLUX_PARAMS), \
                mock.patch.object(A.requests, "post", return_value=err) as post, \
                mock.patch.object(A.time, "sleep"):
            with self.assertRaises(RuntimeError) as cm:
                A.openrouter_image("x", "linkedin")
        self.assertEqual(post.call_count, 1)
        self.assertIn("402", str(cm.exception))
        self.assertIn("Insufficient credits", str(cm.exception))

    def test_server_error_retries_once(self):
        err = Resp(502, {"error": {"message": "upstream"}})
        with mock.patch.object(A, "_or_params", return_value=FLUX_PARAMS), \
                mock.patch.object(A.requests, "post", return_value=err) as post, \
                mock.patch.object(A.time, "sleep"):
            with self.assertRaises(RuntimeError):
                A.openrouter_image("x", "linkedin")
        self.assertEqual(post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
