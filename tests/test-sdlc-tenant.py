#!/usr/bin/env python3
"""chuang_sdlc.tenant: one host serving several boards, each delivery inside its tenant's layer.

Pinned (the positive case first): inside using() a module's table reads the tenant's value; a dict is merged over the
host's; a name the tenant leaves out is the host's; the layer ends with its block, a raised error included; layers nest
and the inner one wins; configure() inside a layer writes the host's table, never the layer; an unknown module or name is
refused before the block runs; an empty key is refused; tickets' own readers (_board, person, collection) and the
listeners' and scrum's settings see the layer; a thread started outside a layer sees none; board.loads reads TOML and
JSON to the same dict, and load() reads a .toml file.

Run:  python -B tests/test-sdlc-tenant.py
"""
import os
import sys
import tempfile
import threading

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc import board as B, listeners, scrum, tenant, tickets  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


HOST_BOARD = {'id': 'PVT_host', 'stages': {'shipyard': 'Shipyard'}}
OTHER_BOARD = {'id': 'PVT_other', 'stages': {'shipyard': 'Building'}}
tickets.configure(board=lambda: HOST_BOARD, people={'captain': 'Host Captain', 'triager': 'Host Triager'},
                  collections={'loe': 'ticket_loe', 'starts': 'ticket_starts'})
listeners.configure(claims='ci_releases')
scrum.configure(lead='Gene', state='scrum')

print('a tenant layer:')
with tenant.using('other', tickets={'board': lambda: OTHER_BOARD, 'people': {'captain': 'Other Captain'},
                                    'collections': {'loe': 'other__ticket_loe'}},
                  listeners={'claims': 'other__ci_releases'}, scrum={'state': 'other__scrum'}):
    check("the tenant's board is the one tickets reads", tickets._board()['id'], 'PVT_other')
    check("the tenant's captain", tickets.person('captain'), 'Other Captain')
    check("a role the tenant leaves out is the host's", tickets.person('triager'), 'Host Triager')
    check("the tenant's collection", tickets.collection('loe'), 'other__ticket_loe')
    check("a collection it leaves out is the host's", tickets.collection('starts'), 'ticket_starts')
    check("the listeners' claims", listeners.setting('claims'), 'other__ci_releases')
    check("the scrum master's state", scrum.setting('state'), 'other__scrum')
    check("a scrum setting it leaves out is the host's", scrum.lead(), 'Gene')
    check('current() names it', tenant.current(), 'other')
    seen = {}
    t = threading.Thread(target=lambda: seen.update(board=tickets._board()['id'], key=tenant.current()))
    t.start()
    t.join()
    check('a thread started inside sees no layer (a context variable)', seen, {'board': 'PVT_host', 'key': ''})
    tickets.configure(people={'captain': 'New Host Captain'})
    check("configure() inside a layer leaves the layer's value", tickets.person('captain'), 'Other Captain')
    with tenant.using('inner', tickets={'people': {'captain': 'Inner'}}):
        check('a nested layer wins', tickets.person('captain'), 'Inner')
        check('a nested layer replaces the outer one whole', tickets._board()['id'], 'PVT_host')
    check('the outer layer is back after the nested one', tickets.person('captain'), 'Other Captain')

print('outside it:')
check('the host board again', tickets._board()['id'], 'PVT_host')
check("configure() inside a layer wrote the host's table", tickets.person('captain'), 'New Host Captain')
check('current() is empty', tenant.current(), '')
try:
    with tenant.using('boom', listeners={'claims': 'boom__claims'}):
        raise KeyError('x')
except KeyError:
    pass
check('a raised error ends the layer', listeners.setting('claims'), 'ci_releases')

print('refusals:')
for label, call in (('an unknown module', lambda: tenant.using('x', billing={'a': 1}).__enter__()),
                    ('a name the module does not take', lambda: tenant.using('x', tickets={'boards': 1}).__enter__()),
                    ('an empty key', lambda: tenant.using('', tickets={}).__enter__())):
    try:
        call()
        check(f'{label} is refused', 'ran', 'refused')
    except (TypeError, ValueError):
        check(f'{label} is refused', 'refused', 'refused')
check('a refused layer leaves none in force', tenant.current(), '')

print('board.loads:')
TOML = 'repo = "o/r"\nnumber = 2\n[stages]\nshipyard = "Shipyard"\n'
check('TOML read', B.loads(TOML, 'toml'), {'repo': 'o/r', 'number': 2, 'stages': {'shipyard': 'Shipyard'}})
check('JSON read to the same dict', B.loads('{"repo": "o/r", "number": 2, "stages": {"shipyard": "Shipyard"}}'),
      B.loads(TOML, 'toml'))
with tempfile.NamedTemporaryFile('w', suffix='.toml', delete=False, encoding='utf-8') as f:
    f.write(TOML)
check('load() reads a .toml file', B.load(f.name)['number'], 2)
os.unlink(f.name)
try:
    B.loads('x', 'yaml')
    check('another format is refused', 'read', 'refused')
except ValueError:
    check('another format is refused', 'refused', 'refused')

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
