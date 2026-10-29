from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from email_validator import EmailNotValidError, validate_email

from subscriber_store import (
    PGP_BEGIN,
    PGP_END,
    PGPDecryptor,
    decrypt_records,
    email_digest,
    encrypt_records,
)


PLAINTEXT_EMAIL_RE = re.compile(
    r"(?i)(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+"
)
PGP_MESSAGE_RE = re.compile(
    rf"{re.escape(PGP_BEGIN)}\s*\n[\s\S]+?\n{re.escape(PGP_END)}"
)
FORM_WRAPPER_RE = re.compile(r"\s*(?:###\s+PGP 密文\s*)?", re.IGNORECASE)
MAX_DISCUSSION_BODY = 40 * 1024


@dataclass(frozen=True)
class ProcessResult:
    changed: bool
    records: list[dict[str, Any]]
    response: str


class UserInputError(ValueError):
    pass


def extract_pgp_message(body: str) -> str:
    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    if len(normalized.encode("utf-8")) > MAX_DISCUSSION_BODY:
        raise UserInputError("正文过长")
    if PLAINTEXT_EMAIL_RE.search(normalized):
        raise UserInputError("正文包含明文邮箱")
    matches = list(PGP_MESSAGE_RE.finditer(normalized))
    if len(matches) != 1:
        raise UserInputError("正文必须且只能包含一个 PGP 密文块")
    before = normalized[: matches[0].start()]
    after = normalized[matches[0].end() :]
    if not FORM_WRAPPER_RE.fullmatch(before) or after.strip():
        raise UserInputError("PGP 密文块外包含其他内容")
    return matches[0].group(0).strip()


def normalize_email(plaintext: str) -> str:
    value = plaintext.strip()
    if "\n" in value or "\r" in value or len(value) > 320:
        raise UserInputError("解密内容不是单一邮箱地址")
    try:
        return validate_email(value, check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise UserInputError("解密内容不是有效邮箱地址") from exc


def process_subscription(
    payload: dict[str, Any],
    records: list[dict[str, Any]],
    decrypt_email: Callable[[str], str],
) -> ProcessResult:
    discussion = payload.get("discussion")
    if not isinstance(discussion, dict):
        raise ValueError("事件缺少 discussion")
    category = discussion.get("category")
    if not isinstance(category, dict) or category.get("name") != "Subscribe":
        raise ValueError("Discussion 分类不是 Subscribe")

    title = discussion.get("title")
    body = discussion.get("body")
    user = discussion.get("user")
    if not isinstance(title, str) or not isinstance(body, str) or not isinstance(user, dict):
        raise ValueError("Discussion 事件结构无效")
    github_id = user.get("id")
    github_login = user.get("login")
    if not isinstance(github_id, int) or not isinstance(github_login, str):
        raise ValueError("Discussion 用户身份无效")

    if title not in {"Subscribe", "Unsubscribe"}:
        return ProcessResult(
            False,
            records,
            "请求未处理：标题必须严格填写 `Subscribe` 或 `Unsubscribe`，请按 README 重新提交。",
        )

    try:
        pgp_message = extract_pgp_message(body)
        normalized_email = normalize_email(decrypt_email(pgp_message))
    except (UserInputError, ValueError):
        return ProcessResult(
            False,
            records,
            "请求未处理：正文必须只包含一个可解密的 PGP 邮箱密文，且不能出现明文邮箱。请按 README 重新提交。",
        )
    email_hash = email_digest(normalized_email)
    match = lambda item: (
        item["github_id"] == github_id and item["email_hash"] == email_hash
    )

    if title == "Subscribe":
        if any(match(item) for item in records):
            return ProcessResult(False, records, "该账户与邮箱已经订阅，无需重复操作。")
        updated = [
            *records,
            {
                "github_id": github_id,
                "github_login": github_login,
                "email_hash": email_hash,
                "email_encrypted": pgp_message,
            },
        ]
        return ProcessResult(True, updated, "订阅成功。你的明文邮箱没有写入公开区域。")

    updated = [item for item in records if not match(item)]
    if len(updated) == len(records):
        # 不区分账户不匹配还是邮箱不匹配，防止利用退订接口探测。
        return ProcessResult(False, records, "未找到匹配记录。")
    return ProcessResult(True, updated, "取消订阅成功。")


def write_github_output(changed: bool) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"changed={'true' if changed else 'false'}\n")


def run_init(output: Path, encryption_key: str) -> None:
    output.write_text(encrypt_records([], encryption_key), encoding="utf-8")
    print(f"已生成空订阅变量文件：{output}")


def run_process(
    event_path: Path,
    state_path: Path,
    output_path: Path,
    response_path: Path,
    encryption_key: str,
    private_key: str,
    passphrase: str,
) -> None:
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    records = decrypt_records(state_path.read_text(encoding="utf-8"), encryption_key)
    with PGPDecryptor(private_key, passphrase) as decryptor:
        result = process_subscription(payload, records, decryptor.decrypt)

    if result.changed:
        try:
            encrypted = encrypt_records(result.records, encryption_key)
        except OverflowError:
            result = ProcessResult(
                False,
                records,
                "订阅列表容量已满，本次请求未处理。请联系仓库维护者。",
            )
        else:
            output_path.write_text(encrypted, encoding="utf-8")
            os.chmod(output_path, 0o600)

    response_path.write_text(result.response + "\n", encoding="utf-8")
    write_github_output(result.changed)
    print("订阅请求处理完成；未输出邮箱或订阅数据")


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化或处理 Steam Freebie 订阅数据")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="生成空的 SUBSCRIBERS_ENC")
    init_parser.add_argument("--output", type=Path, default=Path("subscribers.enc"))

    process_parser = subparsers.add_parser("process", help="处理 discussion created 事件")
    process_parser.add_argument(
        "--event", type=Path, default=Path(os.environ.get("GITHUB_EVENT_PATH", "event.json"))
    )
    process_parser.add_argument("--state", type=Path, default=Path("subscribers.enc"))
    process_parser.add_argument(
        "--output", type=Path, default=Path("subscribers-updated.enc")
    )
    process_parser.add_argument(
        "--response", type=Path, default=Path("subscription-response.md")
    )
    args = parser.parse_args()

    encryption_key = os.environ.get("LIST_ENCRYPTION_KEY", "")
    if args.command == "init":
        run_init(args.output, encryption_key)
        return

    run_process(
        args.event,
        args.state,
        args.output,
        args.response,
        encryption_key,
        os.environ.get("PGP_PRIVATE_KEY", ""),
        os.environ.get("PGP_PASSPHRASE", ""),
    )


if __name__ == "__main__":
    main()
