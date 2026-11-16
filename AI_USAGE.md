# AI usage

> Draft written with the agent from the real session. **Edit into your own words before sending.**

## Setup

- **Coding agent:** Claude (model Claude Fable 5.2) in the Claude desktop app, connected to the
  project folder on my Windows machine so it could read the package, write files, run Python and
  make git commits there. One session; no instruction files (no CLAUDE.md / AGENTS.md), no
  subagents, no custom skills, no MCP servers used for the work.
- **Model inside `parse`:** OpenAI `gpt-5-mini` only.

## How I worked

I gave the agent the brief and asked for its plan before code, with one constraint: simple,
few lines, smart logic rather than a framework. It proposed "code decides facts, one model call
per email, code checks the answer", then built it in steps I could follow from the commits:
readers -> model call + cache + checks -> scoring script and model-free tests -> first real run
-> three prompt/code fixes driven by the scoring script. Review was by numbers
(`evaluate.py -v`) and by reading `parsed.json` for the emails that failed, not by trusting the
agent's summary.

## Three moments

1. **The agent was wrong, and how it was caught.** Its prompt v2 told the model to write
   "concise" descriptions; the next scored run showed it had dropped "carbon steel" from four
   pipe lines (E019) and, separately, that the model had classified a plain resend (E008) as a
   revision, returning 44 duplicate lines. The scoring script caught both; the fix moved
   duplicate detection into code. Commit `b4fe5a5`, session `ai-sessions/` (this session).
2. **I overruled it.** [Fill in with what you actually changed or refused - for example the
   "simple, fewer lines" constraint that shaped the one-call design, or the agent's suggestion
   to ask for an extension, which I turned down.] Commit `3f08383`, same session.
3. **It saved real time.** The oracle test (answer key plays the model) proved the code checks
   never delete a true line before a single paid model call was made, and the whole
   reader/cache/check pipeline was working in one sitting. Commit `f7c68a8`, same session.

The agent also refused one thing I asked for: generating prompts to re-create the session logs
after the fact. The logs in `ai-sessions/` are the real session.
