#!/usr/bin/env python3
"""
encrypt_py_files.py — encrypt ONLY daily.py and daily9.py.
AES-256-GCM, PBKDF2-HMAC-SHA256 (600000 iters), idempotent.

Usage (local):
    pip install cryptography
    export CODE_ENCRYPTION_PASSWORD='your-strong-passphrase'
    python encrypt_py_files.py
    unset CODE_ENCRYPTION_PASSWORD

File format: PYENC1 || SALT(16) || NONCE(12) || CIPHERTEXT+TAG
"""
import os
import sys
from pathlib import Path
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC       = b"PYENC1"
SALT_LEN    = 16
NONCE_LEN   = 12
PBKDF2_ITER = 600_000

# Only these files get encrypted
TARGETS = ["daily.py", "daily9.py"]


def derive_key(password: bytes, salt: bytes) -> bytes:
    return PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITER,
    ).derive(password)


def main() -> int:
    pw = os.environ.get("CODE_ENCRYPTION_PASSWORD", "")
    if not pw:
        print("❌ CODE_ENCRYPTION_PASSWORD env var not set", file=sys.stderr)
        return 1
    if len(pw) < 16:
        print("❌ Password must be at least 16 characters", file=sys.stderr)
        return 1
    password = pw.encode()

    encrypted, skipped = [], []
    for name in TARGETS:
        path = Path(name)
        if not path.exists():
            print(f"⚠️  missing: {name} — skipping")
            continue
        data = path.read_bytes()
        if data.startswith(MAGIC):
            skipped.append(name)
            continue
        salt  = os.urandom(SALT_LEN)
        nonce = os.urandom(NONCE_LEN)
        key   = derive_key(password, salt)
        ct    = AESGCM(key).encrypt(nonce, data, None)
        path.write_bytes(MAGIC + salt + nonce + ct)
        encrypted.append(name)

    print(f"🔐 Encrypted: {len(encrypted)}")
    for n in encrypted:
        print(f"   + {n}")
    print(f"⏭️  Skipped (already encrypted): {len(skipped)}")
    for n in skipped:
        print(f"   = {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
