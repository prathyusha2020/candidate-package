"""Edge cases that need no model: file formats, quoting styles, duplicate rules, cache, bad input.
Run:  python -m unittest discover tests
"""
import io
import json
import os
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from rfq import __main__ as app, llm  # noqa: E402
from rfq.readers import MAX_PDF_PAGES, html_to_text, load_email, render, split_quoted  # noqa: E402


def att(name, data, mime="application/octet-stream", inline=False):
    return render({"name": name, "mime": mime, "data": data, "inline": inline})


def eml(tmp, name="T1", frm="a@cust.example", subject="rfq", body="please quote", date="Mon, 02 Nov 2026 09:00:00 -0600", files=(), **headers):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"], m["Message-ID"] = frm, "rfq@ridgeline-supply.example", subject, date, f"<{name}@t>"
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    for fname, data, mime in files:
        m.add_attachment(data, maintype=mime.split("/")[0], subtype=mime.split("/")[1], filename=fname)
    p = Path(tmp) / f"{name}.eml"
    p.write_bytes(m.as_bytes())
    return p


def line(raw, qty=1, uom="EA", source="body"):
    return {"raw": raw, "description": raw, "qty": qty, "uom": uom, "source": source}


def answer(cls="rfq", lines=(), review=False):
    return {"classification": cls, "needs_review": review, "review_reason": "r" if review else None, "line_items": list(lines)}


