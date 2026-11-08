# chuang-sdlc

A project's tickets on a GitHub Project (v2) board, project-agnostic: the board's columns by role and the flow they
make, a ticket's fields and moves along it (never a step back), filing with the one estimate stage every new ticket
passes, the `Triage:` line and its sweep, the shipped stage (the Release, the linked branch, the closing comment), the
change order a ship files, the board's column labels and placeholder cards kept as its config says, and the level of
effort estimated and actual. And the commit trailers a project's pipelines read: the plumbing lines a renderer drops
(`trailers`), the `Pipeline-Parity:` check over the project's own pipelines (`parity`), and a release's `Post-Ship:`
steps against its allow-list, each started and waited for with the project's own job start and status (`post_ship`).
And one pull request a ship (`ship_pr`): opened from its own branch before the ship's push of the base, merged by that
push, and linked to each shipped ticket as its closing reference only after the ticket closed, so a ticket's
Development shows the release that shipped it. And a ship's release notes in its tickets' own words: the tickets a
range of commits names, read from GitHub by their labels (`titles`), grouped New, Fixed and Behind the scenes and
written as the Slack post, the GitHub Release (made here, with its tag) and the ship pull request's body, with the
ship's change-order ticket (`notes`); the post itself, its channel and token, stay the project's. And the board's
ship card (a draft the lead drags into the ship column to ship), kept at its home and title by the project's ship record
(`tickets.ship_card_keep`). And the scrum master (`scrum`): a sweep of the shipyard column that reads each owner's one
`Scrum:` line (building with an ETA, next, blocked by, depends on, needs the lead, paused, done), asks each owner only
what the board cannot say, in one letter a sweep, keeps one comment a card, and catches the closed cards outside the
finished columns. And the one backlog read from the board and rendered by state, P1 first (`backlog`). And an alert's
incident as a GitHub issue, filed once per opening and closed once it is fixed, with a triage pass's create-only claim
(`alerts`); the project's watcher, its words, its repository and collections are configured. And the board's event
listeners (`listeners`): each board or ticket event told once, a commit's working time, the owner of a card newly in the
shipyard woken, a dragged card's light, a dock landing's cards, a closed ticket settled in the ship or the failed
column, the scrum master's nudge and the triage sweep's catch, with the project's letter, job, sweeps and board helpers
passed in; and what one of GitHub's deliveries means, decided from the delivery alone (`webhooks`). And a triage pass
on a box (`triage`): one headless Claude run over the project's untriaged issues, asked for over its bus, in a
read-only worktree, one at a time, capped and logged, on the subscriptions' accounts
(chuang_platform_kit.headless_claude).

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
