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

Every version is tagged `sdlc-v<version>`; a host pins
`chuang-sdlc @ git+https://github.com/genechuang/chuang-platform-kit@sdlc-v<version>#subdirectory=sdlc` only where the
code that runs it is installed.
"""
__version__ = '0.1.0'
