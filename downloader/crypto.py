Attempting to perform the InitializeDefaultDrives operation on the 'FileSystem' provider failed.
from cryptography.fernet import Fernet
from django.conf import settings


def _fernet():
    if not settings.CREDENTIAL_ENCRYPTION_KEY:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY no está configurada")
    return Fernet(settings.CREDENTIAL_ENCRYPTION_KEY.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()

