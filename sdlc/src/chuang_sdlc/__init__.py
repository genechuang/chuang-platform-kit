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
  post_ship  a release's Post-Ship: steps read against the host's allow-list, and their lines in the release notes
             (standard library only)
  ship_pr    one pull request a ship, from its own branch into the base, its merge the ship's push, linked to each
             ticket as its closing reference only after the ticket closed (tickets.live_numbers), and the
             closing-keyword scrub its title and body go through
  testing    the fake GitHub a host's suites of the ship pull request share (ShipPRs, Minimal, ShipFake, ship_fake)

Every version is tagged `sdlc-v<version>`; a host pins
`chuang-sdlc @ git+https://github.com/genechuang/chuang-platform-kit@sdlc-v<version>#subdirectory=sdlc` only where the
code that runs it is installed.
"""
__version__ = '0.3.0'
