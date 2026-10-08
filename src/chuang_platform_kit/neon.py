"""A Neon project as code: find or create a project, its database and a connection URL, idempotently, from an API key.

Written for ChuangFinance's ledger (10/8/26, kit 0.5.1; Gene: "infra as code" before a console
click), general since the next project asks the same. Everything is by name and reusable: a
project that exists is reused, a database that exists is reused, and the only things a run
returns to its caller are the ids, the host and the connection URL — the URL never reaches a
log or a printout from here (`host_of()` is the printable part; `provision()` registers the
URL with `redact` the moment it has it). The Neon REST API v2 with a personal or organization
API key (`key`); `opener` is the seam (anything called as urlopen(request, timeout=...)), so
the suites run offline.

    from chuang_platform_kit import neon
    made = neon.provision(key, 'chuang-finance', region_id='aws-us-west-2', pg_version=18, database='ledger')
    print(made['project_id'], made['host'], made['created_project'])      # never made['connection_uri']
    secrets.ensure('chuang-finance', 'DATABASE_URL'); secrets.add_version('chuang-finance', 'DATABASE_URL', made['connection_uri'])

    python -m chuang_platform_kit.neon --name chuang-finance --database ledger \\
        --key-secret smad-pickleball/NEON_API_KEY --store chuang-finance/DATABASE_URL

The URL is the DIRECT one (`pooled=False`) unless asked: migrations and session-level settings
want the direct endpoint; a web tier wants the pooler. The free plan's region nearest GCP
us-west1 is AWS Oregon, `aws-us-west-2`, the default here because every project of Gene's runs
there; `pg_version` defaults to 18, the major SMAD PickleBot runs.
"""
import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from . import redact as _redact

API = 'https://console.neon.tech/api/v2'
DEFAULT_REGION = 'aws-us-west-2'
DEFAULT_PG_VERSION = 18
PAGE = 100


def _call(path, key, method='GET', body=None, opener=None, timeout=60):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {'Authorization': f'Bearer {key}', 'Accept': 'application/json'}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(f'{API}{path}', data=data, method=method, headers=headers)
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            text = r.read().decode('utf-8')
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PermissionError(f'Neon refused {method} {path} ({e.code}): the API key cannot do this '
                                  f'(an organization project needs a key issued for that organization)') from e
        # Neon says WHY in the body ({"code": ..., "message": ...}); a bare HTTPError hid it (10/8/26: a 400 on
        # GET /projects with nothing to go on). The body is redacted before it is shown: it can echo request fields.
        try:
            detail = e.read().decode('utf-8', errors='replace')[:500]
        except Exception:
            detail = ''
        raise RuntimeError(f'Neon answered {e.code} to {method} {path}: {_redact.redact(detail) or e.reason}') from e


def projects(key, org_id=None, opener=None):
    """Every project the key can see (in `org_id` when given), paged through."""
    found, cursor = [], None
    while True:
        q = {'limit': PAGE}
        if org_id:
            q['org_id'] = org_id
        if cursor:
            q['cursor'] = cursor
        page = _call(f'/projects?{urllib.parse.urlencode(q)}', key, opener=opener) or {}
        batch = page.get('projects') or []
        found.extend(batch)
        cursor = (page.get('pagination') or {}).get('cursor')
        if len(batch) < PAGE or not cursor:
            return found


def organizations(key, opener=None):
    """The organizations the key can see: every one of the user's for a personal key, the owning one for an
    organization key. Each is Neon's own dict (`id`, `name`)."""
    return (_call('/users/me/organizations', key, opener=opener) or {}).get('organizations') or []


def organization_id(key, opener=None):
    """The one organization a run is scoped to when the caller names none. Neon keeps every project inside an
    organization, and a PERSONAL API key must say which (`org_id`) on every project call or the API answers
    400 (10/8/26: the first run, with Gene's personal key, died there); an organization key infers its own.
    One organization -> its id; none -> None (the key infers it); several -> RuntimeError naming them, so the
    caller passes `org_id` rather than this guessing."""
    found = organizations(key, opener=opener)
    if len(found) == 1:
        return found[0].get('id')
    if not found:
        return None
    names = ', '.join(f"{o.get('name') or '?'} ({o.get('id')})" for o in found)
    raise RuntimeError(f'the Neon key sees {len(found)} organizations - say which with org_id: {names}')


