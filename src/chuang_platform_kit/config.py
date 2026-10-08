"""A versioned configuration document, read at runtime from one source, cached, with a change signal.

The shape every host's runtime config takes once it stops living in deploy-time environment
variables: **a JSON document with a version** that one authority serves (a settings row behind an
API, a file, a flag service), that every process reads through this module, and that changes
without a deploy. Environment variables keep the wiring (URLs, resource names, keys); the document
keeps the rules an operator may change at runtime. A value lives in one of them, never both.

    cfg = Config(fetch, defaults={'session_hours': 2}, fallback=os.environ, ttl=300)
    cfg.get('booking.target_time')        # a dotted path into the document, cached
    cfg.version                           # the document's version, for an ETag or a response header
    cfg.mark_dirty()                      # a change signal arrived (a push, a header, a listener): read again next time

`fetch(version)` is the host's one line of transport: it is given the version the process holds and
answers a `Document` (new), `None` (unchanged, the 304 case) or raises (unreachable). The document is
read again when `ttl` seconds have passed or `mark_dirty()` was called; a failed read keeps the last
document, marks the config `stale`, and waits `retry_after` before trying again, so an outage of the
source never becomes a storm against it. Before any document has been read, `fallback` (a mapping or
a callable giving one — a host's environment) answers; once a document exists, a fallback value that
disagrees with it is a WARNING, once per key, so a variable that outlived its move to the document
is found. `defaults` are the code's own for keys the document lacks.

`provider(cfg)` wraps a Config as an OpenFeature provider (the `config` extra installs the SDK), so a
host reads flags through the OpenFeature API and can later put a flag service (GrowthBook, flagd,
LaunchDarkly) behind the same call sites by swapping the provider. Without the SDK, `Config` works on
its own; only `provider()` needs it.

`etag(version)` and `unchanged(if_none_match, version)` are the two lines an HTTP server needs to
answer a conditional GET for the document and to stamp every response with the current version.
"""
import logging
import threading
import time

log = logging.getLogger(__name__)

MISSING = object()   # get()'s "no default: raise"
_ABSENT = object()   # the provider's "the document has no such key: the flag's default"


class Document:
    """One read of the configuration: its `version` (any string that changes when the content
    does: an updated-at stamp, a hash, a counter), its `data` (a mapping) and when it was read."""
    __slots__ = ('version', 'data', 'fetched_at')

    def __init__(self, version, data, fetched_at=None):
        self.version = str(version or '')
        self.data = dict(data or {})
        self.fetched_at = fetched_at

    def __repr__(self):
        return f"Document(version={self.version!r}, keys={sorted(self.data)})"


def lookup(data, key, default=MISSING):
    """The value at a dotted `key` path ('booking.target_time') in nested mappings, else `default`;
    a flat key with a dot in it is tried first, so either spelling works."""
    if isinstance(data, dict) and key in data:
        return data[key]
    node = data
    for part in str(key).split('.'):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


