from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag

from subscriber_store import PGPDecryptor, decrypt_records, decrypt_subscriber_emails


SEARCH_URL = "https://store.steampowered.com/search"
DEFAULT_STATE_OUTPUT = Path("steam_state.json")
MAX_PAGES = 10
RESEND_BATCH_URL = "https://api.resend.com/emails/batch"
RESEND_BATCH_SIZE = 100

PARAMS = {
    "sort_by": "Price_ASC",
    "maxprice": "free",
    "specials": "1",
    "ndl": "1",
}

CATEGORIES = {
    "game": "998",
    "dlc": "21",
}

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
}


def fetch_search_page(
    session: requests.Session, category_id: str, page: int
) -> requests.Response:
    """请求指定分类的一页 Steam 搜索结果。"""
    params = {**PARAMS, "category1": category_id, "page": str(page)}
    headers = HEADERS.copy()

    response = session.get(
        SEARCH_URL,
        params=params,
        headers=headers,
        cookies=None,
        timeout=30,
    )
    response.raise_for_status()
    return response


def element_text(parent: Tag, selector: str) -> str | None:
    """返回子节点文本；节点不存在时返回 None。"""
    element = parent.select_one(selector)
    return element.get_text(" ", strip=True) if element else None


def parse_games(html_text: str, item_type: str) -> list[dict[str, Any]]:
    """从 Steam 搜索结果 HTML 中提取游戏或 DLC 信息。"""
    soup = BeautifulSoup(html_text, "html.parser")
    results = soup.select_one("#search_resultsRows")
    result_container = soup.select_one("#search_result_container")
    if results is None and result_container is None:
        raise ValueError("Steam 页面缺少搜索结果容器，可能是页面结构变化或访问被拦截")
    if results is None:
        return []

    games: list[dict[str, Any]] = []

    for row in results.select("a.search_result_row"):
        raw_url = str(row.get("href", ""))
        parts = urlsplit(raw_url)
        game_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

        image = row.select_one(".search_capsule img")
        review = row.select_one(".search_review_summary")
        price = row.select_one(".search_price_discount_combined")
        appid = str(row.get("data-ds-appid", "")).strip() or None

        # 无 appid 和链接的行无法稳定比较，直接忽略。
        if not appid and not game_url:
            continue

        tooltip = str(review.get("data-tooltip-html", "")) if review else ""
        review_text = BeautifulSoup(
            html.unescape(tooltip), "html.parser"
        ).get_text(" ", strip=True)

        try:
            tag_ids = json.loads(str(row.get("data-ds-tagids", "[]")))
        except (json.JSONDecodeError, TypeError):
            tag_ids = []

        games.append(
            {
                "item_type": item_type,
                "appid": appid,
                "title": element_text(row, ".title") or "未命名内容",
                "url": game_url,
                "image": str(image.get("src")) if image and image.get("src") else None,
                "release_date": element_text(row, ".search_released"),
                "platforms": sorted(
                    {
                        platform
                        for element in row.select(".platform_img")
                        for platform in ("win", "mac", "linux")
                        if platform in element.get("class", [])
                    }
                ),
                "tag_ids": tag_ids if isinstance(tag_ids, list) else [],
                "review": {
                    "level": next(
                        (
                            name
                            for name in review.get("class", [])
                            if name != "search_review_summary"
                        ),
                        None,
                    )
                    if review
                    else None,
                    "summary": review_text or None,
                },
                "price": {
                    "discount_percent": element_text(row, ".discount_pct"),
                    "original": element_text(row, ".discount_original_price"),
                    "final": element_text(row, ".discount_final_price"),
                    "final_minor_units": int(price.get("data-price-final", 0))
                    if price and str(price.get("data-price-final", "")).isdigit()
                    else None,
                },
            }
        )

    return games


def game_key(game: dict[str, Any]) -> str:
    """生成跨天比较使用的稳定标识。"""
    identity = game.get("appid") or game.get("url")
    return f"{game.get('item_type', 'game')}:{identity}"


def deduplicate(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for game in games:
        unique.setdefault(game_key(game), game)
    return list(unique.values())


def fetch_all_games() -> list[dict[str, Any]]:
    """抓取所有分页；发现页面重复或空页时停止。"""
    games: list[dict[str, Any]] = []
    seen: set[str] = set()

    with requests.Session() as session:
        for item_type, category_id in CATEGORIES.items():
            for page in range(1, MAX_PAGES + 1):
                response = fetch_search_page(session, category_id, page)
                response.encoding = "utf-8"
                items = parse_games(response.text, item_type)
                new_items = [item for item in items if game_key(item) not in seen]

                if not items or not new_items:
                    break

                games.extend(new_items)
                seen.update(game_key(item) for item in new_items)
                print(f"{item_type} 第 {page} 页：提取 {len(new_items)} 条")

    return games


def load_previous_keys(raw_state: str) -> set[str]:
    """读取仓库变量中的状态，并兼容早期保存的完整对象列表。"""
    if not raw_state.strip():
        return set()

    try:
        value = json.loads(raw_state)
    except json.JSONDecodeError as exc:
        raise ValueError("STEAM_FREEBIE_STATE 不是有效 JSON，已拒绝继续") from exc

    if not isinstance(value, list):
        raise ValueError("STEAM_FREEBIE_STATE 必须是 JSON 数组")

    keys: set[str] = set()
    for item in value:
        if isinstance(item, str):
            keys.add(item)
        elif isinstance(item, dict):
            keys.add(game_key(item))
        else:
            raise ValueError("STEAM_FREEBIE_STATE 包含不支持的数据")
    return keys


def save_json(value: object, output: Path, *, compact: bool = False) -> None:
    output.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":") if compact else None,
            indent=None if compact else 2,
        ),
        encoding="utf-8",
    )