def find_project(key, name, org_id=None, opener=None):
    """The project named `name` (exact, case-insensitive), or None."""
    for p in projects(key, org_id=org_id, opener=opener):
        if (p.get('name') or '').lower() == name.lower():
            return p
    return None


def create_project(key, name, region_id=DEFAULT_REGION, pg_version=DEFAULT_PG_VERSION, database='neondb',
                   role=None, org_id=None, opener=None):
    """A new project with one branch (`main`) holding `database` owned by `role`
    (Neon's default role when None). The answer is Neon's: `project`, `branch`,
    `databases`, `roles`, `endpoints` and `connection_uris` (secrets: never log it)."""
    branch = {'name': 'main', 'database_name': database}
    if role:
        branch['role_name'] = role
    body = {'project': {'name': name, 'region_id': region_id, 'pg_version': int(pg_version), 'branch': branch}}
    if org_id:
        body['project']['org_id'] = org_id
    return _call('/projects', key, 'POST', body, opener=opener)


def ensure_project(key, name, region_id=DEFAULT_REGION, pg_version=DEFAULT_PG_VERSION, database='neondb',
                   role=None, org_id=None, opener=None):
    """The project named `name`, created when missing: (project, created). An existing
    project is reused as it is — its region and major are reported, never changed."""
    found = find_project(key, name, org_id=org_id, opener=opener)
    if found:
        return found, False
    made = create_project(key, name, region_id=region_id, pg_version=pg_version, database=database,
                          role=role, org_id=org_id, opener=opener)
    return made.get('project') or {}, True


def default_branch(key, project_id, opener=None):
    """The project's default branch (`main` on a new project)."""
    branches = (_call(f'/projects/{project_id}/branches', key, opener=opener) or {}).get('branches') or []
    for b in branches:
        if b.get('default') or b.get('primary'):
            return b
    if not branches:
        raise RuntimeError(f'Neon project {project_id} has no branch')
    return branches[0]


def roles(key, project_id, branch_id, opener=None):
    """The branch's roles (Neon's own roles are excluded)."""
    found = (_call(f'/projects/{project_id}/branches/{branch_id}/roles', key, opener=opener) or {}).get('roles') or []
    return [r for r in found if not r.get('protected')]


def databases(key, project_id, branch_id, opener=None):
    """The branch's databases."""
    return (_call(f'/projects/{project_id}/branches/{branch_id}/databases', key, opener=opener) or {}).get('databases') or []


def ensure_database(key, project_id, branch_id, name, owner, opener=None):
    """The database `name` on the branch, created for `owner` when missing: (database, created)."""
    for d in databases(key, project_id, branch_id, opener=opener):
        if (d.get('name') or '') == name:
            return d, False
    made = _call(f'/projects/{project_id}/branches/{branch_id}/databases', key, 'POST',
                 {'database': {'name': name, 'owner_name': owner}}, opener=opener)
    return (made or {}).get('database') or {'name': name, 'owner_name': owner}, True


def connection_uri(key, project_id, branch_id, database, role, pooled=False, opener=None):
    """The connection URL for `role` on `database` of the branch: the direct endpoint
    unless `pooled`. A secret: register it, never print it."""
    q = urllib.parse.urlencode({'branch_id': branch_id, 'database_name': database, 'role_name': role,
                                'pooled': 'true' if pooled else 'false'})
    uri = (_call(f'/projects/{project_id}/connection_uri?{q}', key, opener=opener) or {}).get('uri') or ''
    if not uri:
        raise RuntimeError(f'Neon answered no connection URL for {database} on {project_id}')
    _redact.register_secret_values(uri)
    return uri


