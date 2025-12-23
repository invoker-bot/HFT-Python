import base64
from typing import Annotated
from pydantic import SecretStr, PlainSerializer, PlainValidator
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.fernet import Fernet


salt = b"326f74b7-b013-447b-b320-ce769ec0f4e8"


def serialize_secret(value) -> str:
    if isinstance(value, SecretStr):
        return encrypt(value.get_secret_value())
    return value


def get_decrypted_secret(required: bool = False):
    def decrypt_secret(value: str) -> SecretStr:
        if not isinstance(value, SecretStr):
            value = SecretStr(decrypt(value))
        if required and len(value.get_secret_value()) == 0:
            raise ValueError("SecretStr value is required and cannot be empty")
        return value
    return decrypt_secret


SecretStrAnnotated = Annotated[SecretStr, PlainSerializer(serialize_secret),
                               PlainValidator(get_decrypted_secret(required=True))]

OptionalSecretStrAnnotated = Annotated[SecretStr, PlainSerializer(serialize_secret),
                                       PlainValidator(get_decrypted_secret(required=False))]


def key_from_password(password: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


fernet_instance = None


def init_fernet(key: str):
    global fernet_instance
    fernet_instance = Fernet(key_from_password(key))


def encrypt(data: str) -> str:
    if fernet_instance is None:
        raise ValueError("Fernet instance not initialized")
    return fernet_instance.encrypt(data.encode('utf-8')).decode('utf-8')


def decrypt(token: str) -> str:
    if fernet_instance is None:
        raise ValueError("Fernet instance not initialized")
    return fernet_instance.decrypt(token.encode('utf-8')).decode('utf-8')
