"""The board subcommands every project's `scripts/tickets.py` shares (0.66.0; SMADPickleBot #2449).

A project's ticket tool calls `dispatch(argv, run, cfg)` first: it runs `status`, `eta`, `light` or `triage` and
returns the exit code, or None for any other command, which the project's own wiring then handles (`new`, `bug`,
`start` and `loe` run through this package's file_new, file_bug, start and loe with each project's own hooks).

    status <role or column> N [N ...] [--back]   the tickets to that column; --back is the one deliberate step back
    eta N <when | none>                           the card's ETA field (a date, or a date and time whose day it keeps)
    light N [N ...] needs --ask "<the ask>"       🟡 Needs you with its ask posted as `Scrum: needs <lead> | ...`
    light N [N ...] clear                         the light off
    triage N[,N ...] P1..P4 [--owner O] [--area A] [--feature F]   one batched read and one batched write

Every line says what moved or was set and what was refused, and a command exits 1 when anything was refused, so a
refusal is never read as a success (ChuangFinance, 11/17/26: `start` on five Dock cards printed them all while none
moved). Lifted from SMADPickleBot's scripts/tickets.py, which keeps no copy of its own.
"""
import sys
from datetime import datetime, timezone

from . import tickets as T


def _numbers(args) -> list:
    return [int(a.lstrip('#')) for a in args if a.lstrip('#').isdigit()]


def status(argv, run=None, cfg: dict = None, out=print) -> int:
    """`status <role or column> N ... [--back]`: T.move, forward only unless --back; exit 1 on any refusal."""
    words = [a for a in argv[1:] if not a.startswith('--')]
    if len(words) < 2 or not _numbers(words[1:]):
        out('tickets: usage: tickets.py status <role or column> N [N ...] [--back]', file=sys.stderr)
        return 2
    back = '--back' in argv
    moved = T.move(_numbers(words[1:]), words[0], run, cfg, back=back)
    out(f'tickets: {T.column(words[0], cfg)}{" (back)" if back else ""}: '
        + ', '.join(f'#{n}{"" if ok else " (not moved)"}' for n, ok in moved))
    return 0 if all(ok for _, ok in moved) else 1


def eta(argv, run=None, cfg: dict = None, out=print, now=None) -> int:
    """`eta N <when | none>`: the board's ETA field, the day the scrum master reads; 2 for a word it cannot read."""
    from . import scrum
    if len(argv) < 3 or not argv[1].lstrip('#').isdigit():
        out('tickets: usage: tickets.py eta N <when>   (a date, 11/10/26; a date and time, 11/10/26 3 PM; or none)',
            file=sys.stderr)
        return 2
    n, when = int(argv[1].lstrip('#')), ' '.join(a for a in argv[2:] if not a.startswith('--'))
    clear = when.strip().lower() in ('none', 'clear')
    day = '' if clear else scrum.eta_day(when, now or datetime.now(timezone.utc))   # utc-ok: an instant
    if not day and not clear:
        out(f'tickets: "{when}" is no day the scrum master reads: say a date (11/10/26, 11/10) or a date and time '
            '(11/10/26 3 PM), or `none` to clear it', file=sys.stderr)
        return 2
    item = T.item_id(n, run, cfg)
    ok = bool(item) and T.set_field(item, 'ETA', day, run, cfg)
    out(f"tickets: #{n} ETA {day or 'cleared'}" + ('' if ok else ' (not set: GitHub refused it, or the card is not on the board)'))
    return 0 if ok else 1


def light(argv, run=None, cfg: dict = None, out=print) -> int:
    """`light N ... needs --ask "<ask>"` or `light N ... clear`: 🟡 Needs you in the card's column with its ask posted
    and the lead's account assigned, or the light off and unassigned. A closed ticket takes no Needs you."""
    run, cfg = T._run(run), T._cfg(cfg)
    words = [a for i, a in enumerate(argv) if not a.startswith('--') and not (i and argv[i - 1] == '--ask')]
    if len(words) < 3 or words[-1] not in ('needs', 'clear'):
        out('tickets: usage: tickets.py light N [N ...] needs --ask "<ask>" | clear', file=sys.stderr)
        return 2
    ask = T._opt(argv, '--ask').strip()
    if words[-1] == 'needs' and not ask:
        out('tickets: no light set: 🟡 Needs you takes --ask "<what the lead must decide or do, in full>"', file=sys.stderr)
        return 2
    value = cfg['lights']['needs'] if words[-1] == 'needs' else ''
    lead = (cfg.get('people') or {}).get('lead') or 'Gene'   # the name the scrum master's `Scrum: needs` line reads
    done = []
    for a in words[1:-1]:
        n = a.lstrip('#')
        if ask and n.isdigit() and str((run.issue(int(n)) or {}).get('state') or '').upper() == 'CLOSED':
            out(f'tickets: #{n} is closed, so no Needs you on it: put the ask on the open ticket that carries the work',
                file=sys.stderr)
            done.append(f'#{n} (not set: closed)')
            continue
        item = T.item_id(int(n), run, cfg) if n.isdigit() else None
        ok = bool(item) and T.set_field(item, 'Light', value, run, cfg)
        if ok and ask:
            run.comment(int(n), f'Scrum: needs {lead} | {ask}')
        if ok:
            run.assign(int(n), T.repo_owner(cfg), on=bool(ask))
        done.append(f'#{n}{"" if ok else " (not set)"}')
    out(f"tickets: {value or 'light off'}: {', '.join(done)}")
    return 0 if all('not set' not in d for d in done) else 1


def triage(argv, run=None, cfg: dict = None, out=print) -> int:
    """`triage N[,N ...] P1..P4 [--owner O] [--area A] [--feature F]`: one card through T.triage, several through
    T.triage_many (one batched read, one batched write)."""
    if len(argv) < 3:
        out('tickets: usage: tickets.py triage N[,N ...] P1..P4 [--owner O] [--area A] [--feature F]', file=sys.stderr)
        return 2
    numbers = [int(n.lstrip('#')) for n in argv[1].split(',') if n.strip().lstrip('#').isdigit()]
    args = (argv[2].upper(), T._opt(argv, '--owner'), T._opt(argv, '--area'),
            int(T._opt(argv, '--feature', '0').lstrip('#') or 0))
    if len(numbers) > 1:
        out('\n'.join(T.triage_many(numbers, *args, run=run, cfg=cfg)))
    else:
        out(T.triage(numbers[0], *args, run=run, cfg=cfg))
    return 0


COMMANDS = {'status': status, 'eta': eta, 'light': light, 'triage': triage}


def dispatch(argv, run=None, cfg: dict = None, out=print):
    """The exit code of a shared subcommand, or None when argv names none (the project's own wiring runs it)."""
    handler = COMMANDS.get((argv or [''])[0])
    return handler(argv, run, cfg, out=out) if handler else None
