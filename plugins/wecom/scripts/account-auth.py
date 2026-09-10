"""Native private adapter entry; preserve the exact child exit status."""

import json
import sys
import native_runtime as native


def main(arguments):
    if arguments == ["export"]:
        with native.locked():
            return native.invoke(["__wegent", *arguments]).returncode
    return native.invoke(["__wegent", *arguments]).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception:
        print(json.dumps({"error": "plugin_auth_wecom_failed"}))
        raise SystemExit(1)
