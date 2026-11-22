"""chuang_sdlc.cicd: the generic half of a project's release pipeline, standard library only, so every pipeline a host
runs (a CI provider's runner, a cloud build step, the developer's own box) can import it before it has anything else
installed (SMADPickleBot #235, 11/8/26; the host's `pipeline/shared` files are shims that load it and pass the host's
own names in).

  proc            one command runner for every pipeline script: UTF-8 text, the Windows shell only for a .cmd shim, no
                  console window, a capped run that kills the whole tree, the throttled `gh` where the kit is present,
                  JSON helpers, a GET with a bearer, the primary checkout a worktree belongs to
  build_once      build once, deploy the same bytes (twelve-factor V): an image or a function source named by the
                  content key of exactly the files that go into it, built only when no artifact for that key exists
  smoke_rest      a backend smoke's reads in one process over REST (Cloud Functions, Cloud Run, Pub/Sub, a token)
  migration_kind  whether a push's Alembic migrations only add (`expand`), so they may climb a shared database's
                  lower rung, or change what a live revision still reads (`contract`)
  jest            the app's Jest suites a pipeline change reaches, read from the suites' own text, and jest on them by
                  path, passed on its own JSON's word; the host's repo, app folder, prefix and command configured (#1188)
  gh_deploy       a release's rung as a GitHub deployment (the Deployments view as the CI/CD console): start and finish,
                  the token's fallbacks, the kit's client or the standard library, never stopping a release (#1285)
  claim           the push decider's half of a two-path pipeline claim: a push's files and the pipelines they touch,
                  the pending check, the sweep, starting a cloud build trigger and dispatching a workflow, a host's
                  names handed to Decider; the per-job claim stays the host's own standard-library file (#1681)
  pids            a process named by its pid and the start a lock recorded: alive, its start, a wait on it with its
                  exit code, a stop of its tree, and lock_alive, so a reused pid is never taken for it (#1125, #2159);
                  HolderLock, a lock file naming its holder, taken by one taker, freed when its holder is gone
  name_reach      which names of a host's shared package a deploy runs and which a push changed, so a deploy gate
                  redeploys only what a changed name reaches; the package's import name the host's (#2160)
"""
