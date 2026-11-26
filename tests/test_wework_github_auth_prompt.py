"""Public device-prompt parsing; provider diagnostics never leave the parser."""

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/wework-github/scripts"))
prompt = importlib.import_module("github_connector.auth_prompt")


class PromptParserTests(unittest.TestCase):
    def test_clipboard_success_prompt_uses_exact_safe_format(self):
        transcript = (
            b"\x1b[33m!\x1b[0m One-time code (ABCD-1234) copied to clipboard\n"
            b"Open this URL: https://github.com/login/device\n"
        )
        for split in range(len(transcript) + 1):
            parser = prompt.PromptParser()
            result = parser.feed(transcript[:split]) or parser.feed(transcript[split:])
            self.assertEqual(result["userCode"], "ABCD-1234")
            self.assertEqual(
                result["verificationUrl"], "https://github.com/login/device"
            )
        for line in (
            "PRIVATE One-time code (ABCD-1234) copied to clipboard",
            "! One-time code (ABCD-12345) copied to clipboard",
            "! One-time code (abcd-1234) copied to clipboard",
            "! One-time code (ABCD-1234) copied to clipboard PRIVATE",
        ):
            parser = prompt.PromptParser()
            self.assertIsNone(
                parser.feed((line + "\nhttps://github.com/login/device\n").encode())
            )

    def test_all_byte_boundaries_ansi_and_utf8_produce_only_safe_fields(self):
        transcript = (
            "中文 \x1b[33m!\x1b[0m First copy your one-time code: \x1b[1mAB12-CD34\x1b[0m\n"
            "Open this URL to continue in your web browser: https://github.com/login/device\n"
            "PRIVATE_DIAGNOSTIC\n"
        ).encode()
        for split in range(len(transcript) + 1):
            with self.subTest(split=split):
                parser = prompt.PromptParser()
                result = parser.feed(transcript[:split]) or parser.feed(
                    transcript[split:]
                )
                self.assertEqual(
                    result,
                    {
                        "status": "waiting_browser",
                        "userCode": "AB12-CD34",
                        "verificationUrl": "https://github.com/login/device",
                    },
                )
                self.assertNotIn("PRIVATE", str(result))
                self.assertIsNone(parser.finish())

    def test_url_and_code_must_both_be_present_and_finish_flushes_partial_line(self):
        parser = prompt.PromptParser()
        self.assertIsNone(parser.feed(b"https://github.com/login/device\n"))
        self.assertIsNone(parser.feed(b"Your code: ABCD-1234"))
        self.assertEqual(parser.finish()["userCode"], "ABCD-1234")
        self.assertIsNone(parser.finish())
        self.assertIsNone(parser.feed(b"ignored after EOF\n"))
        for line in (b"Your code: ABCD-1234\n", b"https://github.com/login/device\n"):
            parser = prompt.PromptParser()
            self.assertIsNone(parser.feed(line))
            self.assertIsNone(parser.finish())

    def test_stdout_and_stderr_fragments_never_concatenate(self):
        parser = prompt.PromptParser()
        self.assertIsNone(parser.feed(b"https://github.com/log", stream="stdout"))
        self.assertIsNone(parser.feed(b"in/device\nAAAA-BBBB\n", stream="stderr"))
        self.assertIsNone(parser.finish("stderr"))
        result = parser.feed(b"in/device\n", stream="stdout")
        self.assertEqual(result["userCode"], "AAAA-BBBB")
        parser = prompt.PromptParser()
        self.assertIsNone(
            parser.feed(b"https://github.com/login/device\nAAAA-", stream="stderr")
        )
        self.assertIsNone(parser.feed(b"BBBB\n", stream="stdout"))
        self.assertIsNone(parser.finish("stdout"))
        self.assertIsNone(parser.finish("stderr"))

    def test_rejects_untrusted_or_parameterized_browser_targets(self):
        invalid = [
            "http://github.com/login/device",
            "https://github.com.evil.example/login/device",
            "https://evil.example/?next=https://github.com/login/device",
            "https://github.com@evil.example/login/device",
            "https://user@github.com/login/device",
            "https://github.com:443/login/device",
            "https://github.com:/login/device",
            "https://github.com/login/device?token=PRIVATE",
            "https://github.com/login/device?",
            "https://github.com/login/device#PRIVATE",
            "https://github.com/login/device#",
            "https://github.com/login/device/../other",
            "https://github.com/login/%64evice",
            "https://github.com\\@evil.example/login/device",
            "https://github.com/login/device\\evil",
            "https://github.com/login/device\x00",
            "https://github.com/login/device\x1bX",
            "https://github.com/login/oauth/authorize?client_id=foo&token=PRIVATE",
            "https://github.com/login/oauth/authorize",
        ]
        for url in invalid:
            with self.subTest(url=repr(url)):
                parser = prompt.PromptParser()
                self.assertIsNone(parser.feed(("ABCD-EF12\n" + url + "\n").encode()))
                self.assertIsNone(parser.finish())

    def test_otp_is_exactly_two_uppercase_alphanumeric_groups(self):
        for code in (
            "abcD-EF12",
            "ABCD-ef12",
            "ABCDE-F123",
            "ABC-1234",
            "ABCD-12345",
            "XABCD-1234",
            "ABCD-1234-X",
            "ABCD_1234",
        ):
            with self.subTest(code=code):
                parser = prompt.PromptParser()
                self.assertIsNone(
                    parser.feed(
                        ("https://github.com/login/device\n" + code + "\n").encode()
                    )
                )
                self.assertIsNone(parser.finish())

    def test_code_shaped_diagnostics_or_url_secrets_are_not_exposed_as_otp(self):
        for diagnostic in (
            "token=ABCD-EF12",
            "PRIVATE diagnostic ABCD-EF12",
            "https://evil.example/?token=ABCD-EF12",
            "https://github.com/login/device?token=ABCD-EF12",
        ):
            with self.subTest(diagnostic=diagnostic):
                parser = prompt.PromptParser()
                self.assertIsNone(
                    parser.feed(
                        (
                            "https://github.com/login/device\n" + diagnostic + "\n"
                        ).encode()
                    )
                )
                self.assertIsNone(parser.finish())

    def test_overlong_lines_are_discarded_without_retaining_or_scanning_suffix(self):
        parser = prompt.PromptParser()
        huge = (
            b"x" * (prompt.MAX_LINE * 4)
            + b" AAAA-BBBB https://github.com/login/device\n"
        )
        self.assertIsNone(parser.feed(huge))
        self.assertLessEqual(len(parser._streams["stderr"]["line"]), prompt.MAX_LINE)
        self.assertIsNone(parser.feed(b"https://github.com/login/device\r"))
        self.assertEqual(parser.feed(b"AAAA-BBBB\r\n")["userCode"], "AAAA-BBBB")

    def test_rejects_controls_and_invalid_streams_without_echoing_them(self):
        for control in ("\x00", "\x08", "\x7f", "\t"):
            parser = prompt.PromptParser()
            self.assertIsNone(
                parser.feed(
                    (
                        "https://github.com/login/device\nAAAA-BBBB"
                        + control
                        + "PRIVATE\n"
                    ).encode()
                )
            )
            self.assertIsNone(parser.finish())
        with self.assertRaises(TypeError):
            prompt.PromptParser().feed("PRIVATE")
        with self.assertRaises(ValueError) as caught:
            prompt.PromptParser().feed(b"", stream="PRIVATE")
        self.assertNotIn("PRIVATE", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
