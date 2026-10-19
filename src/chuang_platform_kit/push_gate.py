"""A push gate for git's pre-push hook: checks run before a push leaves the box, each skipped when its result
already exists, and a push that lost the race to the remote's main rebased for the second push.

    chuang-push-gate [--config FILE] [remote url]     # git's pre-push hook hands it the pushed refs on stdin
    python -m chuang_platform_kit.push_gate ...       # the same

A repo turns it on with a hook that calls it (`.githooks/pre-push`: `exec python -m chuang_platform_kit.push_gate
"$@"`, and `git config core.hooksPath .githooks`) and a `.push-gate.toml` at its root:

    remote = "origin"                    # the remote and branch the gate guards (defaults shown)
    branch = "main"
    override = "PUSH_GATE_UNCHECKED"     # VAR=1 pushes without the gate
    gate = ["**"]                        # the gate runs its checks only for a push touching these; any other push
                                         # is told, one line each, which checks' marks are missing
    require_head = true                  # the checks test the checkout, so the pushed commit must be its HEAD

    [[check]]
    name = "tests"
    run = ["{python}", "-m", "pytest", "-q"]     # an argv, never a shell line; nonzero refuses the push
    paths = ["src/**", "tests/**", "!docs/**"]   # what its result depends on (default every path)
    paths_command = ["{python}", "x.py"]         # more of those, one glob a line (optional)
    when = ["**"]                                # what makes it run (default its paths)
    mark = ["{python}", "marked.py", "{tree}"]   # 0 when its result already exists (optional)
    mark_url = "https://host/marks/{tree}.json"  # or: a 200 there is the mark (optional)
    carry = ["{python}", "carry.py", "{old}", "{new}"]   # moves the mark across a rebase (optional)
    missing = "has no test mark"                 # the line a push outside `gate` gets when the mark is missing
    refusal = "fix the tests, then push"         # the hint after a refusal
    skip_codes = [2]                             # exits that mean "not applicable here", not a failure

Placeholders in a command or URL: {python} (this interpreter), {root}, {sha} (the pushed commit), {tree} (its tree),
{base} (the remote's old sha), {old} and {new} (the commit before and after a rebase), {remote}, {branch}.

What it does, for each ref pushed to the guarded branch, one line per decision:
  1. Fetch the branch; a head that does not descend from it is refused at once (git would refuse it anyway, after the
     checks had spent their minutes).
  2. Run each check whose `when` the push touches (against the remote's old sha), skipped when its mark answers; a
     failure refuses the push.
  3. Fetch again. If the branch moved during the checks, rebase the pushed commits onto it (a dirty checkout or a
     conflict refuses with nothing rewritten), run again only the checks whose `paths` the rebase brought changes
     to, carry the others' marks, and refuse with "rebased onto <sha>; push again, the gate is satisfied": git
     pushes the sha it gave the hook, so the push must be repeated, and the repeat clears on the marks.

Standard library only; it knows nothing of the host but its config. Exits 0 to let the push go, 1 to refuse it.
"""
from __future__ import annotations

import argparse
import functools
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field

ZERO = '0' * 40
CONFIG = '.push-gate.toml'
PREFIX = 'push-gate: '

_TOP_KEYS = {'remote', 'branch', 'override', 'gate', 'require_head', 'check'}
_CHECK_KEYS = {'name', 'run', 'paths', 'paths_command', 'when', 'mark', 'mark_url', 'carry', 'missing', 'refusal',
               'skip_codes'}


@dataclass
class Check:
    """One check of the gate (see the module docstring for each field)."""
    name: str
    run: list
    paths: list = field(default_factory=lambda: ['**'])
    paths_command: list | None = None
    when: list | None = None
    mark: list | None = None
    mark_url: str = ''
    carry: list | None = None
    missing: str = ''
    refusal: str = ''
    skip_codes: list = field(default_factory=list)


@dataclass
class Config:
    """A repo's `.push-gate.toml`."""
    remote: str = 'origin'
    branch: str = 'main'
    override: str = 'PUSH_GATE_UNCHECKED'
    gate: list = field(default_factory=lambda: ['**'])
    require_head: bool = True
    checks: list = field(default_factory=list)