class Readers(unittest.TestCase):
    def test_xlsx_hidden_sheet_struck_and_hidden_rows(self):
        import openpyxl
        from openpyxl.styles import Font
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["ALL PIPE SCH 40"]); ws.append([5, "EA", "keep"]); ws.append([6, "EA", "struck"]); ws.append([7, "EA", "hidden"])
        ws["A3"].font = Font(strike=True)
        ws.row_dimensions[4].hidden = True
        old = wb.create_sheet("Rev A")
        old.append([99, "EA", "old revision row"])
        old.sheet_state = "hidden"
        buf = io.BytesIO(); wb.save(buf)
        t = att("a.xlsx", buf.getvalue())["text"]
        self.assertIn("5 | EA | keep", t)
        self.assertIn("[STRUCK OUT] 6 | EA | struck", t)
        self.assertIn("[HIDDEN ROW] 7 | EA | hidden", t)
        self.assertNotIn("old revision row", t)
        self.assertIn("HIDDEN - omitted", t)

    def test_docx_heading_stays_with_its_table_and_strike_is_marked(self):
        import docx
        d = docx.Document()
        d.add_heading("Pipe - SCH 80", 2)
        t1 = d.add_table(1, 2); t1.rows[0].cells[0].text, t1.rows[0].cells[1].text = "10", "2 in"
        d.add_heading("Fittings - 3000#", 2)
        t2 = d.add_table(1, 2); t2.rows[0].cells[0].text, t2.rows[0].cells[1].text = "4", "tee"
        d.add_paragraph().add_run("gone").font.strike = True
        buf = io.BytesIO(); d.save(buf)
        t = att("a.docx", buf.getvalue())["text"]
        self.assertLess(t.index("SCH 80"), t.index("10 | 2 in"))
        self.assertLess(t.index("10 | 2 in"), t.index("3000#"))
        self.assertLess(t.index("3000#"), t.index("4 | tee"))
        self.assertIn("[STRUCK OUT] gone", t)

    def test_long_pdf_is_capped_and_noted_and_scan_becomes_pictures(self):
        import pypdfium2 as pdfium
        from PIL import Image
        pdf = pdfium.PdfDocument.new()
        for _ in range(MAX_PDF_PAGES + 5):
            pdf.new_page(200, 200)
        buf = io.BytesIO(); pdf.save(buf)
        r = att("big.pdf", buf.getvalue())
        self.assertIsNone(r["error"])
        self.assertIn("only the first", r["note"])               # -> needs_review, never an unbounded request
        self.assertTrue(0 < len(r["images"]) <= 8)               # no text layer: the model must look
        img = io.BytesIO(); Image.new("RGB", (300, 300), "white").save(img, "PDF")
        r = att("scan.pdf", img.getvalue())
        self.assertEqual((r["text"], len(r["images"])), ("", 1))

    def test_image_formats_rotation_and_tiny_decoration(self):
        from PIL import Image
        for fmt in ("PNG", "JPEG", "GIF", "BMP", "TIFF", "WEBP"):
            b = io.BytesIO(); Image.new("RGB", (120, 80), "white").save(b, fmt)
            self.assertEqual(len(att(f"x.{fmt.lower()}", b.getvalue(), "image/" + fmt.lower())["images"]), 1, fmt)
        b = io.BytesIO(); Image.new("RGB", (1, 1)).save(b, "GIF")
        r = att("pixel.gif", b.getvalue(), "image/gif", inline=True)
        self.assertTrue(r["skip"] and not r["error"])            # tracking pixel: ignored, not an error
        b = io.BytesIO(); Image.new("RGB", (5000, 3000), "white").save(b, "JPEG")
        from PIL import Image as I
        self.assertLessEqual(max(I.open(io.BytesIO(att("big.jpg", b.getvalue(), "image/jpeg")["images"][0])).size), 2000)

    def test_noise_attachments_are_ignored_not_flagged(self):
        for name, mime in [("smime.p7s", "application/pkcs7-signature"), ("invite.ics", "text/calendar"), ("card.vcf", "text/vcard")]:
            self.assertTrue(att(name, b"x", mime)["skip"], name)

    def test_unreadable_things_return_an_error_never_raise(self):
        cases = [("a.pdf", b"%PDF-1.4 garbage"), ("b.xlsx", b"PK\x03\x04nope"), ("c.jpg", b"\xff\xd8junk"), ("d.docx", b""),
                 ("e.dwg", b"x"), ("winmail.dat", b"\x78\x9f\x3e\x22"), ("f.heic", b"\x00\x00\x00\x18ftypheic"), ("g.zip", b"PK\x05\x06" + b"\0" * 18),
                 ("h.xls", b"not an xls"), ("i.msg", b"\xd0\xcf\x11\xe0")]
        for name, data in cases:
            self.assertTrue(att(name, data)["error"], name)

    def test_csv_tsv_text_and_odd_charsets(self):
        self.assertIn("5 | EA | 2\" tee", att("a.csv", b'\xef\xbb\xbfQty,UOM,Desc\r\n5,EA,"2"" tee"\r\n', "text/csv")["text"])
        self.assertIn("5 | EA | tee", att("a.tsv", b"5\tEA\ttee\n", "text/tab-separated-values")["text"])
        self.assertTrue(att("a.txt", "10 ea v\xe1lvula".encode("latin-1"), "text/plain")["text"])    # wrong charset: readable, no crash

    def test_html_table_and_strike(self):
        t = html_to_text("<table><tr><td>5</td><td>tee</td></tr><tr><td><del>6</del></td><td>cap</td></tr></table><style>x{}</style>")
        self.assertIn("5 | tee", t)
        self.assertIn("[STRUCK OUT] 6", t)
        self.assertNotIn("x{}", t)

    def test_quote_styles(self):
        new = "please add 2 more\n\n"
        for hist in ["On Mon, Nov 2, 2026 at 9:00 AM Bob <b@x.example> wrote:\n> 5 ea tee", "> 5 ea tee",
                     "-----Original Message-----\nFrom: Bob\nSent: Monday\n5 ea tee", "From: Bob <b@x.example>\nSent: Monday, November 2\nTo: us\n\n5 ea tee",
                     "________________________________\nFrom: Bob\n5 ea tee"]:
            fresh, quoted = split_quoted(new + hist)
            self.assertEqual(fresh, new, hist[:20])
            self.assertIn("5 ea tee", quoted)
        self.assertEqual(split_quoted("Once upon a time\n5 ea tee")[1], "")

    def test_email_headers(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(load_email(eml(d, "A", Auto_Submitted="auto-replied"))["machine"])
            self.assertTrue(load_email(eml(d, "B", frm="MAILER-DAEMON@mx.example"))["machine"])
            self.assertFalse(load_email(eml(d, "C", Precedence="bulk"))["machine"])        # newsletters are 'other', the model decides
            self.assertFalse(load_email(eml(d, "D", Auto_Submitted="no"))["machine"])
            e = load_email(eml(d, "E", frm="Rep <rep@RIDGELINE-supply.example>"))
            self.assertTrue(e["internal"] and e["forward"])
            self.assertTrue(load_email(eml(d, "F", subject="Fwd: list"))["forward"])
            self.assertEqual(load_email(eml(d, "G", date="not a date"))["date"], 0.0)


class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def email(self, **kw):
        return load_email(eml(self.tmp.name, **kw))

    def test_non_requests_never_carry_lines_or_flags(self):
        e = self.email(body="5 ea tee")
        for cls in ("vendor_quote", "status_inquiry", "auto_reply", "other"):
            r = app.check(e, answer(cls, [line("5 ea tee")], review=True), None)
            self.assertEqual((r["line_items"], r["needs_review"]), ([], False), cls)

    def test_quoted_history_is_not_a_new_request_but_a_forward_is(self):
        body = "Please also quote what is below again\n\nOn Mon, Nov 2, 2026 at 9:00 AM Bob <b@x.example> wrote:\n> 5 ea 2in tee\n"
        r = app.check(self.email(body=body), answer("rfq", [line("5 ea 2in tee")]), None)
        self.assertEqual(r["line_items"], [])
        self.assertTrue(r["needs_review"])                         # nothing left: a human looks, no silent loss
        r = app.check(self.email(body=body, subject="FW: list"), answer("rfq", [line("5 ea 2in tee")]), None)
        self.assertEqual(len(r["line_items"]), 1)
        r = app.check(self.email(body=body), answer("purchase_order", [line("5 ea 2in tee")]), None)
        self.assertEqual(len(r["line_items"]), 1)                  # 'go ahead with the quote' legitimately uses history

    def test_qty_uom_and_source_are_sanitised(self):
        e = self.email(body="5 ea tee\n6 ea cap\n7 ea plug\n8 ea union", files=[("list.csv", b"9,EA,nipple\n", "text/csv")])
        r = app.check(e, answer("rfq", [line("5 ea tee", qty=0), line("6 ea cap", qty=-2), line("7 ea plug", qty=7.0, uom="JTS"),
                                         line("8 ea union", qty=None, uom="EA"), line("9 | EA | nipple", qty=9, source="LIST.CSV"),
                                         {"raw": "", "description": "x", "qty": 1, "uom": "EA", "source": "body"}]), None)
        self.assertEqual([(l["qty"], l["uom"], l["source"]) for l in r["line_items"]],
                         [(None, None, "body"), (None, None, "body"), (7, None, "body"), (None, None, "body"), (9, "EA", "list.csv")])

    def test_request_with_nothing_readable_is_always_flagged(self):
        r = app.check(self.email(body="see attached"), answer("rfq"), None)
        self.assertTrue(r["needs_review"] and r["review_reason"])

    def test_lines_read_from_a_picture_are_not_grounding_checked(self):
        from PIL import Image
        b = io.BytesIO(); Image.new("RGB", (200, 200), "white").save(b, "PNG")
        e = self.email(body="see pic", files=[("IMG_1.png", b.getvalue(), "image/png")])
        r = app.check(e, answer("rfq", [line("8 - 2in galv elbow", source="IMG_1.png")]), None)
        self.assertEqual((len(r["line_items"]), r["needs_review"]), (1, False))


class Runs(unittest.TestCase):
    """Whole runs with a scripted model."""

    def run_inbox(self, build, script):
        with tempfile.TemporaryDirectory() as d:
            paths = build(d)
            by_key = {llm.cache_key(p.read_bytes(), b""): p.stem for p in paths}

            def fake(key, content):
                eid = by_key.get(key)
                if eid is None:                                   # a threaded reply: key includes its parent
                    eid = next(p.stem for p in paths for q in paths if llm.cache_key(p.read_bytes(), q.read_bytes()) == key)
                return script[eid], {"cached": True, "calls": 0, "usd": 0, "model": "x", "usage": {"prompt_tokens": 0, "completion_tokens": 0}}

            with mock.patch.object(app.llm, "ask", fake):
                out = Path(d) / "out" / "parsed.json"
                out.parent.mkdir()
                app.run(Path(d), out)
                return json.loads(out.read_text(encoding="utf-8"))

    def test_duplicates(self):
        items = "5 ea tee\n6 ea cap\n7 ea plug"
        lines = [line("5 ea tee", 5), line("6 ea cap", 6), line("7 ea plug", 7)]

        def build(d):
            return [eml(d, "A", body=items, date="Mon, 02 Nov 2026 09:00:00 -0600"),
                    eml(d, "B", body="resend\n" + items, date="Tue, 03 Nov 2026 09:00:00 -0600"),                    # later -> duplicate
                    eml(d, "C", body=items, date="Mon, 02 Nov 2026 08:00:00 -0600", frm="other@x.example"),         # earliest -> the original
                    eml(d, "D", body=items, date="Wed, 04 Nov 2026 09:00:00 -0600"),                                 # PO for the same items: NOT a duplicate
                    eml(d, "E", body=items, date="Wed, 30 Dec 2026 09:00:00 -0600"),                                 # weeks later: a new order
                    eml(d, "F", body=items + "\n8 ea union", date="Thu, 05 Nov 2026 09:00:00 -0600")]                # one more line: not the same request
        got = self.run_inbox(build, {"A": answer("rfq", lines), "B": answer("rfq_revision", lines), "C": answer("rfq", lines),
                                     "D": answer("purchase_order", lines), "E": answer("rfq", lines), "F": answer("rfq", lines + [line("8 ea union", 8)])})
        self.assertEqual({k: v["duplicate_of"] for k, v in got.items()}, {"A": "C", "B": "C", "C": None, "D": None, "E": None, "F": None})
        self.assertEqual((got["B"]["classification"], got["B"]["line_items"]), ("rfq", []))
        self.assertEqual(len(got["D"]["line_items"]), 3)

    def test_revision_links_to_its_original_only_inside_the_inbox(self):
        def build(d):
            return [eml(d, "A", body="5 ea tee\n6 ea cap"),
                    eml(d, "B", body="make the tees 9", date="Mon, 02 Nov 2026 15:00:00 -0600", In_Reply_To="<A@t>"),
                    eml(d, "C", body="5 ea plug, was 4", date="Mon, 02 Nov 2026 16:00:00 -0600", In_Reply_To="<not-in-inbox@t>")]
        got = self.run_inbox(build, {"A": answer("rfq", [line("5 ea tee", 5), line("6 ea cap", 6)]),
                                     "B": answer("rfq_revision", [line("5 ea tee", 9), line("6 ea cap", 6)]),
                                     "C": answer("rfq_revision", [line("5 ea plug", 5)])})
        self.assertEqual((got["B"]["revises"], got["C"]["revises"], got["B"]["duplicate_of"]), ("A", None, None))
        self.assertEqual(len(got["B"]["line_items"]), 2)                # unchanged line carried over from the parent is grounded

    def test_messy_directory(self):
        def build(d):
            (Path(d) / "notes.txt").write_text("not an email")
            (Path(d) / "sub").mkdir()
            (Path(d) / "EMPTY.eml").write_bytes(b"")
            (Path(d) / "BIN.EML").write_bytes(bytes(range(256)) * 20)
            return [eml(d, "A", body="5 ea tee"), eml(d, "B", body="5 ea tee", date="Tue, 03 Nov 2026 09:00:00 -0600", Message_ID2="x")]
        with tempfile.TemporaryDirectory() as d:
            build(d)
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}), mock.patch.object(llm, "CACHE", Path(d) / "no-cache"):
                out = Path(d) / "parsed.json"
                self.assertEqual(app.run(Path(d), out), 0)
                got = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(sorted(got), ["A", "B", "BIN", "EMPTY"])       # every .eml appears, whatever its case or content
        self.assertTrue(all(v["needs_review"] and v["review_reason"] for v in got.values()))

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "parsed.json"
            self.assertEqual(app.run(Path(d), out), 0)
            self.assertEqual(json.loads(out.read_text()), {})


