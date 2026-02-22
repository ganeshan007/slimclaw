import subprocess

from slimclaw.logger import logger

CONTAINER_RUNTIME_BIN = "docker"


def readonly_mount_args(host_path: str, container_path: str) -> list[str]:
    return ["-v", f"{host_path}:{container_path}:ro"]


def stop_container(name: str) -> str:
    return f"{CONTAINER_RUNTIME_BIN} stop {name}"


def ensure_container_runtime_running() -> None:
    try:
        subprocess.run(
            [CONTAINER_RUNTIME_BIN, "info"],
            capture_output=True,
            timeout=10,
            check=True,
        )
        logger.debug("Container runtime already running")
    except Exception as err:
        logger.error("Failed to reach container runtime", error=str(err))
        print(
            "\n"
            "+" + "=" * 62 + "+\n"
            "|  FATAL: Container runtime failed to start                    |\n"
            "|                                                              |\n"
            "|  Agents cannot run without a container runtime. To fix:      |\n"
            "|  1. Ensure Docker is installed and running                   |\n"
            "|  2. Run: docker info                                         |\n"
            "|  3. Restart SlimClaw                                         |\n"
            "+" + "=" * 62 + "+\n",
            file=__import__("sys").stderr,
        )
        raise RuntimeError("Container runtime is required but failed to start") from err


def cleanup_orphans() -> None:
    try:
        result = subprocess.run(
            [CONTAINER_RUNTIME_BIN, "ps", "--filter", "name=slimclaw-", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        orphans = [name for name in result.stdout.strip().split("\n") if name]
        for name in orphans:
            try:
                subprocess.run(
                    stop_container(name).split(),
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                pass  # already stopped
        if orphans:
            logger.info("Stopped orphaned containers", count=len(orphans), names=orphans)
    except Exception as err:
        logger.warning("Failed to clean up orphaned containers", error=str(err))
