#!/usr/bin/env python3
"""A session that says it is starting the next piece of work now does not end its turn without starting it (SMADPickleBot
#1802; Gene, 11/18/26 4:01 AM PT: "you last respond 'I'm done with ticket X and now will do ticket Y'. And then proceed
to do nothing, until I prompt you with 'What's going on with Y??'"). Run by stamp-reply.py once the stamp and the
evidence check pass, so it rides the Stop hook every session already has and reaches a running session through boot.sh.

The final reply is the last thing the turn says. A sentence in it that promises work starting now ("now I'll start
#1793", "next I'll build the gate", "moving on to #1801", "starting #1801 now", "I'm on #1801 next") with no condition
in the same sentence ("when it lands", "once you say yes", "after #1794 ships", "until", "if", "on your word", "waits",
"later", "tomorrow") is a promise the ending turn breaks, so the turn is held once: start it, its first tool call in
this turn, or send one line saying what it waits on. A reply that only reports or waits on something named is never
held, and the held turn's second stop always ends (stamp-reply's stop_hook_active). Standard library only.

Since 1.32.0 (SMADPickleBot #1816; Gene, 11/18/26 7:39 AM PT: "Do haiku check if it means you keep your words forever",
after the regex let "I'm filing it as a ticket now and will build it next" end a turn): a reply the regex lets go whose
closing paragraphs carry a candidate word (CANDIDATE) is read by Haiku, one headless turn with no tools, settings, MCP
or session (the listener daemon's wake flags), the text on stdin, bounded at JUDGE_LIMIT. It answers `PROMISE: <the
sentence>` or `NONE`. Anything else, a failure or the bound passed lets the reply go: the gate never traps a session.
PROMISE_JUDGE_OFF=1 turns the judge off.
"""
import os
import re
import shutil
import subprocess
import tempfile

VERBS = (r'(?:start(?:ing)?|begin(?:ning)?|build(?:ing)?|do(?:ing)?|work(?:ing)? on|pick(?:ing)? up|tak(?:e|ing) (?:on|up)|'
         r'tackl(?:e|ing)|fix(?:ing)?|implement(?:ing)?|writ(?:e|ing)|mov(?:e|ing) on to|get(?:ting)? (?:on|to)|'
         r'fil(?:e|ing)|send(?:ing)?|run(?:ning)?|ship(?:ping)?|open(?:ing)?|mak(?:e|ing))')
PROMISES = [
    # 1.31.1 (SMADPickleBot #1815): "I'm filing it now and will build it next" slipped: no "file" verb, and "and will"
    # has no "I" before it.
    re.compile(rf"\band\s+(?:I(?:'ll| will)|will)\s+{VERBS}\b[^.\n]*\b(?:now|next)\b", re.I),
    re.compile(rf"\b(?:now|next),?\s+I(?:'ll| will| am going to|'m going to)?\s+{VERBS}\b", re.I),
    re.compile(r"\b(?:I'm|I am) (?:now )?(?:starting|beginning|building|picking up) (?:on )?#\d+", re.I),
    re.compile(rf"\bI(?:'ll| will)\s+(?:now|next)\s+{VERBS}\b", re.I),
    re.compile(rf"\bI(?:'ll| will)\s+{VERBS}\b[^.\n]*\b(?:now|next)\b", re.I),
    re.compile(r"\b(?:now\s+)?moving on to\b", re.I),
    re.compile(rf"\b(?:I'm|I am)\s+{VERBS}\b[^.\n]*\b(?:now|next)\b", re.I),
    re.compile(r"\b(?:I'm|I am) on #?\d+ next\b", re.I),
    re.compile(r"\bstarting (?:#\d+|on #?\d+|it|that|the \w+)(?: \w+)? now\b", re.I),
    re.compile(r"\bnext (?:up|for me)(?: is|:)\s", re.I),
    re.compile(r"\b(?:that's|that is|this is|it's|is)\s+(?:my\s+)?next(?: step| thing| up)?\b", re.I),
    re.compile(r"\bmy next (?:step|move|task|ticket|card)\b", re.I),
    re.compile(rf"\bI(?:'m| am) (?:about to|going to)\s+{VERBS}\b", re.I),
]
CONDITION = re.compile(
    r"\b(?:when|whenever|once|after|until|unless|if|on your|your (?:word|yes|go|ok)|wait(?:s|ing)?|later|tomorrow|"
    r"next (?:ship|release|push|word|prompt|turn|session)|in the background|as soon as)\b", re.I)
SENTENCE = re.compile(r'[^.!?\n]+[.!?]?')


