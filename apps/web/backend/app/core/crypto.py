"""provider api_key 的 Fernet 加解密。"""
from cryptography.fernet import Fernet


def encrypt_key(plain: str, fernet_key: str) -> tuple[str, bool]:
    """加密 key。

    Args:
        plain: 明文 key。
        fernet_key: Fernet key（为空表示不加密，开发模式）。

    Returns:
        (密文, 是否加密)；fernet_key 为空时返回 (明文, False)。
    """
    if not fernet_key:
        return plain, False
    return Fernet(fernet_key.encode()).encrypt(plain.encode()).decode(), True


def decrypt_key(stored: str, fernet_key: str, encrypted: bool) -> str:
    """解密 key（未加密存储时原样返回）。

    Args:
        stored: 存储的密文（或未加密模式下的明文）。
        fernet_key: Fernet key。
        encrypted: 存储时是否经过加密。

    Returns:
        明文 key。
    """
    if not encrypted:
        return stored
    return Fernet(fernet_key.encode()).decrypt(stored.encode()).decode()
