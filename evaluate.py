#!/usr/bin/env python3
"""Score parsed.json against data/dev/answer_key.json - our reading of data/README.md 'What we grade'.

    python evaluate.py parsed.json [--key data/dev/answer_key.json] [-v]
"""
import argparse
import json
import re

REQ = ("rfq", "rfq_revision", "purchase_order")
SYN = [(r"schedule", "sch"), (r"(\d+)\s*(lb|#)", r"\1#"), (r"class\s*(\d+)", r"\1#"), (r"butt\s*weld", "bw"),
       (r"seamless", "smls"), (r"galvanized", "galv"), (r"\bnpt\b", "threaded"), (r"\bthd\b", "threaded")]


def norm(s):
    s = s.lower()
    for a, b in SYN:
        s = re.sub(a, b, s)
    return re.sub(r"[^a-z0-9#/&|]", "", s)


def toks(s):
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def pair(pred, gold):
    """Greedy pairing on raw-token overlap, best first."""
    cand = sorted(((len(toks(p["raw"]) & toks(g["raw"])) / max(1, len(toks(p["raw"]) | toks(g["raw"]))), i, j)
                   for i, p in enumerate(pred) for j, g in enumerate(gold)), reverse=True)
    used_p, used_g, pairs = set(), set(), []
    for s, i, j in cand:
        if s >= 0.5 and i not in used_p and j not in used_g:
            used_p.add(i), used_g.add(j), pairs.append((pred[i], gold[j]))
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("parsed")
    ap.add_argument("--key", default="data/dev/answer_key.json")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    pred, key = json.load(open(a.parsed, encoding="utf-8")), json.load(open(a.key, encoding="utf-8"))["emails"]
    c = dict(silent=[], lost=[], phantom=[], cls=0, gold=0, pred=0, paired=0, qty=0, uom=0, src=0, ctx=0, links=0, rev_tp=0, rev_fp=0, rev_fn=0)
    for eid, g in key.items():
        p = pred.get(eid) or {"classification": None, "line_items": [], "needs_review": False}
        act = bool(g["line_items"]) or g["needs_review"]
        if act and not p["line_items"] and not p["needs_review"] and not (g["duplicate_of"] and p.get("duplicate_of")):
            c["silent"].append(eid)
        if g["classification"] in REQ and p["classification"] not in REQ:
            c["lost"].append(eid)
        if g["classification"] not in REQ and p["classification"] in REQ:
            c["phantom"].append(eid)
        c["cls"] += p["classification"] == g["classification"]
        c["links"] += p.get("revises") == g["revises"] and p.get("duplicate_of") == g["duplicate_of"]
        c["rev_tp"] += p["needs_review"] and g["needs_review"]
        c["rev_fp"] += p["needs_review"] and not g["needs_review"]
        c["rev_fn"] += g["needs_review"] and not p["needs_review"]
        pairs = pair(p["line_items"], g["line_items"])
        c["gold"] += len(g["line_items"]); c["pred"] += len(p["line_items"]); c["paired"] += len(pairs)
        for pl, gl in pairs:
            ok_q = pl["qty"] == gl["qty"]
            ok_u = (pl["uom"] or "EA") == (gl["uom"] or "EA")
            d = norm(pl["description"])
            ok_c = all(any(norm(alt) in d for alt in m.split("|")) for m in gl["must_mention"]) and not any(norm(m) in d for m in gl["must_not_mention"])
            c["qty"] += ok_q; c["uom"] += ok_u; c["src"] += pl["source"] == gl["source"]; c["ctx"] += ok_c
            if a.v and not (ok_q and ok_u and ok_c and pl["source"] == gl["source"]):
                print(f"  {gl['id']}: qty {pl['qty']}/{gl['qty']} uom {pl['uom']}/{gl['uom']} src {pl['source']}/{gl['source']} ctx {ok_c}\n     desc: {pl['description']}\n     must: {gl['must_mention']} not: {gl['must_not_mention']}")
        if a.v and (p["classification"] != g["classification"] or len(pairs) != len(g["line_items"]) or len(pairs) != len(p["line_items"]) or p["needs_review"] != g["needs_review"]):
            print(f"{eid}: class {p['classification']}/{g['classification']} lines pred {len(p['line_items'])} gold {len(g['line_items'])} paired {len(pairs)} review {p['needs_review']}/{g['needs_review']} {p.get('review_reason') or ''}")
    n, pr = len(key), max(1, c["paired"])
    print(f"silent losses      {len(c['silent'])} {c['silent']}")
    print(f"hallucinated lines {c['pred'] - c['paired']}")
    print(f"lost requests      {len(c['lost'])} {c['lost']}   phantom requests {len(c['phantom'])} {c['phantom']}")
    print(f"classification     {c['cls']}/{n}")
    print(f"line recall        {c['paired']}/{c['gold']}   precision {c['paired']}/{c['pred']}")
    print(f"on paired lines    qty {c['qty']}/{pr}  uom {c['uom']}/{pr}  source {c['src']}/{pr}  context {c['ctx']}/{pr}")
    print(f"links ok           {c['links']}/{n}   needs_review tp {c['rev_tp']} fp {c['rev_fp']} fn {c['rev_fn']}")


if __name__ == "__main__":
    main()
