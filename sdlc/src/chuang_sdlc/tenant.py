"""One host serving several boards (SMADPickleBot #1652, 0.30.0): a tenant's wiring laid over each module's own.

`tickets`, `listeners` and `scrum` each keep the host's wiring in one table (`configure()` writes it). A host that
serves more than one board (a webhook receiving every Project's edits, a sweep run per board) wraps each delivery in
`using(...)`: inside it every table reads the tenant's values first and the host's `configure()` values for anything the
tenant leaves out, so nothing one board configured leaks into another's call and nothing is configured twice.

    with tenant.using('chuangfinance', tickets={'board': lambda: cfg, 'people': {...}},
                      scrum={'lead': 'Gene'}, listeners={'claims': 'chuangfinance__ci_releases'}):
        listeners.board_wake(data, store, now)

A dict value is merged over the host's (a tenant's `people` replaces only the roles it names). The layer is a context
variable, so it holds for the thread or task that entered it and ends with the block, a raised error included.
`configure()` inside a layer still writes the host's own table. Standard library only.
"""
import contextvars
import importlib
from contextlib import contextmanager

_LAYER = contextvars.ContextVar('chuang_sdlc_tenant', default=None)
_TABLES = {}   # module name -> its Layered table, registered when the module is imported
MODULES = ('tickets', 'listeners', 'scrum')


class Layered(dict):
    """A module's wiring table: the host's values (the dict itself), with the current tenant's layered over them."""

    def __init__(self, module: str, base: dict):
        super().__init__(base)
        self.module = module
        _TABLES[module] = self

    def _layer(self) -> dict:
        current = _LAYER.get()
        return ((current or {}).get('modules') or {}).get(self.module) or {}

    def __getitem__(self, name):
        layer = self._layer()
        base = dict.__getitem__(self, name)
        if name not in layer:
            return base
        value = layer[name]
        if isinstance(value, dict) and isinstance(base, dict):
            return dict(base, **value)
        return value

    def get(self, name, default=None):
        try:
            return self[name]
        except KeyError:
            return default


@contextmanager
def using(key: str, **modules):
    """Run the block as tenant `key`, each module's wiring (`tickets=`, `listeners=`, `scrum=`, dicts of the names that
    module's configure() takes) laid over the host's. A module or a name the module does not take is refused, before
    anything runs."""
    if not key:
        raise ValueError('tenant.using() needs the tenant key')
    for module, wiring in modules.items():
        if module not in MODULES:
            raise TypeError(f'tenant.using() takes no module {module!r}')
        if module not in _TABLES:
            importlib.import_module(f'{__package__}.{module}')   # registers its table
        table = _TABLES.get(module)
        for name in (wiring or {}):
            if table is None or not dict.__contains__(table, name):
                raise TypeError(f'tenant.using({module}=) takes no {name!r}')
    token = _LAYER.set({'key': key, 'modules': {m: dict(w or {}) for m, w in modules.items()}})
    try:
        yield
    finally:
        _LAYER.reset(token)


def current() -> str:
    """The key of the tenant whose layer is in force, '' outside any."""
    return ((_LAYER.get() or {}).get('key')) or ''
