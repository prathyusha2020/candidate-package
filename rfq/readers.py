"""Turn an .eml and its attachments into things a model can read.

Code decides everything that is a fact about the file: what is hidden, struck out, quoted,
encrypted or broken. Each attachment becomes {name, inline, text, images, error}.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

MAX_PDF_PAGES = 30       # a 300-page PDF is read up to here, then flagged for a human
MAX_IMAGES = 8           # per attachment
PDF_LOOK_PAGES = 3       # text PDFs: also show the first pages as pictures
MAX_TEXT = 60_000        # characters per attachment
STRUCK = "[STRUCK OUT] "
QUOTE_MARK = "----- QUOTED EARLIER MESSAGES (history, already handled) -----"
OURS = "@ridgeline-supply.example"


class _HTML(HTMLParser):
    """HTML -> text: table cells joined with ' | ', struck-through text marked."""

    def __init__(self):
        super().__init__()
        self.out, self.strike, self.skip = [], 0, 0

    def handle_starttag(self, tag, attrs):
        style = (dict(attrs).get("style") or "").replace(" ", "")
        if tag in ("s", "strike", "del") or "line-through" in style:
            self.strike += 1
            self.out.append(STRUCK)
        elif tag in ("script", "style"):
            self.skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "blockquote"):
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")
        elif tag == "img":
            self.out.append(f"[image: {dict(attrs).get('src', '')}]")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("p", "div", "tr", "table"):
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _HTML()
    p.feed(html)
    text = "".join(p.out).replace("\xa0", " ")
    text = re.sub(r"\n[ \t]*\| ", "\n", text)            # leading cell separator
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


HISTORY = re.compile(r"(?mi)^(On .{5,200}wrote:\s*$|>|-{2,} ?Original Message ?-{2,}|_{20,}\s*$|From: .+\n(Sent|Date): )")
NOISE = (".p7s", ".p7m", ".ics", ".vcf", ".asc", ".sig")     # signatures, invites, contact cards: never the request


def split_quoted(body: str) -> tuple[str, str]:
    """(what the sender wrote now, quoted reply history) - Gmail, Apple Mail and Outlook styles."""
    m = HISTORY.search(body)
    return (body[: m.start()], body[m.start():]) if m else (body, "")


def _part_text(part) -> str:
    try:
        return part.get_content()
    except Exception:
        return (part.get_payload(decode=True) or b"").decode("utf-8", "replace")


def load_email(path: Path) -> dict:
    return parse_message(BytesParser(policy=policy.default).parsebytes(Path(path).read_bytes()), Path(path).stem)


def parse_message(msg, eid: str = "") -> dict:
    plain = html = None
    atts, stack = [], [msg]
    while stack:
        part = stack.pop(0)
        ct = part.get_content_type()
        if ct == "message/rfc822":
            inner = part.get_payload(0) if part.is_multipart() else None
            data = inner.as_bytes() if inner else (part.get_payload(decode=True) or b"")
            atts.append({"name": part.get_filename() or f"attached-{len(atts) + 1}.eml", "mime": ct, "data": data, "inline": False})
        elif part.is_multipart():
            stack[0:0] = list(part.iter_parts())
        elif part.get_content_disposition() in ("attachment", "inline") and (part.get_filename() or not ct.startswith("text/")):
            atts.append({"name": part.get_filename() or f"unnamed-{len(atts) + 1}", "mime": ct,
                         "data": part.get_payload(decode=True) or b"",
                         "inline": part.get_content_disposition() == "inline"})
        elif ct == "text/plain" and plain is None:
            plain = _part_text(part)
        elif ct == "text/html" and html is None:
            html = _part_text(part)
    # plain text loses strike-through; only then is the HTML the better reading
    body = html_to_text(html) if html and (plain is None or re.search(r"<(s|strike|del)\b|line-through", html, re.I)) else (plain or "")
    try:
        date = parsedate_to_datetime(msg.get("Date")).timestamp()
    except Exception:
        date = 0.0
    sender = parseaddr(str(msg.get("From") or ""))[1].lower()
    return {
        "id": eid,
        "from": str(msg.get("From") or ""), "to": str(msg.get("To") or ""),
        "subject": str(msg.get("Subject") or ""), "date_str": str(msg.get("Date") or ""), "date": date,
        "sender": sender, "internal": sender.endswith(OURS),
        "message_id": str(msg.get("Message-ID") or "").strip(),
        "refs": re.findall(r"<[^>]+>", f"{msg.get('In-Reply-To') or ''} {msg.get('References') or ''}"),
        "machine": str(msg.get("Auto-Submitted") or "no").lower() != "no"
                   or bool(re.match(r"(mailer-daemon|postmaster)@", sender)) or msg.get_content_type() == "multipart/report",
        "body": body.replace("\r\n", "\n").strip()[:MAX_TEXT],
        "forward": sender.endswith(OURS) or bool(re.match(r"\s*(fw|fwd)\s*:", str(msg.get("Subject") or ""), re.I)),
        "attachments": [r for r in (render(a) for a in atts) if not r["skip"]],
    }


# ---------------------------------------------------------------- attachments

def _xlsx(data: bytes) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    out = []
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            out.append(f"## Sheet '{ws.title}' is HIDDEN - omitted (old revision / not part of the request)")
            continue
        out.append(f"## Sheet '{ws.title}'")
        for row in ws.iter_rows():
            cells = [c for c in row if c.value is not None and str(c.value).strip()]
            if not cells:
                continue
            flag = ""
            if ws.row_dimensions[row[0].row].hidden:
                flag = "[HIDDEN ROW] "
            elif any(c.font and c.font.strike for c in cells):
                flag = STRUCK
            out.append(flag + " | ".join(str(c.value).strip() for c in cells))
    return "\n".join(out)


def _xls(data: bytes) -> str:
    import xlrd

    out = []
    for sh in xlrd.open_workbook(file_contents=data).sheets():
        if sh.visibility:
            out.append(f"## Sheet '{sh.name}' is HIDDEN - omitted")
            continue
        out.append(f"## Sheet '{sh.name}'")
        for r in range(sh.nrows):
            cells = [str(v).strip() for v in sh.row_values(r) if str(v).strip()]
            if cells:
                out.append(" | ".join(re.sub(r"\.0$", "", c) for c in cells))
    return "\n".join(out)


def _docx(data: bytes) -> tuple[str, list[bytes]]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = docx.Document(io.BytesIO(data))

    def para(p) -> str:
        text = "".join((STRUCK + r.text) if (r.font.strike or r.font.double_strike) and r.text.strip() else r.text for r in p.runs) or p.text
        return ("# " if p.style is not None and p.style.name.lower().startswith(("heading", "title")) and text.strip() else "") + text

    out = []
    for child in doc.element.body.iterchildren():      # document order: a heading belongs to the table under it
        if child.tag.endswith("}p"):
            out.append(para(Paragraph(child, doc)))
        elif child.tag.endswith("}tbl"):
            for row in Table(child, doc).rows:
                seen, cells = set(), []
                for c in row.cells:                    # merged cells repeat; keep one
                    if id(c._tc) not in seen:
                        seen.add(id(c._tc))
                        cells.append(" ".join(para(p) for p in c.paragraphs).strip())
                out.append(" | ".join(cells))
            out.append("")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        media = [z.read(n) for n in z.namelist() if n.startswith("word/media/")]
    return "\n".join(l for l in out if l is not None), media


def _pdf(data: bytes) -> tuple[str, list[bytes], str | None]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)                     # raises on password-protected / corrupt files
    n = len(pdf)
    texts = [pdf[i].get_textpage().get_text_bounded() for i in range(min(n, MAX_PDF_PAGES))]
    note = f"PDF has {n} pages; only the first {MAX_PDF_PAGES} were read" if n > MAX_PDF_PAGES else None
    has_text = sum(len(t.strip()) for t in texts) >= 40 * len(texts)   # a real text layer
    text = "\n".join(f"--- page {i + 1} ---\n{t}" for i, t in enumerate(texts)) if has_text else ""
    # A scan has to be looked at. A short text PDF is shown as well: crossed-out rows and
    # column layout are only visible on the page, not in the text layer.
    images = []
    for i in range(min(n, PDF_LOOK_PAGES if has_text else MAX_IMAGES)):
        buf = io.BytesIO()
        pdf[i].render(scale=2 if not has_text else 1.5).to_pil().convert("RGB").save(buf, "JPEG", quality=85)
        images.append(buf.getvalue())
    if not has_text and n > MAX_IMAGES:
        note = f"scanned PDF has {n} pages; only the first {MAX_IMAGES} were read"
    return text, images, note


def _image(data: bytes) -> list[bytes]:
    from PIL import Image, ImageOps, ImageSequence

    out = []
    for frame in list(ImageSequence.Iterator(Image.open(io.BytesIO(data))))[:MAX_IMAGES]:
        im = ImageOps.exif_transpose(frame).convert("RGB")
        if min(im.size) < 48:                           # tracking pixels, bullets, social icons
            continue
        im.thumbnail((2000, 2000))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        out.append(buf.getvalue())
    return out


def render(att: dict) -> dict:
    """Never raises: an unreadable attachment comes back with `error` set."""
    name, mime, data = att["name"], att["mime"], att["data"]
    ext = Path(name).suffix.lower()
    r = {"name": name, "inline": att["inline"], "text": "", "images": [], "error": None, "note": None,
         "raw_sha": hashlib.sha256(data).hexdigest(), "skip": ext in NOISE or mime in ("text/calendar", "application/pkcs7-signature")}
    if r["skip"]:
        return r
    try:
        if not data:
            raise ValueError("attachment is empty (0 bytes)")
        if ext in (".xlsx", ".xlsm") or "spreadsheetml" in mime:
            r["text"] = _xlsx(data)
        elif ext == ".xls" or mime == "application/vnd.ms-excel":
            r["text"] = _xls(data)
        elif ext == ".docx" or "wordprocessingml" in mime:
            r["text"], media = _docx(data)
            r["images"] = [j for m in media[:MAX_IMAGES] for j in _image(m)]
        elif ext == ".pdf" or mime == "application/pdf":
            r["text"], r["images"], r["note"] = _pdf(data)
        elif mime.startswith("image/") or ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp"):
            r["images"] = _image(data)
            r["skip"] = not r["images"]                # only tiny decoration: not content, not an error
        elif mime == "message/rfc822" or ext == ".eml":
            inner = parse_message(BytesParser(policy=policy.default).parsebytes(data))
            r["text"] = (f"From: {inner['from']}\nTo: {inner['to']}\nDate: {inner['date_str']}\nSubject: {inner['subject']}\n\n{inner['body']}"
                         + "".join(f"\n[its attachment {a['name']}]\n{a['text']}" for a in inner["attachments"]))
        elif ext in (".csv", ".tsv", ".txt") or mime.startswith("text/"):
            text = data.decode("utf-8-sig", "replace")
            if ext in (".csv", ".tsv") or mime == "text/csv":
                rows = csv.reader(io.StringIO(text), delimiter="\t" if ext == ".tsv" else ",")
                text = "\n".join(" | ".join(c.strip() for c in row) for row in rows if any(c.strip() for c in row))
            r["text"] = html_to_text(text) if ext in (".htm", ".html") or mime == "text/html" else text
        else:
            raise ValueError(f"unsupported attachment type {mime}")
        if not r["text"].strip() and not r["images"] and not r["skip"]:
            raise ValueError("attachment has no readable content")
        if len(r["text"]) > MAX_TEXT:
            r["text"], r["note"] = r["text"][:MAX_TEXT], f"attachment text truncated at {MAX_TEXT} characters"
    except Exception as e:  # broken on purpose, password-protected, unknown format ...
        r.update(text="", images=[], error=f"{type(e).__name__}: {e}"[:200])
    return r
