import os
import re
import time
from pathlib import Path

from slimclaw.env import read_env_file

# Read config values from .env (falls back to os.environ).
# Secrets are NOT read here — they stay on disk and are loaded only
# where needed (container_runner.py) to avoid leaking to child processes.
_env_config = read_env_file([
    "ASSISTANT_NAME",
    "ASSISTANT_HAS_OWN_NUMBER",
])

ASSISTANT_NAME: str = (
    os.environ.get("ASSISTANT_NAME") or _env_config.get("ASSISTANT_NAME") or "andy"
)
ASSISTANT_HAS_OWN_NUMBER: bool = (
    os.environ.get("ASSISTANT_HAS_OWN_NUMBER") or _env_config.get("ASSISTANT_HAS_OWN_NUMBER")
) == "true"

POLL_INTERVAL: float = 2.0  # seconds
SCHEDULER_POLL_INTERVAL: float = 60.0
IPC_POLL_INTERVAL: float = 1.0

# Absolute paths needed for container mounts
PROJECT_ROOT: Path = Path.cwd()
HOME_DIR: str = os.environ.get("HOME") or "/Users/user"

# Mount security: allowlist stored OUTSIDE project root, never mounted into containers
MOUNT_ALLOWLIST_PATH: Path = Path(HOME_DIR) / ".config" / "slimclaw" / "mount-allowlist.json"

STORE_DIR: Path = (PROJECT_ROOT / "store").resolve()
GROUPS_DIR: Path = (PROJECT_ROOT / "groups").resolve()
DATA_DIR: Path = (PROJECT_ROOT / "data").resolve()
MAIN_GROUP_FOLDER: str = "main"

CONTAINER_IMAGE: str = os.environ.get("CONTAINER_IMAGE") or "slimclaw-agent:latest"
CONTAINER_TIMEOUT: int = int(os.environ.get("CONTAINER_TIMEOUT") or "1800000")
CONTAINER_MAX_OUTPUT_SIZE: int = int(os.environ.get("CONTAINER_MAX_OUTPUT_SIZE") or "10485760")
IDLE_TIMEOUT: int = int(os.environ.get("IDLE_TIMEOUT") or "1800000")
MAX_CONCURRENT_CONTAINERS: int = max(
    1, int(os.environ.get("MAX_CONCURRENT_CONTAINERS") or "5")
)

TRIGGER_PATTERN: re.Pattern[str] = re.compile(
    rf"^@{re.escape(ASSISTANT_NAME)}\b", re.IGNORECASE
)

# Timezone for scheduled tasks
TIMEZONE: str = os.environ.get("TZ") or time.tzname[0]
