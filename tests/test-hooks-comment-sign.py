#!/usr/bin/env python3
"""The comment gate (plugins/gene-hooks/hooks/comment-sign.py, run from stamp-gate.sh on every Bash call naming gh;
1.23.0, SMADPickleBot #1279; Gene, 11/9/26 9:55 AM PT: sessions' comments "get confused as comments by me"): a GitHub
comment a session writes with the owner's token ends with its signature line, or the call is refused with the line to
add; everything else passes; its regex is chuang_sdlc.signature's.

Run:  python tests/test-hooks-comment-sign.py      (prints PASS/FAIL per case and `N failure(s)`)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
HOOKS = os.path.join(ROOT, 'plugins', 'gene-hooks', 'hooks')
BASH = shutil.which('bash') or 'bash'
sys.path.insert(0, HOOKS)
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))
failures = []

import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location('comment_sign', os.path.join(HOOKS, 'comment-sign.py'))
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)
from chuang_sdlc import signature as G  # noqa: E402


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ('' if ok else f'\n        got  {got!r}\n        want {want!r}'))
    if not ok:
        failures.append(label)


SIGN = '\n\n— **Snow White** · Claude session'
home = tempfile.mkdtemp()
os.makedirs(os.path.join(home, '.claude', 'sessions'))
with open(os.path.join(home, '.claude', 'sessions', '77.json'), 'w', encoding='utf-8') as f:
    json.dump({'sessionId': 'sid-1', 'name': 'Black Widow'}, f)
signed_file = os.path.join(home, 'signed.md')
with open(signed_file, 'w', encoding='utf-8') as f:
    f.write('Found it.' + SIGN + '\n')
plain_file = os.path.join(home, 'plain.md')
with open(plain_file, 'w', encoding='utf-8') as f:
    f.write('Found it.\n')


def gate(command, sid='sid-1', env=None):
    return C.refusal({'tool_name': 'Bash', 'tool_input': {'command': command}, 'session_id': sid, 'cwd': home},
                     env=env or {}, home=home)


def main():
    said = gate('gh issue comment 1279 --body "Scrum: next | after #1021"')
    check('the positive case: an unsigned gh issue comment is refused, naming the line to add with the session\'s name',
          (bool(said), '— **Black Widow** · Claude session' in said), (True, True))
    check('  the same comment signed passes', gate(f'gh issue comment 1279 --body "Scrum: next | after #1021{SIGN}"'), '')
    check('  -b, --body=, a pr comment, --edit-last, a chained command and a path-named gh are read the same',
          [bool(gate(c)) for c in ('gh pr comment 5 -b "ok"', 'gh issue comment 5 --body=ok',
                                   'cd /d/x && gh issue comment 5 --edit-last --body "ok" >/dev/null && echo done',
                                   '/usr/bin/gh issue comment 5 --body ok', f'gh issue comment 5 -b "ok{SIGN}"')],
          [True, True, True, True, False])
    check('  a --body-file is read: signed passes, unsigned and stdin are refused',
          [bool(gate(f'gh issue comment 5 --body-file "{signed_file}"')), bool(gate(f'gh issue comment 5 -F "{plain_file}"')),
           bool(gate('gh issue comment 5 --body-file -'))], [False, True, True])
    check('  a heredoc body through $(cat) is read with its own lines',
          gate("gh issue comment 5 --body \"$(cat <<'EOF'\nFound it." + SIGN + "\nEOF\n)\""), '')
    check('  close --comment and pr review --body need it; a bare close or an approval does not',
          [bool(gate('gh issue close 5 --comment "Shipped."')), bool(gate(f'gh issue close 5 -c "Shipped.{SIGN}"')),
           bool(gate('gh issue close 5 --reason completed')), bool(gate('gh pr review 9 --body "looks right"')),
           bool(gate('gh pr review 9 --approve'))], [True, False, False, True, False])
    check('  gh api: a POST of body= to an issue\'s comments needs it; a read, another path and a signed one pass',
          [bool(gate('gh api repos/o/r/issues/5/comments -f body="hi"')),
           bool(gate(f'gh api repos/o/r/issues/5/comments -f "body=hi{SIGN}"')),
           bool(gate('gh api -X PATCH repos/o/r/issues/comments/99 -f body=x')),
           bool(gate('gh api repos/o/r/issues/5/comments --jq ".[].body"')),
           bool(gate('gh api repos/o/r/issues/5 -X PATCH -f state=closed'))], [True, False, True, False, False])
    check('  everything else passes: a read, an issue filed, a search that names the words, an unparseable read',
          [gate('gh issue view 5 --comments'), gate('gh issue create --title t --body b'),
           gate('grep -n "gh issue comment" CLAUDE.md'), gate('gh issue view 5 --json "unclosed')], ['', '', '', ''])
    check('  an unparseable comment write is read from its own text: unsigned refused, a footer line in it passes',
          [bool(gate('gh issue comment 5 --body "unclosed')), bool(gate('gh issue comment 5 --body "unclosed' + SIGN + '\n'))],
          [True, False])
    check("  no registry row: CHUANG_SIGNED_AS names the session; neither: a placeholder",
          ('— **Mr Sandman** · Claude session' in gate('gh issue comment 5 -b x', sid='none', env={'CHUANG_SIGNED_AS': 'Mr Sandman|Claude session'}),
           '— **<your session name>** · Claude session' in gate('gh issue comment 5 -b x', sid='none')), (True, True))
    check("the regex is chuang_sdlc.signature's, character for character", C.SIGNED.pattern, G.SIGNED.pattern)

    # the review of 11/9/26 10:25 AM PT, each confirmed finding a case
    check('review: a heredoc to --body-file - is read from the command (signed passes, unsigned refused)',
          [bool(gate("gh issue comment 5 --body-file - <<'EOF'\nIt's fixed" + SIGN + "\nEOF")),
           bool(gate("gh issue comment 5 --body-file - <<'EOF'\nIt's fixed\nEOF"))], [False, True])
    check('  a second gh on its own line is its own command, checked', bool(gate('gh issue view 5\ngh issue comment 5 --body "x"')), True)
    check('  $(cat FILE) is read; a $VAR body is read from the command and refused unsigned',
          [bool(gate(f'gh issue comment 5 --body "$(cat {signed_file})"')), bool(gate(f'gh issue comment 5 --body "$(cat {plain_file})"')),
           bool(gate('gh issue comment 5 --body "$BODY"'))], [False, True, True])
    check('  PowerShell is checked too', bool(C.refusal({'tool_name': 'PowerShell', 'session_id': 'sid-1', 'cwd': home,
                                                       'tool_input': {'command': 'gh issue comment 5 --body "Yes"'}}, env={}, home=home)), True)
    check('  a global -R/--repo before the verb, and gh.exe', [bool(gate('gh -R o/r issue comment 12 --body "Done"')),
                                                                bool(gate('gh.exe --repo o/r pr comment 3 -b Done'))], [True, True])
    check("  the gate finds the signature's text anywhere (the readers hold the last-line rule, signature.signed_by)",
          [bool(gate('gh issue comment 5 --body "— **Snow White** · Claude session\n\nmore words"')),
           bool(gate(f'gh issue comment 5 --body "more words{SIGN}\n"'))], [False, False])

    # the second review of 11/9/26 10:34 AM PT: no shell parse, so no quoting refuses a signed comment
    win_file = signed_file.replace('/', '\\')
    ps = lambda c: bool(C.refusal({'tool_name': 'PowerShell', 'session_id': 'sid-1', 'cwd': home, 'tool_input': {'command': c}},  # noqa: E731
                                  env={}, home=home))
    check('review 2: a signed body with $(...) or $VAR, a here-string, `n escapes, a printf, $\'...\' and a continued line pass',
          [bool(gate(f'gh issue comment 5 --body "Fixed in $(git rev-parse --short HEAD).{SIGN}"')),
           ps("gh issue comment 5 --body @'\nhi" + SIGN + "\n'@"), ps('gh issue comment 5 --body "hi`n`n— **Snow White** · Claude session"'),
           bool(gate("printf 'hi\\n\\n— **Snow White** · Claude session' | gh issue comment 5 --body-file -")),
           bool(gate("gh issue comment 5 --body $'hi\\n\\n— **Snow White** · Claude session'")),
           bool(gate(f'gh issue comment 5 \\\n  --body-file "{signed_file}"')), ps(f'gh issue comment 5 --body-file {win_file}')],
          [False] * 7)
    check('  an unsigned continued gh api write is seen; --help, a grep with an apostrophe, a # comment and a heredoc message are no writes',
          [bool(gate('gh api repos/o/r/issues/5/comments \\\n  -f body=x')), bool(gate('gh issue comment --help')),
           bool(gate('grep -rn "gh issue comment" plugins/  # who\'s posting')),
           bool(gate('ls\n# then gh pr comment 5 by hand')),
           bool(gate("git commit -F - <<'MSG'\ngh issue comment 5 is gated now\nMSG"))], [True, False, False, False, False])
    import time
    t0 = time.perf_counter()
    gate('gh ' + ' '.join(f'-x{i} v{i}' for i in range(40)) + ' issue comment 5 --body x' + ' <<' * 300)
    check('  a long command with many flags and << is checked in well under a second', time.perf_counter() - t0 < 0.5, True)
    if os.name == 'nt':
        msys = '/' + signed_file[0].lower() + signed_file[2:].replace('\\', '/')
        check('  an MSYS /d/... body file opens on Windows', gate(f'gh issue comment 5 --body-file {msys}'), '')
    long_home = tempfile.mkdtemp()
    os.makedirs(os.path.join(long_home, '.claude', 'sessions'))
    with open(os.path.join(long_home, '.claude', 'sessions', '1.json'), 'w', encoding='utf-8') as f:
        json.dump({'sessionId': 'sid-2', 'name': 'x' * 45}, f)
    check('  a name a signature cannot carry (over 40, a *) is never offered: the placeholder is',
          '<your session name>' in C.refusal({'tool_name': 'Bash', 'session_id': 'sid-2', 'tool_input': {'command': 'gh issue comment 5 -b x'}},
                                             env={}, home=long_home), True)
    utf8 = json.dumps({'tool_name': 'Bash', 'session_id': 'x', 'tool_input': {'command': f'gh issue comment 5 --body "Done.{SIGN}"'}},
                      ensure_ascii=False).encode('utf-8')
    plain_env = {k: v for k, v in os.environ.items() if k not in ('PYTHONUTF8', 'PYTHONIOENCODING')}
    r = subprocess.run([BASH, os.path.join(HOOKS, 'comment-sign.sh')], input=utf8, capture_output=True, timeout=30, env=plain_env)
    check('  the event as Claude Code sends it, raw UTF-8 with no PYTHONUTF8: a signed inline body passes', r.returncode, 0)
    gate_text = open(os.path.join(HOOKS, 'stamp-gate.sh'), encoding='utf-8').read()
    check("  a helper's call is checked: stamp-gate.sh runs the gate before a helper's early exit",
          gate_text.index('comment-sign.sh') < gate_text.index("helper; exit 0"), True)

    event = {'tool_name': 'Bash', 'tool_input': {'command': 'gh issue comment 5 --body "plain words"'}, 'session_id': 'x'}
    r = subprocess.run([BASH, os.path.join(HOOKS, 'comment-sign.sh')], input=json.dumps(event), capture_output=True,
                       text=True, timeout=30)
    ok = subprocess.run([BASH, os.path.join(HOOKS, 'comment-sign.sh')], input='not json', capture_output=True, text=True,
                        timeout=30)
    check('through the launcher: exit 2 with the reason on stderr; a broken event exits 0',
          (r.returncode, 'Sign the comment' in r.stderr, ok.returncode), (2, True, 0))
    gate_src = open(os.path.join(HOOKS, 'stamp-gate.sh'), encoding='utf-8').read()
    check('stamp-gate.sh runs it on a Bash call naming gh', 'comment-sign.sh' in gate_src, True)
    print(f'\n{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
