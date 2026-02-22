"""Standalone WhatsApp authentication script.

Connects to WhatsApp, displays QR code for scanning, and saves credentials.
Opens a browser window with the QR code for easy scanning.
"""
from __future__ import annotations

import asyncio
import io
import platform
import subprocess
import sys
from pathlib import Path

from slimclaw.config import STORE_DIR
from slimclaw.logger import logger

_QR_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html><head><title>SlimClaw - WhatsApp Auth</title>
<meta http-equiv="refresh" content="3">
<style>
  body { font-family: -apple-system, sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; margin: 0; background: #f5f5f5; }
  .card { background: white; border-radius: 16px; padding: 40px; box-shadow: 0 4px 24px rgba(0,0,0,0.1); text-align: center; max-width: 400px; }
  h2 { margin: 0 0 8px; }
  .timer { font-size: 18px; color: #666; margin: 12px 0; }
  .timer.urgent { color: #e74c3c; font-weight: bold; }
  .instructions { color: #666; font-size: 14px; margin-top: 16px; }
  svg { width: 280px; height: 280px; }
</style></head><body>
<div class="card">
  <h2>Scan with WhatsApp</h2>
  <div class="timer" id="timer">Expires in <span id="countdown">60</span>s</div>
  <div id="qr">{{QR_SVG}}</div>
  <div class="instructions">Settings &rarr; Linked Devices &rarr; Link a Device</div>
</div>
<script>
  var startKey = 'slimclaw_qr_start';
  var start = localStorage.getItem(startKey);
  if (!start) { start = Date.now().toString(); localStorage.setItem(startKey, start); }
  var elapsed = Math.floor((Date.now() - parseInt(start)) / 1000);
  var remaining = Math.max(0, 60 - elapsed);
  var countdown = document.getElementById('countdown');
  var timer = document.getElementById('timer');
  countdown.textContent = remaining;
  if (remaining <= 10) timer.classList.add('urgent');
  if (remaining <= 0) {
    timer.textContent = 'QR code expired \\u2014 a new one will appear shortly';
    timer.classList.add('urgent');
    localStorage.removeItem(startKey);
  }
</script></body></html>
"""

_SUCCESS_HTML = """\
<!DOCTYPE html>
<html><head><title>SlimClaw - Authenticated</title>
<style>
  body { font-family: -apple-system, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; background: #f5f5f5; }
  .card { background: white; border-radius: 16px; padding: 40px; box-shadow: 0 4px 24px rgba(0,0,0,0.1); text-align: center; max-width: 400px; }
  h2 { margin: 0 0 8px; color: #27ae60; }
  p { color: #666; }
</style></head><body>
<div class="card">
  <h2>Authenticated!</h2>
  <p>WhatsApp is connected. You can close this page.</p>
</div></body></html>
"""


def _open_in_browser(path: Path) -> None:
    """Open a file in the default browser."""
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as err:
        logger.debug("Could not open browser", error=str(err))


def _generate_qr_svg(data: str) -> str:
    """Generate an SVG string from QR code data."""
    import qrcode
    import qrcode.image.svg

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


async def authenticate() -> None:
    try:
        from neonize.client import NewClient
        from neonize.events import ConnectedEv, PairStatusEv, QREv
    except ImportError:
        print("neonize not installed. Install with: pip install neonize")
        sys.exit(1)

    auth_dir = STORE_DIR / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(auth_dir / "neonize.db")
    qr_html_path = STORE_DIR / "qr-auth.html"

    client = NewClient(db_path)
    connected = asyncio.Event()
    browser_opened = False
    qr_count = 0

    @client.event(QREv)
    def on_qr(client_ref, event):
        nonlocal browser_opened, qr_count
        codes = list(event.Codes)
        if not codes:
            return

        qr_count += 1
        qr_data = codes[0]

        # Debug: show data type and prefix to diagnose invalid QR issues
        data_type = type(qr_data).__name__
        preview = str(qr_data)[:50]
        print(f"  QR code #{qr_count} received ({data_type}, {len(str(qr_data))} chars)")
        logger.debug("QR code received", count=qr_count, data_type=data_type, preview=preview)

        # Ensure qr_data is a string
        if isinstance(qr_data, bytes):
            qr_data = qr_data.decode("utf-8", errors="replace")

        # Generate SVG and write HTML
        try:
            svg = _generate_qr_svg(qr_data)
            html = _QR_HTML_TEMPLATE.replace("{{QR_SVG}}", svg)
            qr_html_path.write_text(html)

            if not browser_opened:
                browser_opened = True
                _open_in_browser(qr_html_path)
                print(f"  Opened in browser: {qr_html_path}")
            else:
                print(f"  Browser page will auto-refresh in a few seconds")
        except Exception as err:
            logger.debug("QR HTML generation failed, terminal-only", error=str(err))

        # Always print to terminal as fallback
        try:
            import qrcode
            qr = qrcode.QRCode()
            qr.add_data(qr_data)
            qr.make(fit=True)
            qr.print_ascii(invert=True)
        except Exception:
            print(f"  QR data: {qr_data}")

    loop = asyncio.get_event_loop()

    @client.event(ConnectedEv)
    def on_connected(client_ref, event):
        print("\nAuthenticated successfully!")
        print("Syncing account data (this may take a moment)...")
        logger.info("WhatsApp authentication successful")

        # Write success page
        try:
            qr_html_path.write_text(_SUCCESS_HTML)
        except Exception:
            pass

        # Callback runs in Go thread — must use call_soon_threadsafe
        # to wake up the asyncio event loop
        loop.call_soon_threadsafe(connected.set)

    print("Scan the QR code with WhatsApp to authenticate...")
    print("(Open WhatsApp > Settings > Linked Devices > Link a Device)")
    print("Waiting for QR code...\n")

    # Fire-and-forget: client.connect() is neonize's Go event loop and blocks forever.
    # We just need to wait for the ConnectedEv callback to set the event.
    loop.run_in_executor(None, client.connect)
    await connected.wait()

    # Give neonize a few seconds to finish the initial sync (prekeys, push names)
    # before killing the Go thread — prevents incomplete state
    print("Waiting for initial sync to complete...")
    await asyncio.sleep(5)

    # Clean up QR file
    try:
        qr_html_path.unlink(missing_ok=True)
    except Exception:
        pass

    print("Credentials saved. You can now start SlimClaw.")

    # Disconnect and force exit — neonize's Go thread won't stop on its own
    try:
        client.disconnect()
    except Exception:
        pass
    os._exit(0)


def run() -> None:
    asyncio.run(authenticate())


if __name__ == "__main__":
    run()
