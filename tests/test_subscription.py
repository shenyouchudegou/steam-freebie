import unittest

from subscriber_store import email_digest
from subscription import extract_pgp_message, process_subscription


PGP = "-----BEGIN PGP MESSAGE-----\nabc123\n-----END PGP MESSAGE-----"


def event(title: str, body: str = PGP, user_id: int = 123) -> dict:
    return {
        "discussion": {
            "title": title,
            "body": body,
            "category": {"name": "Subscribe"},
            "user": {"id": user_id, "login": "alice"},
        }
    }


class SubscriptionTests(unittest.TestCase):
    def test_form_wrapper_is_accepted(self) -> None:
        self.assertEqual(extract_pgp_message(f"### PGP 密文\n\n{PGP}\n"), PGP)

    def test_plaintext_email_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            extract_pgp_message(f"alice@example.com\n{PGP}")

    def test_extra_text_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            extract_pgp_message(f"hello\n{PGP}")

    def test_subscribe_and_duplicate(self) -> None:
        first = process_subscription(event("Subscribe"), [], lambda _: " Alice@Example.com ")
        duplicate = process_subscription(
            event("Subscribe"), first.records, lambda _: "alice@example.com"
        )

        self.assertTrue(first.changed)
        self.assertEqual(first.records[0]["email_hash"], email_digest("alice@example.com"))
        self.assertFalse(duplicate.changed)
        self.assertEqual(len(duplicate.records), 1)

    def test_unsubscribe_requires_same_github_id(self) -> None:
        subscribed = process_subscription(
            event("Subscribe"), [], lambda _: "alice@example.com"
        )

        wrong_account = process_subscription(
            event("Unsubscribe", user_id=456),
            subscribed.records,
            lambda _: "alice@example.com",
        )
        correct_account = process_subscription(
            event("Unsubscribe"), subscribed.records, lambda _: "alice@example.com"
        )

        self.assertFalse(wrong_account.changed)
        self.assertEqual(wrong_account.response, "未找到匹配记录。")
        self.assertTrue(correct_account.changed)
        self.assertEqual(correct_account.records, [])

    def test_wrong_email_uses_same_not_found_response(self) -> None:
        subscribed = process_subscription(
            event("Subscribe"), [], lambda _: "alice@example.com"
        )
        result = process_subscription(
            event("Unsubscribe"), subscribed.records, lambda _: "other@example.com"
        )
        self.assertEqual(result.response, "未找到匹配记录。")

    def test_title_is_strict(self) -> None:
        result = process_subscription(event("subscribe"), [], lambda _: "alice@example.com")
        self.assertFalse(result.changed)
        self.assertIn("标题", result.response)

    def test_undecryptable_message_is_rejected_without_crashing(self) -> None:
        def fail(_: str) -> str:
            raise ValueError("gpg failure")

        result = process_subscription(event("Subscribe"), [], fail)
        self.assertFalse(result.changed)
        self.assertIn("请求未处理", result.response)


if __name__ == "__main__":
    unittest.main()
