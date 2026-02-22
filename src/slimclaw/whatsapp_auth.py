"""Standalone WhatsApp authentication script.

Connects to WhatsApp, displays QR code for scanning, and saves credentials.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from slimclaw.config import STORE_DIR
from slimclaw.logger import logger


async def authenticate() -> None:
    try:
        from neonize.client import NewClient
        from neonize.events import ConnectedEv, PairStatusEv
    except ImportError:
        print("neonize not installed. Install with: pip install neonize")
        sys.exit(1)

    auth_dir = STORE_DIR / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(auth_dir / "neonize.db")

    client = NewClient(db_path)
    connected = asyncio.Event()

    @client.event(ConnectedEv)
    def on_connected(client_ref, event):
        print("\nAuthenticated successfully!")
        logger.info("WhatsApp authentication successful")
        connected.set()

    print("Scan the QR code with WhatsApp to authenticate...")
    print("(Open WhatsApp > Settings > Linked Devices > Link a Device)\n")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, client.connect)
    await connected.wait()

    print("Credentials saved. You can now start SlimClaw.")


def run() -> None:
    asyncio.run(authenticate())


if __name__ == "__main__":
    run()
