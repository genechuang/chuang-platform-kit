"""A module imported on first use, not at import time.

`requests` costs ~0.25 s to import on the desktop (0.43 s for the whole shared
package before this). weather.py and greenapi.py imported it at the top, so every
test suite that touched the formatters paid for a library it never calls, and a
Cloud Function cold start paid for it before its first line ran (2026-10-02 PT:
the test-coverage push added 29 suites, and CI runs them on two cores).

`requests = lazy('requests')` keeps a module-level name, so a test can still
replace it (`weather.requests = fake`) or one attribute on it
(`weather.requests.get = fake`), and `except requests.RequestException` works.

`build = lazy_attr('googleapiclient.discovery', 'build')` does the same for a
`from x import Y` name that is only called or read from (`build(...)`,
`Credentials.from_authorized_user_file(...)`). It is NOT a class: never use one
in an `except` clause or `isinstance()`; read the class off a `lazy()` module
there instead (`except _api_exceptions.AlreadyExists`). 2026-10-02 PT:
`google.api_core` alone costs ~0.8 s on the desktop (its import-time
dependency-version check walks every installed distribution), and
`googleapiclient.discovery` and `google.cloud.firestore` both pull it in.

`importable(...)` answers "is it installed?" without importing it, and
`require(...)` raises ImportError when it is not, so a module that used to fail
fast at import (`except ImportError: sys.exit(1)`) still does.
"""
import importlib
import importlib.util
import sys


class _Lazy:
    def __init__(self, name):
        object.__setattr__(self, '_name', name)
        object.__setattr__(self, '_mod', None)

    def _load(self):
        mod = object.__getattribute__(self, '_mod')
        if mod is None:
            mod = importlib.import_module(object.__getattribute__(self, '_name'))
            object.__setattr__(self, '_mod', mod)
        return mod

    def __getattr__(self, attr):
        # Only reached for names not set on this object: a test's
        # `lazy.get = fake` is found first and wins, as on the real module.
        return getattr(self._load(), attr)


class _LazyAttr:
    def __init__(self, module, attr):
        self._module = module
        self._attr = attr

    def _load(self):
        return getattr(importlib.import_module(self._module), self._attr)

    def __getattr__(self, attr):
        return getattr(self._load(), attr)

    def __call__(self, *args, **kwargs):
        return self._load()(*args, **kwargs)


def lazy(name: str):
    """A stand-in for module `name` that imports it on its first attribute read."""
    return _Lazy(name)


def lazy_attr(module: str, attr: str):
    """A stand-in for `from module import attr` that imports `module` on its first call or attribute read."""
    return _LazyAttr(module, attr)


def importable(*names: str) -> bool:
    """True when every module in `names` is installed, without importing any of them (only their parent packages)."""
    for name in names:
        if name in sys.modules:   # already imported, or a test's stand-in (None blocks it, as import does)
            if sys.modules[name] is None:
                return False
            continue
        try:
            if importlib.util.find_spec(name) is None:
                return False
        except ImportError:   # a parent package is missing
            return False
    return True


def require(*names: str) -> None:
    """Raise ImportError unless every module in `names` is installed (see importable()): the fail-fast an eager import gave."""
    if not importable(*names):
        raise ImportError(f"not installed: {', '.join(n for n in names if not importable(n))}")
