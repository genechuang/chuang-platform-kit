#!/usr/bin/env python3
"""Whether a push's API migrations only add (Gene, 10/24/26 10:57 PM PT: "Yes add migrations climb").

In SMADPickleBot, the first host, the dock's dev rung shares production's database, so since 10/23/26 a dock batch
with a migration left the API to the ship (deploy.yml's API decide step). A migration that only adds (a table, a
column, an index, a constraint, rows) cannot break the revision production still runs, so it now climbs to dev with
the rest of the batch; anything else still waits for Gene's ship word.

    python -m chuang_sdlc.cicd.migration_kind BEFORE AFTER     prints `expand` (exit 0) or `contract: <why>` (exit 1)

A migration is `expand` when every statement of its `upgrade()` is a call to one of ADDS (or `pass`, or a docstring),
an `op.execute` of SQL that only creates or inserts, or, since 10/25/26 (Gene, 7:41 PM PT: "treat a migration whose
UPDATEs only fill columns it adds as add-only, so the API climbs dev in the warm"), an `UPDATE <table> SET <col> = ...`
whose every SET column this same migration added to that table earlier in upgrade(): production's revision never reads
a column it did not know existed, so filling one cannot break it (0046's backfill of the player-id columns, which made
its API wait for the ship and cost 011ad6e7 about 30 s). The forms that come with it are followed too: a `with
op.batch_alter_table(t) as b:` block whose calls only add, and a `for` loop over a module constant (a literal list of
tuples), unrolled with f-strings filled in. A file changed or removed (an applied migration rewritten), a drop, a
rename, an alter, an UPDATE of any other column, a DELETE, a loop over anything else, a helper call, or a file that
does not parse is `contract`. No migration in the diff is `expand`. Standard library and git.
"""
import ast
import re
import subprocess
import sys

VERSIONS = 'services/api/migrations/versions/'
ADDS = {'create_table', 'add_column', 'create_index', 'create_unique_constraint', 'create_foreign_key',
        'create_check_constraint', 'create_primary_key', 'bulk_insert'}
ADDING_SQL = re.compile(r'^\s*(create\s+(table|index|unique\s+index|extension|sequence|type)|insert\s+into)\b', re.I)
UPDATE_SQL = re.compile(r'^\s*update\s+([a-z_][a-z0-9_]*)(?:\s+(?:as\s+)?(?!set\b)[a-z_][a-z0-9_]*)?\s+set\s+(.*)$', re.I | re.S)


def _top_split(text, seps):
    """`text` cut at its depth-0 occurrences (outside parentheses and quotes) of a comma when ',' is in `seps`, or at
    the first depth-0 keyword in `seps`; the pieces in order."""
    out, depth, quote, start, i = [], 0, '', 0, 0
    low = text.lower()
    while i < len(text):
        ch = text[i]
        if quote:
            quote = '' if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        elif depth == 0:
            if ',' in seps and ch == ',':
                out.append(text[start:i])
                start = i + 1
            else:
                for w in seps:
                    if w != ',' and low.startswith(w, i) and (i == 0 or not low[i - 1].isalnum()) \
                            and (i + len(w) >= len(low) or not low[i + len(w)].isalnum()):
                        return out + [text[start:i], text[i + len(w):]]
        i += 1
    return out + [text[start:]]


def update_targets(sql):
    """(table, {columns}) an `UPDATE table [alias] SET a = ..., b = ... [FROM ...] [WHERE ...]` sets, or None."""
    m = UPDATE_SQL.match(sql)
    if not m:
        return None
    sets = _top_split(m.group(2), ('where', 'from', 'returning'))[0]
    cols = set()
    for part in _top_split(sets, (',',)):
        name = part.split('=', 1)[0].strip().lower()
        if '=' not in part or not re.fullmatch(r'[a-z_][a-z0-9_]*', name):
            return None
        cols.add(name)
    return m.group(1).lower(), cols


def _string(node, env):
    """A string constant, a loop name bound in `env` to a string, or an f-string whose holes are such names, as text;
    None otherwise."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and isinstance(env.get(node.id), str):
        return env[node.id]
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            elif isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) and v.value.id in env \
                    and v.conversion == -1 and v.format_spec is None:
                out.append(str(env[v.value.id]))
            else:
                return None
        return ''.join(out)
    return None


def _column_name(node, env):
    """The name of `sa.Column(<name>, ...)`, or None."""
    if isinstance(node, ast.Call) and node.args and isinstance(node.func, ast.Attribute) and node.func.attr == 'Column':
        return _string(node.args[0], env)
    return None


def _bind(target, value, env):
    """`env` with a for-loop's target bound to one item of its constant, or None when the shapes differ."""
    if isinstance(target, ast.Name):
        return {**env, target.id: value}
    if isinstance(target, ast.Tuple) and isinstance(value, (tuple, list)) and len(target.elts) == len(value):
        for t, v in zip(target.elts, value):
            if not isinstance(t, ast.Name):
                return None
            env = {**env, t.id: v}
        return env
    return None


