"""python -m rfq parse --emails <dir> --out parsed.json

Pipeline per email:  read (code) -> one model call (cached) -> checks (code).
After all emails:    duplicates and thread links (code).
One email can never take down the run: every failure becomes a record flagged for review.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import llm
from .readers import QUOTE_MARK, load_email, split_quoted

REQUESTS = ("rfq", "rfq_revision", "purchase_order")
DUP_WINDOW = 14 * 86400      # a resend arrives within days; the same list a month later is a new order


def tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def grounded(raw: str, text: str) -> float:
    """Share of the line's tokens that really occur in the text it claims to come from."""
    t, have = tokens(raw), set(tokens(text))
    return sum(x in have for x in t) / len(t) if t else 0.0


def blank(cls: str = "other") -> dict:
    return {"classification": cls, "revises": None, "duplicate_of": None,
            "needs_review": False, "review_reason": None, "line_items": []}


def needs_human(reason: str) -> dict:
    """The safe failure: we could not read it, so a person must - never a silent loss."""
    return {**blank("rfq"), "needs_review": True, "review_reason": reason[:300]}


def prompt_text(email: dict, parent: dict | None) -> str:
    fresh, quoted = split_quoted(email["body"]) if not email["internal"] else (email["body"], "")
    parts = [f"From: {email['from']}\nTo: {email['to']}\nDate: {email['date_str']}\nSubject: {email['subject']}",
             "Sender is one of OUR OWN reps (@ridgeline-supply.example)." if email["internal"] else "",
             "=== BODY ===\n" + fresh.strip() + (f"\n\n{QUOTE_MARK}\n{quoted}" if quoted else "")]
    for a in email["attachments"]:
        head = f"=== {'INLINE IMAGE' if a['inline'] else 'ATTACHMENT'}: {a['name']} ==="
        if a["error"]:
            parts.append(f"{head}\nCOULD NOT BE READ ({a['error']})")
        else:
            parts.append(f"{head}\n{a['text'] or '(a picture - see the image with this filename)'}" + (f"\nNOTE: {a['note']}" if a["note"] else ""))
    if parent:
        parts.append(f"=== EARLIER MESSAGE IN THIS THREAD ({parent['id']}; already handled when it arrived - use it only to build the complete revised order) ===\n"
                     f"From: {parent['from']}\nDate: {parent['date_str']}\n{parent['body']}"
                     + "".join(f"\n--- its attachment {a['name']} ---\n{a['text']}" for a in parent["attachments"] if a["text"]))
    return "\n\n".join(p for p in parts if p)


def check(email: dict, ans: dict, parent: dict | None) -> dict:
    """What code decides about the model's answer."""
    rec = blank(ans["classification"])
    if rec["classification"] not in REQUESTS:
        return rec                                       # non-requests never carry lines or flags
    atts = {a["name"]: a for a in ((parent or {}).get("attachments", []) + email["attachments"])}
    fresh, quoted = split_quoted(email["body"])
    is_forward = email["internal"] or re.match(r"\s*(fw|fwd)\s*:", email["subject"], re.I)
    reasons, dropped = [], 0
    if ans.get("needs_review"):
        reasons.append(ans.get("review_reason") or "model asked for review")
    for ln in ans["line_items"]:
        raw, desc = str(ln.get("raw") or "").strip(), str(ln.get("description") or "").strip()
        if not raw or not desc:
            continue
        src = str(ln.get("source") or "body")
        if src != "body" and src not in atts:            # repair a mangled filename, else fall back
            src = next((n for n in atts if n.lower() == src.lower()), None) or (
                next(iter(atts)) if len(atts) == 1 and not atts[next(iter(atts))]["error"] else "body")
        if src == "body":
            where, has_pictures = email["body"] + "\n" + (parent or {}).get("body", ""), False
            # a plain rfq must not re-list what is only in the quoted history of a reply
            if rec["classification"] == "rfq" and quoted and not is_forward and grounded(raw, fresh) < 0.6 <= grounded(raw, quoted):
                dropped += 1
                continue
        else:
            where, has_pictures = atts[src]["text"], bool(atts[src]["images"])
        if where.strip() and grounded(raw, where) < 0.6 and not has_pictures:
            dropped += 1                                 # the model wrote a line that is not in its source
            continue
        qty = ln.get("qty")
        qty = qty if isinstance(qty, (int, float)) and not isinstance(qty, bool) and qty > 0 else None
        if isinstance(qty, float) and qty.is_integer():
            qty = int(qty)
        uom = ln.get("uom") if ln.get("uom") in llm.UOMS and qty is not None else None
        rec["line_items"].append({"raw": raw, "description": desc, "qty": qty, "uom": uom, "source": src})
    if dropped:
        reasons.append(f"{dropped} line(s) the model returned were not found in their source and were removed")
    for a in email["attachments"]:
        if a["error"] and not a["inline"]:
            reasons.append(f"attachment {a['name']} could not be read ({a['error']})")
        elif a["note"]:
            reasons.append(a["note"])
    if not rec["line_items"] and not reasons:
        reasons.append("looks like a request but no items could be read")
    if reasons:
        rec["needs_review"], rec["review_reason"] = True, "; ".join(dict.fromkeys(reasons))[:500]
    if rec["classification"] == "rfq_revision" and parent:
        rec["revises"] = parent["id"]
    return rec


