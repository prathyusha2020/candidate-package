# Running

Python 3.11+ on Windows, macOS or Linux. No Docker needed; all dependencies are pip wheels.

## Setup (one step)

```
pip install -r requirements.txt
```

## Run

```
python -m rfq parse --emails data/dev/emails --out parsed.json
python data/check_format.py parsed.json --emails data/dev/emails
```

Run it from the repository root. It writes `parsed.json` and, next to it, `parsed.report.json`
(model calls, tokens and dollars per email).

- **Dev inbox needs no API key**: every model answer for it is committed under `cache/`.
- **A new inbox** needs `OPENAI_API_KEY` in the environment (or in a git-ignored `.env` file at
  the repo root, `OPENAI_API_KEY=...`). Answers are cached as they arrive, so a second run makes
  zero model calls and gives the same output with the key removed.
- **No key and no cache**: the run still finishes; every email it could not parse comes back
  `needs_review: true` with the reason. Nothing crashes.

## Model

`gpt-5-mini` through the OpenAI Chat Completions API (vision + strict JSON schema), one call per
email. Machine-generated mail (bounces, auto-replies) is recognised from headers and costs no call.

Options (environment variables): `RFQ_MODEL` (another OpenAI model), `RFQ_WORKERS` (parallel
emails, default 6), `RFQ_CACHE` (cache directory), `OPENAI_BASE_URL`.

## Cost

Committed dev run (24 emails): 23 model calls (the bounce needs none), 57,405 input and
39,529 output tokens, **$0.085 total, about $0.0035 per email**. Per-email calls, tokens and
dollars are in `parsed.report.json`. A replay from the cache makes 0 calls and costs nothing.

## Checks

```
python run_all_tests.py                # everything below in one go, no key needed
```

Or one at a time:

```
python -m unittest discover tests      # 27 tests, no model, no key
python evaluate.py parsed.json -v      # score against data/dev/answer_key.json
python tests/run_scenarios.py          # 24 hand-written edge-case emails (replays from cache)
```

`tests/make_scenarios.py` regenerates the scenario emails (not needed to run them).

## Recording

(link to the screen recording goes here)