def statement_kind(stmt, env=None, added=None, consts=None):
    """'' when one statement of upgrade() only adds, else why it does not. `added` collects the (table, column) pairs
    added so far, which a later UPDATE may fill; `env` binds an unrolled loop's names; `consts` the module's literals."""
    env = {} if env is None else env
    added = set() if added is None else added
    consts = {} if consts is None else consts
    if isinstance(stmt, ast.Pass) or (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)):
        return ''
    if isinstance(stmt, ast.For):
        if stmt.orelse or not isinstance(stmt.iter, ast.Name) or stmt.iter.id not in consts:
            return f'line {stmt.lineno} loops over something other than a module constant'
        for value in consts[stmt.iter.id]:
            inner = _bind(stmt.target, value, env)
            if inner is None:
                return f'line {stmt.lineno} loops in a shape this check cannot follow'
            for s in stmt.body:
                why = statement_kind(s, inner, added, consts)
                if why:
                    return why
        return ''
    if isinstance(stmt, ast.With):
        item = stmt.items[0] if len(stmt.items) == 1 else None
        call = item.context_expr if item else None
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name)
                and call.func.value.id == 'op' and call.func.attr == 'batch_alter_table' and call.args
                and isinstance(item.optional_vars, ast.Name)):
            return f'line {stmt.lineno} opens a block other than op.batch_alter_table'
        table, alias = _string(call.args[0], env), item.optional_vars.id
        if table is None:
            return f'line {stmt.lineno} alters a table this check cannot name'
        for s in stmt.body:
            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and isinstance(s.value.func, ast.Attribute)
                    and isinstance(s.value.func.value, ast.Name) and s.value.func.value.id == alias):
                return f'line {s.lineno} is not a plain call on the batch'
            if s.value.func.attr not in ADDS:
                return f'line {s.lineno} calls {alias}.{s.value.func.attr}'
            if s.value.func.attr == 'add_column':
                col = _column_name(s.value.args[0], env) if s.value.args else None
                if col:
                    added.add((table.lower(), col.lower()))
        return ''
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return f'line {stmt.lineno} is not a plain op call'
    func = stmt.value.func
    if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == 'op'):
        return f'line {stmt.lineno} calls something other than op'
    if func.attr in ADDS:
        if func.attr == 'add_column' and len(stmt.value.args) >= 2:
            table, col = _string(stmt.value.args[0], env), _column_name(stmt.value.args[1], env)
            if table and col:
                added.add((table.lower(), col.lower()))
        return ''
    if func.attr == 'execute':
        args = stmt.value.args
        sql = _string(args[0], env) if args else None
        if sql is None:
            return f'line {stmt.lineno} executes SQL this check cannot read'
        for part in (p for p in sql.split(';') if p.strip()):
            if ADDING_SQL.match(part):
                continue
            got = update_targets(part)
            if got and all((got[0], c) in added for c in got[1]):
                continue
            return (f'line {stmt.lineno} updates a column this migration did not add' if got
                    else f'line {stmt.lineno} executes SQL that does more than create, insert or fill a new column')
        return ''
    return f'line {stmt.lineno} calls op.{func.attr}'


def migration_kind(source):
    """'' when a migration file's upgrade() only adds (an UPDATE filling a column it added counts), else why not."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 'it does not parse'
    upgrade = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'upgrade'), None)
    if upgrade is None:
        return 'it has no upgrade()'
    consts = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            try:
                value = ast.literal_eval(n.value)
            except (ValueError, SyntaxError):
                continue
            if isinstance(value, (list, tuple)):
                consts[n.targets[0].id] = value
    added = set()
    for stmt in upgrade.body:
        why = statement_kind(stmt, {}, added, consts)
        if why:
            return why
    return ''


def _git(*args):
    return subprocess.run(['git', *args], capture_output=True, text=True, encoding='utf-8', errors='replace')


def push_kind(before, after, git=_git):
    """('expand', '') or ('contract', why) for the migrations a push changes."""
    diff = git('diff', '--name-status', before, after, '--', VERSIONS)
    if diff.returncode != 0:
        return 'contract', 'the diff could not be read'
    for line in diff.stdout.splitlines():
        status, _, path = line.partition('\t')
        if not path.endswith('.py'):
            continue
        if status.strip() != 'A':
            return 'contract', f'{path} was changed or removed, not added'
        shown = git('show', f'{after}:{path}')
        why = migration_kind(shown.stdout) if shown.returncode == 0 else 'it could not be read'
        if why:
            return 'contract', f'{path}: {why}'
    return 'expand', ''


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        return 2
    kind, why = push_kind(*argv)
    print(kind if kind == 'expand' else f'contract: {why}')
    return 0 if kind == 'expand' else 1


if __name__ == '__main__':
    raise SystemExit(main())
