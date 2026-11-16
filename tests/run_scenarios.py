"""Runs parse on tests/scenarios and compares with what a careful rep would do (my judgment).

    python tests/run_scenarios.py        (replays from cache/ without a key once recorded)
Each check: classification, number of lines, needs_review, links, and words that must / must not
appear in the returned lines.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# id: (classification, n_lines, needs_review, {extra})
EXPECT = {
    "S01": ("auto_reply", 0, False, {"why": "out-of-office with no auto headers"}),
    "S02": ("vendor_quote", 0, False, {"why": "supplier answering our rep"}),
    "S03": ("other", 0, False, {"why": "we went with someone else, quoting our quote"}),
    "S04": ("other", 0, False, {"why": "invoice with line items"}),
    "S05": ("status_inquiry", 0, False, {"why": "ETA question quoting the PO (Outlook-style history)"}),
    "S06": ("rfq", 0, True, {"why": "same as last order but double it"}),
    "S07": ("rfq", 3, False, {"has": ["galv", "sch 40"]}),
    "S08": ("rfq_revision", 3, False, {"revises": "S07", "has": ["cap"], "hasnt": ["union"], "qty": [6, 12, 4], "why": "original not quoted, only in inbox"}),
    "S09": ("rfq", 2, False, {"has": ["304"], "hasnt": ["tee"], "why": "HTML-only body, struck row"}),
    "S10": ("rfq", 4, False, {"has": ["300#", "a105", "150#"], "hasnt": ["xh bore", "8\""], "why": "xlsx: struck row, hidden row, total row, override"}),
    "S11": ("purchase_order", 0, True, {"why": "PO attachment missing"}),
    "S12": ("vendor_quote", 0, False, {"why": "our rep forwarding a vendor's quote"}),
    "S13": ("rfq", 4, False, {"qty": [12, None, 3, 150], "uom": ["EA", None, "BX", "FT"], "why": "spelled-out and vague quantities"}),
    "S14": ("rfq", 2, False, {"why": "prompt injection inside the email"}),
    "S15": ("other", 0, False, {"why": "job application with a resume"}),
    "S16": ("rfq", 0, False, {"duplicate_of": "S13", "why": "resend in the body, different wording around it"}),
    "S17": ("rfq", 0, True, {"why": "corrupt spreadsheet"}),
    "S18": ("other", 0, False, {"why": "internal chatter from our own rep"}),
    "S19": ("rfq", 3, False, {"has": ["316"], "hasnt": ["union"], "why": "picture with a crossed-out line"}),
    "S20": ("rfq", 3, False, {"sources": {"body", "SR-5-020.csv"}, "why": "items in attachment AND body"}),
    "S21": ("rfq", 2, False, {"qty": [10, 25], "uom": ["EA", "M"], "why": "Spanish, metres"}),
    "S22": ("rfq", 2, False, {"why": "customer forwards a colleague's list: forwarded content counts"}),
    "S23": ("purchase_order", 2, False, {"why": "accepts all lines of a quoted quote"}),
    "S24": ("auto_reply", 0, False, {"why": "read receipt by headers, no model call"}),
}


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "parsed.json"
        subprocess.run([sys.executable, "-m", "rfq", "parse", "--emails", str(ROOT / "tests" / "scenarios"), "--out", str(out)], cwd=ROOT, check=True)
        got = json.loads(out.read_text(encoding="utf-8"))
    bad = 0
    for sid, (cls, n, review, x) in EXPECT.items():
        g = got[sid]
        text = " ".join(l["description"] + " " + l["raw"] for l in g["line_items"]).lower()
        problems = []
        if g["classification"] != cls:
            problems.append(f"class {g['classification']} != {cls}")
        if len(g["line_items"]) != n:
            problems.append(f"{len(g['line_items'])} lines != {n}")
        if g["needs_review"] != review:
            problems.append(f"needs_review {g['needs_review']} ({g['review_reason']})")
        for k in ("revises", "duplicate_of"):
            if g[k] != x.get(k):
                problems.append(f"{k} {g[k]} != {x.get(k)}")
        problems += [f"missing '{w}'" for w in x.get("has", []) if w not in text]
        problems += [f"should not contain '{w}'" for w in x.get("hasnt", []) if w in text]
        if "qty" in x and [l["qty"] for l in g["line_items"]] != x["qty"]:
            problems.append(f"qty {[l['qty'] for l in g['line_items']]} != {x['qty']}")
        if "uom" in x and [l["uom"] for l in g["line_items"]] != x["uom"]:
            problems.append(f"uom {[l['uom'] for l in g['line_items']]} != {x['uom']}")
        if "sources" in x and {l["source"] for l in g["line_items"]} != x["sources"]:
            problems.append(f"sources {sorted({l['source'] for l in g['line_items']})}")
        bad += bool(problems)
        print(f"{'FAIL' if problems else 'ok  '} {sid} {x.get('why', '')}" + "".join(f"\n       - {p}" for p in problems))
    print(f"\n{len(EXPECT) - bad}/{len(EXPECT)} scenarios as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
