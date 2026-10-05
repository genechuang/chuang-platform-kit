"""Scrub tokens and keys out of text before it reaches a log line, an email or
a GitHub issue.

WHY. GREEN-API puts its API token in the URL path
(`/waInstance<id>/<method>/<token>`), and `requests` prints the full URL in
its exception text. On 2026-09-25 PT a 30-second connect timeout in
`whatsapp-message-sender` wrote that URL into Cloud Logging, into Error
Reporting, into two alert emails in Gene's inbox and into a diagnose issue
body (#71). greenapi.py's docstring said the URL "is never logged"; the
exception path broke that. The fix is one choke point, not a hunt through
~110 lines that log exception text: every log formatter, both email senders,
the alert-issue action and the diagnose workflow pass their text through
redact() here (Gene's ask, via Mr Sandman's 2026-09-25 19:45 PT note).

What it removes:
- the GREEN-API URL token: `waInstance123/sendMessage/abc...` becomes
  `waInstance<id>/sendMessage/***`;
- token-shaped strings: GitHub (`ghp_`, `gho_`, `github_pat_`), Anthropic
  (`sk-ant-`), and `Bearer <token>` headers;
- the literal VALUE of every secret this process holds (SECRET_ENV_NAMES,
  from the environment or a `.env` file), which is what catches a key nobody
  has thought of yet. JSON credentials (the Gmail token, the service
  account) are split into their secret fields.

Standard library only, so a host can run it anywhere, including as a stdin
filter in a GitHub Action (`python -m chuang_platform_kit.redact`).

WHICH VALUES. Two lists, so a host never has to tell the kit its secrets'
names: SECRET_ENV_NAMES, the ones every host of this kit is handed (GREEN-API,
GitHub, Anthropic, Gmail, Google credentials), and SECRET_NAME_SHAPE, every
environment or .env variable whose NAME says it is a secret (ends in TOKEN,
KEY, SECRET, PASSWORD, CREDENTIALS, PRIVATE_KEY, or a credential JSON). A host
with a secret named outside both shapes adds it with register_secret_names()
from its own config module.
"""
import json
import os
import re

MASK = '***'

# The secrets every host of this kit is handed. A value shorter than
# MIN_SECRET_LEN is never matched: masking "true" or a 4-digit id would shred
# ordinary text.
SECRET_ENV_NAMES = (
    'GREENAPI_API_TOKEN', 'GREENAPI_WEBHOOK_TOKEN', 'ANTHROPIC_API_KEY',
    'GITHUB_TOKEN', 'GITHUB_PAT', 'GH_TOKEN', 'GH_PAT_TOKEN', 'GH_BILLING_TOKEN', 'GCP_ACCESS_TOKEN',
    'GMAIL_OAUTH_TOKEN_JSON', 'GMAIL_OAUTH_CLIENT_JSON', 'GOOGLE_CREDENTIALS_JSON',
)
# ...and any variable whose name has this shape (a host's VENMO_ACCESS_TOKEN,
# ATHENAEUM_PASSWORD, SMAD_GOOGLE_CREDENTIALS_JSON, GOOGLE_GEOCODER_KEY are all
# caught by it, with no list to keep).
SECRET_NAME_SHAPE = re.compile(r'(_|^)(TOKEN|KEY|SECRET|PASSWORD|PASSWD|CREDENTIALS|CREDENTIALS_JSON|PRIVATE_KEY)(_JSON)?$')
_registered = set()
MIN_SECRET_LEN = 12


def register_secret_names(*names: str) -> None:
    """A host adds the names of secrets neither list would catch."""
    _registered.update(n for n in names if n)


def secret_names(env: dict) -> list:
    """Every name in `env` that is treated as a secret: the fixed list, the
    registered ones, and every name of the secret shape."""
    names = set(SECRET_ENV_NAMES) | _registered
    names.update(n for n in env if SECRET_NAME_SHAPE.search(n))
    return sorted(names)
# Fields of a JSON credential that are secret (the rest -- client_email,
# token_uri, scopes -- are addresses, not keys).
JSON_SECRET_FIELDS = ('token', 'access_token', 'refresh_token', 'client_secret',
                      'private_key', 'private_key_id', 'id_token')

_GREENAPI_URL = re.compile(r'(waInstance)\d+(/[A-Za-z]+/)[A-Za-z0-9]+')
_PATTERNS = (
    (re.compile(r'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b'), MASK),
    (re.compile(r'\bgithub_pat_[A-Za-z0-9_]{20,}\b'), MASK),
    (re.compile(r'\bsk-ant-[A-Za-z0-9_\-]{10,}'), MASK),
    (re.compile(r'(Bearer\s+)[A-Za-z0-9._\-~+/=]{16,}', re.I), r'\1' + MASK),
)


def _dotenv_values(path: str = '.env') -> dict:
    """KEY=VALUE pairs from a .env file in the working directory (the one a
    workflow writes before running a script); {} when there is none."""
    out = {}
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def secret_values(env: dict = None) -> list:
    """The literal secret strings to mask, longest first so a value that
    contains another is masked whole. `env` defaults to the process
    environment plus the working directory's .env."""
    if env is None:
        env = dict(_dotenv_values())
        env.update(os.environ)
    vals = set()
    for name in secret_names(env):
        v = (env.get(name) or '').strip()
        if not v:
            continue
        if v.startswith('{'):
            try:
                doc = json.loads(v)
            except ValueError:
                doc = None
            if isinstance(doc, dict):
                for field in JSON_SECRET_FIELDS:
                    fv = doc.get(field)
                    if isinstance(fv, str) and len(fv) >= MIN_SECRET_LEN:
                        vals.add(fv)
                        vals.add(fv.replace('\n', '\\n'))   # as it prints inside JSON
                continue
        if len(v) >= MIN_SECRET_LEN:
            vals.add(v)
    return sorted(vals, key=len, reverse=True)


_cache = {'key': None, 'vals': []}


def _process_secret_values() -> list:
    """secret_values() for this process, rebuilt only when a secret env var
    or the .env file changes (redact() runs on every log line)."""
    try:
        mtime = os.path.getmtime('.env')
    except OSError:
        mtime = None
    key = (os.getcwd(), mtime, tuple(sorted((n, v) for n, v in os.environ.items()
                                            if n in SECRET_ENV_NAMES or n in _registered or SECRET_NAME_SHAPE.search(n))))
    if _cache['key'] != key:
        _cache['key'], _cache['vals'] = key, secret_values()
    return _cache['vals']


def redact(text, env: dict = None) -> str:
    """`text` with every token and secret value masked. Never raises: a
    scrub that fails returns the input with the URL pattern still applied,
    because the log line or email it guards must still go out."""
    if text is None:
        return text
    s = str(text)
    try:
        s = _GREENAPI_URL.sub(r'\1<id>\2' + MASK, s)
        for rx, repl in _PATTERNS:
            s = rx.sub(repl, s)
        for v in (secret_values(env) if env is not None else _process_secret_values()):
            if v in s:
                s = s.replace(v, MASK)
    except Exception:
        pass
    return s


def main():
    """`python -m chuang_platform_kit.redact < in > out`: the filter a host's
    alert-issue action and log workflow pipe their text through."""
    import sys
    sys.stdout.write(redact(sys.stdin.read()))


if __name__ == '__main__':
    main()
