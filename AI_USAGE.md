# AI usage

## Setup

- **Coding agent:** Claude (model Claude Fable 5.2) in the Claude desktop app, connected to the
  project folder on my Windows machine so it could read the package, write files, run Python
  and make git commits there. One session. No instruction files (no CLAUDE.md / AGENTS.md), no
  subagents, no custom skills, no MCP servers for the work itself.
- **Model inside `parse`:** OpenAI `gpt-5-mini` only. The agent ran `parse` with the OpenAI key
  from a git-ignored `.env`; the key was used for nothing else.
- **Session log:** `ai-sessions/claude-session.md` (the whole session, keys redacted).

## How I worked

I gave the agent the brief and asked for its plan before any code, with one constraint:
simple, few lines, smart logic rather than a framework. It answered with "code decides the
facts, one model call per email, code checks the answer" and built it in steps that map to the
commits: readers -> model call, cache and checks -> scoring script and model-free tests ->
first real run -> prompt and code fixes driven by the scoring script -> edge-case suite.
Progress was judged by numbers (`evaluate.py -v`, `run_all_tests.py`), not by the agent's
summaries. I asked it what the code logic was and how to test it before accepting it, pushed
for a much wider test set than the dev inbox, and asked about using Mistral OCR and Scrape.do
from my earlier scraping work; the agent argued against both for this submission (graders run
an OpenAI key only, and the brief forbids calls to anything but the model provider) and I
agreed to keep them for a separate version.

## Three moments

1. **The agent was wrong, and how it was caught.** Its prompt v2 asked for "concise"
   descriptions. The next scored run showed the model had dropped "carbon steel" from four
   pipe lines (E019) and had classified a plain resend (E008) as a revision, returning 44
   duplicate lines. Reading the prompt would not have found either; the scoring run did. The
   fix moved duplicate detection out of the model and into code. Commit `b4fe5a5`, session
   `ai-sessions/claude-session.md`.
2. **I overruled it.** After dev scored 210/210 the agent called the work done and moved on
   to documents. I did not accept a perfect score on the tuning set as proof and told it to
   think of every test case it could and to get ready for real Outlook and Gmail mail. That
   produced 27 model-free tests and 24 new scenario emails, and exposed three real problems:
   Outlook-style reply history was not recognised, signature/calendar attachments would have
   flagged ordinary requests for review, and one run put a pasted screenshot's lines on the
   wrong image. Commit `cf17e55`, same session.
3. **It saved real time.** Before a single paid model call, the agent wrote a test in which
   the answer key plays the model, proving the code checks never delete a true line. The
   readers, cache, checks and that test were working within the first half hour. Commit
   `f7c68a8`, same session.

Two things in the log I would rather say here than have you find: the agent suggested asking
for an extension and I declined; and I asked it to help produce session logs after the fact,
which it refused. The log in `ai-sessions/` is the real session, unedited apart from keys.
