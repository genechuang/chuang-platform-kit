# Changelog

A change that breaks a function's signature is a minor-version bump and names the host call sites to change.

## 0.2.0 - 2026-10-06 PT

Additive; no host call site changes.

- `email_notify.send_message(service, to, subject, text, *, html=None, sender=None)` -> Gmail message id. For a host that
  keeps its own OAuth token and records delivery (ChuangFinance's finance watch and collector): its Gmail service in, the id
  out, an API error raised. Subject and body pass `redact()`.
- `redact.register_secret_values(*values)`: register secret VALUES the name lists cannot see, such as the contents of one
  JSON secret that Cloud Run mounts as a single variable. A JSON credential is split into its secret fields.
- `__version__` in `__init__.py` had stayed at 0.1.0 through 0.1.1 and 0.1.2; it now matches `pyproject.toml`.
- README: pin the tag's tarball URL in an image with no `git`.

## 0.1.2

`email_notify.configure(footer=)` puts the host's own line under the notification footer.

## 0.1.1

`tzdata` on Windows, so `clock.zone()` resolves on a host with no OS zone database.

## 0.1.0

Extracted from SMAD PickleBot (2026-10-04 PT).