def load(path: str) -> Config:
    """The Config in a TOML file; an unknown key or a check without `name` and `run` is a ValueError, so a typo
    never quietly turns a check off."""
    with open(path, 'rb') as f:
        data = tomllib.load(f)
    extra = set(data) - _TOP_KEYS
    if extra:
        raise ValueError(f'{path}: unknown key(s) {", ".join(sorted(extra))}')
    checks = []
    for i, c in enumerate(data.get('check', [])):
        bad = set(c) - _CHECK_KEYS
        if bad:
            raise ValueError(f'{path}: check {i + 1}: unknown key(s) {", ".join(sorted(bad))}')
        if not c.get('name') or not isinstance(c.get('run'), list) or not c['run']:
            raise ValueError(f'{path}: check {i + 1} needs a name and a run argv')
        checks.append(Check(**c))
    top = {k: v for k, v in data.items() if k != 'check'}
    return Config(checks=checks, **top)


@functools.lru_cache(maxsize=1024)
def _glob(pattern: str):
    """A path glob as a regex: `**` any directories, `*` and `?` within one; a pattern ending in `/` is its folder."""
    if pattern.endswith('/'):
        pattern += '**'
    out, i = '', 0
    while i < len(pattern):
        if pattern.startswith('**/', i):
            out, i = out + '(?:.*/)?', i + 3
        elif pattern.startswith('**', i):
            out, i = out + '.*', i + 2
        elif pattern[i] == '*':
            out, i = out + '[^/]*', i + 1
        elif pattern[i] == '?':
            out, i = out + '[^/]', i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out)


def matches(patterns, path: str) -> bool:
    """True when `path` matches one of `patterns` and none of its `!` exclusions."""
    keep = [p for p in patterns if not p.startswith('!')]
    drop = [p[1:] for p in patterns if p.startswith('!')]
    return (any(_glob(p).fullmatch(path) for p in keep)
            and not any(_glob(p).fullmatch(path) for p in drop))


def touched(patterns, files) -> list:
    """The files among `files` that `patterns` cover."""
    return [f for f in files if matches(patterns, f)]


