#!/usr/bin/env python3
"""A push to a project's main owes a release profile, and the session that pushed cannot end a turn without it
(Gene, 10/22/26 1:28 PM PT: "build the claude-shared hook that refuses to end a turn after a push to main until the
release profile is reported", after the CI/CD release 49cf7b8b went live and its profile waited to be asked for).

    release-profile-gate.py post     # PostToolUse (Bash, Read, Agent): a push's own line `<old>..<new>  X -> main` to
                                     # the project's GitHub repository in the tool's output records <new> as owed;
                                     # an Agent started with a prompt naming "Release profile" and the commit pays it;
                                     # a Bash call running `ship.py ... --push` owes the release its stages file names
    release-profile-gate.py stop     # Stop: the owing session's turn is held open (decision: block) once the commit
                                     # is promoted to production (a finished Promote run) with no run still going and
                                     # the profile is neither delegated nor reported; a reply naming "Release profile"
                                     # and the commit pays it too
    release-profile-gate.py status   # what is owed, by hand

The way to pay is an agent, never the session's own turn (Gene, 10/22/26 1:30 PM PT: "I don't want you blocked on
the profile/optimize/report though - return immediately after deployment is done and then fire off an agent to do the
needful"): once the deploy is done the session reports it, starts a background agent to profile, optimize and report,
and its turn ends at once. Why the runs first: the push, the Deploy watch and the promote run in the background, and a
turn ends while they do ("I'll report when Deploy finishes"); holding it then would trap the session with nothing to
do. The owed list is per session (the one whose tool printed the push), lives
in OWED under the user's profile, and lapses after LAPSE_HOURS. The profile itself is claude-shared CLAUDE.md
"Every stage is timed; the release ends with a profile against the last one".

A hook blocks only on purpose: any error exits 0, it holds a turn at most MAX_BLOCKS times for one commit (then
says the profile is still owed and lets go), and it reads only the transcript's tail. Standard library only.

In this plugin since SMADPickleBot #1810 (claude-shared's hooks/release-profile-gate.py until then, wired by hand in a
box's own settings, with one project's repository, folders and command spelled in it). The project's facts are now the
`[release]` table of its committed `.claude/gene-hooks.toml` (project_config.py), read for the folder the hook event
names (`cwd`, else CLAUDE_PROJECT_DIR, else the working folder):

    repo            the GitHub repository, "owner/name" (REPO)
    captain_folder  the release captain's clone, its folder's name in lower case (CAPTAIN_FOLDER)
    ship_logs       the folder under TEMP holding the ship's stages files (SHIP_DIR)
    tf_lock         the store chain's lock file under TEMP (TF_LOCK)
    profile_command the command that prints a release's numbers, given the commit (PROFILE_COMMAND)

A project with no `[release]` table, or no `repo` in it, has no gate: every mode does nothing and says nothing.
"""
import json
import os
import re
import subprocess
import sys
import time

OWED = os.path.join(os.path.expanduser('~'), '.claude', 'release-profile-owed.json')
TEMP = os.environ.get('TEMP') or os.environ.get('TMPDIR') or '/tmp'
# The project's facts, '' until configure() reads them from its `[release]` table; a suite sets them itself.
REPO = ''              # `repo`: "owner/name" on GitHub
SHIP_DIR = ''          # `ship_logs`, under TEMP: the ship's stages-<sha>.json files
TF_LOCK = ''           # `tf_lock`, under TEMP: the store chain's lock
CAPTAIN_FOLDER = ''    # `captain_folder`: the release captain's session runs in the repo's own clone
PROFILE_COMMAND = ''   # `profile_command`: printed with the commit when a profile is owed
LAPSE_HOURS = 12
MAX_BLOCKS = 3
TAIL = 4 * 1024 * 1024   # 512 KB was 295 lines of a long session, an hour of work (10/25/26)
PUSHED = re.compile(r'([0-9a-f]{7,40})\.\.([0-9a-f]{7,40})\s+\S+\s+->\s+main\b')
# A ship (the project's ship.py --push) pushes inside its own process and prints neither git
# line, so the gate was blind to every ship from 10/24/26 to 10/25/26 7:35 AM PT (Gene: "Noting misses in memory is
# pretty much useless ... enforce with code/hook"): its own lines name the release instead.
SHIPPED = (re.compile(r'desktop-smoke\.py profile-wait ([0-9a-f]{40})\b'),
           re.compile(r'^(?:dock|hopper): moved to ([0-9a-f]{7,40}) with main\b', re.M))   # `hopper` until 10/26/26
