#!/usr/bin/env python3
"""chuang_sdlc.cicd.migration_kind: whether a push's API migrations only add, so they climb the host's dev rung
(Gene, 10/24/26 10:57 PM PT: "Yes add migrations climb").

Pinned (positive case first): add_column, create_table, create_index and a CREATE or INSERT op.execute are add-only;
a drop, a rename, an alter, an UPDATE, a batch_alter_table, a helper call and a file that does not parse are not; an
UPDATE filling only columns the same migration added is add-only, through a batch block and an unrolled loop too; a
push that adds only add-only migrations is expand, and one that changes or removes an existing migration is contract.

Run:  python -B tests/test-sdlc-cicd-migration-kind.py
"""
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc.cicd import migration_kind as M  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


def _mig(body):
    return f'from alembic import op\nimport sqlalchemy as sa\n\n\ndef upgrade() -> None:\n{body}\n\n\ndef downgrade() -> None:\n    op.drop_table("t")\n'


def test_adding_is_add_only():
    """the positive case: add_column, create_table, create_index and a CREATE or INSERT op.execute only add (the downgrade's drops never count)"""
    eq("the positive case: these only add",
       [M.migration_kind(_mig(b)) for b in (
           '    op.add_column("t", sa.Column("c", sa.String(), nullable=True))',
           '    op.create_table("t", sa.Column("id", sa.Integer(), primary_key=True))\n    op.create_index("ix", "t", ["id"])',
           '    """docstring"""\n    op.execute("CREATE INDEX ix ON t (c); INSERT INTO t VALUES (1)")')],
       ['', '', ''])


def test_anything_else_waits():
    """  a drop, a rename, an alter, an UPDATE, a batch_alter_table, a helper call and a file that does not parse do more than add"""
    got = [bool(M.migration_kind(_mig(b))) for b in (
        '    op.drop_column("t", "c")', '    op.rename_table("a", "b")', '    op.alter_column("t", "c", nullable=False)',
        '    op.execute("UPDATE t SET c = 1")', '    with op.batch_alter_table("t") as b:\n        b.drop_column("c")',
        '    _backfill()')]
    eq("  these wait", (got, bool(M.migration_kind('def upgrade(:'))), ([True] * 6, True))


def _mod(consts, body):
    return f'from alembic import op\nimport sqlalchemy as sa\n\n{consts}\n\n\ndef upgrade() -> None:\n{body}\n'


def test_filling_a_column_it_adds_is_add_only():
    """  10/25/26 (Gene: "treat a migration whose UPDATEs only fill columns it adds as add-only"): an UPDATE setting only
    columns this migration added to that table, after it added them, is add-only; through a batch block and a loop over a
    module constant with f-strings too. An UPDATE of an existing column, of the column before it is added, of a new and an
    old column together, of the new column's name on another table, a loop over anything but a constant, and a DELETE wait"""
    add = '    op.add_column("t", sa.Column("c_id", sa.Integer()))\n'
    eq("  fills its own new column: add-only",
       [M.migration_kind(_mig(b)) for b in (
           add + '    op.execute("UPDATE t SET c_id = (SELECT p.id FROM p WHERE p.slug = t.c, p.x) WHERE c IS NOT NULL")',
           add + '    op.execute("UPDATE t AS x SET c_id = 1 FROM p WHERE x.c = p.slug")',
           '    with op.batch_alter_table("t") as b:\n        b.add_column(sa.Column("c_id", sa.Integer()))\n'
           '        b.create_index("ix", ["c_id"])\n    op.execute("UPDATE t SET c_id = 2")')]
       + [M.migration_kind(_mod('COLS = (("t", "w"), ("u", "player"))',
                               '    for table, slug in COLS:\n        with op.batch_alter_table(table) as batch:\n'
                               '            batch.add_column(sa.Column(f"{slug}_id", sa.Integer()))\n'
                               '        op.execute(f"UPDATE {table} SET {slug}_id = (SELECT 1) WHERE {slug} IS NOT NULL")'))],
       ['', '', '', ''])
    waits = [M.migration_kind(_mig(b)) for b in (
        add + '    op.execute("UPDATE t SET c = 1")',
        '    op.execute("UPDATE t SET c_id = 1")\n' + add,
        add + '    op.execute("UPDATE t SET c_id = 1, c = 2")',
        add + '    op.execute("UPDATE u SET c_id = 1")',
        add + '    op.execute("DELETE FROM t")')]
    waits.append(M.migration_kind(_mod('', '    for table in TABLES:\n        op.execute(f"UPDATE {table} SET x = 1")')))
    eq("  everything else waits, saying why",
       [w.split(' ', 2)[-1] for w in waits],
       ['updates a column this migration did not add'] * 4
       + ['executes SQL that does more than create, insert or fill a new column', 'loops over something other than a module constant'])


# The host's test_the_real_migrations (SMADPickleBot's own 0023 to 0047 files judged by name) stays in the host: it
# reads the host repository's services/api/migrations/versions, which the kit has none of.


def test_a_push_is_expand_only_when_it_adds_add_only_files():
    """  a push that adds only add-only migrations is expand; one that changes an existing migration is contract, saying which"""
    files = {'a.py': _mig('    op.add_column("t", sa.Column("c"))'), 'b.py': _mig('    op.drop_column("t", "c")')}

    def fake(status):
        def git(*args):
            if args[0] == 'diff':
                return subprocess.CompletedProcess(args, 0, status, '')
            return subprocess.CompletedProcess(args, 0, files[args[1].rsplit('/', 1)[1]], '')
        return git
    eq("  pushes", (M.push_kind('x', 'y', fake(f'A\t{M.VERSIONS}a.py\n')),
                    M.push_kind('x', 'y', fake(f'A\t{M.VERSIONS}b.py\n'))[0],
                    M.push_kind('x', 'y', fake(f'M\t{M.VERSIONS}a.py\n'))),
       (('expand', ''), 'contract', ('contract', f'{M.VERSIONS}a.py was changed or removed, not added')))


if __name__ == '__main__':
    for _name, _fn in list(globals().items()):
        if _name.startswith('test_') and callable(_fn):
            _fn()
    print(f'\n{len(fails)} failure(s)')
    sys.exit(1 if fails else 0)