class Gate:
    """One run of the gate in a checkout. `say` prints a decision; `env` is the environment the override is read
    from; `execute` runs a command (an argv, in the checkout) and answers its exit code; `head` answers a URL's
    HTTP status. All four are seams for the tests."""

    def __init__(self, root: str, cfg: Config, say=None, env=None, execute=None, head=None):
        self.root, self.cfg = root, cfg
        self.say = say or (lambda m: print(PREFIX + m, flush=True))
        self.env = os.environ if env is None else env
        self.execute = execute or (lambda argv: subprocess.run(argv, cwd=root).returncode)
        self.head = head or _http_status
        self._paths = {}

    # git
    def git(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(['git', '-C', self.root, *args], capture_output=True, text=True,
                              encoding='utf-8', errors='replace')

    def rev(self, ref: str) -> str:
        r = self.git('rev-parse', '--verify', '-q', ref)
        return r.stdout.strip() if r.returncode == 0 else ''

    def fetch(self) -> str:
        """The remote branch's tip as fetched now; '' when the fetch fails (offline: git's own push decides)."""
        if self.git('fetch', '-q', self.cfg.remote, self.cfg.branch).returncode:
            return ''
        return self.rev('FETCH_HEAD')

    def is_ancestor(self, a: str, b: str) -> bool:
        return self.git('merge-base', '--is-ancestor', a, b).returncode == 0

    def changed(self, a: str, b: str):
        """The paths that differ between two commits; None when git cannot say."""
        r = self.git('diff', '--name-only', a, b)
        return [ln for ln in r.stdout.splitlines() if ln] if r.returncode == 0 else None

    def subject(self, sha: str) -> str:
        return self.git('log', '-1', '--format=%h %s', sha).stdout.strip()[:80]

    # commands
    def fill(self, text: str, **kw) -> str:
        values = dict(python=sys.executable, root=self.root, remote=self.cfg.remote, branch=self.cfg.branch, **kw)
        for k, v in values.items():
            text = text.replace('{' + k + '}', v or '')
        return text

    def argv(self, cmd, **kw) -> list:
        return [self.fill(a, **kw) for a in cmd]

    def paths(self, c: Check) -> list:
        """A check's paths: its `paths` and whatever its `paths_command` prints (once per run)."""
        if c.name not in self._paths:
            out = list(c.paths)
            if c.paths_command:
                r = subprocess.run(self.argv(c.paths_command), cwd=self.root, capture_output=True, text=True,
                                   encoding='utf-8', errors='replace')
                if r.returncode:
                    self.say(f'{c.name}: its paths_command failed (exit {r.returncode}); every path counts as its')
                    out = ['**']
                else:
                    out += [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
            self._paths[c.name] = out
        return self._paths[c.name]

    def marked(self, c: Check, **kw) -> bool:
        """True when the check's mark answers for these values; never true for a check without one."""
        if c.mark_url:
            return self.head(self.fill(c.mark_url, **kw)) == 200
        if c.mark:
            return self.execute(self.argv(c.mark, **kw)) == 0
        return False

    def run_check(self, c: Check, **kw) -> bool:
        """Run one check; True when it passed or answered a skip code."""
        argv = self.argv(c.run, **kw)
        self.say(f'{c.name}: running {" ".join(os.path.basename(a) if a == sys.executable else a for a in argv)}')
        rc = self.execute(argv)
        if rc == 0:
            return True
        if rc in c.skip_codes:
            self.say(f'{c.name}: answered {rc}: not applicable here, skipped')
            return True
        return False

    def refuse(self, line: str) -> int:
        self.say(f'{line} ({self.cfg.override}=1 to push anyway)')
        return 1

    # the gate
    def ref(self, local_sha: str, remote_sha: str) -> int:
        """The gate for one pushed commit: 0 lets it go, 1 refuses it."""
        cfg, short = self.cfg, local_sha[:8]
        target = f'{cfg.remote}/{cfg.branch}'
        tip = self.fetch()
        if not tip:
            self.say(f'cannot fetch {target}; the base check is skipped and git\'s own push decides')
        elif not self.is_ancestor(tip, local_sha):
            self.say(f'{cfg.branch} moved ({self.subject(tip)}): git fetch {cfg.remote} && git rebase {target}, '
                     'then push again')
            return 1
        base = remote_sha if remote_sha != ZERO and self.rev(remote_sha + '^{commit}') else ''
        if not base and tip:
            base = self.git('merge-base', tip, local_sha).stdout.strip()
        files = self.changed(base, local_sha) if base else None
        if files is None:
            self.say(f'cannot diff {short} against {target}; every check applies')
        tree = self.rev(local_sha + '^{tree}')
        kw = dict(sha=local_sha, tree=tree, base=remote_sha)
        if files is not None and not touched(cfg.gate, files):
            self.say(f'{short} touches nothing the gate guards ({", ".join(cfg.gate)}): it goes unchecked')
            for c in cfg.checks:
                if c.missing and (c.mark or c.mark_url) and not self.marked(c, **kw):
                    self.say(f'{short} {c.missing}')
            return 0
        due = [c for c in cfg.checks if files is None or touched(c.when or self.paths(c), files)]
        for c in cfg.checks:
            if c not in due:
                self.say(f'{c.name}: the push touches none of its paths; skipped')
        if due and cfg.require_head:
            head = self.rev('HEAD')
            if head != local_sha:
                return self.refuse(f'{short} is not this checkout\'s HEAD ({head[:8]}), and the checks test the '
                                   'checkout: check it out and push from it')
        for c in due:
            if self.marked(c, **kw):
                self.say(f'{c.name}: already done for {short} (its mark answers)')
            elif not self.run_check(c, **kw):
                return self.refuse(f'{c.name} refused the push' + (f': {c.refusal}' if c.refusal else ''))
        if not tip or not due:
            return 0
        return self.after(local_sha, due)

    def after(self, local_sha: str, due: list) -> int:
        """Step 3: the branch fetched again after the checks; if it moved, the rebase and the refusal."""
        cfg = self.cfg
        tip = self.fetch()
        if not tip or self.is_ancestor(tip, local_sha):
            self.say(f'{cfg.branch} has not moved during the checks; the push goes')
            return 0
        moved = f'{cfg.branch} moved during the checks ({self.subject(tip)[:60]})'
        if self.git('status', '--porcelain', '--untracked-files=no').stdout.strip():
            return self.refuse(f'{moved}; the checkout has uncommitted changes, so nothing was rebased: commit them, '
                               f'git rebase {cfg.remote}/{cfg.branch}, then push again')
        if self.rev('HEAD') != local_sha:
            return self.refuse(f'{moved}; git rebase {cfg.remote}/{cfg.branch} by hand, then push again')
        fork = self.git('merge-base', local_sha, tip).stdout.strip()
        if self.git('rebase', '-q', tip).returncode:
            self.git('rebase', '--abort')
            return self.refuse(f'{moved} and the rebase onto it conflicts; nothing was rewritten: '
                               f'git rebase {cfg.remote}/{cfg.branch} by hand, then push again')
        new = self.rev('HEAD')
        brought = self.changed(fork, tip) if fork else None
        kw = dict(sha=new, tree=self.rev(new + '^{tree}'), base=tip, old=local_sha, new=new)
        for c in due:
            hit = None if brought is None else touched(self.paths(c), brought)
            if hit is None or hit:
                why = 'the rebase\'s diff could not be read' if hit is None else \
                    f'the rebase changed {len(hit)} of its paths ({hit[0]}{", ..." if len(hit) > 1 else ""})'
                self.say(f'{c.name}: {why}: running it again on {new[:8]}')
                ok = self.run_check(c, **kw)
            elif c.carry:
                ok = self.execute(self.argv(c.carry, **kw)) == 0
                if ok:
                    self.say(f'{c.name}: the rebase changed none of its paths; its mark carried to {new[:8]}')
                else:
                    self.say(f'{c.name}: its carry refused; running it again on {new[:8]}')
                    ok = self.run_check(c, **kw)
            else:
                self.say(f'{c.name}: the rebase changed none of its paths; its result stands')
                ok = True
            if not ok:
                return self.refuse(f'{moved}: rebased onto {tip[:8]} (now {new[:8]}), but {c.name} refused it; '
                                   'push again and the gate runs again')
        self.say(f'{moved}: rebased onto {tip[:8]} (now {new[:8]}); push again, the gate is satisfied')
        return 1


def _http_status(url: str) -> int:
    """The status of a HEAD request (0 when it cannot be made)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError, ValueError):
        return 0


def run(lines, root: str, cfg: Config, **seams) -> int:
    """The gate over git's pre-push stdin lines (`<local ref> <local sha> <remote ref> <remote sha>`)."""
    gate = Gate(root, cfg, **seams)
    if gate.env.get(cfg.override) == '1':
        gate.say(f'{cfg.override}=1: the push goes unchecked')
        return 0
    for line in lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        _lref, local_sha, remote_ref, remote_sha = parts
        if remote_ref != f'refs/heads/{cfg.branch}' or local_sha == ZERO:
            continue
        if gate.ref(local_sha, remote_sha):
            return 1
    return 0


def main(argv=None, stdin=None) -> int:
    """The console entry: `chuang-push-gate [--config FILE] [remote url]`, the refs on stdin."""
    p = argparse.ArgumentParser(prog='chuang-push-gate', description='A pre-push gate configured by .push-gate.toml.')
    p.add_argument('--config', help=f'the config file (default: {CONFIG} at the repo root)')
    p.add_argument('remote', nargs='?', help='the remote name git passes the hook (unused: the config names it)')
    p.add_argument('url', nargs='?', help='the remote URL git passes the hook (unused)')
    a = p.parse_args(argv)
    top = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    if top.returncode:
        print(PREFIX + 'not in a git checkout; nothing to gate')
        return 0
    root = top.stdout.strip()
    path = a.config or os.path.join(root, CONFIG)
    if not os.path.exists(path):
        print(PREFIX + f'no {os.path.basename(path)} in {root}; nothing to gate')
        return 0
    try:
        cfg = load(path)
    except (ValueError, tomllib.TOMLDecodeError) as e:
        print(PREFIX + f'the config cannot be read, so the push is refused: {e}')
        return 1
    return run((stdin or sys.stdin).read().splitlines(), root, cfg)


if __name__ == '__main__':
    sys.exit(main())
