# test_module.py
import base64
import json
import time
import hmac
import hashlib
import secrets
import pytest

from module import (
    JwtValidationError,
    JwtToken,
    _b64url_encode,
    _b64url_decode,
    parse_jwt,
    verify_signature,
    validate_claims,
    validate_jwt,
)

# ----------------------------------------------------------------------
# 3.1  Helper – create a signed JWT (HS256)
# ----------------------------------------------------------------------
def _make_jwt(
    payload: dict,
    secret: bytes,
    header: dict | None = None,
) -> str:
    """Return a compact JWT string signed with HS256."""
    header = header or {"alg": "HS256", "typ": "JWT"}
    header_b = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b}.{payload_b}".encode()
    signature = hmac.new(secret, signing_input, hashlib.sha256).digest()
    signature_b = _b64url_encode(signature)
    return f"{header_b}.{payload_b}.{signature_b}"


# ----------------------------------------------------------------------
# 3.2  Fixtures
# ----------------------------------------------------------------------
@pytest.fixture
def secret() -> bytes:
    return secrets.token_bytes(32)


@pytest.fixture
def now() -> float:
    return time.time()


# ----------------------------------------------------------------------
# 3.3  Tests for base64‑url helpers
# ----------------------------------------------------------------------
def test_b64url_encode_decode_roundtrip():
    data = b"\x00\xff\xab\xcd" * 5
    encoded = _b64url_encode(data)
    assert "=" not in encoded  # padding‑less
    assert _b64url_decode(encoded) == data


def test_b64url_decode_invalid_characters():
    with pytest.raises(ValueError):
        _b64url_decode("!!invalid!!")


# ----------------------------------------------------------------------
# 3.4  parse_jwt – happy path
# ----------------------------------------------------------------------
def test_parse_jwt_success(secret):
    payload = {"sub": "123", "exp": int(time.time()) + 3600}
    token_str = _make_jwt(payload, secret)
    token = parse_jwt(token_str)

    assert isinstance(token, JwtToken)
    assert token.raw == token_str
    assert token.header["alg"] == "HS256"
    assert token.payload == payload
    # signature must be bytes, not base64 string
    assert isinstance(token.signature, bytes)


# ----------------------------------------------------------------------
# 3.5  parse_jwt – input validation
# ----------------------------------------------------------------------
@pytest.mark.parametrize("bad_input", [None, 123, b"bytes", "", "   "])
def test_parse_jwt_type_errors(bad_input):
    with pytest.raises(TypeError):
        parse_jwt(bad_input)  # type: ignore[arg-type]


def test_parse_jwt_wrong_part_count():
    with pytest.raises(JwtValidationError):
        parse_jwt("only.onepart")


def test_parse_jwt_invalid_base64():
    # three parts, but header is not base64url
    token = "!!bad!!." + _b64url_encode(b"{}") + "." + _b64url_encode(b"sig")
    with pytest.raises(JwtValidationError):
        parse_jwt(token)


def test_parse_jwt_non_object_header_and_payload():
    # header is a JSON array, payload is a JSON number
    header = _b64url_encode(json.dumps([1, 2, 3]).encode())
    payload = _b64url_encode(b"42")
    token = f"{header}.{payload}.{_b64url_encode(b'sig')}"
    with pytest.raises(JwtValidationError):
        parse_jwt(token)


def test_parse_jwt_wrong_alg():
    header = {"alg": "none", "typ": "JWT"}
    payload = {"sub": "x", "exp": int(time.time()) + 10}
    token = _make_jwt(payload, b"dummy", header=header)
    with pytest.raises(JwtValidationError):
        parse_jwt(token)


# ----------------------------------------------------------------------
# 3.6  verify_signature
# ----------------------------------------------------------------------
def test_verify_signature_success(secret):
    payload = {"sub": "user", "exp": int(time.time()) + 60}
    token_str = _make_jwt(payload, secret)
    token = parse_jwt(token_str)
    # should not raise
    verify_signature(token, secret)


def test_verify_signature_failure(secret):
    payload = {"sub": "user", "exp": int(time.time()) + 60}
    token_str = _make_jwt(payload, secret)
    token = parse_jwt(token_str)
    # tamper with payload part (still valid JSON)
    parts = token_str.split(".")
    tampered_payload = {"sub": "evil", "exp": int(time.time()) + 60}
    parts[1] = _b64url_encode(json.dumps(tampered_payload, separators=(",", ":")).encode())
    tampered_token = ".".join(parts)
    tampered = parse_jwt(tampered_token)
    with pytest.raises(JwtValidationError):
        verify_signature(tampered, secret)


def test_verify_signature_wrong_secret(secret):
    payload = {"sub": "x", "exp": int(time.time()) + 30}
    token_str = _make_jwt(payload, secret)
    token = parse_jwt(token_str)
    other_secret = secrets.token_bytes(32)
    with pytest.raises(JwtValidationError):
        verify_signature(token, other_secret)


# ----------------------------------------------------------------------
# 3.7  validate_claims – temporal checks
# ----------------------------------------------------------------------
def test_validate_claims_expired(secret, now):
    payload = {"sub": "x", "exp": int(now) - 1}
    token = parse_jwt(_make_jwt(payload, secret))
    with pytest.raises(JwtValidationError):
        validate_claims(token, now=now)


def test_validate_claims_nbf_in_future(secret, now):
    payload = {"sub": "x", "exp": int(now) + 100, "nbf": int(now) + 10}
    token = parse_jwt(_make_jwt(payload, secret))
    with pytest.raises(JwtValidationError):
        validate_claims(token, now=now)


def test_validate_claims_iat_in_future(secret, now):
    payload = {"sub": "x", "exp": int(now) + 100, "iat": int(now) + 5}
    token = parse_jwt(_make_jwt(payload, secret))
    with pytest.raises(JwtValidationError):
        validate_claims(token, now=now)


def test_validate_claims_success(secret, now):
    payload = {
        "sub": "good",
        "exp": int(now) + 300,
        "nbf": int(now) - 10,
        "iat": int(now) - 20,
    }
    token = parse_jwt(_make_jwt(payload, secret))
    # should not raise
    validate_claims(token, now=now)


# ----------------------------------------------------------------------
# 3.8  validate_jwt – end‑to‑end happy path
# ----------------------------------------------------------------------
def test_validate_jwt_success(secret):
    payload = {"user": "alice", "exp": int(time.time()) + 120}
    token = _make_jwt(payload, secret)
    result = validate_jwt(token, secret)
    assert result == payload


# ----------------------------------------------------------------------
# 3.9  validate_jwt – propagation of errors
# ----------------------------------------------------------------------
def test_validate_jwt_invalid_signature(secret):
    payload = {"user": "bob", "exp": int(time.time()) + 60}
    token = _make_jwt(payload, secret)
    # modify secret after token creation
    with pytest.raises(JwtValidationError):
        validate_jwt(token, secret + b"extra")


def test_validate_jwt_missing_exp(secret):
    payload = {"user": "charlie"}  # no exp claim
    token = _make_jwt(payload, secret)
    with pytest.raises(JwtValidationError):
        validate_jwt(token, secret)


def test_validate_jwt_type_error():
    with pytest.raises(TypeError):
        validate_jwt(12345, "secret")  # token not a str
