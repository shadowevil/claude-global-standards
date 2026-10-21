"""M1: an Agent call must pick its model (user rule, 2026-10-21: "Pick the cheapest model that does the
job well"; and the standing rule that every agent brief states "the model chosen and why").

Class (c), partly checkable. A hook can see THAT the dispatcher left the model unset; it cannot judge
whether the chosen tier FITS the task (that is a judgment about the work), so M1 never second-guesses
a model that was set. Candidate check (ii) - haiku on a prompt that says hooks/delete/security, opus on
a lookup - was replayed over 1,247 real Agent calls and is NOT built: the words are noise (see README).

What M1 flags: a PreToolUse call on the Agent tool where nothing picks a tier. Claude Code resolves a
subagent's model in this order (docs, code.claude.com/docs/en/sub-agents):
  1. the call's `model` parameter
  2. the agent definition's `model` frontmatter (`inherit` selects the main conversation's model)
  3. the CLAUDE_CODE_SUBAGENT_MODEL environment variable
  4. the main conversation's model
So the call is flagged only when step 1 is empty AND no agent definition supplies a model AND the
environment variable is unset: it will run on whatever the main session runs on (often the most
expensive tier), even if the brief says "Model: Haiku" - a sentence in the prompt does not select a model.

Silent (never flagged): a call with `model` set (any value); a subagent_type whose definition
(<project>/.claude/agents, a parent of cwd up to the repo root, ~/.claude/agents, matched by file
stem or `name:`, and opening with a `---` header) carries a `model:` other than `inherit` - this
includes a definition that reuses a built-in name such as Explore, which overrides the built-in;
claude-code-guide (Haiku) and statusline-setup (Sonnet), which carry their own models; a
subagent_type that is neither a built-in (general-purpose, Explore, Plan) nor a definition this hook
can find (a plugin or managed agent - unverifiable, so no guess).

Advisory only: no permissionDecision, never a deny. The entry point (model_guard.py) logs a row and,
with --advise, prints hookSpecificOutput.additionalContext.
"""
import os
import re

RULE_ID = "M1"
# Built-ins that inherit the main model. claude-code-guide (Haiku) and statusline-setup (Sonnet) carry
# their own models per the sub-agents docs, so they are NOT here: they fall to the lookup and, with no
# definition found, stay silent. A project/user definition reusing one of these names overrides it
# (docs: .claude/agents and ~/.claude/agents outrank the built-in), so these names are looked up too.
BUILTIN_TYPES = ("", "general-purpose", "Explore", "Plan")
ENV_MODEL = "CLAUDE_CODE_SUBAGENT_MODEL"
MAX_DEF_FILES = 300          # agent definition files read per lookup (recursive scan cap)
MAX_DEF_BYTES = 8192         # only the frontmatter head of a definition is read
MAX_WALK_UP = 8              # parent folders of cwd searched for .claude/agents

MSG = ("M1: this Agent call sets no `model`, so the subagent runs on the main session's model%s. "
       "Pick the cheapest tier that does the job and pass it as the `model` parameter (a line in the "
       "brief does not select one): haiku for clear, fully specified actions - single edits and bulk "
       "edits included - and mechanical lookups; sonnet is the default; opus for design, "
       "security-sensitive code, hard debugging and attack-testing; a reviewer is never a lower tier "
       "than the author on security-sensitive code. State the model chosen and why in the brief "
       "(user rule, 2026-10-21).")
STATED_NOTE = (" - the brief names %s, but only the `model` parameter takes effect")

PROMPT_MODEL = re.compile(r"\bmodel\s*[:=]\s*(haiku|sonnet|opus)\b", re.IGNORECASE)
FM_MODEL = re.compile(r"^\s*model\s*:\s*(.*?)\s*$", re.IGNORECASE)
FM_NAME = re.compile(r"^\s*name\s*:\s*(.*?)\s*$", re.IGNORECASE)


