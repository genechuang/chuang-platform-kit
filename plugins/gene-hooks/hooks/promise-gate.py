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
"""
import re

VERBS = (r'(?:start(?:ing)?|begin(?:ning)?|build(?:ing)?|do(?:ing)?|work(?:ing)? on|pick(?:ing)? up|tak(?:e|ing) (?:on|up)|'
         r'tackl(?:e|ing)|fix(?:ing)?|implement(?:ing)?|writ(?:e|ing)|mov(?:e|ing) on to|get(?:ting)? (?:on|to))')
PROMISES = [
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


def check(reply: str) -> dict:
    """The Stop hook's block when the final reply promises work starting now, else None."""
    said = promise(reply)
    if not said:
        return None
    return {'decision': 'block',
            'reason': f'Your reply promises work starting now ("{said}") and the turn is ending, so it would not happen '
                      f'(Gene, 11/18/26: "you last respond \'I\'m done with ticket X and now will do ticket Y\'. And then '
                      f'proceed to do nothing"). Start it now: its first tool call in this turn. If it truly waits on '
                      f'something, send one short line naming what it waits on instead (do not repeat the reply).'}
