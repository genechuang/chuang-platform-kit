# Changelog: chuang-sdlc

A change that breaks a function's signature is a minor-version bump and names the host call sites to change. Tags are
`sdlc-v<version>`, cut from a green main.

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