def env_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"缺少环境变量 {name}")
    return value


def build_email_content(games: list[dict[str, Any]]) -> tuple[str, str, str]:
    subject = f"Steam 喜加一：今日新增 {len(games)} 款免费内容"
    text_lines = [subject, ""]
    html_items: list[str] = []
    for game in games:
        kind = "DLC" if game["item_type"] == "dlc" else "游戏"
        title = str(game["title"])
        url = str(game["url"])
        text_lines.append(f"[{kind}] {title}\n{url}")
        html_items.append(
            "<li>"
            f"<strong>[{kind}] {html.escape(title)}</strong><br>"
            f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>'
            "</li>"
        )
    html_body = (
        "<html><body>"
        f"<h2>{html.escape(subject)}</h2><ul>{''.join(html_items)}</ul>"
        "</body></html>"
    )
    return subject, "\n\n".join(text_lines), html_body


def send_notification(games: list[dict[str, Any]], recipients: list[str]) -> None:
    if not recipients:
        print("订阅列表为空，跳过邮件")
        return

    api_key = env_required("RESEND_API_KEY")
    sender = env_required("MAIL_FROM")
    subject, text_body, html_body = build_email_content(games)

    for batch_index, start in enumerate(
        range(0, len(recipients), RESEND_BATCH_SIZE), start=1
    ):
        batch = recipients[start : start + RESEND_BATCH_SIZE]
        payload = [
            {
                "from": sender,
                "to": [recipient],
                "subject": subject,
                "text": text_body,
                "html": html_body,
            }
            for recipient in batch
        ]
        payload_bytes = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        idempotency_key = "steam-freebie/" + hashlib.sha256(payload_bytes).hexdigest()
        response = requests.post(
            RESEND_BATCH_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            data=payload_bytes,
            timeout=30,
        )
        response.raise_for_status()
        print(f"Resend 批次 {batch_index} 已接受 {len(batch)} 封邮件")


def load_recipients(state_path: Path) -> list[str]:
    records = decrypt_records(
        state_path.read_text(encoding="utf-8"), env_required("LIST_ENCRYPTION_KEY")
    )
    with PGPDecryptor(
        env_required("PGP_PRIVATE_KEY"), os.environ.get("PGP_PASSPHRASE", "")
    ) as decryptor:
        return decrypt_subscriber_emails(records, decryptor)


def append_job_summary(games: list[dict[str, Any]], new_games: list[dict[str, Any]]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    lines = [
        "## Steam 免费内容检查",
        "",
        f"- 当前免费内容：{len(games)}",
        f"- 今日新增：{len(new_games)}",
    ]
    lines.extend(f"- [{game['title']}]({game['url']})" for game in new_games)
    with Path(summary_path).open("a", encoding="utf-8") as summary:
        summary.write("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="抓取 Steam 限时免费内容并发送增量通知")
    parser.add_argument("--input", type=Path, help="解析本地 HTML，便于调试")
    parser.add_argument("--item-type", choices=CATEGORIES, default="game")
    parser.add_argument("--output", type=Path, default=Path("steam_games.json"))
    parser.add_argument("--state-output", type=Path, default=DEFAULT_STATE_OUTPUT)
    parser.add_argument(
        "--subscriber-state-file",
        type=Path,
        default=Path("subscribers.enc"),
        help="从仓库变量下载的加密订阅列表",
    )
    parser.add_argument("--send-email", action="store_true", help="有新增时发送邮件")
    args = parser.parse_args()

    if args.input:
        games = parse_games(args.input.read_text(encoding="utf-8"), args.item_type)
    else:
        games = fetch_all_games()
    games = deduplicate(games)

    previous_keys = load_previous_keys(os.environ.get("STEAM_FREEBIE_STATE", ""))
    new_games = [game for game in games if game_key(game) not in previous_keys]
    current_keys = sorted(game_key(game) for game in games)

    save_json(games, args.output)
    save_json(current_keys, args.state_output, compact=True)
    append_job_summary(games, new_games)

    print(f"当前共 {len(games)} 条，较上次新增 {len(new_games)} 条")
    if args.send_email and new_games:
        recipients = load_recipients(args.subscriber_state_file)
        send_notification(new_games, recipients)
        print(f"新增内容通知处理完成，订阅地址数：{len(recipients)}")
    elif args.send_email:
        print("没有新增内容，跳过邮件")
    else:
        print("未启用邮件发送")


if __name__ == "__main__":
    main()