def _lines(text):
    """The text's lines, with a leading UTF-8 BOM (U+FEFF) dropped and CR/CRLF normalised."""
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def has_header(text):
    """True when the text opens with a `---` frontmatter line (a file without one is not an agent
    definition: Claude Code needs the frontmatter, so M1 must not count it as one)."""
    lines = _lines(text)
    return bool(lines) and lines[0].strip() == "---"


def parse_frontmatter(text):
    """(name, model) from a markdown agent definition's YAML frontmatter; '' for a missing key or a
    file with no frontmatter. Quotes around a value are dropped. Cheap line parse, not full YAML."""
    name = model = ""
    lines = _lines(text)
    if not lines or lines[0].strip() != "---":
        return name, model
    for ln in lines[1:80]:
        if ln.strip() == "---":
            break
        m = FM_NAME.match(ln)
        if m and not name:
            name = m.group(1).strip("'\" ")
        m = FM_MODEL.match(ln)
        if m and not model:
            model = m.group(1).strip("'\" ")
    return name, model


def agent_dirs(event, home=None):
    """Folders that may hold agent definitions, highest priority first: <cwd and each parent up to the
    repo root>/.claude/agents, <project>/.claude/agents, ~/.claude/agents."""
    out = []

    def add(p):
        p = os.path.normpath(p)
        if p and p not in out:
            out.append(p)

    cwd = event.get("cwd") or ""
    root = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
    d = os.path.normpath(cwd) if cwd else ""
    for _ in range(MAX_WALK_UP):
        if not d:
            break
        add(os.path.join(d, ".claude", "agents"))
        if os.path.exists(os.path.join(d, ".git")):
            break
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    if root:
        add(os.path.join(root, ".claude", "agents"))
    h = home if home is not None else (os.environ.get("USERPROFILE") or os.environ.get("HOME") or "")
    if h:
        add(os.path.join(h, ".claude", "agents"))
    return out


def find_definition_model(agent_type, dirs):
    """(found, model): `found` is True when a definition for `agent_type` exists in `dirs`; `model`
    is its frontmatter model ('' when it has none). The first definition found wins."""
    want = agent_type.lower()
    seen = 0
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for base, _subdirs, files in os.walk(d):
            for fn in files:
                if not fn.lower().endswith(".md"):
                    continue
                seen += 1
                if seen > MAX_DEF_FILES:
                    return False, ""
                try:
                    with open(os.path.join(base, fn), "rb") as fh:
                        head = fh.read(MAX_DEF_BYTES).decode("utf-8", "replace")
                except OSError:
                    continue
                if not has_header(head):
                    continue                # no frontmatter: not an agent definition
                name, model = parse_frontmatter(head)
                if name.lower() == want or os.path.splitext(fn)[0].lower() == want:
                    return True, model
    return False, ""


def evaluate(event, lookup=None):
    """[{"rule", "kind", ...}] - one M1 row when this Agent call leaves its model unresolved, else [].
    `lookup(agent_type, event) -> (found, model)` is injectable for the self-test and the replay."""
    if (event.get("tool_name") or "") != "Agent":
        return []
    ti = event.get("tool_input")
    if not isinstance(ti, dict):
        return []
    model = ti.get("model")
    if isinstance(model, str) and model.strip() and model.strip().lower() != "inherit":
        return []
    atype = ti.get("subagent_type")
    atype = atype.strip() if isinstance(atype, str) else ""
    if atype:                               # "" is the default general-purpose: no definition to look up
        if lookup is None:
            found, dmodel = find_definition_model(atype, agent_dirs(event))
        else:
            found, dmodel = lookup(atype, event)
        if not found and atype not in BUILTIN_TYPES:
            return []                       # plugin/managed/unknown agent: cannot verify, stay silent
        if found and dmodel and dmodel.strip().lower() != "inherit":
            return []                       # the definition's own model is step 2 of the resolution
    env = os.environ.get(ENV_MODEL, "").strip()
    if env and env.lower() != "inherit":
        return []
    prompt = ti.get("prompt") if isinstance(ti.get("prompt"), str) else ""
    m = PROMPT_MODEL.search(prompt)
    return [{"rule": RULE_ID, "kind": "unset", "subagent_type": atype or "(default)",
             "brief_names": m.group(1).lower() if m else "",
             "words": len(prompt.split())}]


