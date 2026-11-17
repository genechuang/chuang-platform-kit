"""A link that carries its own proof: an HMAC-SHA256 signature of a value under a key, as a query parameter, so a
reader with no sign-in (an image proxy, a mail client) loads the address and only for a value the host signed.

The host that writes the link and the host that serves it share the key and this one copy of the signature. The key
never appears in the link; the signature is cut to `length` hex characters (32 by default, 128 bits). Standard library
only.
"""
import hashlib
import hmac

LENGTH = 32


def signature(key: str, value: str, length: int = LENGTH) -> str:
    """The signature of `value` under `key`: HMAC-SHA256, its first `length` hex characters. The value is signed as
    given: a host that reads an id in any case normalizes it before it signs and before it checks."""
    return hmac.new((key or '').encode(), str(value if value is not None else '').encode(), hashlib.sha256).hexdigest()[:length]


def verify(key: str, value: str, sig: str, length: int = LENGTH) -> bool:
    """Whether `sig` is `value`'s signature under `key`, compared in constant time; False without a key or a
    signature."""
    if not key or not sig:
        return False
    return hmac.compare_digest(str(sig), signature(key, value, length))


def signed_url(base: str, path: str, key: str, value: str, param: str = 'sig', length: int = LENGTH) -> str:
    """`base` + `path` with `?<param>=<signature of value>` ('' without a key). `path` starts with a slash and is
    already quoted; `base`'s trailing slash is dropped."""
    if not key:
        return ''
    return f"{base.rstrip('/')}{path}?{param}={signature(key, value, length)}"