# Python running ship.py, never a mention of it (a commit message naming `ship.py ... --push` was owed as a ship).
SHIP_CALL = re.compile(r'\bpy(?:thon[0-9.]*)?(?:\.exe)?\s+(?:-\S+\s+)*\S*\bship\.py\b[^\n`]*--push\b')
SHIP_START = 300   # seconds from the ship call to its stages file's start, at most
# A word's chain (SMADPickleBot #1242; Gene, 11/9/26 1:26 AM PT: "Why the heck would you not profile the entire ship
# apps when I issued a ship apps???"): "ship apps", "ship tf" and "ship play" run tf_pipe, whose ship's stages file says
# `source: word`, and the release is the whole chain, from the word to both stores' ends. Its profile is due once the
# chain has ended (its record for the sha in tf-history.jsonl, or no tf_pipe lock left) and is paid only by a report
# with the chain's end-to-end row (`word to`) or an agent started after that end and asked for the whole chain. On
# 11/9/26 a ship-only profile of 514dc30, started 12 minutes before its chain ended, paid it.
CHAIN_ROW = re.compile(r'\bword to\b', re.I)
CHAIN_ASK = re.compile(r'\bword to\b|\bend to end\b|\bend-to-end\b', re.I)


def configure(cwd: str) -> bool:
    """Read the project's `[release]` table for the folder `cwd` into REPO, SHIP_DIR, TF_LOCK, CAPTAIN_FOLDER and
    PROFILE_COMMAND; False, with nothing set, for a project that has no table or names no `repo`."""
    global REPO, SHIP_DIR, TF_LOCK, CAPTAIN_FOLDER, PROFILE_COMMAND
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import project_config
        cfg = project_config.table(cwd, 'release')[1]
    except Exception:   # noqa: BLE001 - no config reader beside this file: no gate
        return False

    def text(key: str) -> str:
        got = cfg.get(key)
        return got.strip() if isinstance(got, str) else ''

    if not text('repo'):
        return False
    REPO = text('repo')
    SHIP_DIR = os.path.join(TEMP, text('ship_logs')) if text('ship_logs') else ''
    TF_LOCK = os.path.join(TEMP, text('tf_lock')) if text('tf_lock') else ''
    CAPTAIN_FOLDER = text('captain_folder').lower()
    PROFILE_COMMAND = text('profile_command')
    return True


def _target():
    """The line a push to REPO prints (`To https://github.com/<REPO>.git`), as a pattern; None with no REPO."""
    return re.compile(r'To\s+\S*github\.com[/:]' + re.escape(REPO) + r'(?:\.git)?\s*$', re.M) if REPO else None


def _exists(path: str) -> bool:
    """Whether a named file is there; a project that names none ('') has none."""
    return bool(path) and os.path.exists(path)


def _stages(sha: str, ship_dir: str = None) -> dict:
    try:
        with open(os.path.join(ship_dir or SHIP_DIR, f'stages-{sha}.json'), encoding='utf-8') as f:
            got = json.load(f)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _in_history(sha: str, ship_dir: str = None) -> bool:
    """Whether tf_pipe's history holds a record for the sha: a store chain shipped it and has ended."""
    try:
        with open(os.path.join(ship_dir or SHIP_DIR, 'tf-history.jsonl'), encoding='utf-8') as f:
            for line in f:
                try:
                    if json.loads(line).get('sha', '').startswith(sha[:12]):
                        return True
                except (ValueError, AttributeError):
                    continue
    except OSError:
        pass
    return False


def chain(sha: str, ship_dir: str = None, lock: str = None) -> bool:
    """Whether a store word's chain (tf_pipe: "ship apps", "ship tf", "ship play") shipped this release: its stages
    file says `source: word` AND tf_pipe is its chain, its record in tf-history.jsonl or its lock still held. `source:
    word` alone is no chain since SMADPickleBot #1659 (11/16/26): Gene's plain "ship" runs ship_card.py word, and
    ship.py writes 'word' for any ship with no other source, so on 11/18/26 three plain ships were held for a "word
    to" row no plain ship has (SMADPickleBot #1822)."""
    if _stages(sha, ship_dir).get('source') != 'word':
        return False
    return _in_history(sha, ship_dir) or _exists(lock or TF_LOCK)


def chain_done(sha: str, ship_dir: str = None, lock: str = None) -> bool:
    """Whether the chain of a word's release has ended: tf_pipe's record for the sha in its history, or no lock."""
    return _in_history(sha, ship_dir) or not _exists(lock or TF_LOCK)


