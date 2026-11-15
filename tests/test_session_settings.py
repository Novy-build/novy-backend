import base64
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from novy.infrastructure.settings import Settings, decode_session_key

KEY_A = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
KEY_B = base64.urlsafe_b64encode(bytes(range(32, 64))).decode("ascii")
OTHER_SECRET = "synthetic-other-secret-value"


def make_settings(**values: Any) -> Settings:
    return Settings(**values)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in tuple(os.environ):
        if name.upper().startswith(("NOVY", "MONGO", "SUPABASE", "NEXT_PUBLIC_SUPABASE")):
            monkeypatch.delenv(name, raising=False)


def assert_redacted(error: ValidationError, *secrets: str) -> None:
    representations = [str(error), repr(error), str(error.errors()), error.json()]
    current: BaseException | None = error
    while current is not None:
        representations.extend((str(current), repr(current)))
        current = current.__cause__ or current.__context__
    for secret in secrets:
        if secret and not secret.isspace():
            assert all(secret not in text for text in representations)


def test_missing_keys_are_not_generated() -> None:
    settings = make_settings(_env_file=None)
    assert settings.session_encryption_key is None
    assert settings.session_lookup_key is None


@pytest.mark.parametrize("source", ["constructor", "environment"])
def test_valid_keys_and_redaction(source: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if source == "environment":
        monkeypatch.setenv("NOVY_SESSION_ENCRYPTION_KEY", KEY_A)
        monkeypatch.setenv("NOVY_SESSION_LOOKUP_KEY", KEY_B)
        settings = make_settings(_env_file=None)
    else:
        settings = make_settings(
            _env_file=None, session_encryption_key=KEY_A, session_lookup_key=KEY_B
        )
    assert settings.session_encryption_key is not None
    assert settings.session_lookup_key is not None
    assert decode_session_key(settings.session_encryption_key) == bytes(range(32))
    assert decode_session_key(settings.session_lookup_key) == bytes(range(32, 64))
    for text in (
        repr(settings),
        str(settings),
        repr(settings.model_dump()),
        settings.model_dump_json(),
    ):
        assert KEY_A not in text and KEY_B not in text
    assert "session_encryption_key" not in settings.model_dump()
    assert "session_lookup_key" not in settings.model_dump()


@pytest.mark.parametrize("field", ["session_encryption_key", "session_lookup_key"])
def test_one_key_rejected_and_other_secrets_redacted(field: str) -> None:
    with pytest.raises(ValidationError) as exc:
        make_settings(_env_file=None, supabase_publishable_key=OTHER_SECRET, **{field: KEY_A})
    assert_redacted(exc.value, KEY_A, OTHER_SECRET)


def test_equal_keys_rejected_and_other_secrets_redacted() -> None:
    with pytest.raises(ValidationError) as exc:
        make_settings(
            _env_file=None,
            session_encryption_key=KEY_A,
            session_lookup_key=KEY_A,
            supabase_publishable_key=OTHER_SECRET,
        )
    assert_redacted(exc.value, KEY_A, OTHER_SECRET)


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        " ",
        KEY_A + " ",
        " " + KEY_A,
        KEY_A[:-1],
        KEY_A + "=",
        KEY_A[:-2] + "B=",  # Nonzero padding bits decode to the same bytes.
        "+" + KEY_A[1:],
        "/" + KEY_A[1:],
        "!" + KEY_A[1:],
        base64.urlsafe_b64encode(bytes(31)).decode("ascii"),
        base64.urlsafe_b64encode(bytes(33)).decode("ascii"),
        "\u2603" * 43 + "=",
    ],
)
def test_invalid_encodings_are_rejected_without_exposure(invalid: str) -> None:
    with pytest.raises(ValueError) as helper_error:
        decode_session_key(SecretStr(invalid))
    assert str(helper_error.value) == "Invalid session key"
    assert helper_error.value.__context__ is None
    assert helper_error.value.__cause__ is None
    with pytest.raises(ValidationError) as exc:
        make_settings(
            _env_file=None,
            session_encryption_key=invalid,
            session_lookup_key=KEY_B,
            supabase_publishable_key=OTHER_SECRET,
        )
    assert_redacted(exc.value, invalid, KEY_B, OTHER_SECRET)


@pytest.mark.parametrize(
    "value",
    [b"private-bytes", bytearray(b"private-bytes"), ["private-bytes"], {"private-bytes": "x"}],
)
def test_non_string_inputs_are_masked(value: object) -> None:
    with pytest.raises(ValidationError) as exc:
        make_settings(_env_file=None, session_encryption_key=value, session_lookup_key=KEY_B)
    assert_redacted(exc.value, "private-bytes", KEY_B)


def test_dotenv_sources_precedence_and_extra_forbid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dotenv = tmp_path / "synthetic-settings.env"
    dotenv.write_text(
        f"NOVY_SESSION_ENCRYPTION_KEY={KEY_A}\nNOVY_SESSION_LOOKUP_KEY={KEY_B}\n"
        "MONGODB_CONNECTION_STRING=mongodb://localhost/test\n"
        "NEXT_PUBLIC_SUPABASE_URL=https://example.invalid\n"
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=synthetic\n",
        encoding="utf-8",
    )
    settings = make_settings(_env_file=dotenv)
    assert settings.session_encryption_key == SecretStr(KEY_A)
    assert settings.session_lookup_key == SecretStr(KEY_B)
    assert settings.mongodb_connection_string is not None
    assert settings.supabase_url is not None
    assert settings.supabase_publishable_key is not None
    monkeypatch.setenv("NOVY_SESSION_ENCRYPTION_KEY", KEY_B)
    monkeypatch.setenv("NOVY_SESSION_LOOKUP_KEY", KEY_A)
    assert make_settings(_env_file=dotenv).session_encryption_key == SecretStr(KEY_B)
    settings = make_settings(
        _env_file=dotenv, session_encryption_key=KEY_A, session_lookup_key=KEY_B
    )
    assert settings.session_encryption_key == SecretStr(KEY_A)
    assert settings.session_lookup_key == SecretStr(KEY_B)
    with dotenv.open("a", encoding="utf-8") as file:
        file.write("NOVY_UNKNOWN=not-recognized\n")
    with pytest.raises(ValidationError) as exc:
        make_settings(_env_file=dotenv)
    assert any(error["type"] == "extra_forbidden" for error in exc.value.errors())
    assert_redacted(exc.value, KEY_A, KEY_B)