def promise(reply: str) -> str:
    """The first sentence of `reply` that promises work starting now with no condition, '' when there is none."""
    # Quoted words are someone's words, never this reply's promise (1.30.3: the reply describing the gate quoted its
    # own examples and was held).
    unquoted = re.sub(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`', '""', reply or '')
    for raw in SENTENCE.findall(unquoted):
        s = raw.strip()
        if not s or s.startswith(('—', '>', '`')):
            continue
        if any(p.search(s) for p in PROMISES) and not CONDITION.search(s):
            return s[:200]
    return ''


CANDIDATE = re.compile(r"\b(?:now|next|then|I'll|I will|will|going to|about to|starting|moving on|on ?to|after this|"
                       r"right away|at once|let me)\b|\b\w+ing\b[^.\n]{0,40}#\d+", re.I)
JUDGE_LIMIT = 20   # seconds; a one-turn Haiku answer is a few
JUDGE_CHARS = 1200   # the closing paragraphs: where an ending turn's promise sits
JUDGE_FLAGS = ('--model', 'haiku', '--max-turns', '1', '--output-format', 'text', '--strict-mcp-config',
               '--disable-slash-commands', '--no-session-persistence', '--setting-sources', '')
JUDGE_PROMPT = (
    "You check the closing words of an AI coding session's reply, just before its turn ends. Decide whether it promises "
    "to do some work starting now, in this turn (for example \"I'm filing it now\", \"building it next\", \"now I'll "
    "start #12\", \"on to #12\"). These are NOT promises: work it says is done; work that waits on something it names "
    "(a yes, a ship, a time, another session, a run finishing); a question or offer to the user (\"should I build it?\"); "
    "what another session will do; anything quoted as an example. Answer with exactly one line: PROMISE: <the promising "
    "sentence, word for word> or NONE.\n\nThe closing words:\n")


def closing(reply: str) -> str:
    """The reply's last JUDGE_CHARS, without its closing stamp line (`— 11/18/26 ... PT`)."""
    lines = [ln for ln in (reply or '').strip().splitlines() if not ln.strip().startswith('—')]
    return '\n'.join(lines)[-JUDGE_CHARS:]


def judge(text: str, run=None) -> str:
    """Haiku's reading of `text`: the promising sentence, or '' (none, or no answer). `run` is a test's fake for
    subprocess.run."""
    if os.environ.get('PROMISE_JUDGE_OFF') == '1':
        return ''
    claude = shutil.which('claude')
    if not claude and run is None:
        return ''
    try:
        cwd = os.path.join(tempfile.gettempdir(), 'promise-judge')   # empty: no CLAUDE.md, no project settings
        os.makedirs(cwd, exist_ok=True)
        flags = {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)} if os.name == 'nt' else {}
        r = (run or subprocess.run)([claude or 'claude', '-p', *JUDGE_FLAGS], input=JUDGE_PROMPT + text, cwd=cwd,
                                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                                    timeout=JUDGE_LIMIT, env={**os.environ, 'PROMISE_JUDGE_OFF': '1'}, **flags)
    except Exception:  # noqa: BLE001 - never trap a session
        return ''
    if r.returncode != 0:
        return ''
    line = next((ln.strip() for ln in (r.stdout or '').splitlines() if ln.strip()), '')
    m = re.match(r'PROMISE:\s*(.+)', line, re.I)
    said = m.group(1).strip().strip('"')[:200] if m else ''
    # The model's yes is narrowed by the regex's own conditions: a sentence that names what it waits on ("once the smoke
    # lands") is a wait, whatever Haiku read (the live check of 11/18/26 7:42 AM PT held exactly that one).
    return '' if said and WAITS.search(said) else said


# CONDITION less a bare "after": "right after this" is a promise, "after #1794 ships" a wait.
WAITS = re.compile(CONDITION.pattern.replace('after|', r'after (?!this\b|that\b)|'), re.I)


def check(reply: str, run=None) -> dict:
    """The Stop hook's block when the final reply promises work starting now, else None: the regex first, then Haiku
    for a candidate the regex let go."""
    said = promise(reply)
    if not said:
        tail = closing(re.sub(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`', '""', reply or ''))
        if CANDIDATE.search(tail):
            said = judge(tail, run)
    if not said:
        return None
    return {'decision': 'block',
            'reason': f'Your reply promises work starting now ("{said}") and the turn is ending, so it would not happen '
                      f'(Gene, 11/18/26: "you last respond \'I\'m done with ticket X and now will do ticket Y\'. And then '
                      f'proceed to do nothing"). Start it now: its first tool call in this turn. If it truly waits on '
                      f'something, send one short line naming what it waits on instead (do not repeat the reply).'}
