# chuang-sdlc

A project's tickets on a GitHub Project (v2) board, project-agnostic: the board's columns by role and the flow they
make, a ticket's fields and moves along it (never a step back), filing with the one estimate stage every new ticket
passes, the `Triage:` line and its sweep, the shipped stage (the Release, the linked branch, the closing comment), the
change order a ship files, the board's column labels and placeholder cards kept as its config says, and the level of
effort estimated and actual. And the commit trailers a project's pipelines read: the plumbing lines a renderer drops
(`trailers`), the `Pipeline-Parity:` check over the project's own pipelines (`parity`), and a release's `Post-Ship:`
steps against its allow-list (`post_ship`).

It is a distribution of its own in the chuang-platform-kit repository, so a release of it redeploys only what runs
it: a host pins it where its ticket tools run, never in every service the main package is in.

```
chuang-sdlc @ git+https://github.com/genechuang/chuang-platform-kit@sdlc-v0.1.0#subdirectory=sdlc
```

## Wiring

```python
from chuang_platform_kit import github
from chuang_sdlc import board, tickets

tickets.configure(
    board=lambda: board.load('board.json'),          # the board's config (chuang_sdlc/board.py has its shape)
    runner=lambda: GH,                               # GH = tickets.Client(tok=github.token)
    store=lambda: my_store,                          # get / set / merge / create on (collection, id)
    people={'triager': 'Alice', 'default_owner': 'Bob', 'captain': 'Bob'},
    labels={'triage': 'waiting', 'admin_ask': 'urgent', 'change_order': 'release', 'member_title': 'reword'},
    collections={'loe': 'loe_records', 'starts': 'loe_starts'},   # the kit names no label or collection of its own
    default_area='Product', change_order_area='Pipeline',
    mail=lambda to, text, subject: send(to, text, subject),
)
tickets.new('feature', 'Title', 'The plan.', area='Product', priority='P3', loe='2h')
```

Nothing has a default that reaches a real service: unwired, a call that needs the board or the runner raises
`NotConfigured`, and a missing store or hook is a step not done, said in the line the call returns. A host's suites pass
their own fake runner as `run=` (or wire it), with the same methods as `tickets.Client`.

## Tests

`tests/test-sdlc-*.py` in the repository, run with the rest (`python -m pytest -n auto`).