def load(path=None) -> dict:
    path = path or OWED
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(owed: dict, path=None) -> None:
    path = path or OWED
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(owed, f, indent=1)
    os.replace(tmp, path)


def response_text(event: dict) -> str:
    """Everything a tool printed, whatever shape its response has (Bash's stdout/stderr, Read's file text)."""
    r = event.get('tool_response')
    if isinstance(r, str):
        return r
    if isinstance(r, dict):
        parts = []
        for k in ('stdout', 'stderr', 'output', 'content', 'text'):
            v = r.get(k)
            if isinstance(v, str):
                parts.append(v)
        f = r.get('file')
        if isinstance(f, dict) and isinstance(f.get('content'), str):
            parts.append(f['content'])
        return '\n'.join(parts) if parts else json.dumps(r)
    return json.dumps(r) if r is not None else ''


def pushes(text: str) -> list:
    """The new main heads a push to REPO printed (`To https://github.com/<REPO>.git` then `a..b  X -> main`)."""
    out = []
    target_line = _target()
    for target in (target_line.finditer(text) if target_line else ()):
        block = text[target.end():target.end() + 400]
        m = PUSHED.search(block)
        if m:
            out.append(m.group(2))
    for pattern in SHIPPED:   # a ship's own lines (10/25/26)
        out += [m.group(1) for m in pattern.finditer(text) if m.group(1) not in out]
    return out


def shipped(ship_dir: str = None, since: float = 0.0) -> list:
    """(started, sha) of every ship that pushed, from its stages file (`stages-<sha>.json` in SHIP_DIR, "pushed": true),
    started at or after `since`; [] when there is none."""
    ship_dir = ship_dir or SHIP_DIR
    if not os.path.isdir(ship_dir):
        return []
    out = []
    for name in os.listdir(ship_dir):
        m = re.fullmatch(r'stages-([0-9a-f]{40})\.json', name)
        if not m:
            continue
        try:
            with open(os.path.join(ship_dir, name), encoding='utf-8') as f:
                stages = json.load(f)
        except (OSError, ValueError):
            continue
        started = float(stages.get('started') or 0)
        if stages.get('pushed') and started >= since:
            out.append((started, m.group(1)))
    return out


def ship_call(event: dict, owed: dict, now=None) -> str:
    """A Bash call running `ship.py ... --push` is a ship this session made: kept as `ship@<time>` until the Stop hook
    finds its stages file. The call itself, never its output, so a ship in the background is owed from the moment it
    starts (10/25/26: the transcript's tail no longer reached the call of a ship made an hour before). '' otherwise."""
    if event.get('tool_name') != 'Bash' or not SHIP_CALL.search(str((event.get('tool_input') or {}).get('command') or '')):
        return ''
    now = now or time.time()
    key = f'ship@{now:.0f}'
    owed[key] = {'session': event.get('session_id', ''), 'cwd': event.get('cwd', ''), 'at': now, 'blocks': 0,
                 'paid': False, 'pending': True}
    return key


def ships_in(lines: list, ship_dir: str = None, since: float = 0.0) -> list:
    """The releases this session's own ships made, from its transcript: each Bash call running `ship.py ... --push`,
    matched to the ship's stages file (started at or after the call), so a background ship whose output the session
    never read is still owed. The fallback for a call made before ship_call was installed. [] when nothing matches."""
    calls = []
    for line in lines:
        if 'ship.py' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        content = (entry.get('message') or {}).get('content')
        if entry.get('type') != 'assistant' or not isinstance(content, list):
            continue
        for c in content:
            if isinstance(c, dict) and c.get('type') == 'tool_use' and SHIP_CALL.search(str((c.get('input') or {}).get('command') or '')):
                calls.append(_epoch(entry.get('timestamp')))
    out = [(s, sha) for s, sha in shipped(ship_dir, since) if any(s >= t - 60 for t in calls if t)] if calls else []
    # The newest ship alone: its report compares it with the one before, so an older unreported ship is not held over.
    return [max(out)[1]] if out else []


