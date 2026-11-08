"""A release's post-ship steps: the one-off jobs a shipped commit asks for in a `Post-Ship:` trailer, one a line,

    Post-Ship: <job> <its arguments>

read against the host's allow-list, {(job, (arg, ...)): what it does, in a reader's words}, so a commit can ask only
for what the host listed: a step not on it is kept, marked not allowed, for the notes to name. Running the steps (the
host's job runner and its wait) is the host's; this module reads them and words the results.

    found = steps(commits, ALLOWED, script_of)      [{'job', 'args', 'sha', 'what', 'allowed'}]
    lines(done)                                     one line a step for the release notes

Standard library only.
"""
import re

TRAILER = re.compile(r'^\s*Post-Ship:\s*(\S+)((?:\s+\S+)*)\s*$', re.I)
ICON = {'passed': '✅', 'failed': '🔴', 'timed out': '⏳', 'refused': '🚫'}
NOT_ALLOWED = 'not on the allow-list, so not run'


def steps(commits: list, allowed: dict, script_of=None) -> list:
    """The steps the commits ({'sha', 'message'}) ask for, in commit order, each once: [{'job', 'args', 'sha', 'what',
    'allowed'}]. `script_of(job)` is the arguments the host's runner adds before a job's own (its script), dropped
    when a trailer repeats them."""
    out, seen = [], set()
    for c in commits or []:
        for line in (c.get('message') or '').splitlines():
            m = TRAILER.match(line)
            if not m:
                continue
            job, args = m.group(1), tuple(m.group(2).split())
            script = tuple(script_of(job) or ()) if script_of else ()
            if script and args[:len(script)] == script:
                args = args[len(script):]
            if (job, args) in seen:
                continue
            seen.add((job, args))
            what = allowed.get((job, args))
            out.append({'job': job, 'args': list(args), 'sha': c.get('sha') or '', 'what': what or '',
                        'allowed': what is not None})
    return out


def label(step: dict) -> str:
    """'<job> <arg> ...'."""
    return ' '.join([step['job']] + list(step['args']))


def lines(done: list, markdown: bool = False, not_allowed: str = NOT_ALLOWED) -> list:
    """One line a run step ({..., 'result', 'seconds', 'url'}) for the notes: '• <job> <args>: ✅ passed in 74 s, <what>
    (<sha8>)', its label linked to `url` (a markdown link, else Slack's <url|text>); a step off the allow-list says
    `not_allowed` (by its `allowed` flag: an allowed step with no words says none)."""
    out = []
    for s in done:
        icon = ICON.get(s['result'], '🔴')
        took = f" in {s['seconds']} s" if s.get('seconds') is not None else ''
        what = s['what'] if s.get('allowed', bool(s['what'])) else not_allowed
        what = f', {what}' if what else ''
        name = f"`{label(s)}`"
        if s.get('url'):
            name = f"[{name}]({s['url']})" if markdown else f"<{s['url']}|{label(s)}>"
        out.append(f"{'-' if markdown else '•'} {name}: {icon} {s['result']}{took}{what} ({s['sha'][:8]})")
    return out


def failed(done: list) -> list:
    """The steps that did not pass."""
    return [s for s in done if s['result'] != 'passed']
