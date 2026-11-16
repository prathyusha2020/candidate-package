# Decisions

Time spent: **[fill in honestly before sending]**. Model: `gpt-5-mini`, one call per email.

## Something in the data that surprised you

Too much context is punished exactly like too little, and the files are built to tempt you into
it. E006's PDF form says `Material: ASTM A105`, but the two gasket lines typed in the body must
*not* say A105; E005 says "ALL FITTINGS BW SCH 40 ... UNLESS NOTED" and one row quietly says
SCH 80. The same workbook carries a hidden sheet "Rev A (old)" with different quantities and a
10" elbow that no longer exists: any reader that loops over "all sheets" quotes a superseded
order with full confidence. And in E019's Word file, `python-docx` hands you all paragraphs and
then all tables, which silently detaches each heading ("SCH 80 SMLS A106B") from the table it
governs; it has to be read in document order.

## Something in the brief or README I disagree with, and what I did

1. **"The email with the later `Date:` is the duplicate."** `Date:` is the sender's clock and is
   trivially wrong or equal; arrival order is the fact we actually own. I followed the README
   (ties broken by id), but duplicates are decided by code on *content* (same items, or same
   sender + same attachment bytes), never by the model: in one dev run the model called the plain
   resend E008 an `rfq_revision`, which would have quoted 44 lines twice.
2. **`needs_review` "only for rfq / rfq_revision / purchase_order".** That leaves no honest
   answer for "I could not read this email at all" (provider down, corrupt file). I return
   `rfq` + `needs_review` there on purpose: a possible phantom request is cheaper than a silent
   loss, which the README itself ranks first.
3. `20 jts` scored as `EA`: I follow the key, but a joint is ~21 ft of pipe and the quote is
   priced per foot; `raw` keeps "jts" so the rep still sees it.

## Predicted held-out numbers, and the two kinds of email I expect to get wrong

Dev (committed run): 24/24 classes, 210/210 lines, qty/uom/source 210/210, context 210/210,
0 silent losses, 0 hallucinated lines. That is the 4th prompt version and dev was the tuning
set, so I discount it. Held-out prediction: classification **92-95%**; silent losses **0**
(at most 1); hallucinated lines **1-3%** of returned lines; line recall **~95%**; qty **~97%**;
context carried **~88-92%**; needs_review 1-2 false flags; links 1 wrong.

1. **Handwriting, photos and scans.** Nothing checks the model's reading of a picture (the
   grounding check only works on text). Across my four dev runs the same photo (E007) was read
   correctly three times and once with a ditto mark left unresolved and a needless review
   flag. Crossed-out rows on a scan are the same risk.
2. **Thread situations dev doesn't show.** A revision whose original list is not quoted and
   lives in an earlier email's attachment; a partial "go ahead" mixed with new items; a rep
   forwarding a vendor's quote; formats I only flag rather than read (`.msg`, HEIC, zip,
   pictures inside Excel).

## How this runs for real

- **3,000 emails/day by webhook.** Designed, not built: webhook writes the raw `.eml` to storage
  and enqueues its hash; workers run exactly `parse_one`. At dev cost ($0.0037/email, ~1.6k
  output tokens) that is about **$11/day**. Built: per-email isolation, 6 parallel workers.
- **Provider down for two hours.** Built: 3 retries with backoff, then the email comes back
  `needs_review` with the reason, the run finishes, and failures are **not cached**, so the next
  run retries only those. Tested (`test_model_down...`). Not built: the queue that re-drives them.
- **A 300-page PDF.** Built: text is read for the first 30 pages (scans: 8 pages as images), and
  the email is flagged `needs_review` saying how much was read. Never an unbounded request.
- **Same email delivered twice.** Built: the cache key is a hash of the raw `.eml` bytes, so the
  second delivery costs no model call, and content dedupe marks it `duplicate_of`.

## Where the model decides and where code decides

**Model (one call, strict JSON schema):** the classification, which rows are requests, the
reading of pictures, the self-contained description, qty/uom.
**Code, before the call:** bounce/auto-reply from headers (no call); hidden sheets dropped;
struck-through rows and quoted reply history marked; encrypted, empty, corrupt or unsupported
attachments detected. **Code, after the call:** non-request classes forced to `[]`; every `raw`
from a text source must share >=60% of its tokens with that source or it is removed and the
email flagged (hallucination guard); a plain `rfq` cannot return lines that exist only in quoted
history; `source` must be `body` or a real filename; uom whitelist; unreadable attachment or a
request with zero lines forces `needs_review` (silent-loss guard); `revises` from
`In-Reply-To`; duplicates.

## How I measured

`evaluate.py` re-implements "What we grade" against the dev key; `tests/` has an oracle test
(the answer key plays the model: true lines must survive every code check) and failure tests.
Runs: v1 209/210 lines, 1 context miss -> v2 fixed both but broke E008 (44 duplicate lines) and
dropped "carbon steel" -> v3 fixed, E007 ditto miss + false flag -> v4 clean. Each fix is a
general rule in the prompt or in code, not a dev string; then I stopped.

## Rejected

Separate classify and extract calls (twice the cost, and the two can disagree); an OCR step
before the model (tesseract loses table structure and ditto marks; the vision model reads the
page directly); a non-OpenAI OCR provider (graders run with an OpenAI key only); hashing the
rendered request for the cache (JPEG bytes differ across OS/library versions and replay breaks).

## Known broken / next

Model output is not deterministic and only the cached answer is stable; pictures are unverified
(next: read twice and flag disagreement); text-PDF strike-outs rely on page images of the first
3 pages; no matcher and no web app (skipped for time; `catalog.csv` is untouched).
