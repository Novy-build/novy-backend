import base64
import binascii
import hmac
import re
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def decode_session_key(value: SecretStr) -> bytes:
    encoded = value.get_secret_value()
    decoded = None
    if re.fullmatch(r"[A-Za-z0-9_-]{43}=", encoded, flags=re.ASCII):
        try:
            decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
        except (ValueError, binascii.Error):
            pass
    if (
        decoded is None
        or len(decoded) != 32
        or base64.urlsafe_b64encode(decoded).decode("ascii") != encoded
    ):
        raise ValueError("Invalid session key")
    return decoded


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NOVY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        hide_input_in_errors=True,
        populate_by_name=True,
    )

    environment: Literal["development", "test", "preview", "production"] = "production"
    allowed_origins: str = ""
    mongodb_connection_string: SecretStr | None = Field(
        default=None, validation_alias="MONGODB_CONNECTION_STRING", repr=False, exclude=True
    )
    # These shared dotenv entries are accepted but not used until auth is implemented.
    supabase_url: SecretStr | None = Field(
        default=None, validation_alias="NEXT_PUBLIC_SUPABASE_URL", repr=False, exclude=True
    )
    supabase_publishable_key: SecretStr | None = Field(
        default=None,
        validation_alias="NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
        repr=False,
        exclude=True,
    )
    session_encryption_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    session_lookup_key: SecretStr | None = Field(default=None, repr=False, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def mask_secret_inputs(cls, data: Any) -> Any:
        # Mask raw values before Pydantic can attach them to structured errors.
        if isinstance(data, dict):
            data = data.copy()
            for name in (
                "MONGODB_CONNECTION_STRING",
                "mongodb_connection_string",
                "NEXT_PUBLIC_SUPABASE_URL",
                "supabase_url",
                "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
                "supabase_publishable_key",
                "NOVY_SESSION_ENCRYPTION_KEY",
                "session_encryption_key",
                "NOVY_SESSION_LOOKUP_KEY",
                "session_lookup_key",
            ):
                value = data.get(name)
                if isinstance(value, str):
                    data[name] = SecretStr(value)
                elif isinstance(value, (bytes, bytearray)):
                    data[name] = SecretStr(value.decode("utf-8", errors="replace"))
                elif value is not None and not isinstance(value, SecretStr):
                    data[name] = object()
        return data

    @field_validator("session_encryption_key", "session_lookup_key")
    @classmethod
    def validate_session_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            decode_session_key(value)
        return value

    @model_validator(mode="after")
    def validate_session_keys(self) -> "Settings":
        encryption, lookup = self.session_encryption_key, self.session_lookup_key
        message = None
        if (encryption is None) != (lookup is None):
            message = "Both session keys must be configured together"
        elif encryption is not None and lookup is not None:
            if hmac.compare_digest(decode_session_key(encryption), decode_session_key(lookup)):
                message = "Session keys must be distinct"
        if message:
            # Pydantic serializes the entire model as error input for after validators.
            raise ValidationError.from_exception_data(
                "Settings",
                [
                    {
                        "type": "value_error",
                        "loc": (),
                        "input": None,
                        "ctx": {"error": ValueError(message)},
                    }
                ],
            )
        return self

    @field_validator("mongodb_connection_string")
    @classmethod
    def validate_mongodb_connection_string(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None

        uri = value.get_secret_value()
        try:
            if "#" in uri or "\ufffd" in uri:
                raise ValueError("Invalid MongoDB connection string")
            parts = urlsplit(uri)
            if ";" in parts.query:
                raise ValueError("Invalid MongoDB connection string")
            options = parse_qsl(parts.query, keep_blank_values=True)
            hostname = parts.hostname
        except ValueError:
            raise ValueError("Invalid MongoDB connection string") from None

        if uri != uri.strip() or parts.scheme not in {"mongodb", "mongodb+srv"} or not hostname:
            raise ValueError("Invalid MongoDB connection string")

        for name, option in options:
            name, option = name.lower(), option.lower()
            if (
                (name in {"tls", "ssl"} and option != "true")
                or (
                    name
                    in {"tlsinsecure", "tlsallowinvalidcertificates", "tlsallowinvalidhostnames"}
                    and option != "false"
                )
                or name == "srvallowedhostssuffix"
            ):
                raise ValueError("MongoDB connection must use secure TLS settings")
        return value
