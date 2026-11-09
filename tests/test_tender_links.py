# Offline tests for reading tender lists from page links (tenders.from_links). Run: python3 -m unittest discover -s tests
import json, os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import tenders as T  # noqa: E402

SITE = {"id": "ecepp"}
CFG = {"pattern": r"ecepp\.ebrd\.com/delta/viewNotice\.html\?displayNoticeId=\d+", "id_from_url": r"displayNoticeId=(\d+)",
       "deadline_regex": r"UK Time\s+(\d{1,2}/\d{1,2}/20\d\d)"}


def link(nid, title, row):
    return {"href": f"https://ecepp.ebrd.com/delta/viewNotice.html?displayNoticeId={nid}", "text": title, "card": row}


class FromLinks(unittest.TestCase):
    def test_open_notice_gets_its_closing_date(self):
        row = ("Uzbekistan: Supply of SCADA\tInvitation For Tenders Single\tSupply of SCADA\t"
               "06/11/2026 08:13\nUK Time\t27/11/2026 16:30\nUK Time\tOpen")
        it, = T.from_links(SITE, CFG, {"links": [link(47495755, "Uzbekistan: Supply of SCADA", row)]})
        self.assertEqual(it["key"], "ecepp:47495755")
        self.assertEqual(it["deadline"], "2026-11-27")
        self.assertEqual(it["title"], "Uzbekistan: Supply of SCADA")

    def test_notice_without_closing_date_keeps_none_when_asked(self):
        row = "Uzbekistan: UTY Digitalization\tGeneral Procurement Notice\tUTY Digitalization\t30/04/2026 10:00\nUK Time\tN/A\tInformation Only"
        cfg = {**CFG, "infer_deadline": False}
        it, = T.from_links(SITE, cfg, {"links": [link(1, "Uzbekistan: UTY Digitalization", row)]})
        self.assertIsNone(it["deadline"])

    def test_default_still_infers_a_date(self):
        row = "Uzbekistan: UTY Digitalization\tGeneral Procurement Notice\tUTY Digitalization\t30/04/2026 10:00\nUK Time\tN/A\tInformation Only"
        it, = T.from_links(SITE, CFG, {"links": [link(1, "Uzbekistan: UTY Digitalization", row)]})
        self.assertEqual(it["deadline"], "2026-04-30")


class Registry(unittest.TestCase):
    def test_ecepp_entry(self):
        d = json.load(open(os.path.join(ROOT, "data", "tender_sites.json")))
        sites = {s["id"]: s for s in (d["sites"] if isinstance(d, dict) else d)}
        self.assertIn("ecepp", sites)
        pages = sites["ecepp"]["pages"]
        self.assertTrue(all(p.get("actions") for p in pages))
        self.assertEqual([p.get("infer_deadline", True) for p in pages], [True, False])


if __name__ == "__main__":
    unittest.main()