class Cache(unittest.TestCase):
    def test_key_ignores_line_endings_and_changes_with_content(self):
        self.assertEqual(llm.cache_key(b"a\r\nb"), llm.cache_key(b"a\nb"))      # git autocrlf cannot break replay
        self.assertNotEqual(llm.cache_key(b"a"), llm.cache_key(b"b"))
        self.assertNotEqual(llm.cache_key(b"a", b""), llm.cache_key(b"a", b"parent"))

    def test_hit_miss_corrupt_and_failures_are_not_cached(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(llm, "CACHE", Path(d)):
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                with self.assertRaises(llm.ModelUnavailable):
                    llm.ask("k1", [])                                      # no key, no cache
                (Path(d) / "k2.json").write_text("{ not json")
                with self.assertRaises(llm.ModelUnavailable):
                    llm.ask("k2", [])                                      # corrupt cache file is a miss, not a crash
                (Path(d) / "k3.json").write_text(json.dumps({"answer": answer(), "meta": {"model": "m", "usd": 0.1, "usage": {}}}))
                ans, meta = llm.ask("k3", [])
                self.assertEqual((ans["classification"], meta["cached"], meta["calls"]), ("rfq", True, 0))
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), mock.patch.object(llm, "RETRIES", 2), \
                    mock.patch.object(llm.time, "sleep", lambda s: None), mock.patch.object(llm.urllib.request, "urlopen", side_effect=TimeoutError("slow")) as net:
                with self.assertRaises(llm.ModelUnavailable):
                    llm.ask("k4", [])
                self.assertEqual(net.call_count, 2)                        # retried, then gave up
                self.assertFalse((Path(d) / "k4.json").exists())           # a failure is never cached

    def test_cost_arithmetic(self):
        u = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000, "prompt_tokens_details": {"cached_tokens": 0}}
        self.assertAlmostEqual(llm.cost_usd(u, "gpt-5-mini"), 2.25)


if __name__ == "__main__":
    unittest.main()