def message_for(row):
    note = STATED_NOTE % row["brief_names"] if row.get("brief_names") else ""
    return MSG % note


def output_for(text):
    """The advisory PreToolUse payload: additionalContext only, never a permissionDecision."""
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}


# ------------------------------------------------------------------ self-test

CASES_RUN = [0]


def run_self_test():
    """Yields a failure string per failed case; CASES_RUN[0] counts the cases run."""
    CASES_RUN[0] = 0
    saved_env = os.environ.pop(ENV_MODEL, None)

    def no_defs(_t, _e):
        return False, ""

    def defs(table):
        return lambda t, _e: (t in table, table.get(t, ""))

    def ev(**ti):
        base = {"description": "d", "prompt": "Do the thing in C:\\x\\y.", "subagent_type": "general-purpose"}
        base.update(ti)
        base = {k: v for k, v in base.items() if v is not None}
        return {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": "C:\\Users\\Someone\\proj",
                "session_id": "s", "tool_input": base}

    sonnet_def = defs({"client": "sonnet", "review": "Sonnet", "bare": "", "inh": "inherit"})
    try:
        # ---- must fire
        must_fire = [
            ("no model, built-in general-purpose", ev(), no_defs),
            ("no model, Explore", ev(subagent_type="Explore"), no_defs),
            ("no model, Plan", ev(subagent_type="Plan"), no_defs),
            ("built-in Explore overridden by a definition with no model", ev(subagent_type="Explore"),
             defs({"Explore": ""})),
            ("built-in Plan overridden by a definition saying inherit", ev(subagent_type="Plan"),
             defs({"Plan": "inherit"})),
            ("no model, no subagent_type at all", ev(subagent_type=None), no_defs),
            ("empty-string model", ev(model=""), no_defs),
            ("whitespace model", ev(model="   "), no_defs),
            ("model inherit", ev(model="inherit"), no_defs),
            ("non-string model", ev(model=5), no_defs),
            ("custom agent whose definition has no model", ev(subagent_type="bare"), sonnet_def),
            ("custom agent whose definition says inherit", ev(subagent_type="inh"), sonnet_def),
            ("brief says Model: Haiku but no parameter", ev(prompt="Model: Haiku. Edit line 3."), no_defs),
        ]
        for label, e, lk in must_fire:
            CASES_RUN[0] += 1
            rows = evaluate(e, lk)
            if len(rows) != 1 or rows[0]["rule"] != "M1":
                yield "M1 must fire: %s (got %r)" % (label, rows)
        # ---- must NOT fire
        must_not = [
            ("model set: haiku", ev(model="haiku"), no_defs),
            ("model set: sonnet", ev(model="sonnet"), no_defs),
            ("model set: opus on a custom agent", ev(model="opus", subagent_type="bare"), sonnet_def),
            ("model set: full id", ev(model="claude-sonnet-5-5"), no_defs),
            ("custom agent whose definition sets sonnet", ev(subagent_type="client"), sonnet_def),
            ("definition model in other case", ev(subagent_type="review"), sonnet_def),
            ("unknown agent type (plugin/managed): cannot verify", ev(subagent_type="mystery"), no_defs),
            ("claude-code-guide carries its own model (Haiku)", ev(subagent_type="claude-code-guide"), no_defs),
            ("statusline-setup carries its own model (Sonnet)", ev(subagent_type="statusline-setup"), no_defs),
            ("built-in Explore overridden by a definition with model haiku", ev(subagent_type="Explore"),
             defs({"Explore": "haiku"})),
            ("built-in general-purpose overridden by a definition with model opus",
             ev(subagent_type="general-purpose"), defs({"general-purpose": "opus"})),
            ("not the Agent tool", dict(ev(), tool_name="Bash"), no_defs),
            ("Task tool name is not Agent", dict(ev(), tool_name="Task"), no_defs),
            ("tool_input is not an object", dict(ev(), tool_input="x"), no_defs),
            ("no tool_input", {"tool_name": "Agent"}, no_defs),
        ]
        for label, e, lk in must_not:
            CASES_RUN[0] += 1
            rows = evaluate(e, lk)
            if rows:
                yield "M1 must NOT fire: %s (got %r)" % (label, rows)
        # ---- env var is step 3 of the resolution
        os.environ[ENV_MODEL] = "haiku"
        CASES_RUN[0] += 1
        if evaluate(ev(), no_defs):
            yield "M1 must NOT fire when CLAUDE_CODE_SUBAGENT_MODEL=haiku"
        os.environ[ENV_MODEL] = "inherit"
        CASES_RUN[0] += 1
        if len(evaluate(ev(), no_defs)) != 1:
            yield "M1 must fire when CLAUDE_CODE_SUBAGENT_MODEL=inherit"
        os.environ.pop(ENV_MODEL, None)
        # ---- row content and message
        CASES_RUN[0] += 1
        r = evaluate(ev(prompt="Model: Haiku. Edit it."), no_defs)[0]
        if r["brief_names"] != "haiku" or "names haiku" not in message_for(r) or "only the `model` parameter" not in message_for(r):
            yield "M1 message must note that the brief names a tier: %r" % (message_for(r),)
        CASES_RUN[0] += 1
        r = evaluate(ev(), no_defs)[0]
        m = message_for(r)
        if r["brief_names"] or "names" in m or not m.isascii() or "%" in m or "2026-10-21" not in m:
            yield "M1 plain message wrong (ascii/format/date): %r" % (m,)
        CASES_RUN[0] += 1
        r = evaluate(ev(subagent_type=None), no_defs)[0]
        if r["subagent_type"] != "(default)":
            yield "M1 row subagent_type for a missing type must be (default), got %r" % r["subagent_type"]
        CASES_RUN[0] += 1
        out = output_for(m)
        if set(out) != {"hookSpecificOutput"} or "permissionDecision" in out["hookSpecificOutput"] \
                or out["hookSpecificOutput"]["hookEventName"] != "PreToolUse":
            yield "M1 output must be additionalContext only, never a decision: %r" % (out,)
        # ---- frontmatter parser
        for label, text, want in (
                ("plain", "---\nname: client\nmodel: sonnet\n---\nbody model: opus\n", ("client", "sonnet")),
                ("crlf and quotes", "---\r\nname: \"x\"\r\nmodel: 'haiku'\r\n---\r\n", ("x", "haiku")),
                ("no model key", "---\nname: y\ndescription: z\n---\n", ("y", "")),
                ("no frontmatter", "model: opus\n", ("", "")),
                ("model only in body", "---\nname: y\n---\nmodel: opus\n", ("y", "")),
                ("empty", "", ("", "")),
                ("model before name, upper case key", "---\nModel: opus\nName: q\n---\n", ("q", "opus")),
                ("BOM-prefixed header", "﻿---\nname: b\nmodel: haiku\n---\n", ("b", "haiku")),
                ("BOM-prefixed header, CRLF", "﻿---\r\nname: b\r\nmodel: sonnet\r\n---\r\n", ("b", "sonnet"))):
            CASES_RUN[0] += 1
            if parse_frontmatter(text) != want:
                yield "M1 frontmatter %s: got %r want %r" % (label, parse_frontmatter(text), want)
        for label, text, want in (("header", "---\nname: a\n---\n", True),
                                  ("BOM header", "﻿---\nname: a\n---\n", True),
                                  ("no header, plain prose", "You are an agent.\nmodel: opus\n", False),
                                  ("header not on line 1", "\n---\nname: a\n---\n", False),
                                  ("empty", "", False)):
            CASES_RUN[0] += 1
            if has_header(text) is not want:
                yield "M1 has_header %s: want %r" % (label, want)
    finally:
        os.environ.pop(ENV_MODEL, None)
        if saved_env is not None:
            os.environ[ENV_MODEL] = saved_env