def host_of(uri):
    """The host of a connection URL: the one part safe to print."""
    try:
        return urllib.parse.urlsplit(uri).hostname or ''
    except ValueError:
        return ''


def provision(key, name, region_id=DEFAULT_REGION, pg_version=DEFAULT_PG_VERSION, database='neondb',
              role=None, org_id=None, pooled=False, opener=None):
    """Find or create the project, find or create its database, and read its connection
    URL. Idempotent: a second run creates nothing. The answer's `connection_uri` is the
    secret; everything else (`project_id`, `project_name`, `region_id`, `pg_version`,
    `branch_id`, `database`, `role`, `host`, `created_project`, `created_database`) may
    be printed. `org_id` is discovered from the key when not given (`organization_id`)."""
    org_id = org_id or organization_id(key, opener=opener)
    project, created = ensure_project(key, name, region_id=region_id, pg_version=pg_version, database=database,
                                      role=role, org_id=org_id, opener=opener)
    project_id = project['id']
    branch = default_branch(key, project_id, opener=opener)
    branch_roles = roles(key, project_id, branch['id'], opener=opener)
    if not branch_roles:
        raise RuntimeError(f'Neon project {project_id} has no role to own {database}')
    owner = role if role and any(r.get('name') == role for r in branch_roles) else branch_roles[0]['name']
    db, made_db = ensure_database(key, project_id, branch['id'], database, owner, opener=opener)
    uri = connection_uri(key, project_id, branch['id'], database, db.get('owner_name') or owner, pooled=pooled,
                         opener=opener)
    return {'project_id': project_id, 'project_name': project.get('name') or name,
            'region_id': project.get('region_id') or region_id, 'pg_version': project.get('pg_version') or pg_version,
            'branch_id': branch['id'], 'database': database, 'role': db.get('owner_name') or owner,
            'host': host_of(uri), 'created_project': created, 'created_database': made_db, 'connection_uri': uri}


FREE_TRANSFER_BYTES = 5 * 1024 ** 3   # the free plan's monthly public network transfer; past it the database suspends


def endpoints(key, project_id, opener=None):
    """The project's compute endpoints (one read-write per branch)."""
    return (_call(f'/projects/{project_id}/endpoints', key, opener=opener) or {}).get('endpoints') or []


def cap_compute(key, project_id, max_cu, min_cu=None, endpoint_id=None, opener=None):
    """Cap the project's compute at `max_cu` (and floor it at `min_cu`) on `endpoint_id`,
    or on every endpoint when None: the answer is the endpoints as Neon shows them
    afterwards. Neon's paid plans have spending notifications but no hard cap, so the
    compute ceiling is what bounds the bill: SMAD PickleBot's Launch upgrade raised it to
    8 CU (~$620 a month worst case) and was capped back to 0.25 CU the same hour (10/6/26)."""
    targets = [endpoint_id] if endpoint_id else [e['id'] for e in endpoints(key, project_id, opener=opener)]
    body = {'autoscaling_limit_max_cu': float(max_cu)}
    if min_cu is not None:
        body['autoscaling_limit_min_cu'] = float(min_cu)
    out = []
    for eid in targets:
        path = f'/projects/{project_id}/endpoints/{eid}'
        try:
            answer = _call(path, key, 'PATCH', {'endpoint': body}, opener=opener)
        except RuntimeError as e:
            if min_cu is not None or 'min is larger than max' not in str(e):
                raise
            # Neon refuses a max below the endpoint's current min ("autoscaling limit min is larger than max"; a fresh
            # free-plan endpoint sits at 1-1 CU, 10/8/26): the floor comes down with the ceiling unless the caller set it.
            answer = _call(path, key, 'PATCH', {'endpoint': dict(body, autoscaling_limit_min_cu=float(max_cu))}, opener=opener)
        out.append((answer or {}).get('endpoint') or {})
    return out


