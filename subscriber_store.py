from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


STORE_PREFIX = "v1."
STORE_AAD = b"steam-freebie/subscribers/v1"
MAX_VARIABLE_BYTES = 48 * 1024
EMAIL_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
PGP_BEGIN = "-----BEGIN PGP MESSAGE-----"
PGP_END = "-----END PGP MESSAGE-----"


def _derive_key(secret: str) -> bytes:
    if len(secret) < 32:
        raise ValueError("LIST_ENCRYPTION_KEY 至少需要 32 个字符")
    return hashlib.sha256(secret.encode("utf-8")).digest()


def validate_records(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("订阅数据版本无效")
    records = value.get("subscribers")
    if not isinstance(records, list):
        raise ValueError("订阅数据缺少 subscribers 数组")

    validated: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("订阅记录格式无效")
        github_id = record.get("github_id")
        github_login = record.get("github_login")
        email_hash = record.get("email_hash")
        email_encrypted = record.get("email_encrypted")
        if not isinstance(github_id, int) or github_id <= 0:
            raise ValueError("订阅记录 github_id 无效")
        if not isinstance(github_login, str) or not github_login:
            raise ValueError("订阅记录 github_login 无效")
        if not isinstance(email_hash, str) or not EMAIL_HASH_RE.fullmatch(email_hash):
            raise ValueError("订阅记录 email_hash 无效")
        if (
            not isinstance(email_encrypted, str)
            or not email_encrypted.startswith(PGP_BEGIN)
            or not email_encrypted.endswith(PGP_END)
        ):
            raise ValueError("订阅记录 email_encrypted 无效")
        validated.append(
            {
                "github_id": github_id,
                "github_login": github_login,
                "email_hash": email_hash,
                "email_encrypted": email_encrypted,
            }
        )
    return validated


def encrypt_records(records: list[dict[str, Any]], secret: str) -> str:
    payload = json.dumps(
        {"version": 1, "subscribers": records},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    nonce = os.urandom(12)
    encrypted = AESGCM(_derive_key(secret)).encrypt(nonce, payload, STORE_AAD)
    encoded = STORE_PREFIX + base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")
    if len(encoded.encode("utf-8")) > MAX_VARIABLE_BYTES:
        raise OverflowError("加密订阅列表超过 GitHub 变量 48KB 上限")
    return encoded


def decrypt_records(encoded: str, secret: str) -> list[dict[str, Any]]:
    value = encoded.strip()
    if not value.startswith(STORE_PREFIX):
        raise ValueError("SUBSCRIBERS_ENC 格式或版本无效")
    try:
        raw = base64.urlsafe_b64decode(value[len(STORE_PREFIX) :].encode("ascii"))
        if len(raw) < 29:
            raise ValueError("密文长度无效")
        payload = AESGCM(_derive_key(secret)).decrypt(raw[:12], raw[12:], STORE_AAD)
        decoded = json.loads(payload.decode("utf-8"))
    except (
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
        InvalidTag,
        binascii.Error,
    ) as exc:
        raise ValueError("无法解密 SUBSCRIBERS_ENC，请检查密钥和变量内容") from exc
    return validate_records(decoded)


def email_digest(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


class PGPDecryptor:
    """在隔离的临时 keyring 中导入私钥并解密 PGP 消息。"""

    def __init__(self, private_key: str, passphrase: str = "") -> None:
        if not private_key.strip():
            raise ValueError("PGP_PRIVATE_KEY 不能为空")
        self._private_key = private_key
        self._passphrase = passphrase
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._home: Path | None = None

    def __enter__(self) -> PGPDecryptor:
        self._temporary = tempfile.TemporaryDirectory(prefix="steam-freebie-gpg-")
        self._home = Path(self._temporary.name)
        os.chmod(self._home, 0o700)
        try:
            self._run(["--import"], input_text=self._private_key)
        except subprocess.CalledProcessError as exc:
            self.close()
            raise RuntimeError("无法导入 PGP 私钥") from exc
        return self

    def _run(
        self, arguments: list[str], *, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        if self._home is None:
            raise RuntimeError("PGPDecryptor 尚未初始化")
        return subprocess.run(
            [
                "gpg",
                "--homedir",
                str(self._home),
                "--batch",
                "--no-tty",
                *arguments,
            ],
            input=input_text,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )

    def decrypt(self, armored_message: str) -> str:
        if self._home is None:
            raise RuntimeError("PGPDecryptor 尚未初始化")
        message_path = self._home / "message.asc"
        message_path.write_text(armored_message, encoding="utf-8")
        os.chmod(message_path, 0o600)
        arguments = ["--pinentry-mode", "loopback"]
        input_text: str | None = None
        if self._passphrase:
            arguments.extend(["--passphrase-fd", "0"])
            input_text = self._passphrase + "\n"
        arguments.extend(["--decrypt", str(message_path)])
        try:
            return self._run(arguments, input_text=input_text).stdout
        except subprocess.CalledProcessError as exc:
            raise ValueError("PGP 密文无法解密") from exc
        finally:
            message_path.unlink(missing_ok=True)

    def close(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()
        self._temporary = None
        self._home = None

    def __exit__(self, *_: object) -> None:
        self.close()


def decrypt_subscriber_emails(
    records: Iterable[dict[str, Any]], decryptor: PGPDecryptor
) -> list[str]:
    # 同一邮箱可能由多个 GitHub 账户订阅；发送阶段仍只发一封。
    emails: dict[str, None] = {}
    from email_validator import EmailNotValidError, validate_email

    for record in records:
        plaintext = decryptor.decrypt(record["email_encrypted"])
        try:
            normalized = validate_email(
                plaintext.strip(), check_deliverability=False
            ).normalized.lower()
        except EmailNotValidError as exc:
            raise ValueError("订阅列表中存在无法解密或无效的邮箱") from exc
        if email_digest(normalized) != record["email_hash"]:
            raise ValueError("订阅列表中的邮箱哈希不匹配")
        emails[normalized] = None
    return list(emails)
