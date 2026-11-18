"""Shared helpers for the OAuth model layer."""

import hashlib


def sha256_hex(value):
    """Return the hex SHA-256 digest of ``value`` (OAuth secrets are stored hashed)."""
    return hashlib.sha256(value.encode()).hexdigest()