def _epoch(stamp) -> float:
    """A transcript entry's ISO timestamp as epoch seconds; 0 when it has none."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(stamp).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _started(sha: str, ship_dir: str = None) -> float:
    """When the ship of `sha` (full or a prefix) started, from its stages file; 0 when it has none."""
    ship_dir = ship_dir or SHIP_DIR
    try:
        names = os.listdir(ship_dir)
    except OSError:
        return 0.0
    for name in names:
        m = re.fullmatch(r'stages-([0-9a-f]{40})\.json', name)
        if m and (m.group(1).startswith(sha) or sha.startswith(m.group(1))):
            return float(_stages(m.group(1), ship_dir).get('started') or 0)
    return 0.0


def record(event: dict, owed: dict, now=None, ship_dir: str = None) -> list:
    """Each new main head in the tool's output, owed by this session unless already known; the ones added."""
    now = now or time.time()
    added = []
    for sha in pushes(response_text(event)):
        if any(k.startswith(sha) or sha.startswith(k) for k in owed):
            continue
        # An old ship's lines read back from a log are no release of this turn (SMADPickleBot #1664: a log that still
        # held a 10/28/26 ship's lines was printed on 11/16/26 and that ship's profile was owed again): a sha whose
        # stages file started more than LAPSE_HOURS ago is skipped; one with no stages file is owed as before.
        started = _started(sha, ship_dir)
        if started and now - started > LAPSE_HOURS * 3600:
            continue
        owed[sha] = {'session': event.get('session_id', ''), 'cwd': event.get('cwd', ''), 'at': now, 'blocks': 0,
                     'paid': False}
        added.append(sha)
    return added


def delegated(event: dict, owed: dict) -> list:
    """The commits this session owes that an Agent call's prompt names with "Release profile"; marked paid."""
    if event.get('tool_name') != 'Agent':
        return []
    prompt = str((event.get('tool_input') or {}).get('prompt') or '')
    session = event.get('session_id', '')
    out = []
    for sha, o in owed.items():
        if o.get('session') == session and not o.get('paid') and not o.get('pending') and delegated_paid(prompt, sha):
            o['paid'], o['by'] = True, 'agent'
            out.append(sha)
    return out


def runs_done(sha: str, cwd: str = '', run=subprocess.run) -> bool:
    """True when the commit is in production and GitHub is done with it: a Promote run of the commit has finished and
    no run of it is still going (Gene, 10/22/26 2:18 PM PT: profile and optimize "AFTER deployment to prod ... then you
    won't have as much CPU conflict with dev smoke test suite"; the dev deploy alone owes nothing yet). False while
    any runs, before the promote, or on doubt."""
    try:
        full = sha
        if len(sha) < 40:
            got = run(['git', 'rev-parse', sha], cwd=cwd or None, capture_output=True, text=True, timeout=10)
            full = got.stdout.strip() if got.returncode == 0 and got.stdout.strip() else sha
        got = run(['gh', 'run', 'list', '--repo', REPO, '--commit', full, '--json', 'name,status', '--limit', '50'],
                  capture_output=True, text=True, timeout=20, shell=(os.name == 'nt'))
        runs = json.loads(got.stdout or '[]') if got.returncode == 0 else []
        promoted = any(r.get('name') == 'Promote' and r.get('status') == 'completed' for r in runs)
        if not promoted:
            # LADDER full (Gene, 10/22/26 3:10 PM PT, "set LADDER back to full"): the push's own Deploy run reaches
            # production, with no promote to wait for. Only a `dev` hold waits for a Promote run.
            got = run(['gh', 'variable', 'get', 'LADDER', '--repo', REPO], capture_output=True, text=True, timeout=20,
                      shell=(os.name == 'nt'))
            promoted = got.returncode == 0 and got.stdout.strip() != 'dev'
        return promoted and bool(runs) and all(r.get('status') == 'completed' for r in runs)
    except Exception:   # noqa: BLE001 - no gh, no network: never hold a turn on doubt
        return False


def turn_texts(lines: list) -> str:
    """The assistant's text since the turn's typed prompt, from transcript JSONL lines (newest last)."""
    texts = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        msg = entry.get('message') or {}
        content = msg.get('content')
        if entry.get('type') == 'user' and not entry.get('isMeta'):
            if isinstance(content, str) or (isinstance(content, list) and any(
                    isinstance(c, dict) and c.get('type') == 'text' for c in content)):
                break
        if entry.get('type') == 'assistant' and isinstance(content, list):
            texts.extend(c.get('text', '') for c in content if isinstance(c, dict) and c.get('type') == 'text')
    return '\n'.join(reversed(texts))


def all_texts(lines: list) -> str:
    """Every assistant text in the transcript lines given (not only this turn's)."""
    texts = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        content = (entry.get('message') or {}).get('content')
        if entry.get('type') == 'assistant' and isinstance(content, list):
            texts.extend(c.get('text', '') for c in content if isinstance(c, dict) and c.get('type') == 'text')
    return '\n'.join(texts)


