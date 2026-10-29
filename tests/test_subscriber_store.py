import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from subscriber_store import PGPDecryptor, decrypt_records, email_digest, encrypt_records


KEY = "a-secure-random-test-key-that-is-long-enough"
PGP = "-----BEGIN PGP MESSAGE-----\nabc\n-----END PGP MESSAGE-----"


class SubscriberStoreTests(unittest.TestCase):
    def test_aes_gcm_round_trip(self) -> None:
        records = [
            {
                "github_id": 123,
                "github_login": "alice",
                "email_hash": email_digest("alice@example.com"),
                "email_encrypted": PGP,
            }
        ]

        encrypted = encrypt_records(records, KEY)

        self.assertTrue(encrypted.startswith("v1."))
        self.assertNotIn("alice", encrypted)
        self.assertEqual(decrypt_records(encrypted, KEY), records)

    def test_wrong_key_is_rejected(self) -> None:
        encrypted = encrypt_records([], KEY)
        with self.assertRaises(ValueError):
            decrypt_records(encrypted, "another-secure-random-key-that-is-long-enough")

    def test_short_key_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            encrypt_records([], "short")

    @unittest.skipUnless(shutil.which("gpg"), "gpg is not installed")
    def test_real_gpg_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            base = [
                "gpg",
                "--homedir",
                str(home),
                "--batch",
                "--no-tty",
                "--pinentry-mode",
                "loopback",
            ]
            subprocess.run(
                [
                    *base,
                    "--passphrase",
                    "",
                    "--quick-generate-key",
                    "Steam Freebie Test <test@example.com>",
                    "rsa2048",
                    "encr",
                    "0",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            private_key = subprocess.run(
                [*base, "--armor", "--export-secret-keys", "test@example.com"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout
            encrypted = subprocess.run(
                [
                    *base,
                    "--armor",
                    "--trust-model",
                    "always",
                    "--encrypt",
                    "--recipient",
                    "test@example.com",
                ],
                input="Alice@Example.com",
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout

            with PGPDecryptor(private_key) as decryptor:
                self.assertEqual(decryptor.decrypt(encrypted), "Alice@Example.com")


if __name__ == "__main__":
    unittest.main()
