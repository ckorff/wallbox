"""Encrypted model fields for sensitive app settings.

``EncryptedField`` stores its values as Fernet ciphertext in the DB and
decrypts transparently when read via the ORM. The Fernet key is derived
from Django's ``SECRET_KEY`` via HKDF-SHA256, so rotating ``SECRET_KEY``
invalidates stored ciphertexts; the user re-enters the credential
through the settings UI in that case (acceptable for a single-user app).
"""
from __future__ import annotations

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.db import models


_HKDF_SALT = b"wallbox-app-settings-v1"
_HKDF_INFO = b"keba-api-credentials-encryption"


def _derive_fernet_key() -> bytes:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    raw = hkdf.derive(settings.SECRET_KEY.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def _fernet() -> Fernet:
    return Fernet(_derive_fernet_key())


class EncryptedField(models.TextField):
    """A ``TextField`` that transparently encrypts/decrypts via Fernet.

    Plaintext at the Python layer; ciphertext at the DB layer.
    Decryption failures (key rotation, corruption) yield ``""`` instead
    of raising — the user can re-enter the credential via the settings UI.
    """

    description = "Fernet-encrypted text"

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        try:
            return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError, TypeError):
            return ""

    def get_prep_value(self, value):
        if value is None or value == "":
            return value
        return _fernet().encrypt(str(value).encode("utf-8")).decode("utf-8")
