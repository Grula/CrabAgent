from __future__ import annotations

import bcrypt

_ROUNDS = 12
_PREFIX = b"2b"


def hash_password(password: str) -> str:
    """Hash a plaintext password into a bcrypt ``$2b$12$...`` string."""
    salt = bcrypt.gensalt(rounds=_ROUNDS, prefix=_PREFIX)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Check a plaintext password against a stored hash.

    Returns False (never raises) for missing, malformed or non-bcrypt hashes:
    both callers treat the result as a plain boolean.
    """
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (AttributeError, ValueError, TypeError):
        return False
