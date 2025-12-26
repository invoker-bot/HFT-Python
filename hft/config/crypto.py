"""
加密工具模块

提供配置文件中敏感数据的加密解密功能：
- 使用 PBKDF2 从密码派生密钥
- 使用 Fernet 对称加密算法
- 提供 Pydantic 类型注解用于自动加解密
"""
import base64
from typing import Annotated
from pydantic import SecretStr, PlainSerializer, PlainValidator
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.fernet import Fernet


# 固定盐值，用于密钥派生
salt = b"326f74b7-b013-447b-b320-ce769ec0f4e8"


def serialize_secret(value) -> str:
    """序列化器：将 SecretStr 加密后返回"""
    if isinstance(value, SecretStr):
        return encrypt(value.get_secret_value())
    return value


def get_decrypted_secret(required: bool = False):
    """
    创建解密验证器

    Args:
        required: 是否要求非空值

    Returns:
        验证器函数，用于解密并返回 SecretStr
    """
    def decrypt_secret(value: str) -> SecretStr:
        if not isinstance(value, SecretStr):
            value = SecretStr(decrypt(value))
        if required and len(value.get_secret_value()) == 0:
            raise ValueError("SecretStr value is required and cannot be empty")
        return value
    return decrypt_secret


# 必填加密字符串类型注解
SecretStrAnnotated = Annotated[SecretStr, PlainSerializer(serialize_secret),
                               PlainValidator(get_decrypted_secret(required=True))]

# 可选加密字符串类型注解
OptionalSecretStrAnnotated = Annotated[SecretStr, PlainSerializer(serialize_secret),
                                       PlainValidator(get_decrypted_secret(required=False))]


def key_from_password(password: str) -> bytes:
    """
    从密码派生加密密钥

    使用 PBKDF2-HMAC-SHA256 算法，迭代 390000 次。

    Args:
        password: 用户密码

    Returns:
        Base64 编码的密钥
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


# Fernet 实例，需要先调用 init_fernet 初始化
fernet_instance = None


def init_fernet(key: str):
    """
    初始化 Fernet 加密实例

    Args:
        key: 用户密码
    """
    global fernet_instance
    fernet_instance = Fernet(key_from_password(key))


def encrypt(data: str) -> str:
    """
    加密字符串

    Args:
        data: 明文字符串

    Returns:
        加密后的字符串

    Raises:
        ValueError: 如果 Fernet 未初始化
    """
    if fernet_instance is None:
        raise ValueError("Fernet instance not initialized")
    return fernet_instance.encrypt(data.encode('utf-8')).decode('utf-8')


def decrypt(token: str) -> str:
    """
    解密字符串

    Args:
        token: 加密后的字符串

    Returns:
        解密后的明文

    Raises:
        ValueError: 如果 Fernet 未初始化
    """
    if fernet_instance is None:
        raise ValueError("Fernet instance not initialized")
    return fernet_instance.decrypt(token.encode('utf-8')).decode('utf-8')
