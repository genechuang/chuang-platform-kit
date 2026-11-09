"""chuang_sdlc: the ticket tools of Gene Chuang's projects, project-agnostic, a distribution of its own beside
chuang_platform_kit (whose github and clock modules it uses; the record store is the host's to pass in).

  board      a ticket board's names in one place: columns by role, the flow, a card's place along it, the placeholder
             cards, and the `Ticket:` trailers a commit names its work by (standard library only)
  loe        a ticket's level of effort: the working-time rule over a session activity log, the calibration of a raw
             estimate by span, the record writer and its words
  tickets    the tickets on a GitHub Project: fields and moves along the flow, filing with the one estimate stage, the
             triage line and its sweep, the shipped stage, change orders, the board kept as its config says; wired to a
             host once with `configure()` (its board, GitHub runner, record store, people and hooks), with no default
             that reaches a real service
  trailers   a commit message's plumbing trailers, the lines a renderer drops (standard shapes plus the host's), and a
             trailer's values (standard library only)
  parity     a commit that changes one or two of the host's release pipelines carries a Pipeline-Parity: trailer, the
             pipelines passed in as path sets (standard library only)
  post_ship  a release's Post-Ship: steps read against the host's allow-list, each started and waited for with the
             host's job start and status (run; execution_result for a Cloud Run Job), and their lines in the release
             notes (standard library only)
  titles     what a range of commits shipped in its tickets' own words: the tickets read from GitHub by their labels,
             the readers' Area's titles as lines, the rest counted
  notes      a ship's release notes: the tickets grouped New, Fixed and Behind the scenes, as a Slack message (text and
             blocks), Markdown, the ship pull request's body and the GitHub Release's; the Release, its tag and the
             ship's change-order ticket
  ship_pr    one pull request a ship, from its own branch into the base, its merge the ship's push, linked to each
             ticket as its closing reference only after the ticket closed (tickets.live_numbers), and the
             closing-keyword scrub its title and body go through; a refused open leaves no branch, and the next
             ship's open closes any a killed ship left (close_stale)
  scrum      the scrum master: the shipyard column's sweep (each owner's `Scrum:` line, building and its ETA in the
             kit's zone, next and queued, blocked, depends on, paused, Needs you for the lead), the closed cards'
             catch, one letter per owner and one comment a card, the ship card brought home
  backlog    the one backlog read from the board (open tickets, GitHub's own filter) and rendered by state, P1 first
  alerts     an alert's incident as a GitHub issue, filed once per opening and closed once it is fixed, and a triage
             pass's create-only claim; the words, the repository and the collections the host's
  listeners  the board's event listeners, each told once per event: a commit's working time, the owner of a card newly
             in the shipyard woken, a dragged card's light, a dock landing's cards, a closed ticket settled in the ship
             or the failed column, the scrum master's nudge and the triage sweep's catch (here first, the host's job
             after); the letter, the job, the sweeps and the board helpers the host's
  webhooks   what one of GitHub's deliveries means, decided from the delivery alone: a comment's route (a `Scrum:`
             answer, a `Triage:` line), an issue's (a close, a feedback ticket, an alert), a dock push's tickets, a
             Project item's move before and after its read, the delivery's age
  signature  who wrote a comment when everyone writes with one person's token: the signature footer, its signer (the
             host's, CHUANG_SIGNED_AS, the Claude Code session's registry row) and signed_by, the read that tells a
             session's or the pipeline's comment from the person's own words (standard library only)
  triage     a triage pass on a box: the request over the host's bus, the prompt, the read-only worktree, one pass at a
             time by its lock, the headless run on the subscriptions' accounts (chuang_platform_kit.headless_claude),
             capped and logged; the worktree, prompt file, logs and model the host's
  testing    the fake GitHub a host's suites of the ship pull request share (ShipPRs, Minimal, ShipFake, ship_fake), and
             of the sweep, the board's reads and the claims (Board, Records, items_page), and the board helpers the
             listeners take (Moves)
  cicd       the generic half of a release pipeline, standard library only (SMADPickleBot #235): proc, one command
             runner for every pipeline script; build_once, an image or function source built once by content key;
             smoke_rest, a backend smoke's reads over REST; migration_kind, whether a push's migrations only add

Every version is tagged `sdlc-v<version>`; a host pins
`chuang-sdlc @ git+https://github.com/genechuang/chuang-platform-kit@sdlc-v<version>#subdirectory=sdlc` only where the
code that runs it is installed.
"""
__version__ = '0.18.0'   # pyproject.toml's version; tests/test-versions.py holds the two equal (sdlc-v0.7.0 shipped with 0.6.0 here, read 11/8/26)
