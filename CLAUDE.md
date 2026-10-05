@../claude-shared/CLAUDE.md

# chuang-platform-kit — Project Instructions

The shared rules above apply (commits as Claude, tests gate the commit, Pacific
Time, American English). This file holds only what is true of **this** repo.

## What this is

The project-agnostic half of Gene's projects, as one installable package
(`chuang_platform_kit`, `src/` layout). It came out of SMAD PickleBot's
`webhook/shared/` on 2026-10-04 PT (that repo's `docs/ROADMAP.md`, step 1) so
that SMADPickleBot, ChuangFinance and the next project install one tested
package instead of copying files. **Nothing in here may know about pickleball,
a league, a sheet or a specific group**: a module that needs a project fact
takes it as a parameter or reads a documented environment variable
(`CHUANG_PLATFORM_TZ` is the only one the kit itself defines, in `clock.py`).

Modules: `redact`, `cloud_logging`, `lazy_import`, `phone_utils`,
`request_scope`, `whatsapp_message`, `whatsapp_publisher`, `greenapi`,
`email_notify`, `clock`. The host projects' own rules about them (every
WhatsApp send through the publisher, every log line through `setup_logging()`,
every new secret in `redact.SECRET_ENV_NAMES`) still apply where they are used.

## Releases

- **Every change the hosts should pick up is a tag.** Bump `version` in
  `pyproject.toml` and `__init__.py` together, commit, `git tag v<version>`,
  `git push --tags`. A host pins
  `chuang-platform-kit @ git+https://github.com/genechuang/chuang-platform-kit@v<version>`
  in its requirements; Cloud Build installs it from the public repo.
- A tag is cut only from a green `main` (`.github/workflows/tests.yml`, Python
  3.12 and 3.13).
- **Breaking a function's signature is a minor-version bump and a line in
  CHANGELOG.md** naming the host call sites to change. The hosts' suites import
  the kit, so a change here is proven there before the host bumps its pin.

## Tests

Hand-rolled suites in `tests/test-*.py` (one `PASS`/`FAIL` line per check,
a final `N failure(s)` line, the positive case first), run by
`python -m pytest -n auto` through `tests/conftest.py`. Offline and stdlib
fakes only; a suite that needs a project's files belongs in that project.
