#!/usr/bin/env python3
import json
import sys
from native_runtime import adapter_main

if __name__ == "__main__":
    try:
        raise SystemExit(adapter_main("user", sys.argv[1:]))
    except Exception:
        print(json.dumps({"error": "plugin_auth_lark_failed"}))
        raise SystemExit(1)
