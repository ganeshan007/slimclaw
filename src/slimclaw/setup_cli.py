"""Standalone interactive setup wizard for SlimClaw.

No Claude Code required. Run with: slimclaw-setup
"""
from __future__ import annotations

import ctypes.util
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

# --- ANSI colors ---

BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"


def _ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def _header(step: int, title: str) -> None:
    print(f"\n{BOLD}{CYAN}[{step}/13]{RESET} {BOLD}{title}{RESET}")


def _prompt(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {question}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return answer or default


def _confirm(question: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        answer = input(f"  {question} ({hint}): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    if not answer:
        return default
    return answer in ("y", "yes")


def _choose(question: str, options: list[str]) -> int:
    """Present numbered options, return 0-based index."""
    print(f"  {question}")
    for i, opt in enumerate(options, 1):
        print(f"    {BOLD}{i}{RESET}. {opt}")
    while True:
        try:
            answer = input(f"  Choice [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        if not answer:
            return 0
        try:
            idx = int(answer) - 1
            if 0 <= idx < len(options):
                return idx
        except ValueError:
            pass
        print(f"  {RED}Enter a number 1-{len(options)}{RESET}")


def _run(cmd: str, check: bool = True, capture: bool = False, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, shell=True, check=check,
        capture_output=capture, text=True, timeout=timeout,
    )


# --- Setup steps ---

def step_1_name_bot() -> str:
    _header(1, "Name Your Bot")
    print(f"  This name becomes the trigger word in group chats (e.g. @TARS).")
    name = _prompt("Bot name", "TARS")

    if name != "TARS":
        # Update config.py
        config_path = Path("src/slimclaw/config.py")
        if config_path.exists():
            content = config_path.read_text()
            content = re.sub(
                r'or "TARS"',
                f'or "{name}"',
                content,
            )
            config_path.write_text(content)
            _ok(f"Updated config.py → {name}")

        # Update group CLAUDE.md files
        for gpath in [Path("groups/global/CLAUDE.md"), Path("groups/main/CLAUDE.md")]:
            if gpath.exists():
                content = gpath.read_text()
                content = content.replace("# TARS\n\nYou are TARS,", f"# {name}\n\nYou are {name},")
                content = content.replace('"@TARS"', f'"@{name}"')
                gpath.write_text(content)
                _ok(f"Updated {gpath}")
    else:
        _ok(f"Using default name: TARS")

    return name


def step_2_choose_app() -> str:
    _header(2, "Choose Your App")

    # Check for available app skills
    skills_dir = Path(".claude/skills")
    available_apps = ["WhatsApp (built-in)"]
    app_skills: dict[int, str] = {}

    if skills_dir.exists():
        for skill_dir in sorted(skills_dir.iterdir()):
            if skill_dir.name.startswith("add-") and (skill_dir / "SKILL.md").exists():
                app_name = skill_dir.name.removeprefix("add-").title()
                idx = len(available_apps)
                available_apps.append(f"{app_name} (via /add-{skill_dir.name.removeprefix('add-')} skill)")
                app_skills[idx] = skill_dir.name

    available_apps.append("Terminal (direct chat)")

    choice = _choose("Which app do you want to use?", available_apps)

    if choice == 0:
        _ok("WhatsApp selected")
        return "whatsapp"

    if choice == len(available_apps) - 1:
        # Terminal
        skill_path = skills_dir / "add-terminal" / "SKILL.md"
        if skill_path.exists():
            _ok("Terminal skill found — run /add-terminal after base setup")
            return "terminal"
        else:
            _warn("Terminal doesn't have a skill yet.")
            print(f"  {DIM}SlimClaw uses a Skills over Features approach — contribute by creating{RESET}")
            print(f"  {DIM}.claude/skills/add-terminal/SKILL.md (see /customize for the Channel protocol){RESET}")
            if _confirm("Set up with WhatsApp instead?"):
                return "whatsapp"
            _ok("Continuing with base setup only")
            return "base-only"

    if choice in app_skills:
        skill_name = app_skills[choice]
        _ok(f"Skill found: .claude/skills/{skill_name}/")
        print(f"  Run {BOLD}/add-{skill_name.removeprefix('add-')}{RESET} in Claude Code after base setup.")
        return "skill:" + skill_name

    return "whatsapp"


def step_3_check_env() -> dict[str, str]:
    _header(3, "Check Environment")
    status: dict[str, str] = {}

    # Python
    ver = sys.version_info
    if ver >= (3, 11):
        _ok(f"Python {ver.major}.{ver.minor}.{ver.micro}")
        status["python"] = "ok"
    else:
        _fail(f"Python {ver.major}.{ver.minor} — need 3.11+")
        status["python"] = "too_old"

    # Docker
    try:
        _run("docker info", capture=True)
        _ok("Docker running")
        status["docker"] = "running"
    except (subprocess.CalledProcessError, FileNotFoundError):
        if shutil.which("docker"):
            _warn("Docker installed but not running")
            status["docker"] = "installed_not_running"
        else:
            _fail("Docker not found")
            status["docker"] = "not_found"

    # libmagic — ctypes.util.find_library misses Homebrew paths on Apple Silicon,
    # so also check common install locations directly
    libmagic_found = bool(ctypes.util.find_library("magic"))
    if not libmagic_found:
        for candidate in [
            "/opt/homebrew/lib/libmagic.dylib",
            "/usr/local/lib/libmagic.dylib",
            "/usr/lib/libmagic.so.1",
            "/usr/lib/x86_64-linux-gnu/libmagic.so.1",
        ]:
            if Path(candidate).exists():
                libmagic_found = True
                break
    if not libmagic_found:
        # Also check if brew knows about it
        try:
            result = _run("brew list libmagic", capture=True, check=False)
            if result.returncode == 0:
                libmagic_found = True
        except FileNotFoundError:
            pass

    if libmagic_found:
        _ok("libmagic found")
        status["libmagic"] = "ok"
    else:
        _fail("libmagic missing")
        status["libmagic"] = "missing"
        if platform.system() == "Darwin":
            if _confirm("Install libmagic via Homebrew?"):
                _run("brew install libmagic", check=False)
        else:
            print(f"  {DIM}Install with: sudo apt-get install libmagic1{RESET}")

    # Existing state
    status["has_env"] = "true" if Path(".env").exists() else "false"
    status["has_auth"] = "true" if Path("store/auth/neonize.db").exists() else "false"

    if status["has_env"] == "true":
        _ok(".env exists")
    if status["has_auth"] == "true":
        _ok("WhatsApp credentials exist")

    return status


def step_4_install_deps() -> None:
    _header(4, "Install Dependencies")

    already_installed = False
    try:
        import slimclaw.main  # noqa: F401
        already_installed = bool(shutil.which("slimclaw"))
    except ImportError:
        pass

    if already_installed:
        _ok("slimclaw already installed")
        if not _confirm("Reinstall?", default=False):
            return

    print(f"  Running pip install -e .[dev]...\n")
    try:
        _run("pip install -e '.[dev]'", timeout=300)
        print()
        _ok("Dependencies installed")
    except subprocess.CalledProcessError:
        print()
        _fail("pip install failed")
        print(f"  {DIM}Try: pip install -e . (without dev extras){RESET}")
        sys.exit(1)


def step_5_container(status: dict[str, str]) -> None:
    _header(5, "Container Runtime")

    if status.get("docker") == "installed_not_running":
        print(f"  {DIM}Starting Docker...{RESET}")
        if platform.system() == "Darwin":
            _run("open -a Docker", check=False)
        else:
            _run("sudo systemctl start docker", check=False)
        import time
        for _ in range(6):
            time.sleep(5)
            try:
                _run("docker info", capture=True)
                _ok("Docker started")
                break
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
        else:
            _fail("Docker didn't start — start it manually and re-run setup")
            sys.exit(1)
    elif status.get("docker") == "not_found":
        _fail("Docker is required for running agents")
        if platform.system() == "Darwin":
            print(f"  {DIM}Install with: brew install --cask docker{RESET}")
        else:
            print(f"  {DIM}Install with: curl -fsSL https://get.docker.com | sh{RESET}")
        sys.exit(1)

    # Build container image
    try:
        _run("docker inspect slimclaw-agent:latest", capture=True)
        _ok("Container image exists")
        if not _confirm("Rebuild container image?", default=False):
            return
    except subprocess.CalledProcessError:
        pass

    build_script = Path("container/build.sh")
    if not build_script.exists():
        _fail("container/build.sh not found")
        sys.exit(1)

    print(f"  Building container image (this takes a few minutes)...\n")
    try:
        _run("./container/build.sh", timeout=600)
        print()
        _ok("Container image built (slimclaw-agent:latest)")
    except subprocess.CalledProcessError:
        print()
        _fail("Container build failed — check output above")
        sys.exit(1)


def step_6_claude_auth(status: dict[str, str]) -> None:
    _header(6, "Claude Authentication")

    env_path = Path(".env")
    if status.get("has_env") == "true":
        content = env_path.read_text()
        if "ANTHROPIC_API_KEY=" in content or "CLAUDE_CODE_OAUTH_TOKEN=" in content:
            _ok("Credentials already configured")
            if not _confirm("Reconfigure?", default=False):
                return

    choice = _choose("How do you authenticate with Claude?", [
        "API key (ANTHROPIC_API_KEY)",
        "Claude subscription token (CLAUDE_CODE_OAUTH_TOKEN)",
    ])

    if choice == 0:
        key = _prompt("Enter your Anthropic API key")
        if not key:
            _fail("No key provided")
            sys.exit(1)

        # Read existing .env or start fresh
        existing = env_path.read_text() if env_path.exists() else ""
        lines = [l for l in existing.splitlines() if not l.startswith("ANTHROPIC_API_KEY=")]
        lines.append(f"ANTHROPIC_API_KEY={key}")
        env_path.write_text("\n".join(lines) + "\n")
        _ok("API key saved to .env")
    else:
        print(f"  {DIM}Run 'claude setup-token' in another terminal to get your token.{RESET}")
        token = _prompt("Enter your OAuth token")
        if not token:
            _fail("No token provided")
            sys.exit(1)

        existing = env_path.read_text() if env_path.exists() else ""
        lines = [l for l in existing.splitlines() if not l.startswith("CLAUDE_CODE_OAUTH_TOKEN=")]
        lines.append(f"CLAUDE_CODE_OAUTH_TOKEN={token}")
        env_path.write_text("\n".join(lines) + "\n")
        _ok("OAuth token saved to .env")

    # Model selection
    print()
    MODELS = [
        ("claude-haiku-4-5-20251001", "Haiku 4.5 — fastest, cheapest ($1/$5 per MTok)"),
        ("claude-sonnet-4-6", "Sonnet 4.6 — balanced speed and intelligence ($3/$15 per MTok) (recommended)"),
        ("claude-opus-4-6", "Opus 4.6 — most capable, best for complex tasks ($5/$25 per MTok)"),
    ]
    model_choice = _choose("Which Claude model should your bot use?", [m[1] for m in MODELS])
    model_id = MODELS[model_choice][0]

    existing = env_path.read_text() if env_path.exists() else ""
    lines = [l for l in existing.splitlines() if not l.startswith("CLAUDE_MODEL=")]
    lines.append(f"CLAUDE_MODEL={model_id}")
    env_path.write_text("\n".join(lines) + "\n")
    _ok(f"Model set to {model_id}")


def step_7_whatsapp_auth(status: dict[str, str]) -> None:
    _header(7, "WhatsApp Authentication")

    if status.get("has_auth") == "true":
        _ok("WhatsApp credentials exist")
        if not _confirm("Re-authenticate?", default=False):
            return
        # Delete old credentials so neonize generates a fresh QR code
        auth_db = Path("store/auth/neonize.db")
        if auth_db.exists():
            auth_db.unlink()
            _ok("Old credentials deleted")

    while True:
        print(f"  {DIM}Launching WhatsApp auth (QR code will open in browser)...{RESET}")
        print(f"  Scan the QR code: WhatsApp → Settings → Linked Devices → Link a Device")
        print()

        try:
            _run("slimclaw-auth", timeout=300)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass  # slimclaw-auth uses SIGKILL to exit, which looks like a crash

        # Check if auth succeeded by looking for the credentials file
        if Path("store/auth/neonize.db").exists():
            _ok("WhatsApp authenticated")
            return

        if not _confirm("Retry authentication?"):
            _fail("WhatsApp authentication required — run slimclaw-auth manually to continue")
            sys.exit(1)


def step_8_channel_type(bot_name: str) -> dict:
    _header(8, "Set Up Main Channel")

    print(f"  {BOLD}What is the main channel?{RESET}")
    print(f"  {DIM}A private WhatsApp chat where you talk to {bot_name} directly.{RESET}")
    print(f"  {DIM}Every message here goes to {bot_name} — no @{bot_name} prefix needed.{RESET}")
    print()
    print(f"  {BOLD}How to access it:{RESET}")
    print(f"  {DIM}• Self-chat: Open WhatsApp → tap your own name at the top of chats{RESET}")
    print(f"  {DIM}• Solo group: Create a WhatsApp group with just yourself{RESET}")
    print()
    print(f"  {DIM}Want {bot_name} in group chats with other people? You'll add those{RESET}")
    print(f"  {DIM}later — just tell {bot_name} \"join Family Chat\" from the main channel.{RESET}")
    print()

    choice = _choose("Where should your main channel be?", [
        "Self-chat (message yourself) — recommended",
        "Solo group (a group with just you)",
        "DM with bot (bot has its own phone number)",
    ])

    return {"type": ["self", "group", "dm"][choice]}


def step_9_discover_group(channel_info: dict, bot_name: str) -> tuple[str, str]:
    """Returns (jid, display_name)."""
    _header(9, "Connect Main Channel")

    if channel_info["type"] == "self":
        phone = _prompt("Your WhatsApp phone number (country code + number, no + or spaces, e.g. 14155551234)")
        jid = f"{phone}@s.whatsapp.net"
        _ok(f"Self-chat registered for +{phone}")
        return jid, "Self-chat"

    if channel_info["type"] == "dm":
        phone = _prompt("Bot's WhatsApp phone number (country code + number, no + or spaces)")
        jid = f"{phone}@s.whatsapp.net"
        _ok(f"DM registered for +{phone}")
        return jid, "DM with bot"

    # Group — need to sync WhatsApp groups
    print(f"  {DIM}Syncing your WhatsApp groups...{RESET}")
    try:
        _run(f"{sys.executable} -m slimclaw", check=False, capture=True, timeout=30)
    except subprocess.TimeoutExpired:
        pass

    db_path = Path("store/messages.db")
    groups_found = []
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        groups_found = conn.execute(
            "SELECT jid, name FROM chats WHERE is_group = 1 AND jid != '__group_sync__' ORDER BY last_message_time DESC LIMIT 20"
        ).fetchall()
        conn.close()

    if groups_found:
        options = [name for _, name in groups_found]
        choice = _choose("Which WhatsApp group should be your main channel?", options)
        jid, name = groups_found[choice]
        _ok(f"Selected: {name}")
        return jid, name

    # No groups synced — ask for group name and try to find it
    _warn("Couldn't sync groups yet")
    print(f"  {DIM}This can happen on first connection. You have two options:{RESET}")
    print()
    choice = _choose("How to proceed?", [
        f"Create a new solo group — I'll make one on WhatsApp and come back",
        f"Skip for now — I'll start {bot_name} and register a group later",
    ])

    if choice == 0:
        print()
        print(f"  {BOLD}Instructions:{RESET}")
        print(f"  1. Open WhatsApp on your phone")
        print(f"  2. Create a new group (add any contact, then remove them)")
        print(f"  3. Name it something like \"{bot_name}\" or \"My Assistant\"")
        print(f"  4. Come back here and press Enter")
        print()
        _prompt("Press Enter when your group is created")

        # Re-sync
        print(f"  {DIM}Syncing again...{RESET}")
        try:
            _run(f"{sys.executable} -m slimclaw", check=False, capture=True, timeout=30)
        except subprocess.TimeoutExpired:
            pass

        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            groups_found = conn.execute(
                "SELECT jid, name FROM chats WHERE is_group = 1 AND jid != '__group_sync__' ORDER BY last_message_time DESC LIMIT 20"
            ).fetchall()
            conn.close()

        if groups_found:
            options = [name for _, name in groups_found]
            choice = _choose("Which group?", options)
            jid, name = groups_found[choice]
            _ok(f"Selected: {name}")
            return jid, name

        _warn("Still no groups found — you can register one later from the main channel")
        return "", ""

    # Skip
    return "", ""


def step_9_generic_app(app_name: str, bot_name: str) -> tuple[str, str]:
    """Generic app setup for non-WhatsApp channels.

    Collects a bot token and a chat ID using the ``{prefix}:{id}`` JID convention.
    Returns ``(jid, display_name)``.
    """
    _header(9, f"Connect {app_name.title()} Channel")

    PREFIX_MAP = {
        "telegram": "tg",
        "discord": "dc",
        "slack": "sl",
        "signal": "sg",
    }
    prefix = PREFIX_MAP.get(app_name, app_name[:2])

    # Collect bot token
    token_key = f"{app_name.upper()}_BOT_TOKEN"
    print(f"  {DIM}Your {app_name.title()} bot token will be saved to .env as {token_key}{RESET}")
    token = _prompt(f"{app_name.title()} bot token")
    if token:
        env_path = Path(".env")
        existing = env_path.read_text() if env_path.exists() else ""
        lines = [l for l in existing.splitlines() if not l.startswith(f"{token_key}=")]
        lines.append(f"{token_key}={token}")
        env_path.write_text("\n".join(lines) + "\n")
        _ok(f"Token saved to .env")

    # Collect chat ID for main channel
    print()
    print(f"  {DIM}Enter the chat/group ID for your main channel.{RESET}")
    print(f"  {DIM}This will be stored as {prefix}:<id> internally.{RESET}")
    chat_id = _prompt(f"Chat/group ID")
    if not chat_id:
        _warn("No chat ID provided — you can register a channel later")
        return "", ""

    jid = f"{prefix}:{chat_id}"
    display_name = f"{app_name.title()} main channel"
    _ok(f"Registered: {jid}")
    return jid, display_name


def step_10_register(jid: str, bot_name: str) -> None:
    _header(10, "Register Main Channel")

    from slimclaw.db import init_database, set_registered_group
    from slimclaw.types import RegisteredGroup

    init_database()
    group = RegisteredGroup(
        name="main",
        folder="main",
        trigger=f"@{bot_name}",
        added_at=datetime.now(timezone.utc).isoformat(),
        requires_trigger=False,
    )
    set_registered_group(jid, group)

    # Ensure group directories exist
    Path("groups/main/logs").mkdir(parents=True, exist_ok=True)
    Path("groups/global").mkdir(parents=True, exist_ok=True)

    _ok(f"Registered as main channel")


def step_11_mount_allowlist() -> None:
    _header(11, "Mount Allowlist")

    allowlist_path = Path.home() / ".config" / "slimclaw" / "mount-allowlist.json"

    if allowlist_path.exists():
        _ok("Mount allowlist exists")
        if not _confirm("Reconfigure?", default=False):
            return

    print(f"  {DIM}By default, the agent is sandboxed — it can only access its own group folder.{RESET}")
    print(f"  {DIM}You can optionally grant access to other directories (e.g. ~/projects){RESET}")
    print(f"  {DIM}so the agent can read or edit files there when you ask it to.{RESET}")
    print()
    if not _confirm("Grant access to directories outside SlimClaw?", default=False):
        allowlist_path.parent.mkdir(parents=True, exist_ok=True)
        allowlist_path.write_text(json.dumps({
            "allowed_roots": [],
            "blocked_patterns": [],
            "non_main_read_only": True,
        }, indent=2) + "\n")
        _ok("Empty allowlist created")
        return

    roots = []
    while True:
        path = _prompt("Directory path (empty to finish)")
        if not path:
            break
        readonly = not _confirm(f"Allow write access to {path}?", default=False)
        roots.append({"path": os.path.expanduser(path), "readonly": readonly})

    allowlist_path.parent.mkdir(parents=True, exist_ok=True)
    allowlist_path.write_text(json.dumps({
        "allowed_roots": roots,
        "blocked_patterns": [],
        "non_main_read_only": True,
    }, indent=2) + "\n")
    _ok(f"Allowlist saved with {len(roots)} root(s)")


def step_12_service(bot_name: str) -> None:
    _header(12, "Start Service")

    if not _confirm(f"Set up {bot_name} as a background service?"):
        print(f"  {DIM}You can run it manually with: slimclaw{RESET}")
        return

    cwd = os.getcwd()
    python_path = sys.executable

    if platform.system() == "Darwin":
        plist_path = Path.home() / "Library" / "LaunchAgents" / "com.slimclaw.plist"
        plist = textwrap.dedent(f"""\
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
            <plist version="1.0">
            <dict>
                <key>Label</key>
                <string>com.slimclaw</string>
                <key>ProgramArguments</key>
                <array>
                    <string>{python_path}</string>
                    <string>-m</string>
                    <string>slimclaw</string>
                </array>
                <key>WorkingDirectory</key>
                <string>{cwd}</string>
                <key>RunAtLoad</key>
                <true/>
                <key>KeepAlive</key>
                <true/>
                <key>StandardOutPath</key>
                <string>{cwd}/logs/slimclaw.log</string>
                <key>StandardErrorPath</key>
                <string>{cwd}/logs/slimclaw.error.log</string>
            </dict>
            </plist>
        """)
        Path("logs").mkdir(exist_ok=True)
        plist_path.write_text(plist)

        # Unload if previously loaded
        _run(f"launchctl unload {plist_path} 2>/dev/null", check=False, capture=True)
        _run(f"launchctl load {plist_path}", check=False)
        _ok(f"launchd service loaded")
    else:
        service_dir = Path.home() / ".config" / "systemd" / "user"
        service_dir.mkdir(parents=True, exist_ok=True)
        service_path = service_dir / "slimclaw.service"
        service_path.write_text(textwrap.dedent(f"""\
            [Unit]
            Description=SlimClaw Assistant
            After=network.target

            [Service]
            Type=simple
            WorkingDirectory={cwd}
            ExecStart={python_path} -m slimclaw
            Restart=always
            RestartSec=10

            [Install]
            WantedBy=default.target
        """))
        _run("systemctl --user daemon-reload", check=False)
        _run("systemctl --user enable --now slimclaw", check=False)
        _ok("systemd service enabled")


def step_13_verify(bot_name: str) -> None:
    _header(13, "Verify")

    checks = [
        ("Credentials", Path(".env").exists()),
        ("WhatsApp auth", Path("store/auth/neonize.db").exists()),
        ("Mount allowlist", (Path.home() / ".config" / "slimclaw" / "mount-allowlist.json").exists()),
    ]

    # Container image
    try:
        _run("docker inspect slimclaw-agent:latest", capture=True)
        checks.append(("Container image", True))
    except (subprocess.CalledProcessError, FileNotFoundError):
        checks.append(("Container image", False))

    # Registered groups
    db_path = Path("store/messages.db")
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM registered_groups").fetchone()[0]
        conn.close()
        checks.append(("Registered groups", count > 0))
    else:
        checks.append(("Registered groups", False))

    all_ok = True
    for label, ok in checks:
        if ok:
            _ok(label)
        else:
            _fail(label)
            all_ok = False

    print()
    if all_ok:
        print(f"  {GREEN}{BOLD}Your bot {bot_name} is ready!{RESET}")
        print(f"  Send a message in your main channel to try it out.")
        print(f"  In group chats, start messages with @{bot_name}")
        print()
        print(f"  {DIM}Logs: tail -f logs/slimclaw.log{RESET}")
        print(f"  {DIM}Run manually: slimclaw{RESET}")
    else:
        print(f"  {YELLOW}Some checks failed — fix the issues above and re-run slimclaw-setup{RESET}")


def run() -> None:
    print(f"\n{BOLD}SlimClaw Setup{RESET}")
    print(f"{DIM}Interactive setup wizard — no Claude Code required{RESET}")
    print(f"{DIM}For AI-guided setup, use /setup in Claude Code instead{RESET}")

    bot_name = step_1_name_bot()
    app = step_2_choose_app()
    status = step_3_check_env()
    step_4_install_deps()
    step_5_container(status)
    step_6_claude_auth(status)

    channel_name = None
    if app == "whatsapp":
        step_7_whatsapp_auth(status)
        channel_info = step_8_channel_type(bot_name)
        jid, channel_name = step_9_discover_group(channel_info, bot_name)
        if jid:
            step_10_register(jid, bot_name)
            print(f"\n  {BOLD}Your main channel:{RESET} {channel_name}")
            print(f"  {DIM}Open this chat on WhatsApp to talk to {bot_name} — no @{bot_name} needed.{RESET}")
            print(f"  {DIM}To add {bot_name} to other groups, say \"join <group name>\" here.{RESET}")
        else:
            _warn(f"No main channel registered — start {bot_name} and register a group later")
    elif app in ("telegram", "discord", "slack", "signal"):
        jid, channel_name = step_9_generic_app(app, bot_name)
        if jid:
            step_10_register(jid, bot_name)
            print(f"\n  {BOLD}Your main channel:{RESET} {channel_name}")
    elif app.startswith("skill:"):
        skill = app.removeprefix("skill:")
        print(f"\n  {CYAN}Base setup complete!{RESET}")
        print(f"  Run {BOLD}/add-{skill.removeprefix('add-')}{RESET} in Claude Code to configure your app.")
        print(f"  {DIM}Continuing with remaining setup steps...{RESET}")
    else:
        _warn("Skipping app-specific setup — configure your app later with /customize")

    step_11_mount_allowlist()
    step_12_service(bot_name)
    step_13_verify(bot_name)


if __name__ == "__main__":
    run()
