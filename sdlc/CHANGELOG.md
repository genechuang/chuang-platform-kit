# Changelog: chuang-sdlc

A change that breaks a function's signature is a minor-version bump and names the host call sites to change. Tags are
`sdlc-v<version>`, cut from a green main.

## 0.12.1 - 2026-11-09 PT

- `tickets.places`: a ticket the per-issue read (`issue.projectItems`) leaves out is looked up once more through the
  board's own read, `board_items('is:open is:issue')`. Under GitHub's throttle the per-issue read answered 200 with no
  nodes for cards that were on the board, so `move_many` moved none and a passed warm left eight Dock cards on
  Warming. SMADPickleBot #1231; Gene, 11/9/26 12:30 AM PT: "you're hitting graphql throttling dude".

## 0.12.0 - 2026-11-09 PT

- `tickets.triage_many(numbers, priority, owner, area, feature)`: triage() for many tickets in a few GitHub requests,
  every card's item in one aliased read (`places`) and every placed card's Kind, Priority, Owner, Area, Status and Light
  in one aliased mutation a MOVE_CHUNK (`_write_many`), where triage() spends about six GraphQL calls a card; a ticket
  not on the board takes triage()'s one-card path; the labels and the comment stay per issue. SMADPickleBot #1228:
  "when I tell you to bulk prioritize, use bulk graphql ... don't let GH throttle us".

## 0.11.1 - 2026-11-08 PT

- `scrum.dock_catch` says nothing for a runner with no `compare` (an older host's, a suite's fake): 0.11.0 put "could
  not be read" on every sweep such a runner ran, so an empty Shipyard's sweep was no longer empty (SMADPickleBot's
  scrum suite, #1203). A compare GitHub refuses is still the one line.

## 0.11.0 - 2026-11-08 PT

