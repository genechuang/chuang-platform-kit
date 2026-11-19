#!/usr/bin/env python3
"""plugins/gene-hooks/hooks/no-shell-writes.py: authored text never goes into a file through the shell (Gene,
10/10/26). In the plugin since 1.34.0 (SMADPickleBot #1810); claude-shared's hooks/test_no_shell_writes.py until then.

    python tests/test-hooks-no-shell-writes.py      # prints PASS/FAIL per case and "N failure(s)"
"""
import importlib.util
import json
import os
import sys

import bounded

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'plugins', 'gene-hooks', 'hooks')
spec = importlib.util.spec_from_file_location('nsw', os.path.join(HERE, 'no-shell-writes.py'))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)

REFUSED = [
    ('Bash', "cat > notes.md <<'EOF'\nhello\nEOF"),
    ('Bash', 'cat >> a.txt'),
    ('Bash', 'echo "x=1" > config.env'),
    ('Bash', "printf '%s\\n' a b >> list.txt"),
    ('Bash', "cd /d/code && echo hi > /d/tmp/x.txt"),
    ('Bash', "sed -i 's/a/b/' file.yml"),
    ('Bash', "sed -i.bak -e 's/a/b/' file.yml"),
    ('Bash', "perl -pi -e 's/a/b/' f"),
    ('Bash', "python -c \"open('f.yaml','w').write('x')\""),
    ('Bash', "python -c \"import yaml; yaml.safe_dump(d, open('o.yaml','w'))\""),
    ('Bash', "python - <<'EOF'\nfrom pathlib import Path\nPath('a').write_text('x')\nEOF"),
    ('Bash', "python3 -c \"import json; json.dump({}, open('o.json', 'w'))\""),
    ('Bash', "node -e \"require('fs').writeFileSync('a','b')\""),
    ('Bash', "tee out.yml <<'EOF'\na: 1\nEOF"),
    ('Bash', "echo hello | tee notes.txt"),
    ('PowerShell', "Set-Content -Path a.txt -Value 'x'"),
    ('PowerShell', "'x' | Out-File a.txt -Encoding utf8"),
    ('PowerShell', "[IO.File]::WriteAllText('a.txt', 'x')"),
    ('PowerShell', "New-Item -ItemType File -Path a.txt -Value 'x'"),
]

ALLOWED = [
    ('Bash', "git -c user.name=Claude commit -q -F - <<'EOF'\nA subject > with an arrow\n\n- a bullet\nEOF"),
    ('Bash', 'gh run list --limit 3 > /dev/null 2>&1'),
    ('Bash', 'python scripts/run-tests.py --select "$L" 2>&1 | grep -E "FAIL|suites,"'),
    ('Bash', 'python scripts/build-pipelines-page.py D:/tmp/bp.html'),
    ('Bash', 'python report.py > out.txt'),
    ('Bash', 'tar -I "gzip -1" -cf ota.tgz dist-native && gcloud storage cp ota.tgz gs://b/x'),
    ('Bash', 'npx expo export --platform web --output-dir dist'),
    ('Bash', 'grep -n "a > b" file.py'),
    ('Bash', 'npm run test:ci 2>&1 | tee -a "$RUNNER_TEMP/step.log"'),
    ('Bash', 'git log --oneline -3'),
    ('Bash', 'for i in 1 2; do [ -f x ] && break; done'),
    ('Bash', 'python -c "import json,sys; print(json.load(sys.stdin)[0])"'),
    ('Bash', 'echo "status $? after 3 s"'),
    ('Bash', 'echo done >&2'),
    ('PowerShell', "Get-Content a.txt | Select-Object -First 3"),
    ('PowerShell', "Stop-Process -Id 1 -Force; (Get-NetTCPConnection -LocalPort 8092 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count"),
]


def main() -> int:
    failures = 0
    for tool, cmd in REFUSED:
        ok = H.refusal(tool, cmd) is not None
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  refused: {cmd.splitlines()[0][:70]}")
    for tool, cmd in ALLOWED:
        why = H.refusal(tool, cmd)
        failures += why is not None
        print(f"{'PASS' if why is None else 'FAIL'}  allowed: {cmd.splitlines()[0][:70]}{'' if why is None else '  <- ' + why}")
    # End to end: the hook's exit code and stderr, as Claude Code runs it.
    for tool, cmd, want in (('Bash', 'echo x > a.txt', 2), ('Bash', 'git status', 0), ('Read', '', 0)):
        done = bounded.run([sys.executable, os.path.join(HERE, 'no-shell-writes.py')],
                           input=json.dumps({'tool_name': tool, 'tool_input': {'command': cmd}}), capture_output=True,
                           text=True, timeout=30)
        ok = done.returncode == want and (want == 0 or 'Write tool' in done.stderr)
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  exit {done.returncode} for {tool}: {cmd!r}")
    print(f'{failures} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
