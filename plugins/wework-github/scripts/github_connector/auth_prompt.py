"""Parse only the public GitHub device prompt from bounded CLI output lines."""

import codecs
import re
from urllib.parse import urlsplit

MAX_LINE = 4096
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_CODE = re.compile(r"(?<![A-Z0-9-])([A-Z0-9]{4}-[A-Z0-9]{4})(?![A-Z0-9-])")
_CODE_PROMPT = re.compile(
    r"(?:^|\s)(?i:(?:first copy your )?one-time code|(?:your )?code)\s*:\s*"
    r"([A-Z0-9]{4}-[A-Z0-9]{4})(?![A-Z0-9-])"
)
_CLIPBOARD_CODE = re.compile(
    r"^\s*!?\s*(?i:one-time code)\s+\(([A-Z0-9]{4}-[A-Z0-9]{4})\)"
    r"\s+(?i:copied to clipboard)\s*$"
)


def _verification_url(value):
    if "\\" in value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        return None
    # A device prompt has no query. Reject even empty ?/# suffixes so credentials
    # or redirect parameters cannot be smuggled into a browser action.
    if "?" in value or "#" in value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or parsed.netloc.lower() != "github.com"
        or parsed.path not in ("/login/device", "/login/device/")
    ):
        return None
    return "https://github.com/login/device"


class PromptParser:
    """Keep stream fragments separate and emit only a validated URL/code pair.

    The same instance accepts stdout and stderr using the optional stream name.
    Diagnostic text is consumed internally and is never returned to the caller.
    """

    def __init__(self):
        self._streams = {}
        self._url = None
        self._code = None
        self._emitted = False

    def _state(self, stream):
        if stream not in ("stdout", "stderr"):
            raise ValueError("Unsupported GitHub CLI output stream.")
        return self._streams.setdefault(
            stream,
            {
                "decoder": codecs.getincrementaldecoder("utf-8")(errors="replace"),
                "line": [],
                "discard": False,
                "finished": False,
            },
        )

    def _line(self, value):
        value = _ANSI.sub("", value)
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            return
        for match in _URL.finditer(value):
            url = _verification_url(match.group())
            if url and self._url is None:
                self._url = url
        # Ignore code-shaped substrings in URLs or unrelated diagnostic text.
        # gh's English device prompt is stable; a bare code line is also safe.
        code = (
            _CODE.fullmatch(value.strip())
            or _CODE_PROMPT.search(value)
            or _CLIPBOARD_CODE.fullmatch(value)
        )
        if code and self._code is None:
            self._code = code.group(1)

    def _consume(self, state, value):
        for character in value:
            if character in "\r\n":
                if not state["discard"]:
                    self._line("".join(state["line"]))
                state["line"].clear()
                state["discard"] = False
            elif not state["discard"]:
                state["line"].append(character)
                if len(state["line"]) > MAX_LINE:
                    state["line"].clear()
                    state["discard"] = True

    def _result(self):
        if self._url and self._code and not self._emitted:
            self._emitted = True
            return {
                "status": "waiting_browser",
                "userCode": self._code,
                "verificationUrl": self._url,
            }
        return None

    def feed(self, chunk, *, stream="stderr"):
        if type(chunk) is not bytes:
            raise TypeError("GitHub CLI prompt chunks must be bytes.")
        state = self._state(stream)
        if state["finished"]:
            return None
        # Never retain an unbounded provider line, including when a caller gives
        # us one large chunk instead of the normal small pipe reads.
        for position in range(0, len(chunk), MAX_LINE):
            self._consume(
                state, state["decoder"].decode(chunk[position : position + MAX_LINE])
            )
        return self._result()

    def finish(self, stream="stderr"):
        state = self._state(stream)
        if state["finished"]:
            return None
        state["finished"] = True
        self._consume(state, state["decoder"].decode(b"", final=True))
        if not state["discard"]:
            self._line("".join(state["line"]))
        state["line"].clear()
        return self._result()