- `scrum.dock_catch`, run by the sweep beside the closed-card passes: the tickets the dock branch's commits since the
  main branch name (one compare, cfg `main_branch` / `dock_branch`, else main and dock), their places in one aliased
  read and each card still before the dock column moved there in one aliased write (move_many, forward only); the
  catch for a dock landing nothing told (SMADPickleBot #1203, split from #1202). `Client.compare` exposes the kit's
  github.compare to the ticket tools.

## 0.10.0 - 2026-11-08 PT

- `scrum`: a sweep's GitHub reads and writes batched (SMADPickleBot #1207; Gene, 11/8/26 9:27 PM PT: "scrum master
  should be batching graphql calls as much as possible, so we don't get throttled by github"). `GitHub.prefetch` reads
  every card the sweep will read (the shipyard's, the closed cards', the stale Needs you cards') in ONE aliased query a
  PREFETCH_CHUNK (50), `GitHub.remember` keeps the listing's items so a field write needs no item read, and while
  `batch` is on `GitHub.field` queues its single-select writes for `GitHub.flush`, ONE aliased mutation a
  tickets.MOVE_CHUNK (100) at the sweep's end, a card the batch did not take written by itself. `CARD_FIELDS` is the
  card read's field list, shared by CARD_QUERY and the prefetch.

## 0.9.0 - 2026-11-08 PT

- `tickets.places(numbers)`: every card's (item, column, Light, raw column) in ONE aliased GraphQL query a
  PLACES_CHUNK (50) of issues, through the issue's own project items, so no card is added to the board to read it.
- `tickets.move_many(numbers, status)`: move() for a release's cards in a few requests: places' one read, the same
  forward-only rule, the host's ship mark once for all of them, then every card's Status, Light and top position in ONE
  aliased mutation document a MOVE_CHUNK (100) of cards, a refused document halved and sent again. `live_numbers`
  writes its cards' Status, Light and Release the same way, a ticket not on the board taking the one-card path.
  SMADPickleBot #1197 (Gene, 11/8/26 9:11 PM PT: "cant we batch graphql calls for all tickets in dock"): a 20-card
  move spent about 140 calls and the kit's 60-a-minute cap; it now spends about 2 to 4. move() keeps its one-card form.

## 0.8.3 - 2026-11-08 PT

- `scrum.closed_needs_sweep` acts only on a card whose own Light is the needs Light, not on GitHub's filter alone: a
  card the closed sweep had just moved with its Light cleared was answered by the filter's lagging index and cleared a
  second time (SMADPickleBot's scrum suite, #1133).

## 0.8.2 - 2026-11-08 PT

- `scrum.closed_needs_sweep` (and `closed_needs_filter`): the sweep reads the closed cards still carrying the
  needs-the-lead Light, in any column, with GitHub's own filter, reads each issue once more (a reopened one keeps its
  Light), clears the Light and unassigns the lead; `sweep` runs it beside `closed_sweep`. A closed card in a finished
  column was read by no sweep, so a Needs you set after a ticket shipped stayed for days (SMADPickleBot #1133, #953).

## 0.8.1 - 2026-11-08 PT

- `tickets.sweep`: an alert issue (off the Project, not waiting for triage) whose newest owner's `Triage:` line is
  `none (why)` is closed as not planned, the reason in its closing comment (`close_none`, `NONE_COMMENT`); a waiting or
  filed ticket's none line is still left. The host's ticket.closed listener files the card as any not-planned close.
  Before, a none line changed nothing, so an alert a triage pass had claimed sat open in the shipyard as building
  (SMADPickleBot #1199: three alerts, 11/8/26 7:42 PM PT). A refused close is a WARNING the next sweep tries again.

## 0.8.0 - 2026-11-08 PT

The generic half of a release pipeline, `chuang_sdlc.cicd`, standard library only (SMADPickleBot #235; Gene, 10/22/26
6:01 AM PT: "move all pipeline code into its own repository so it can be decoupled from picklebot and reused for other
projects"; 11/8/26 3:27 PM PT, the reuse option). A host's `pipeline/shared` files become shims that load it (SMAD's
`kit_loader.ensure_sdlc()`, the pin's tarball on the path with no pip) and pass the host's own names in, so every
caller on every pipeline runs the command line it ran before. Every 0.7.0 call reads as before.
- `cicd.proc` (from SMAD's `pipeline_lib.py`, its generic half): `run` (UTF-8 text, the Windows shell only for a .cmd
  shim, no console window; `cwd` the caller's own unless given, where the host's wrapper defaulted it to its repo
  root), `run_capped` and `kill_tree`, `gh` through the kit's throttled cli where the kit is importable, `out`,
  `json_out`, `gh_json`, `parse_json`, `http_get`, `needs_shell`, `cmd_line`, `is_link`, `primary_root(start)` (no
  default folder). The host's reports bucket, profile histories and private-bucket reads stay in its module.
- `cicd.build_once` (from `build-once.py`, whole): the base-images lock is the file `BASE_IMAGES_LOCK` names, else a
  `base-images.json` beside the module; the desktop's build mark is read from the bucket `BUILD_MARK_BUCKET` names
  (`mark_bucket`, `from_mark(..., bucket=)`), nothing without one; the unpinned-base refusal names the lock's path.
- `cicd.smoke_rest` (from `smoke_rest.py`): `kit()` imports chuang-platform-kit directly and is Refused when it is not
  importable (the host's shim puts the pinned kit on the path first).
- `cicd.migration_kind` (from `migration-kind.py`): unchanged.
- Suites: `tests/test-sdlc-cicd-proc.py`, `-build-once.py`, `-smoke-rest.py`, `-migration-kind.py`, the modules' own
  cases; the host's integration cases (its workflows, deploy scripts and real migrations) stay in its suites.

## 0.7.0 - 2026-11-08 PT

The work a session hands to delegates counts in a ticket's actual. On 11/8/26 a ticket built by three agents, 451 s of
their own tool calls, read 0m: the stamp hook exempted a helper's calls from the activity log, and the session's one
call that started them was one line charged at most the cap. Every 0.6.0 call reads as before.
- `loe.parse`: two more line shapes, "<epoch> <session> <tickets> delegated <tool_use_id>" (the session's call that
  starts a delegate) as `('delegated', tool_use_id, tickets)` and "<epoch> <session> agent <agent_id> <tool_use_id>" (a
  delegate's own tool call) as `('agent', agent_id, tool_use_id)`; three-field lines as before, any other shape skipped.
- `loe.minutes` (new): `{n: {'active', 'delegated'} or None}`. A delegated line is a ticket line of the session's own;
  each agent's lines open the intervals between them, capped as a session's gaps are, on the tickets of the delegated
  line with its tool_use_id, else of its session's newest line before its first, each ticket's share of an interval
  1 / the tickets it names, as a session's gap is split; a ticket's delegated time is, at each instant, the largest share
  an agent gives it less the share the session's own interval gives it, so parallel agents and an agent beside the
  session count once, and an agent on a two-ticket delegation is counted once, not once per ticket. `active_minutes`
  is the 'active' of it, unchanged to the minute for a log with no agents.
- `loe.record` keeps the event's `delegated` minutes as the record's `delegated_min` (None when the event has none);
  `loe.text` says `, 45m of it by delegates` after the work when the record holds them beside a working time.
- `tickets.loe_event` carries `delegated` beside `active`.

## 0.6.0 - 2026-11-08 PT

The board's event listeners, what one of GitHub's webhook deliveries means, and a triage pass on a box, moved from the
first host's code with no project in them. Every 0.5.0 call reads as before.
- `listeners` (new): `configure(head=, claims=, claim_ttl_days=, moves=, retry=, scrum_state=, acted_seconds=,
  loe_record=, actual_field=, closer=)`; `ticket_committed` (the host's LOE record, the card's Actual), `board_wake` (a
  card newly in the shipyard: its owner's one letter, the drag on the scrum record, a session's own start telling
  nobody), `board_light`, `dock_landed`, `ticket_closed` (an alert closer's close to the ship column, a close as not
  planned to the failed one, any other asked; each move marked first; the host's own word through `also`),
  `scrum_nudge` and `triage_sweep` (the sweep here first, `start(args)` the host's job after). Each claim is a
  create-only record in the host's claims collection; a redelivery is the host's retry exception (`Retry` here unless
  configured). The board helpers are the host's (`moves`, read at call time; the module docstring lists them).
- `webhooks` (new): `TRIAGE_LINE`, `comment_route` (a `Scrum:` answer or the owner's plain reply on an issue assigned to
  them, a `Triage:` line, or ignored with its line), `issue_route` (a close, a feedback ticket by its label or body
  mark, an alert, or ignored), `dock_landed` (the push's `Ticket:` trailers), `project_item` and `item_route` (a
  Project item's move before and after its read), `delivery_age`.
- `triage` (new): `request` (one event on the host's topic, never raising), `prompt_text`, `prepare_worktree` (a
  read-only worktree of origin/main, a failed fetch keeping the sha it has or origin/main as the box saw it),
  `stamp`, `running`, `start_pass` (the host's `-m <module> pass`), `run_pass` (the headless run on the host's accounts
  through chuang_platform_kit.headless_claude: a limited account rests, a refused token rests the host's time, said in
  its `refused_note`), `result_record`, `locked_pass`.
- `testing`: `Moves`, the board helpers in memory, for the listeners and `webhooks.item_route`.

## 0.5.0 - 2026-11-07 PT

The scrum master, the ship card's keeper, the backlog read and the alert issues, moved from the first host's code with no
project in them. Every 0.4.0 call reads as before, apart from board_items' query, which now reads a draft's own id and
body (one more fragment, the same cards).
- `tickets`, the ship card: `ship_card_spec` (the board config's `ship_card`: home, word, title), `is_ship_card`,
  `ship_ended` (the host's ship record under way, else why not; `SHIP_RUN_STALE_SECONDS`), `ship_card_keep` (retitled on
  the draft's own id with its body sent back, `DRAFT_EDIT`; sent home from anywhere but home and the ship column; left
  in the ship column while a ship is under way; a record that cannot be read leaves it with a WARNING), configured by
  `configure(ship_run=, ship_under_way=, ship_run_name=)`. `board_items` reads a draft's `draft` id and `body`;
  `placeholders(drafts=)` takes the caller's drafts read (`DRAFTS_UNREAD` when there is none); `sync_columns(ship_run=)`
  reads the drafts once for the placeholders pass and the card's. `mark_ship_move(numbers, run)`: the host's mark of a
  move into the ship column, whatever runner the caller holds.
- `scrum` (new): `configure(lead=, store=, mail=, state=, finished=, unplanned=, closed_question=, closed_to=,
  closed_told=, closer=, ship_run_of=, tz=, words=)`, `SCRUM_RE` (the one regex of an owner's line, `needs <lead>` and
  `paused` among its kinds, made again for the lead) and `NEEDS_KIND`; `scrum_line`, `lead_answer`, `gh_stamp`,
  `parse_eta` and `localize` (in the kit's clock zone, standard time where the wall clock repeats or skips), `pt`,
  `plan`, `closed_filter`, `closed_plan`, `closed_sweep`, `question_key`, `letter`, `comment_body`, `GitHub` (the
  sweep's reads and writes; `ship` and `wreck` mark their move first), `sweep` (a closed shipyard card to the ship
  column, its move marked first), `ship_card_home`. The captain is tickets' `captain`.
- `backlog` (new): `configure(states=, label_states=, moving=, tail=, id_field=, needs_field=)`, `moving`, `state_of`,
  `board_filter`, `read_open` (one filtered read, P1 first), `short`, `render`.
- `alerts` (new): `configure(repo=, title_prefix=, max_title=, marker_tag=, subject_head=, closer_marks=, alert_titles=,
  person_closes=, bug_label=, cleared_label=, intro=, closes_itself=, close_word=, cleared_note=, collections=,
  claim_ttl_days=, filed=)`; `marker`, `marked`, `is_filed`, `triaged`, `closed_by_closer`, `pt`, `issue_title`,
  `issue_body`, `cleared_why`, `plan`, `plan_cleared`, `for_key`, `claim_key`, `opening_claimed`, `claim_opening`,
  `GitHub` (the repository configure's unless given), `act`, `triage_claim`, `triage_release`.
- `testing`: `Records` (a record store in memory), `items_page` (board_items' answer for a list of cards) and `Board` (a
  client for the sweep and the board's reads).

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
