"""A release's post-ship steps: the one-off jobs a shipped commit asks for in a `Post-Ship:` trailer, one a line,

    Post-Ship: <job> <its arguments>

read against the host's allow-list, {(job, (arg, ...)): what it does, in a reader's words}, so a commit can ask only
for what the host listed: a step not on it is kept, marked not allowed, for the notes to name. Each allowed step is
started and waited for to its end by `run`, the start and the status the host's to pass in (its job runner: on Cloud
Run, the host's starter and `execution_result` over the kit's google_rest), and the results worded for the notes.

    found = steps(commits, ALLOWED, script_of)      [{'job', 'args', 'sha', 'what', 'allowed'}]
    done = run(found, start, state)                 each step with its result, seconds and execution page
    lines(done)                                     one line a step for the release notes

Standard library only (`execution_result` imports the kit's google_rest when called).
"""
import re
import time

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


WAIT = 600     # seconds a step may run before it is called timed out, unless the host says
POLL = 10      # seconds between two reads of a running step's status


def run(found: list, start, state, clock=time.monotonic, sleep=time.sleep, wait: int = WAIT, poll: int = POLL) -> list:
    """Each step of `found` (steps()) started and waited for, in order: the step with `result` (passed, failed,
    refused, did not start (<why>), timed out), `seconds` and `url` (its execution's page). A step not allowed is
    refused and never started. `start(job, args)` is the host's job start, answering {'execution', 'url'} or raising;
    `state(job, execution)` its status read, 'running', 'passed' or 'failed' (a read that raises counts as running, one
    unread poll being no verdict); `clock` and `sleep` the test seams."""
    done = []
    for s in found:
        s = dict(s, result='refused', seconds=None, url='')
        if not s['allowed']:
            done.append(s)
            continue
        t0 = clock()
        try:
            got = start(s['job'], s['args'])
        except Exception as e:   # noqa: BLE001 - the job runner's refusal or the network: said in the notes
            done.append(dict(s, result=f'did not start ({type(e).__name__}: {str(e)[:120]})'))
            continue
        s['url'] = got.get('url') or ''
        ex = got.get('execution') or ''
        result = 'timed out'
        while ex and clock() - t0 < wait:
            try:
                now = state(s['job'], ex)
            except Exception:   # noqa: BLE001 - one unread poll is no verdict
                now = 'running'
            if now != 'running':
                result = now
                break
            sleep(poll)
        done.append(dict(s, result=result if ex else 'did not start (no execution named)', seconds=round(clock() - t0)))
    return done


def execution_result(project: str, region: str, job: str, execution: str, token, opener=None, timeout: int = 15) -> str:
    """'running', 'passed' or 'failed' for one Cloud Run Job execution, a `state` for run(): the kit's
    google_rest.get_execution and execution_state (succeeded is passed, failed or cancelled is failed)."""
    from chuang_platform_kit import google_rest
    doc = google_rest.get_execution(project, region, job, execution, token, opener=opener, timeout=timeout)
    got = google_rest.execution_state(doc)
    return got if got == 'running' else ('passed' if got == 'succeeded' else 'failed')


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
