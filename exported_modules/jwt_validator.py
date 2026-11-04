# module.py
"""
Implementation of a minimal JWT validator supporting HS256 signatures.
Only standard library modules are used, adhering to the specification.
"""

from __future__ import annotations
import string

import base64
import json
import time
import hmac
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Union


# ----------------------------------------------------------------------
# 2.1  Exception
# ----------------------------------------------------------------------
class JwtValidationError(ValueError):
    """Raised for any JWT‑validation failure (format, signature, claim, …)."""
    pass


# ----------------------------------------------------------------------
# 2.2  Immutable token container
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class JwtToken:
    """
    Immutable representation of a parsed JWT.

    Attributes
    ----------
    raw: str
        The original JWT string exactly as supplied by the caller.
    header: Mapping[str, Any]
        Decoded JSON header (must be a dict‑like object).
    payload: Mapping[str, Any]
        Decoded JSON payload (must be a dict‑like object).
    signature: bytes
        The raw signature bytes after base64‑url decoding.
    """
    raw: str
    header: Mapping[str, Any]
    payload: Mapping[str, Any]
    signature: bytes


# ----------------------------------------------------------------------
# 2.3  Helper – base64‑url decode (padding‑less)
# ----------------------------------------------------------------------
def _b64url_decode(input_str: str) -> bytes:
    """
    Decode a base‑64‑url string **without** padding.

    Parameters
    ----------
    input_str: str
        Base‑64‑url encoded data (may be empty).

    Returns
    -------
    bytes
        Decoded binary data.

    Raises
    ------
    ValueError
        If `input_str` contains characters outside the URL‑safe alphabet
        (`A‑Z a‑z 0‑9 - _`) **or** if the length is not a multiple of 4 after
        padding is added.
    """
    if not isinstance(input_str, str):
        raise TypeError("input_str must be a str")
    # Validate allowed characters
    if not re.fullmatch(r"[A-Za-z0-9\-_]*", input_str):
        raise ValueError("Invalid characters in base64url string")
    # Pad with '=' to a multiple of 4
    pad_len = (4 - len(input_str) % 4) % 4
    padded = input_str + ("=" * pad_len)
    try:
        return base64.urlsafe_b64decode(padded)
    except Exception as exc:
        raise ValueError("Invalid base64url encoding") from exc


# ----------------------------------------------------------------------
# 2.4  Helper – base64‑url encode (padding‑less)
# ----------------------------------------------------------------------
def _b64url_encode(input_bytes: bytes) -> str:
    """
    Encode `input_bytes` to a base‑64‑url string **without** padding.

    This function never raises; it always returns a `str`.

    Parameters
    ----------
    input_bytes: bytes

    Returns
    -------
    str
        URL‑safe base64 representation with trailing ``=`` characters removed.
    """
    encoded = base64.urlsafe_b64encode(input_bytes).decode("ascii")
    return encoded.rstrip("=")


# ----------------------------------------------------------------------
# 2.5  Parse a JWT string
# ----------------------------------------------------------------------
def parse_jwt(token: str) -> JwtToken:
    """
    Validate the syntactic structure of a JWT and return an immutable
    :class:`JwtToken`.

    Validation performed here
    -------------------------
    * ``token`` must be a **non‑empty** ``str`` → otherwise ``TypeError``.
    * Exactly two ``'.'`` separators must be present (three parts total);
      otherwise ``JwtValidationError``.
    * The first two parts are base64‑url decoded; any decoding error raises
      ``JwtValidationError``.
    * Decoded header and payload must be **JSON objects** (`dict`);
      otherwise ``JwtValidationError``.
    * Header must contain ``"alg": "HS256"``; any other value → ``JwtValidationError``.

    Returns
    -------
    JwtToken
        Frozen dataclass with the raw token, decoded header/payload and the
        raw signature bytes.

    Raises
    ------
    TypeError
        If ``token`` is not a ``str``.
    JwtValidationError
        For any format‑related problem (wrong number of parts, bad base64,
        non‑object JSON, missing/incorrect ``alg``).
    """
    if not isinstance(token, str):
        raise TypeError("token must be a str")
    if token.strip() == "":
        raise TypeError("token must be a non‑empty string")

    parts = token.split(".")
    if len(parts) != 3:
        raise JwtValidationError("JWT must have exactly three parts")

    header_b64, payload_b64, signature_b64 = parts

    try:
        header_bytes = _b64url_decode(header_b64)
        payload_bytes = _b64url_decode(payload_b64)
        signature_bytes = _b64url_decode(signature_b64)
    except ValueError as exc:
        raise JwtValidationError(f"Base64url decoding failed: {exc}") from exc

    try:
        header_obj = json.loads(header_bytes.decode("utf-8"))
        payload_obj = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise JwtValidationError(f"JSON decoding failed: {exc}") from exc

    if not isinstance(header_obj, dict):
        raise JwtValidationError("Header is not a JSON object")
    if not isinstance(payload_obj, dict):
        raise JwtValidationError("Payload is not a JSON object")

    alg = header_obj.get("alg")
    if alg != "HS256":
        raise JwtValidationError("Unsupported or missing 'alg' claim")

    return JwtToken(
        raw=token,
        header=header_obj,
        payload=payload_obj,
        signature=signature_bytes,
    )