def consumption(key, project_id, since, until, org_id=None, granularity='daily', opener=None):
    """The project's metered consumption between `since` and `until` (RFC 3339 UTC stamps),
    summed over the period: every `*_bytes` and `*_seconds` metric Neon reports
    (`data_transfer_bytes`, `written_data_bytes`, `compute_time_seconds`, `active_time_seconds`;
    `synthetic_storage_size_bytes` is a gauge and answers its latest value), plus `periods`,
    the raw answer, for anything else. A metric Neon did not report is absent, never zero."""
    q = {'project_ids': project_id, 'from': since, 'to': until, 'granularity': granularity}
    if org_id:
        q['org_id'] = org_id
    answer = _call(f'/consumption_history/projects?{urllib.parse.urlencode(q)}', key, opener=opener) or {}
    totals = {}
    for project in answer.get('projects') or []:
        if project.get('project_id') not in (None, project_id):
            continue
        for period in project.get('periods') or []:
            for entry in period.get('consumption') or []:
                for metric, value in entry.items():
                    if not isinstance(value, (int, float)) or isinstance(value, bool):
                        continue
                    if not (metric.endswith('_bytes') or metric.endswith('_seconds')):
                        continue
                    if metric == 'synthetic_storage_size_bytes':
                        totals[metric] = value
                    else:
                        totals[metric] = totals.get(metric, 0) + value
    totals['periods'] = answer.get('projects') or []
    return totals


PROJECT_METRICS = ('data_transfer_bytes', 'written_data_bytes', 'compute_time_seconds', 'active_time_seconds')
"""The consumption counters Neon keeps on the project object itself, for the current billing period."""


def project_usage(key, project_id, opener=None):
    """The project's own period counters (`GET /projects/{id}`): the same metrics as `consumption()` for the current
    billing period, readable with any key that can see the project. Tagged `source: 'project'`."""
    project = (_call(f'/projects/{project_id}', key, opener=opener) or {}).get('project') or {}
    totals = {m: project[m] for m in PROJECT_METRICS if isinstance(project.get(m), (int, float)) and not isinstance(project.get(m), bool)}
    totals['source'] = 'project'
    return totals


def month_to_date(key, project_id, org_id=None, now=None, opener=None):
    """This calendar month's consumption so far (UTC month, which is what Neon meters):
    `consumption()` from the first of the month to `now`, tagged `source: 'consumption_history'`. That endpoint answers
    403 to a personal API key (10/8/26, the ledger's project); then the project object's own period counters
    (`project_usage`) stand in - the same four metrics for the current billing period."""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)   # utc-ok: Neon meters the calendar month in UTC, not a Pacific day
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    fmt = '%Y-%m-%dT%H:%M:%SZ'
    try:
        totals = consumption(key, project_id, start.strftime(fmt), now.strftime(fmt), org_id=org_id, opener=opener)
    except PermissionError:
        return project_usage(key, project_id, opener=opener)
    totals['source'] = 'consumption_history'
    return totals


def transfer_budget(totals, budget_bytes=FREE_TRANSFER_BYTES):
    """(used_bytes, percent, line) for the month's `data_transfer_bytes` against a budget
    (the free plan's 5 GB by default): the line is printable, and `percent` is None when
    Neon reported no transfer metric, so a sweep says 'not measured', never 0%."""
    used = totals.get('data_transfer_bytes')
    if used is None:
        return None, None, 'network transfer: not reported by Neon for this period'
    percent = 100.0 * used / budget_bytes if budget_bytes else None
    gb = used / 1024 ** 3
    line = f"network transfer: {gb:.2f} GB of {budget_bytes / 1024 ** 3:.0f} GB ({percent:.0f}%)"
    if percent is not None and percent >= 80:
        line += ' - WARNING: the free plan suspends the database at 100%'
    return used, percent, line


def _key(args):
    value = os.environ.get(args.key_env, '').strip() if args.key_env else ''
    if value:
        return value
    if args.key_secret:
        from . import secrets
        project, _, name = args.key_secret.partition('/')
        value = secrets.access(project, name).decode('utf-8').strip()
        if value:
            _redact.register_secret_values(value)
            return value
    raise SystemExit(f'no Neon API key: set {args.key_env or "the key variable"} or pass --key-secret project/NAME')