class Config:
    """A cached, versioned configuration document with a change signal (module docstring)."""

    def __init__(self, fetch, *, defaults=None, fallback=None, ttl=300.0, retry_after=30.0,
                 clock=time.monotonic, name='config'):
        self._fetch = fetch
        self._defaults = dict(defaults or {})
        self._fallback = fallback
        self.ttl = float(ttl)
        self.retry_after = float(retry_after)
        self._clock = clock
        self.name = name
        self.document = None
        self.stale = False
        self._next_at = None          # the clock reading before which no refresh happens
        self._dirty = False
        self._warned = set()
        self._listeners = []
        self._lock = threading.RLock()

    # -- the document

    @property
    def version(self):
        """The current document's version, '' before one has been read."""
        return self.document.version if self.document else ''

    def mark_dirty(self):
        """A change signal: the next read fetches again, whatever the ttl says."""
        with self._lock:
            self._dirty = True

    def on_change(self, callback):
        """Call `callback(old_version, new_version)` whenever a new document is taken."""
        self._listeners.append(callback)
        return callback

    def refresh(self, force=False):
        """Read the source if it is time (ttl passed, dirty, or `force`): True when a new document was
        taken, False when unchanged, skipped or unreachable (then `stale` says so)."""
        with self._lock:
            now = self._clock()
            if not force and not self._dirty and self._next_at is not None and now < self._next_at:
                return False
            self._dirty = False
            try:
                fresh = self._fetch(self.version)
            except Exception as e:   # noqa: BLE001 - the source is down; the last document stands
                self._next_at = now + self.retry_after
                if not self.stale:
                    log.warning("%s: the configuration was not read (%s: %s); %s stands", self.name,
                                type(e).__name__, e, f"version {self.version}" if self.document else "the fallback")
                self.stale = True
                return False
            self._next_at = now + self.ttl
            self.stale = False
            if fresh is None:
                return False
            old = self.version
            if fresh.fetched_at is None:
                fresh.fetched_at = now
            self.document = fresh
            if fresh.version != old:
                for callback in list(self._listeners):
                    try:
                        callback(old, fresh.version)
                    except Exception as e:   # noqa: BLE001 - a listener never breaks a read
                        log.warning("%s: on_change listener failed (%s: %s)", self.name, type(e).__name__, e)
            return True

    # -- reads

    def _fallback_data(self):
        source = self._fallback() if callable(self._fallback) else self._fallback
        return source if source is not None else {}

    def get(self, key, default=MISSING):
        """The value for `key` (a dotted path): the document's, else the fallback's (before any document
        has been read), else the code's default, else `default`; KeyError when none has it. A fallback
        value that disagrees with the document is a WARNING, once per key."""
        self.refresh()
        value = lookup(self.document.data, key) if self.document else MISSING
        if value is not MISSING:
            other = lookup(self._fallback_data(), key)
            if other is not MISSING and str(other) != str(value) and key not in self._warned:
                self._warned.add(key)
                log.warning("%s: %s is %r in the document and %r in the fallback; the document wins", self.name, key, value, other)
            return value
        for source in (self._fallback_data() if not self.document else {}, self._defaults):
            value = lookup(source, key)
            if value is not MISSING:
                return value
        if default is MISSING:
            raise KeyError(key)
        return default

    def typed(self, key, kind, default=MISSING):
        """`get(key)` coerced to `kind` (bool reads 'true'/'1'/'yes' from a string); a value that
        cannot be coerced answers `default` (or raises ValueError without one)."""
        value = self.get(key, default)
        try:
            if kind is bool:
                return value if isinstance(value, bool) else str(value).strip().lower() in ('1', 'true', 'yes', 'on')
            return kind(value)
        except (TypeError, ValueError):
            if default is MISSING:
                raise ValueError(f"{self.name}: {key} is {value!r}, not {kind.__name__}")
            return default

    def snapshot(self):
        """Every key as the reads would answer it: the defaults under the document (or the fallback)."""
        self.refresh()
        out = dict(self._defaults)
        out.update(self.document.data if self.document else self._fallback_data())
        return out


# -- HTTP: the two lines a server needs

def etag(version):
    """The ETag for a document version (quoted, as the header wants it)."""
    return f'"{version}"'


def unchanged(if_none_match, version):
    """True when a request's If-None-Match names the current version (the 304 case)."""
    if not if_none_match or not version:
        return False
    wanted = [t.strip().strip('"').removeprefix('W/').strip('"') for t in str(if_none_match).split(',')]
    return str(version) in wanted or '*' in wanted


# -- OpenFeature

def provider(config, name='document'):
    """An OpenFeature provider over a Config: every flag read is `config.get(key)`, typed, with the
    document's version as the variant. Needs the `config` extra (openfeature-sdk)."""
    try:
        from openfeature.provider import AbstractProvider, Metadata
        from openfeature.flag_evaluation import FlagResolutionDetails, Reason
        from openfeature.exception import ErrorCode
    except ImportError as e:   # pragma: no cover - the extra is not installed
        raise ImportError("provider() needs the OpenFeature SDK: pip install chuang-platform-kit[config]") from e

    class DocumentProvider(AbstractProvider):
        def get_metadata(self):
            return Metadata(name=name)

        def get_provider_hooks(self):
            return []

        def _resolve(self, key, default, kind):
            value = config.get(key, _ABSENT)
            if value is _ABSENT:
                return FlagResolutionDetails(value=default, reason=Reason.DEFAULT, variant=config.version or None)
            if kind is bool and not isinstance(value, bool):
                value = str(value).strip().lower() in ('1', 'true', 'yes', 'on')
            elif kind in (int, float, str):
                try:
                    value = kind(value)
                except (TypeError, ValueError):
                    return FlagResolutionDetails(value=default, reason=Reason.ERROR, error_code=ErrorCode.TYPE_MISMATCH,
                                                 error_message=f"{key} is {value!r}, not {kind.__name__}")
            return FlagResolutionDetails(value=value, reason=Reason.STATIC, variant=config.version or None)

        def resolve_boolean_details(self, flag_key, default_value, evaluation_context=None):
            return self._resolve(flag_key, default_value, bool)

        def resolve_string_details(self, flag_key, default_value, evaluation_context=None):
            return self._resolve(flag_key, default_value, str)

        def resolve_integer_details(self, flag_key, default_value, evaluation_context=None):
            return self._resolve(flag_key, default_value, int)

        def resolve_float_details(self, flag_key, default_value, evaluation_context=None):
            return self._resolve(flag_key, default_value, float)

        def resolve_object_details(self, flag_key, default_value, evaluation_context=None):
            return self._resolve(flag_key, default_value, object)

    return DocumentProvider()