# ----------------------------------------------------------------------
# 2.6  Verify the HMAC‑SHA256 signature
# ----------------------------------------------------------------------
def verify_signature(token: JwtToken, secret: Union[str, bytes]) -> None:
    """
    Compute an HMAC‑SHA256 over the exact string
    ``base64url(header) + "." + base64url(payload)`` using ``secret`` and
    compare it with ``token.signature`` via ``hmac.compare_digest``.

    Parameters
    ----------
    token: JwtToken
        Result of :func:`parse_jwt`.
    secret: str | bytes
        The shared secret. ``str`` values are UTF‑8 encoded.

    Raises
    ------
    JwtValidationError
        If the computed digest differs from ``token.signature``.
    """
    if isinstance(secret, str):
        secret_bytes = secret.encode("utf-8")
    elif isinstance(secret, bytes):
        secret_bytes = secret
    else:
        raise TypeError("secret must be str or bytes")

    # Recreate the signing input (header.payload)
    signing_input = token.raw.rsplit(".", 1)[0].encode("utf-8")
    expected_sig = hmac.new(secret_bytes, signing_input, hashlib.sha256).digest()

    if not hmac.compare_digest(expected_sig, token.signature):
        raise JwtValidationError("Signature verification failed")


# ----------------------------------------------------------------------
# 2.7  Validate standard time‑based claims
# ----------------------------------------------------------------------
def validate_claims(token: JwtToken, *, now: float | None = None) -> None:
    """
    Validate the following registered claims in ``token.payload``:

    * ``exp`` (required) – must be a numeric UNIX timestamp and **greater**
      than the current time.
    * ``nbf`` (optional) – if present, must be numeric and **≤** current time.
    * ``iat`` (optional) – if present, must be numeric and **≤** current time.

    The current time defaults to ``time.time()``; a custom ``now`` can be
    supplied for testing.

    Raises
    ------
    JwtValidationError
        If any claim is missing, malformed, or violates the temporal rules.
    """
    if now is None:
        now = time.time()

    payload = token.payload

    # Helper to validate numeric claim (int/float but not bool)
    def _is_number(val: Any) -> bool:
        return isinstance(val, (int, float)) and not isinstance(val, bool)

    # exp – required
    if "exp" not in payload:
        raise JwtValidationError("Missing required 'exp' claim")
    exp_val = payload["exp"]
    if not _is_number(exp_val):
        raise JwtValidationError("'exp' claim must be numeric")
    if exp_val <= now:
        raise JwtValidationError("Token has expired")

    # nbf – optional
    if "nbf" in payload:
        nbf_val = payload["nbf"]
        if not _is_number(nbf_val):
            raise JwtValidationError("'nbf' claim must be numeric")
        if nbf_val > now:
            raise JwtValidationError("Token not valid yet (nbf)")

    # iat – optional
    if "iat" in payload:
        iat_val = payload["iat"]
        if not _is_number(iat_val):
            raise JwtValidationError("'iat' claim must be numeric")
        if iat_val > now:
            raise JwtValidationError("Token issued in the future (iat)")


# ----------------------------------------------------------------------
# 2.8  High‑level convenience validator
# ----------------------------------------------------------------------
def validate_jwt(token: str, secret: Union[str, bytes]) -> Mapping[str, Any]:
    """
    Full validation pipeline:

    1. ``parse_jwt`` → ``JwtToken``.
    2. ``verify_signature`` against ``secret``.
    3. ``validate_claims`` (uses ``time.time()``).

    If all steps succeed, returns the **payload** mapping.

    Parameters
    ----------
    token: str
    secret: str | bytes

    Returns
    -------
    Mapping[str, Any]
        The decoded payload.

    Raises
    ------
    TypeError
        Propagated from ``parse_jwt`` when ``token`` is not a string.
    JwtValidationError
        Any validation failure (format, alg, signature, or claim).
    """
    jwt_token = parse_jwt(token)
    verify_signature(jwt_token, secret)
    validate_claims(jwt_token)
    return jwt_token.payload