def parse_one(path: Path, by_msgid: dict) -> tuple[dict, dict, dict | None]:
    """-> (record, cost report, email). Never raises."""
    t0, rep = time.time(), {"calls": 0, "cached": False, "prompt_tokens": 0, "completion_tokens": 0, "usd": 0.0}
    email = None
    try:
        email = load_email(path)
        if email["machine"]:                             # bounce / auto-reply headers: a fact, no model needed
            return blank("auto_reply"), {**rep, "note": "auto_reply by headers", "seconds": round(time.time() - t0, 2)}, email
        ppath = next((by_msgid[r] for r in email["refs"] if r in by_msgid and by_msgid[r] != path), None)
        parent = load_email(ppath) if ppath else None
        images = [(a["name"], img) for a in email["attachments"] for img in a["images"]]
        key = llm.cache_key(path.read_bytes(), ppath.read_bytes() if ppath else b"")
        ans, meta = llm.ask(key, llm.build_content(prompt_text(email, parent), images))
        rep.update(calls=meta["calls"], cached=meta["cached"], usd=meta["usd"], model=meta["model"],
                   prompt_tokens=meta["usage"]["prompt_tokens"], completion_tokens=meta["usage"]["completion_tokens"])
        rec = check(email, ans, parent)
    except llm.ModelUnavailable as e:
        rec = needs_human(f"model unavailable, email not parsed - a person must read it ({e})")
        rep["error"] = str(e)
    except Exception as e:                               # corrupt .eml, a bug of ours: flag and carry on
        rec = needs_human(f"could not process this email ({type(e).__name__}: {e})")
        rep["error"] = f"{type(e).__name__}: {e}"
    rep["seconds"] = round(time.time() - t0, 2)
    return rec, rep, email


def mark_duplicates(records: dict, emails: dict) -> None:
    """Same request sent twice: the later Date is the duplicate. Decided on content, by code."""
    def signature(eid):
        rec, em = records[eid], emails.get(eid)
        if rec["classification"] not in REQUESTS or em is None:
            return None
        if rec["line_items"]:
            return {(" ".join(tokens(l["raw"])), l["qty"]) for l in rec["line_items"]}
        files = sorted(a["raw_sha"] for a in em["attachments"] if not a["inline"])
        return {("files", em["sender"], *files)} if files else None

    seen = []                                            # originals, oldest first
    for eid in sorted(records, key=lambda i: (emails[i]["date"] if emails.get(i) else 0, i)):
        sig = signature(eid)
        if not sig:
            continue
        rec = records[eid]
        for oid, osig in seen:
            orig = records[oid]
            overlap = len(sig & osig) / len(sig | osig)
            # Identical content is a resend whatever the model called it. Nearly identical only
            # counts when it is not a reply in a thread - a revision legitimately differs by one line.
            near = overlap >= 0.9 and not rec["revises"] and rec["classification"] == orig["classification"]
            same_kind = (rec["classification"] == "purchase_order") == (orig["classification"] == "purchase_order")
            if (overlap == 1 or near) and same_kind and emails[eid]["date"] - emails[oid]["date"] <= DUP_WINDOW:
                records[eid] = {**blank(orig["classification"]), "duplicate_of": oid}
                break
        else:
            seen.append((eid, sig))


def run(emails_dir: Path, out: Path) -> int:
    paths = sorted(p for p in emails_dir.iterdir() if p.suffix.lower() == ".eml")
    by_msgid = {}
    for p in paths:                                      # thread index; a broken file just isn't in it
        try:
            by_msgid.setdefault(load_email(p)["message_id"], p)
        except Exception:
            pass
    by_msgid.pop("", None)
    with ThreadPoolExecutor(max_workers=int(os.environ.get("RFQ_WORKERS", "6"))) as pool:
        results = list(pool.map(lambda p: parse_one(p, by_msgid), paths))
    records = {p.stem: r[0] for p, r in zip(paths, results)}
    report = {p.stem: r[1] for p, r in zip(paths, results)}
    mark_duplicates(records, {p.stem: r[2] for p, r in zip(paths, results)})
    out.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    total = {k: round(sum(r[k] for r in report.values()), 6) for k in ("calls", "prompt_tokens", "completion_tokens", "usd")}
    total.update(emails=len(paths), cache_hits=sum(r["cached"] for r in report.values()),
                 failed=sum("error" in r for r in report.values()), model=llm.MODEL,
                 note="usd/tokens = what each answer cost when it was first made; calls = model calls made by THIS run")
    out.with_suffix(".report.json").write_text(json.dumps({"total": total, "emails": report}, indent=2) + "\n", encoding="utf-8")
    print(f"{len(paths)} emails -> {out} | model calls this run: {total['calls']} | cache hits: {total['cache_hits']} | "
          f"failed: {total['failed']} | cost of answers: ${total['usd']:.4f} | details: {out.with_suffix('.report.json').name}")
    return 0


def load_dotenv() -> None:
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() and not k.strip().startswith("#") and v:
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rfq")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parse", help="read every .eml in a directory and write parsed.json")
    p.add_argument("--emails", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    return run(a.emails, a.out)


if __name__ == "__main__":
    load_dotenv()
    sys.exit(main())