def main(argv=None):
    """`python -m chuang_platform_kit.neon --name X --database Y [--store project/SECRET]`:
    provision and, with `--store`, write the connection URL as a new version of that Secret
    Manager secret (created when missing). Prints ids, host and the version name only."""
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--name', required=True, help='the Neon project name (reused when it exists)')
    ap.add_argument('--database', default='neondb', help='the database on the default branch (created when missing)')
    ap.add_argument('--region', default=DEFAULT_REGION, help=f'region for a NEW project (default {DEFAULT_REGION})')
    ap.add_argument('--pg', type=int, default=DEFAULT_PG_VERSION, help=f'Postgres major for a NEW project (default {DEFAULT_PG_VERSION})')
    ap.add_argument('--role', default=None, help='the owning role (the branch\'s first role when omitted)')
    ap.add_argument('--org', default=None, help='the Neon organization id (the key\'s own scope when omitted)')
    ap.add_argument('--pooled', action='store_true', help='the pooled URL instead of the direct one')
    ap.add_argument('--key-env', default='NEON_API_KEY', help='environment variable holding the Neon API key')
    ap.add_argument('--key-secret', default=None, help='Secret Manager project/NAME holding the key, when the variable is empty')
    ap.add_argument('--store', default=None, help='Secret Manager project/NAME to write the URL to (created when missing)')
    ap.add_argument('--cap-cu', type=float, default=None, help='cap every endpoint of the project at this many compute units (0.25 is the smallest)')
    ap.add_argument('--usage', action='store_true', help="print this month's metered consumption and the transfer against --budget-gb; provisions nothing")
    ap.add_argument('--budget-gb', type=float, default=FREE_TRANSFER_BYTES / 1024 ** 3, help='the monthly transfer budget for --usage (default: the free plan, 5)')
    args = ap.parse_args(argv)
    key = _key(args)
    if args.usage:
        org_id = args.org or organization_id(key)
        project = find_project(key, args.name, org_id=org_id)
        if not project:
            raise SystemExit(f'no Neon project named {args.name}')
        totals = month_to_date(key, project['id'], org_id=org_id)
        period = 'this month to now (UTC)' if totals.get('source') == 'consumption_history' else \
            "the current billing period, from the project's own counters (consumption history needs an organization key)"
        print(f"project {project['id']} ({project.get('name')}), {period}:")
        for metric in sorted(k for k in totals if k not in ('periods', 'source')):
            value = totals[metric]
            shown = f"{value / 1024 ** 2:.1f} MB" if metric.endswith('_bytes') else f"{value / 3600:.2f} h"
            print(f"  {metric}: {shown}")
        print('  ' + transfer_budget(totals, int(args.budget_gb * 1024 ** 3))[2])
        return 0
    made = provision(key, args.name, region_id=args.region, pg_version=args.pg, database=args.database,
                     role=args.role, org_id=args.org, pooled=args.pooled)
    verb = 'created' if made['created_project'] else 'reused'
    print(f"project {made['project_id']} ({made['project_name']}, {made['region_id']}, Postgres {made['pg_version']}): {verb}")
    print(f"database {made['database']} owned by {made['role']}: {'created' if made['created_database'] else 'reused'}")
    print(f"host {made['host']} ({'pooled' if args.pooled else 'direct'})")
    if args.cap_cu is not None:
        capped = cap_compute(key, made['project_id'], args.cap_cu)
        print(f"compute capped at {args.cap_cu} CU on {len(capped)} endpoint(s)")
    if args.store:
        from . import secrets
        project, _, name = args.store.partition('/')
        created = secrets.ensure(project, name)
        version = secrets.add_version(project, name, made['connection_uri'])
        print(f"stored as {version}{' (secret created)' if created else ''}")
    return 0


if __name__ == '__main__':   # pragma: no cover - the CLI
    raise SystemExit(main())
