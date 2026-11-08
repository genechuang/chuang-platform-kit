# Changelog: chuang-sdlc

A change that breaks a function's signature is a minor-version bump and names the host call sites to change. Tags are
`sdlc-v<version>`, cut from a green main.

## 0.4.0 - 2026-11-07 PT

A ship's release notes and its post-ship runner, moved from the first host's scripts with no project in them. Every
0.3.1 call reads as before; the main package's floor is 0.27.0 (google_rest.get_execution).
- `titles`, what a range of commits shipped in its tickets' own words: `configure(repo=, area=, bug=, tail=, fixed=,
  token=, mapped=, workers=)` (the repository the board's `repo` and the bug label the board's `labels` {'Bug'} unless
  named; the readers' Area, none by default, NotConfigured when unset; a title's `tail` regex, none by default; the
  token the kit's github.token unless the host passes its own; `mapped` the host's commits from before the trailer
  rule), `setting`, `repo`, `area`, `bug_label`, `member_label` (tickets.label('member_title')), both lower-cased
  as `fetch` reads labels, `issue_url`;
  `numbers_of` (board.ticket_trailers, then `mapped` by sha prefix), `fetch` (one issue over REST through the kit's
  github client: its title, lower-cased labels and state, 404 or 410 missing, any other failure None with a WARNING),
  `read` (side by side, in first-commit order), `title`, `line` and `shipped` ({'new', 'fixed', 'behind' a count,
  'unknown'}).
- `notes`, a ship's release notes over `titles`: `configure(sections=, emoji=, tz=, tag_prefix=, not_allowed=, none=,
  unread=, retitle=, close_note=)` (New, Fixed, Behind the scenes and their emoji, the kit's clock zone and `ship-` by
  default), `setting`; `groups` (each section's (number, title), `unknown`, `member_titles`), `compare_url`,
  `ship_time`, `slack_text`, `markdown_text`, `pr_body` (through ship_pr.no_closing_keyword), `slack_blocks` (a
  section split under `SLACK_TEXT_LIMIT`, the Post-Ship: steps in the host's `not_allowed` words), `release_body`,
  `when` and `tag_for` in the host's zone, `change_order_number` (the issue labelled tickets.label('change_order') whose
  title ends with the head's 8 characters; None when the host labels none), `commits` (github.compare) and `release`
  (github.create_release, an existing tag's Release reused). Posting to Slack, its channel and token, and reading a
  local git stay the host's.
- `post_ship`: `run(found, start, state, clock=, sleep=, wait=WAIT, poll=POLL)`, each allowed step started with the
  host's `start(job, args)` and polled with its `state(job, execution)` to its end (passed, failed, refused, did not
  start (<why>), timed out, with its seconds and execution page; a step off the list never started, an unread poll
  counted as running), and `execution_result(project, region, job, execution, token, opener=, timeout=)`, a Cloud Run
  Job execution's `state` over the kit's google_rest (succeeded is passed, failed or cancelled is failed). The
  allow-list and the job start stay the host's.

## 0.3.1 - 2026-11-07 PT

Two branch leaks in `ship_pr`, from the first host's review; every 0.3.0 call reads as before.
- `open_for` deletes the branch it made (createRef, or the caller's `push`, read back for its id) when
  createPullRequest is then refused (no pull-request write on the token, the head already in the base), so a refused
  open leaves no `<prefix><head8>` on GitHub; a branch that was there before the call is left. The line ends
  `; <branch> deleted again`, or a WARNING naming the branch left.
- `close_stale(current_head='', run=, cfg=)`: every open pull request from `<prefix>*` into the base whose head is
  neither `current_head` nor in the base already (Ref.compare's BEHIND or IDENTICAL, `IN_BASE`) is closed unmerged
  with why and its branch deleted, through `close_unshipped`. A ship killed hard skips its own close; a host's next
  ship open runs this first, and a sweep may. `OPEN_QUERY`, `COMPARE_QUERY`.
- `testing`: `ship_setup(in_base=)` and refusals `open` and `compare` for the two new documents; createRef's answered
  id is the one the reads give the branch, so a made branch can be deleted in the fake.

## 0.3.0 - 2026-11-07 PT

One pull request a ship, moved from the first host's build with no project in it. Every 0.2.0 call reads as before,
apart from one GitHub read `live_numbers` now makes to find a ship pull request (none found, nothing changes).
- `ship_pr`: `configure(prefix=, base=, label=, label_color=, label_description=)` (`ship/`, `main`, `ship` by default),
  `setting`, `branch_of`; `open_for` (the branch made at the head, or the caller's `push`, one pull request into the
  base, labelled, an open one reused), `close_unshipped` and `close_head` (a refused push's), `find` (merged or open,
  never one closed unmerged), `link_after_live` (aliased addCloseIssueReferences, `BATCH` a document, a refused batch
  sent again one ticket a document), `link_document`, `finish` and `wrecked` (each posts its one comment once: a
  second run of the same release finds its `Live:` or `Wrecked:` comment and posts nothing; the branch is never read
  for it, so a host that deletes head branches on merge still gets its comment), `LIVE_WORD`, `WRECK_WORD`,
  `note_change_order`; `no_closing_keyword`
  (`CLOSING`, `BREAK`), which open_for runs on the title and body itself; the GraphQL documents. Never raises.
- `testing`: the fake GitHub every host's suites of the ship pull request share, so none keeps a copy: `ShipPRs` (the
  mixin answering ship_pr's documents from memory, every call recorded, refusals by name), `Minimal` (the ticket
  client's other methods), `ShipFake`, `ship_fake()` (pull request #900 from the configured prefix's branch, merged)
  and `RELEASE`. No network.
- `tickets`: `live_numbers(..., pr=None, settle=True)` closes every ticket first, then links the ship's pull request
  to each closed one (found by its branch when `pr` is None, none looked for when False), its branch only for a
  ticket the pull request did not link, and with `settle` finishes the pull request; `change_order_body(..., pr=None)`
  names it. `link_branch` is the fallback.

## 0.2.0 - 2026-11-07 PT

The commit trailers a project's pipelines read, moved from the first host's scripts with no project in them. Every
0.1.0 name is unchanged, so a host still on 0.1.0's callers runs on 0.2.0 as before.
- `trailers`: `SHAPES` and `STANDARD` (Session, the old Claude-Session and a bare session URL, Ticket, Pipeline-Parity,
  Post-Ship), `shape(key, value)` for a host's own, `matcher(*shapes)`, `is_plumbing(line, pattern)`, `values(message,
  key)`.
- `parity`: `which(path, pipelines)`, `only_paths(diff)`, `touched(files, pipelines, diff_of)` and `verdict(message,
  pipes, pipelines, example, cite)`, the pipelines a host's {name: [glob, ...]}; `TRAILER`, `PATHS_LINE`, `EXAMPLE`.
- `post_ship`: `steps(commits, allowed, script_of)`, `label`, `lines(done, markdown, not_allowed)`, `failed`;
  `TRAILER`, `ICON`, `NOT_ALLOWED`. Running the steps stays the host's.
- Behavior fixes a host inherits from the scripts these came from: a bare `Pipeline-Parity:` no longer passes by
  borrowing the next line (the old `\s*` crossed the newline), and the ten characters are the stripped value's, so
  trailing spaces or a carriage return no longer count (`has_trailer`, `MIN_REASON`; any one valid line passes); the
  same for `values()`; `verdict()` ignores a pipeline name it was not given; `which()` matches case on every platform
  (`fnmatchcase`); `lines()` reads a step's `allowed`, so an allowed step with no words is never called off the list.

## 0.1.0 - 2026-11-07 PT

The first release: the ticket tools of the first host's own ticket module, moved here with no project in them (Gene,
11/7/26 2:02 PM PT: SDLC tooling is no one project's, so any project can use it; 2:28 PM PT: "Own distribution").
- `board`: `load`, `stage`, `named`, `flow`, `rank`, `light_for`, `is_placeholder`, `placeholder_role`, and the
  `Ticket:` trailers (`ticket_trailers`, `ticket_numbers`, `TRAILER`, `NONE`, `TICKET_NUMBER`).
- `loe`: the activity log (`parse`, `read_log`, `active_minutes`), the calibration (`band`, `factor`, `calibrate`),
  `hours`, `words`, `stamp`, `text`, and `record` with the record and start collections passed in.
- `tickets`: `configure` (board, runner, store, LOE records, factor, mail, the before-a-ship-move mark, the decision's
  letter, people, labels, collections, card fields, default areas, words), `Client` over chuang_platform_kit.github; the
  fields and moves (`item_id`, `set_field`, `set_fields`, `set_stage`, `start`, `move`, `ticket_place`, `to_top`); the
  board kept (`sync_columns`, `placeholders`, `board_items`); filing (`new`, `bug`, `similar`, the area labels); triage
  (`triage_line`, `triage`, `queue`, `decide`, `sweep`); the estimate stage (`calibrated`, `estimate_stage`,
  `keep_record`, `loe_factor`, `ticket_estimate`, `record_loe`, `report_loe`, `loe_event`); the shipped stage
  (`live_numbers`, `release_text`, `link_branch`, `shipped_cards`, `backfill_plan`, `live_comment`); the change order
  (`change_order_title`, `change_order_body`, `change_order_for`, `change_order_settle`); `open_bug_for`; the command
  line's helpers.
- As the first host's estimate stage stood once reviewed: `loe_minutes` reads compound and spelled-out estimates
  (`1h 30m`, `2 hours`, `30 min`, `1d 2h`, a closing period) and `body_loe` the whole `LOE estimate:` line; a raw of 0
  or less is no estimate, at filing and in the stage; `estimate_stage(item=, raw_unfactored=)`, the latter leaving the
  Estimate empty with a WARNING when there is no factor (a sweep's case); `triage_line`'s `loe_unread`; `link_branch`
  idempotent (a branch at the commit is done, one behind moved forward by updateRef, never forced); `raw_on_record` with
  the host's `calibrated_since`; `backfill_plan`'s `original`, an Estimate made before the work never recalibrated.
- The kit names no label, collection or record of a host's: `configure(labels=, collections=)` names them, and unwired
  `label()` and `collection()` raise NotConfigured (`label(role, required=False)` for one a board may not use) and
  `loe.record` raises ValueError. `configure(mail=)` is the letter `decide` sends when its caller passes none, and
  `configure(raw_store=)` the store `keep_record` merges the raw estimate into (the record store when unset).
