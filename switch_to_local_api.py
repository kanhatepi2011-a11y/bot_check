#!/usr/bin/env python3
"""One-time migration from Telegram cloud Bot API to the local server."""

from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or token == "YOUR_BOT_TOKEN":
        print("ERROR: Set TELEGRAM_BOT_TOKEN in .env first.", file=sys.stderr)
        return 2

    request = Request(
        f"https://api.telegram.org/bot{token}/logOut",
        data=b"",
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"ERROR: Cloud Bot API logOut failed: {exc}", file=sys.stderr)
        return 1

    if payload.get("ok") is not True:
        description = payload.get("description", "Unknown Telegram error")
        print(f"ERROR: {description}", file=sys.stderr)
        return 1

    print("OK: Bot logged out from Telegram cloud Bot API.")
    print("You can start the Local Bot API stack now.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
