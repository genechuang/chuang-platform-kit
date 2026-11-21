"""An alert catalog's shape and its lookups: what each system does, who is hurt when it fails, and what to do.

A host keeps its catalog as data (one dict a kind of system, keyed by the name the alert carries) and asks these
lookups for the entry an alert reads, so a message says in plain words what broke and what it costs. A system the
catalog does not list still gets an entry that names it, with the host's `unknown` words where a fact is missing:
an unknown is said as unknown, never guessed.

An entry's keys:

    name      what the system is, as a person says it
    broke     the effect when it fails, for the subject line ("a court may not be booked")
    who       who is affected: users, money, a page, or nobody
    todo      what to do: the by-hand fix, or "nothing, it retries at ..." when that is true
    next      when it runs again on its own (a job's or a schedule's)
    variants  (jobs) by the run's arguments, each overriding the keys above
    steps     (jobs) by step name, each with its own broke / who / todo
    queue, log  (dead letters) the host's replay queue's name and its log's address

The tables a host passes: `jobs` {job: entry}, `schedules` {id: entry} with `prefixes` {id prefix: entry} for the
per-item ones, `actions` {task action: entry}, `services` {service: entry} with `rules` ((service, pattern on the
error line, entry), ...) checked first, `dead_letters` {topic: entry}, `builds` {tag: entry} with '' the untagged one.
"""
import re

UNKNOWN = 'not in the catalog yet'
KEYS = ('name', 'broke', 'who', 'todo', 'next', 'variants', 'steps', 'queue', 'log')


def job_entry(jobs: dict, job: str, variant: str = '', unknown: str = UNKNOWN, twin_suffix: str = '-dev') -> dict:
    """A job's entry with its variant's keys over the job's own. A job named `<listed job><twin_suffix>` is that job's
    dev twin, which only a deploy's smoke runs: its entry says a release failed on its dev rung and was rolled back.
    A job not listed gets an entry that names it, `unknown` for who and what to do."""
    if twin_suffix and job.endswith(twin_suffix) and job[:-len(twin_suffix)] in jobs:
        base = job[:-len(twin_suffix)]
        return {'name': f"{jobs[base]['name']}, its dev twin {job} during a deploy",
                'broke': f'a release of {base} failed on its dev rung and was rolled back',
                'who': 'nobody, unless the production job started in the half minute it held the release; it is back on the image it had',
                'todo': 'Read the deploy run the line names: it says "rolled back" (or how to put it back by hand); fix and push again.',
                'next': 'the next push that deploys it'}
    entry = jobs.get(job) or {'name': f'the Cloud Run Job {job}', 'broke': f'{job} failed', 'who': unknown,
                              'todo': unknown, 'next': 'its next scheduled start'}
    out = {k: v for k, v in entry.items() if k != 'variants'}
    out.update((entry.get('variants') or {}).get(variant, {}))
    return out


def schedule_entry(schedules: dict, job_id: str, prefixes: dict = None, unknown: str = UNKNOWN) -> dict:
    """A scheduler job's entry by its id, else by the first of `prefixes` its id starts with, else one that names it."""
    if job_id in schedules:
        return schedules[job_id]
    for prefix, entry in (prefixes or {}).items():
        if job_id.startswith(prefix):
            return entry
    return {'name': f'the Cloud Scheduler job {job_id}', 'broke': f'the schedule {job_id} could not start its run',
            'who': unknown, 'todo': unknown}


def task_entry(actions: dict, action: str, unknown: str = UNKNOWN) -> dict:
    """A queued task's entry by the action its name begins with, else one that names the action."""
    return actions.get(action) or {'name': f'the game action {action or "(unnamed)"}',
                                   'broke': 'a game action could not start its job', 'who': unknown, 'todo': unknown}


def line_rule(rules, service: str, line: str):
    """The index of the first of `rules` ((service, pattern, entry), ...) for `service` whose pattern `line` matches,
    or None. A rule names the actual failure where the service's own entry guesses at the usual one."""
    for i, (svc, pattern, _entry) in enumerate(rules):
        if svc == service and line and re.search(pattern, line):
            return i
    return None


def service_entry(services: dict, service: str, line: str = '', rules=(), unknown: str = UNKNOWN) -> dict:
    """A service's entry: the rule the error line matches (line_rule), else the service's own, else one that names it."""
    i = line_rule(rules, service, line)
    if i is not None:
        return rules[i][2]
    return services.get(service) or {'name': f'the Cloud Run service {service}', 'broke': f'{service} logged an error',
                                     'who': unknown, 'todo': unknown}


def dead_letter_entry(dead_letters: dict, topic: str, unknown: str = UNKNOWN) -> dict:
    """A dead-letter topic's entry, else one that names the topic and says it is not catalogued."""
    return dead_letters.get(topic) or {'queue': '', 'name': f'the queue behind {topic or "an unnamed topic"}',
                                       'broke': 'a message was given up on', 'who': unknown, 'todo': unknown, 'log': ''}


def build_entry(builds: dict, tag: str) -> dict:
    """A build's entry by its first tag; an unknown tag reads as the untagged one (`builds['']`)."""
    return builds.get(tag) or builds['']
