"""Plumbing tests that need no model: the answer key plays the model.

If the key's own lines go through our checks and come out unchanged, then the checks
(grounding, quoted-history drop, source repair, duplicates, revises) do not eat true lines.
Run:  python -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from rfq import __main__ as app, llm  # noqa: E402
from rfq.readers import load_email, render  # noqa: E402

EMAILS = ROOT / "data" / "dev" / "emails"
KEY = json.loads((ROOT / "data" / "dev" / "answer_key.json").read_text(encoding="utf-8"))["emails"]


class Oracle(unittest.TestCase):
    def run_with(self, fake):
        real, app.llm.ask = llm.ask, fake
        try:
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "parsed.json"
                app.run(EMAILS, out)
                return json.loads(out.read_text(encoding="utf-8"))
        finally:
            app.llm.ask = real

    def test_key_survives_the_checks(self):
        by_key = {llm.cache_key(p.read_bytes(), b""): p.stem for p in EMAILS.glob("*.eml")}
        by_key[llm.cache_key((EMAILS / "E004.eml").read_bytes(), (EMAILS / "E003.eml").read_bytes())] = "E004"

        def fake(key, content):
            eid = by_key[key]
            g = KEY["E006" if eid == "E008" else eid]     # the model would read the resend in full
            ans = {"classification": g["classification"], "needs_review": g["needs_review"], "review_reason": "x" if g["needs_review"] else None,
                   "line_items": [{k: l[k] for k in ("raw", "qty", "uom", "source")} | {"description": l["raw"]} for l in g["line_items"]]}
            return ans, {"cached": True, "calls": 0, "usd": 0, "model": "oracle", "usage": {"prompt_tokens": 0, "completion_tokens": 0}}

        got = self.run_with(fake)
        for eid, g in KEY.items():
            p = got[eid]
            self.assertEqual(p["classification"], g["classification"], eid)
            self.assertEqual((p["revises"], p["duplicate_of"], p["needs_review"]), (g["revises"], g["duplicate_of"], g["needs_review"]), eid)
            self.assertEqual([(l["raw"], l["qty"], l["uom"] or "EA", l["source"]) for l in p["line_items"]],
                             [(l["raw"], l["qty"], l["uom"] or "EA", l["source"]) for l in g["line_items"]], eid)

    def test_model_down_flags_everything_and_finishes(self):
        def down(key, content):
            raise llm.ModelUnavailable("provider down")
        got = self.run_with(down)
        self.assertEqual(len(got), 24)
        self.assertEqual(got["E013"]["classification"], "auto_reply")          # decided by headers, no model
        # E008 is the same sender re-sending the same file as E006: still a duplicate, and E006 is flagged
        self.assertEqual(got["E008"]["duplicate_of"], "E006")
        self.assertTrue(all(r["needs_review"] for e, r in got.items() if e not in ("E013", "E008")))

    def test_hallucinated_and_quoted_lines_are_removed(self):
        e = load_email(EMAILS / "E001.eml")
        ans = {"classification": "rfq", "needs_review": False, "review_reason": None, "line_items": [
            {"raw": "12 jts - 2\" SCH 40 A53B ERW black pipe, PE", "description": "pipe", "qty": 12, "uom": "EA", "source": "body"},
            {"raw": "40 ea 8\" titanium butterfly valve", "description": "invented", "qty": 40, "uom": "EA", "source": "body"}]}
        rec = app.check(e, ans, None)
        self.assertEqual(len(rec["line_items"]), 1)
        self.assertTrue(rec["needs_review"])
        e = load_email(EMAILS / "E012.eml")                                    # status inquiry quoting an old RFQ
        self.assertEqual(app.check(e, {**ans, "classification": "status_inquiry"}, None)["line_items"], [])

    def test_broken_attachments_do_not_raise(self):
        for name, mime, data in [("a.pdf", "application/pdf", b"%PDF-1.4 garbage"), ("b.xlsx", "application/x", b"PK\x03\x04nope"),
                                 ("c.jpg", "image/jpeg", b"\xff\xd8junk"), ("d.docx", "application/x", b""), ("e.dwg", "application/acad", b"x")]:
            r = render({"name": name, "mime": mime, "data": data, "inline": False})
            self.assertTrue(r["error"], name)

    def test_encrypted_pdf_is_flagged_by_code(self):
        e = load_email(EMAILS / "E022.eml")
        rec = app.check(e, {"classification": "rfq", "needs_review": False, "review_reason": None, "line_items": []}, None)
        self.assertTrue(rec["needs_review"])
        self.assertIn("could not be read", rec["review_reason"])


if __name__ == "__main__":
    unittest.main()