def change_order(sha: str, ship_dir: str = None):
    """The ship's change order number from its stages file (ship.py writes `change_order` since SMADPickleBot #823), or
    None when the file or the number is missing."""
    try:
        with open(os.path.join(ship_dir or SHIP_DIR, f'stages-{sha}.json'), encoding='utf-8') as f:
            number = json.load(f).get('change_order')
        return int(number) if number else None
    except (OSError, ValueError, TypeError):
        return None


def names_release(text: str, sha: str, ship_dir: str = None) -> bool:
    """`text` names the release: the commit's first seven characters, or its change order as 'CO #847' (SMADPickleBot
    #848: since #843 a report names a release by its change order, and a report naming only 'CO #847' was held and
    written twice on 11/2/26)."""
    low = text.lower()
    if sha[:7].lower() in low:
        return True
    number = change_order(sha, ship_dir)
    return bool(number) and bool(re.search(rf'\bco\s*#{number}\b', low))


def paid(text: str, sha: str, ship_dir: str = None) -> bool:
    """The report itself (claude-shared CLAUDE.md, "The profile report Gene gets, after EVERY release"): the release
    named (names_release), a times table (a `|` row), the critical path and the cuts. Naming "release profile" and the
    commit is not enough (10/25/26: four ships went by with a live line and one-line timings)."""
    low = text.lower()
    return (names_release(text, sha, ship_dir) and '|' in text and 'critical path' in low
            and bool(re.search(r'\bcuts?\b', low))
            and (not chain(sha, ship_dir) or bool(CHAIN_ROW.search(text))))   # #1242: a word's report has its end row


def delegated_paid(prompt: str, sha: str, ship_dir: str = None, lock: str = None) -> bool:
    """An Agent's prompt that hands the profile over: "Release profile" and the release named; for a word's chain
    (#1242), only once the chain has ended and when the prompt asks for the whole chain (`word to`, `end to end`)."""
    if not (re.search(r'release profile', prompt, re.I) and names_release(prompt, sha, ship_dir)):
        return False
    return not chain(sha, ship_dir) or (chain_done(sha, ship_dir, lock) and bool(CHAIN_ASK.search(prompt)))


def is_captain(event: dict) -> bool:
    """Whether the Stop is the release captain's session: its folder is the repo's own clone (a folder named
    CAPTAIN_FOLDER, on any drive), or RELEASE_CAPTAIN=1 says so; a worktree of it under another name is another
    session's, and a project that names no captain folder has no captain by folder."""
    if os.environ.get('RELEASE_CAPTAIN') == '1':
        return True
    folder = os.path.basename(str(event.get('cwd') or '').replace('\\', '/').rstrip('/')).lower()
    return bool(CAPTAIN_FOLDER) and folder == CAPTAIN_FOLDER.lower()


