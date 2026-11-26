"""chuang_sdlc.cli: the board subcommands every project's ticket tool shares (0.66.0; SMADPickleBot #2449), on stubs
of chuang_sdlc.tickets' functions only; nothing reaches GitHub.

Pinned (the positive case first): `status` moves forward and says so; `--back` steps back; a refusal is said and exits
1; `eta`, `light` and `triage` run their tickets functions; dispatch answers None for any other command.

Run:  python tests/test-sdlc-cli.py      (prints PASS/FAIL per case and `N failure(s)`)
"""
import os
import sys
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import cli, tickets as T  # noqa: E402

CFG = {'lights': {'needs': '🟡 Needs you'}, 'people': {'lead': 'Lee'}, 'stages': {'shipyard': 'Shipyard'}}


class Out:
    def __init__(self):
        self.lines = []

    def __call__(self, text, file=None):
        self.lines.append(text)


class Run:
    def __init__(self, closed=()):
        self.closed, self.comments, self.assigns = set(closed), [], []

    def issue(self, n):
        return {'state': 'CLOSED' if n in self.closed else 'OPEN'}

    def comment(self, n, body):
        self.comments.append((n, body))
        return True

    def assign(self, n, login, on=True):
        self.assigns.append((n, login, on))
        return True


class Stubs:
    """T's functions replaced for one case, put back after."""
    def __init__(self, **fns):
        self.fns, self.saved = fns, {}

    def __enter__(self):
        for k, v in self.fns.items():
            self.saved[k] = getattr(T, k)
            setattr(T, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            setattr(T, k, v)


def _common(**more):
    return dict({'column': lambda name, cfg=None: name.title(), '_run': lambda run: run, '_cfg': lambda cfg: cfg,
                 'item_id': lambda n, run=None, cfg=None: f'I{n}', 'repo_owner': lambda cfg=None: 'owner'}, **more)


def test_status_moves_forward_and_says_so():
    """the positive case: `status shipyard 8 10` moves both forward (back False), says each, exits 0"""
    calls, out = [], Out()
    with Stubs(**_common(move=lambda ns, st, run=None, cfg=None, back=False: calls.append((ns, st, back)) or [(n, True) for n in ns])):
        code = cli.dispatch(['status', 'shipyard', '#8', '10'], 'run', CFG, out=out)
    assert (code, calls, out.lines) == (0, [([8, 10], 'shipyard', False)], ['tickets: Shipyard: #8, #10']), (code, calls, out.lines)


def test_back_steps_back_and_a_refusal_exits_1():
    """`--back` passes back=True and says (back); a ticket not moved is said as not moved and the command exits 1"""
    calls, out = [], Out()
    with Stubs(**_common(move=lambda ns, st, run=None, cfg=None, back=False: calls.append(back) or [(8, True), (10, False)])):
        code = cli.dispatch(['status', 'shipyard', '8', '10', '--back'], 'run', CFG, out=out)
    assert (code, calls, out.lines) == (1, [True], ['tickets: Shipyard (back): #8, #10 (not moved)']), (code, out.lines)


def test_status_without_tickets_is_usage():
    """no ticket number is the usage line and exit 2, nothing moved"""
    out = Out()
    with Stubs(**_common(move=lambda *a, **k: 1 / 0)):
        assert cli.dispatch(['status', 'shipyard'], 'run', CFG, out=out) == 2


def test_eta_sets_the_day_and_refuses_a_word_it_cannot_read():
    """`eta 8 11/27/26` sets the field to the day; `eta 8 soonish` is exit 2; `eta 8 none` clears it"""
    sets, out = [], Out()
    with Stubs(**_common(set_field=lambda item, f, v, run=None, cfg=None: sets.append((item, f, v)) or True)):
        a = cli.dispatch(['eta', '8', '11/27/26'], 'run', CFG, out=out)
        b = cli.dispatch(['eta', '8', 'soonish'], 'run', CFG, out=out)
        c = cli.dispatch(['eta', '8', 'none'], 'run', CFG, out=out)
    assert (a, b, c, sets) == (0, 2, 0, [('I8', 'ETA', '2026-11-27'), ('I8', 'ETA', '')]), (a, b, c, sets)


def test_light_needs_posts_the_ask_and_skips_a_closed_ticket():
    """`light 8 9 needs --ask X`: 8 lit, its ask posted for the lead and the owner assigned; 9, closed, not set, exit 1"""
    sets, run, out = [], Run(closed={9}), Out()
    with Stubs(**_common(set_field=lambda item, f, v, run=None, cfg=None: sets.append((item, v)) or True)):
        code = cli.dispatch(['light', '8', '9', 'needs', '--ask', 'pick one'], run, CFG, out=out)
    assert (code, sets, run.comments, run.assigns, out.lines[-1]) == (
        1, [('I8', '🟡 Needs you')], [(8, 'Scrum: needs Lee | pick one')], [(8, 'owner', True)],
        'tickets: 🟡 Needs you: #8, #9 (not set: closed)'), (code, out.lines)


def test_light_needs_without_an_ask_is_refused():
    """Needs you with no --ask sets nothing and exits 2"""
    with Stubs(**_common(set_field=lambda *a, **k: 1 / 0)):
        assert cli.dispatch(['light', '8', 'needs'], Run(), CFG, out=Out()) == 2


def test_triage_one_or_many():
    """one card through triage, a comma list through triage_many, the options passed"""
    one, many, out = [], [], Out()
    with Stubs(**_common(triage=lambda n, *a, run=None, cfg=None: one.append((n, a)) or 'one',
                         triage_many=lambda ns, *a, run=None, cfg=None: many.append((ns, a)) or ['m1', 'm2'])):
        cli.dispatch(['triage', '8', 'p2', '--owner', 'Snow'], 'run', CFG, out=out)
        cli.dispatch(['triage', '8,#9', 'P3'], 'run', CFG, out=out)
    assert (one, many, out.lines) == ([(8, ('P2', 'Snow', '', 0))], [([8, 9], ('P3', '', '', 0))], ['one', 'm1\nm2']), (one, many)


def test_any_other_command_is_none():
    """dispatch answers None for a command it does not hold, so the project's own wiring runs it"""
    assert (cli.dispatch(['new', 'x'], 'run', CFG), cli.dispatch([], 'run', CFG)) == (None, None)


if __name__ == '__main__':
    FAILURES = []
    for name, fn in list(globals().items()):
        if not (name.startswith('test_') and callable(fn)):
            continue
        label = (fn.__doc__ or name).strip().splitlines()[0]
        try:
            fn()
            print(f'  PASS  {label}')
        except Exception:
            FAILURES.append(label)
            print(f'  FAIL  {label}')
            traceback.print_exc(file=sys.stdout)
    print(f'{len(FAILURES)} failure(s)')
    sys.exit(1 if FAILURES else 0)
