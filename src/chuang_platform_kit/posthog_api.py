"""PostHog's REST API, one client for every project (SMAD PickleBot #1132, out of its picklebot_core/posthog_api.py of
#487; Gene, 11/7/26: "picklebot runtime should only use chuang-kit sdks", as #569 did for Sentry).

`Client(project_id, key, host=)`: the reads a triager, a dashboard script or a listener makes, over the standard
library: the newest events matching a filter, a HogQL query, one saved insight with its last result, any call by path
(`api`), the links a ticket carries to a session's replay and a person's events, and `run(action, args)`, the named
actions a script or a listener asks for by name. A refusal is `Refused`, a SystemExit whose line names the call, the
status and PostHog's own words (never the key), so a script that lets it go exits with its line and a listener catches
it by name. The key is the caller's (a Personal API key with the scopes its calls need), or a function that returns it
when the first call needs it; where it is stored and under which name is the host's business.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

HOST = 'https://us.posthog.com'
TIMEOUT = 30


class Refused(SystemExit):
    """PostHog refused or did not answer: the line says the call, the status and PostHog's own words."""


class Client:
    """PostHog's API for one project: `host` is its cloud (US by default), `project_id` the one its links name."""

    def __init__(self, project_id: int, key='', host: str = HOST, opener=None, timeout: int = TIMEOUT):
        self.project_id, self.key, self.host = int(project_id), key, host.rstrip('/')
        self.opener, self.timeout = opener or urllib.request.urlopen, timeout

    def _key(self, k='') -> str:
        k = k or self.key
        if callable(k):
            k = k()
        k = (k or '').strip()
        if not k:
            raise Refused('no PostHog personal API key: the host passes one to the client')
        return k

    def replay_url(self, session_id: str) -> str:
        """The session's replay in PostHog ('' for no id): a ticket links the session it was sent from."""
        sid = str(session_id or '').strip()
        return f'{self.host}/project/{self.project_id}/replay/{urllib.parse.quote(sid, safe="")}' if sid else ''

    def person_url(self, distinct_id: str) -> str:
        """The person's events in PostHog by distinct id ('' for none): the id as PostHog keeps it, never an email."""
        did = str(distinct_id or '').strip()
        return f'{self.host}/project/{self.project_id}/person/{urllib.parse.quote(did, safe="")}' if did else ''

    def api(self, method: str, path: str, body=None, k=''):
        """One call to PostHog's API by path under the host; the JSON back (None for an empty answer); Refused on a
        refusal or no answer."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f'{self.host}{path}', data=data, method=method,
                                     headers={'Authorization': f'Bearer {self._key(k)}', 'Content-Type': 'application/json'})
        try:
            with self.opener(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise Refused(f'PostHog answered {e.code} to {method} {path}: {e.read().decode(errors="replace")[:300]}')
        except (OSError, ValueError) as e:
            raise Refused(f'PostHog did not answer {method} {path} ({type(e).__name__})')

    def events(self, params: dict = None, k='') -> list:
        """The newest events matching `params` (event, after, before, limit, properties): GET .../events/."""
        path = f'/api/projects/@current/events/?{urllib.parse.urlencode(params or {})}'
        return (self.api('GET', path, k=k) or {}).get('results', [])

    def query(self, hogql: str, k='') -> dict:
        """A HogQL query ({columns, results}), PostHog's SQL over its events: POST .../query/."""
        got = self.api('POST', '/api/projects/@current/query/', {'query': {'kind': 'HogQLQuery', 'query': hogql}}, k=k) or {}
        return {'columns': got.get('columns') or [], 'results': got.get('results') or []}

    def insight(self, insight_id: int, k='') -> dict:
        """One saved insight with its last result, refreshed before it answers (a dashboard tile)."""
        got = self.api('GET', f'/api/projects/@current/insights/{int(insight_id)}/?refresh=blocking', k=k) or {}
        return {'id': got.get('id'), 'name': got.get('name'), 'result': got.get('result')}

    # The actions a direct call or a listener may ask for, by name: what each takes, and whether it writes. Reads only.
    ACTIONS = {
        'events': ((), False),
        'query': (('hogql',), False),
        'insight': (('id',), False),
    }

    def run(self, action: str, args: dict = None, k=''):
        """One named action with its args ({event, after, before, limit} for events, {hogql}, {id}); Refused for an
        unknown action, a missing arg or PostHog's refusal."""
        if action not in self.ACTIONS:
            raise Refused(f"posthog: no action {action!r} (one of {', '.join(self.ACTIONS)})")
        needs, _write = self.ACTIONS[action]
        a = args or {}
        missing = [n for n in needs if not a.get(n)]
        if missing:
            raise Refused(f"posthog {action}: needs {', '.join(missing)}")
        if action == 'events':
            return self.events({n: a[n] for n in ('event', 'after', 'before', 'limit') if a.get(n)}, k=k)
        if action == 'query':
            return self.query(a['hogql'], k=k)
        return self.insight(a['id'], k=k)