def decide(event: dict, owed: dict, read_tail, done=runs_done, now=None, ship_dir: str = None) -> dict:
    """The Stop hook's answer, or None to let the turn end; `owed` is updated in place."""
    now = now or time.time()
    session = event.get('session_id', '')
    for sha in list(owed):
        if now - owed[sha].get('at', now) > LAPSE_HOURS * 3600:
            del owed[sha]
    lines = read_tail(event.get('transcript_path') or '')
    found = []
    # A ship call this session made (ship_call) becomes its release once the ship's stages file says it pushed: the
    # first ship that started within SHIP_START of the call, so another session's later ship is never taken for it.
    for key in [k for k, o in owed.items() if o.get('pending') and o.get('session') == session]:
        at = owed[key].get('at', now)
        ships = [x for x in shipped(ship_dir, at - 60) if x[0] <= at + SHIP_START]
        if ships:
            entry = owed.pop(key)
            found.append((min(ships)[1], entry))
    # The fallback: a ship call in the transcript's tail, made before ship_call was installed (10/25/26).
    found += [(sha, None) for sha in ships_in(lines, ship_dir, since=now - LAPSE_HOURS * 3600)]
    for sha, entry in found:
        if not any(k.startswith(sha[:8]) or sha.startswith(k) for k in owed):
            # Found for the first time: a report anywhere in the transcript's tail already paid it.
            owed[sha] = {'session': session, 'cwd': (entry or {}).get('cwd', event.get('cwd', '')),
                         'at': (entry or {}).get('at', now), 'blocks': 0, 'paid': paid(all_texts(lines), sha, ship_dir)}
    # SMADPickleBot #1184: a ship no session made (the listener daemon's SHIP card drag, a word it shipped, the release
    # console's button) is owed to the release captain, whose session's folder is the repo's own clone: every pushed
    # ship of the lapse window that nobody owes, and that no other session's unresolved ship call can claim, is the
    # captain's at its next Stop (11/8/26 9:35 AM PT: CO #1174 shipped while every session was out of tokens and its
    # profile was never given).
    if is_captain(event):
        calls = [o.get('at', 0) for o in owed.values() if o.get('pending') and o.get('session') != session]
        for started, sha in shipped(ship_dir, now - LAPSE_HOURS * 3600):
            if any(k.startswith(sha[:8]) or sha.startswith(k) for k in owed):
                continue
            if any(at - 60 <= started <= at + SHIP_START for at in calls):
                continue   # another session's own ship, resolved at its own Stop
            owed[sha] = {'session': session, 'cwd': event.get('cwd', ''), 'at': started, 'blocks': 0,
                         'paid': paid(all_texts(lines), sha, ship_dir), 'adopted': True}
    mine = [s for s, o in owed.items() if o.get('session') == session and not o.get('paid') and not o.get('pending')]
    if not mine:
        return None
    text = turn_texts(lines)
    for sha in mine:
        if paid(text, sha, ship_dir):
            owed[sha]['paid'] = True
    # #1242: a word's release is due once its chain has ended too, never at the ship's push
    due = [s for s in mine if not owed[s]['paid'] and done(s, owed[s].get('cwd', ''))
           and (not chain(s, ship_dir) or chain_done(s, ship_dir))]
    if not due:
        return None
    sha = due[0]
    owed[sha]['blocks'] = owed[sha].get('blocks', 0) + 1
    if owed[sha]['blocks'] > MAX_BLOCKS:
        owed[sha]['paid'] = True
        return {'systemMessage': f'release-profile-gate: the profile of {sha[:8]} is still owed; let go after '
                                 f'{MAX_BLOCKS} holds'}
    return {'decision': 'block', 'reason': (
        f'Release profile owed: {sha[:8]} is in production and Gene has not had its profile report (claude-shared '
        f'CLAUDE.md, "The profile report Gene gets, after EVERY release"; Gene, 10/25/26: "Where\'s my profile and '
        f'optimize"). '
        + (f'Read the numbers with one command, never by hand: `{PROFILE_COMMAND} {sha}` (add '
           f'`--gate-log <the ship log>` when the push had one). Then reply' if PROFILE_COMMAND else 'Reply')
        + ' with the four parts: 1. what is live and the '
        f'smokes; 2. a table `stage | last release | this | change` naming {sha[:7]}; 3. the critical path, with why; '
        f'4. the cuts, each with its owner, its saving and its state, every other session\'s handed to it live by '
        f'SendMessage. A reply with the commit, a table row, "critical path" and "cuts" pays it'
        + (f'; {sha[:7]} was a word\'s ("ship apps", "ship tf", "ship play"), so the release is the whole chain, from '
           'the word to both stores\' ends, and the report carries its "word to" row beside the last chain of its kind '
           '(tf-history.jsonl, profiles/<sha>-tf.json)' if chain(sha, ship_dir) else '')
        + '. Or hand it to a '
        f'background agent (Agent tool, run_in_background) whose prompt names "Release profile: {sha[:8]}" and asks '
        f'for the same four parts, and end the turn.')}


def read_tail(path: str) -> list:
    if not path or not os.path.exists(path):
        return []
    with open(path, 'rb') as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - TAIL))
        return f.read().decode('utf-8', 'replace').splitlines()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    mode = argv[0] if argv else ''
    try:
        event = {} if mode == 'status' else json.loads(sys.stdin.read() or '{}')
        if not isinstance(event, dict):
            return 0
        # The project's facts, unless a caller set them already: no `[release]` table, no gate and no word.
        if not REPO and not configure(str(event.get('cwd') or os.environ.get('CLAUDE_PROJECT_DIR') or os.getcwd())):
            return 0
        if mode == 'status':
            print(json.dumps(load(), indent=1))
            return 0
        owed = load()
        if mode == 'post':
            if delegated(event, owed) or ship_call(event, owed) or record(event, owed):
                save(owed)
            return 0
        if mode == 'stop':
            answer = decide(event, owed, read_tail)
            save(owed)
            if answer:
                print(json.dumps(answer))
            return 0
    except Exception:   # noqa: BLE001 - a broken hook never traps a session
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
