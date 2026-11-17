#!/usr/bin/env python3
"""signed_link: a link that carries an HMAC-SHA256 signature of a value, made and checked with one definition.

Every key here is invented.

Run:  python -B tests/test-signed-link.py
"""
import hashlib
import hmac
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import signed_link as L  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


# The positive case: the signature is HMAC-SHA256 of the value under the key, its first 32 hex characters.
sig = L.signature('invented-key', 'ITEM-9U8HS2')
check("the signature is HMAC-SHA256's first 32 hex characters",
      sig, hmac.new(b'invented-key', b'ITEM-9U8HS2', hashlib.sha256).hexdigest()[:32])
check("it is the value's and the key's",
      (len(sig), sig == L.signature('invented-key', 'ITEM-9U8HS3'), sig == L.signature('other-key', 'ITEM-9U8HS2'),
       sig == L.signature('invented-key', 'item-9u8hs2')),
      (32, False, False, False))
check("a length of the caller's", len(L.signature('k', 'v', 16)), 16)
check("no key and no value still sign, never raise", (len(L.signature('', 'v')), len(L.signature(None, None))), (32, 32))

check("verify: the right signature, a wrong one, none, and no key",
      (L.verify('invented-key', 'ITEM-9U8HS2', sig), L.verify('invented-key', 'ITEM-9U8HS2', sig[:-1] + 'x'),
       L.verify('invented-key', 'ITEM-9U8HS2', ''), L.verify('', 'ITEM-9U8HS2', L.signature('', 'ITEM-9U8HS2'))),
      (True, False, False, False))

check("signed_url: the base without its slash, the path, the parameter",
      L.signed_url('https://api.example.com/', '/v1/items/ITEM-9U8HS2/picture', 'invented-key', 'ITEM-9U8HS2'),
      f'https://api.example.com/v1/items/ITEM-9U8HS2/picture?sig={sig}')
check("signed_url: a parameter name of the caller's",
      L.signed_url('https://api.example.com', '/p', 'invented-key', 'ITEM-9U8HS2', param='s').endswith(f'/p?s={sig}'), True)
check("signed_url: no key, no link", L.signed_url('https://api.example.com', '/p', '', 'v'), '')

print(f"\n{len(fails)} failure(s)")
if fails:
    sys.exit(1)
