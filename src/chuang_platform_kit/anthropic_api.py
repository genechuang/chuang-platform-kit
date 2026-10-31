"""Anthropic's Messages API, one client for every project (SMAD PickleBot #573; Gene, 10/31/26 8:56 AM PT: every
third-party API client and key consolidated in the kit). One call: the model, the messages, the system prompt and the
host's extras (a beta header, `output_config`, `fallbacks`) in; the answer's text and body out together, with the
host's `usage` callback run on the body so its metering rides every call. A refusal is `Refused`, a RuntimeError
carrying the status, Anthropic's own message (never the key) and whether it means the account cannot pay (Gene,
10/2/26: "watch when Anthropic api call throws a no more token error and notify me").

The transport is a `post(url, headers=, json=, timeout=)` in requests.post's shape, answering an object with
`.status_code`, `.text` and `.json()`: the standard library's `urllib_post` by default, `requests.post` or a kept-alive
session's where the host has one, a fake in a suite. Standard library only.
"""
import json as _json
import os
import re
import urllib.error
import urllib.request

API = 'https://api.anthropic.com/v1/messages'
VERSION = '2023-06-01'
TIMEOUT = 60
# What an API error says when the account cannot pay for the call: 400, 402 or 403 with these words. A rate limit
# (429) or an outage (5xx) is not this.
BILLING = re.compile(r'credit balance|purchase credits|usage limit|spend(?:ing)? limit|billing', re.I)


class Refused(RuntimeError):
    """Anthropic refused or did not answer: `status` (0 when nothing answered), `detail` (Anthropic's own message, or
    the error's name) and `out_of_credits`."""

    def __init__(self, message: str, status: int = 0, detail: str = '', out_of_credits: bool = False):
        super().__init__(message)
        self.status, self.detail, self.out_of_credits = status, detail, out_of_credits


def out_of_credits(status: int, text: str) -> bool:
    """True when an API error means the account cannot pay for the call."""
    return status in (400, 402, 403) and bool(BILLING.search(text or ''))


class Answer:
    """What `urllib_post` answers: requests.Response's three members this module reads."""

    def __init__(self, status: int, text: str):
        self.status_code, self.text = status, text

    def json(self):
        return _json.loads(self.text or '{}')


def urllib_post(url: str, headers=None, json=None, timeout: float = TIMEOUT):
    """requests.post's shape over the standard library: an HTTP error is an Answer with its status and body, never a
    raise; no answer at all (a connection error) raises as urllib does."""
    req = urllib.request.Request(url, data=_json.dumps(json or {}).encode('utf-8'), method='POST', headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Answer(getattr(r, 'status', 200), r.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        return Answer(e.code, e.read().decode('utf-8', 'replace'))


def messages(model: str, messages: list, system: str = '', max_tokens: int = 1024, api_key: str = '',
             timeout: float = TIMEOUT, headers=None, extra=None, usage=None, post=None, env=None) -> dict:
    """One Messages call: {'text': the text blocks joined, 'body': the response, 'stop_reason', 'model': the one that
    answered (the body's own, so a server-side fallback names what ran)}. `headers` are added to the request's (a beta
    header), `extra` to the payload (`output_config`, `fallbacks`); `usage(body, model)` is the host's meter, run on
    every 200 and never allowed to fail the call. The key is `api_key`, else the environment's ANTHROPIC_API_KEY; none
    is Refused before any call."""
    key = api_key or (os.environ if env is None else env).get('ANTHROPIC_API_KEY', '')
    if not key:
        raise Refused('no Anthropic API key: pass api_key or set ANTHROPIC_API_KEY')
    payload = {'model': model, 'max_tokens': max_tokens, 'messages': messages}
    if system:
        payload['system'] = system
    payload.update(extra or {})
    hdrs = {'x-api-key': key, 'anthropic-version': VERSION, 'content-type': 'application/json', **(headers or {})}
    try:
        resp = (post or urllib_post)(API, headers=hdrs, json=payload, timeout=timeout)
    except Exception as e:   # noqa: BLE001 - the transport did not answer
        raise Refused(f'Anthropic did not answer ({type(e).__name__}: {e})', detail=f'{type(e).__name__}: {e}')
    if resp.status_code != 200:
        text = resp.text or ''
        try:
            detail = str((resp.json().get('error') or {}).get('message') or text[:200])
        except Exception:   # noqa: BLE001 - a body that is not Anthropic's JSON
            detail = text[:200]
        raise Refused(f'Anthropic answered HTTP {resp.status_code}: {detail[:200]}', status=resp.status_code,
                      detail=detail[:200], out_of_credits=out_of_credits(resp.status_code, text))
    body = resp.json() or {}
    if usage is not None:
        try:
            usage(body, model)
        except Exception:   # noqa: BLE001 - a meter never fails the call
            pass
    text = '\n'.join(b.get('text', '') for b in body.get('content', []) if b.get('type') == 'text').strip()
    return {'text': text, 'body': body, 'stop_reason': body.get('stop_reason'), 'model': body.get('model') or model}


def ask(prompt: str, model: str, system: str = '', **kw) -> dict:
    """`messages` with one user turn."""
    return messages(model, [{'role': 'user', 'content': prompt}], system, **kw)
