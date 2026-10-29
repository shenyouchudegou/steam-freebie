import os
import unittest
from unittest.mock import Mock, patch

from script import (
    build_email_content,
    deduplicate,
    game_key,
    load_previous_keys,
    parse_games,
    send_notification,
)


SAMPLE_HTML = """
<div id="search_resultsRows">
  <a class="search_result_row" data-ds-appid="123" data-ds-tagids="[1,2]"
     href="https://store.steampowered.com/app/123/Demo/?snr=abc">
    <div class="search_capsule"><img src="https://example.com/a.jpg"></div>
    <span class="title">Demo &amp; Friends</span>
    <div class="search_released">19 Oct, 2026</div>
    <span class="platform_img win"></span>
    <span class="platform_img linux"></span>
    <span class="search_review_summary positive" data-tooltip-html="Very Positive&lt;br&gt;90%"></span>
    <div class="search_price_discount_combined" data-price-final="0">
      <div class="discount_pct">-100%</div>
      <div class="discount_original_price">$9.99</div>
      <div class="discount_final_price">Free</div>
    </div>
  </a>
</div>
"""


class ScriptTests(unittest.TestCase):
    def test_parse_games_extracts_and_normalizes_item(self) -> None:
        games = parse_games(SAMPLE_HTML, "game")

        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["appid"], "123")
        self.assertEqual(games[0]["title"], "Demo & Friends")
        self.assertEqual(games[0]["url"], "https://store.steampowered.com/app/123/Demo/")
        self.assertEqual(games[0]["platforms"], ["linux", "win"])
        self.assertEqual(games[0]["price"]["final_minor_units"], 0)

    def test_state_supports_keys_and_legacy_objects(self) -> None:
        raw = '["game:123", {"item_type":"dlc","appid":"456"}]'
        self.assertEqual(load_previous_keys(raw), {"game:123", "dlc:456"})

    def test_invalid_state_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            load_previous_keys("not-json")

    def test_empty_result_container_is_valid(self) -> None:
        html = '<div id="search_result_container"><div class="search_results_count">0</div></div>'
        self.assertEqual(parse_games(html, "game"), [])

    def test_missing_result_container_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            parse_games("<html><body>Access denied</body></html>", "game")

    def test_deduplicate_uses_type_and_appid(self) -> None:
        game = {"item_type": "game", "appid": "123", "url": "a"}
        dlc = {"item_type": "dlc", "appid": "123", "url": "b"}
        self.assertEqual(len(deduplicate([game, game, dlc])), 2)
        self.assertEqual(game_key(game), "game:123")

    def test_email_contains_new_game(self) -> None:
        game = {
            "item_type": "game",
            "appid": "123",
            "title": "Demo & Friends",
            "url": "https://example.com/game",
        }
        subject, text, html_body = build_email_content([game])

        self.assertIn("今日新增 1 款", subject)
        self.assertIn("Demo & Friends", text)
        self.assertIn("Demo &amp; Friends", html_body)

    @patch("script.requests.post")
    @patch.dict(
        os.environ,
        {"RESEND_API_KEY": "re_test", "MAIL_FROM": "Steam <sender@example.com>"},
        clear=True,
    )
    def test_resend_keeps_recipients_separate(self, post: Mock) -> None:
        post.return_value.raise_for_status.return_value = None
        game = {
            "item_type": "game",
            "appid": "123",
            "title": "Demo",
            "url": "https://example.com/game",
        }

        send_notification([game], ["one@example.com", "two@example.com"])

        payload = __import__("json").loads(post.call_args.kwargs["data"])
        self.assertEqual([item["to"] for item in payload], [["one@example.com"], ["two@example.com"]])
        self.assertIn("Idempotency-Key", post.call_args.kwargs["headers"])


if __name__ == "__main__":
    unittest.main()
