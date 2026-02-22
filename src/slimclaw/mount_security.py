"""Mount Security Module for SlimClaw

Validates additional mounts against an allowlist stored OUTSIDE the project root.
This prevents container agents from modifying security configuration.

Allowlist location: ~/.config/slimclaw/mount-allowlist.json
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from slimclaw.config import MOUNT_ALLOWLIST_PATH
from slimclaw.logger import logger
from slimclaw.types import AdditionalMount, AllowedRoot, MountAllowlist

# Cache the allowlist in memory - only reloads on process restart
_cached_allowlist: Optional[MountAllowlist] = None
_allowlist_load_error: Optional[str] = None

DEFAULT_BLOCKED_PATTERNS = [
    ".ssh",
    ".gnupg",
    ".gpg",
    ".aws",
    ".azure",
    ".gcloud",
    ".kube",
    ".docker",
    "credentials",
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
    "private_key",
    ".secret",
]


def load_mount_allowlist() -> Optional[MountAllowlist]:
    global _cached_allowlist, _allowlist_load_error

    if _cached_allowlist is not None:
        return _cached_allowlist

    if _allowlist_load_error is not None:
        return None

    try:
        if not MOUNT_ALLOWLIST_PATH.exists():
            _allowlist_load_error = f"Mount allowlist not found at {MOUNT_ALLOWLIST_PATH}"
            logger.warning(
                "Mount allowlist not found - additional mounts will be BLOCKED. "
                "Create the file to enable additional mounts.",
                path=str(MOUNT_ALLOWLIST_PATH),
            )
            return None

        content = MOUNT_ALLOWLIST_PATH.read_text(encoding="utf-8")
        raw = json.loads(content)

        if not isinstance(raw.get("allowedRoots"), list):
            raise ValueError("allowedRoots must be an array")
        if not isinstance(raw.get("blockedPatterns"), list):
            raise ValueError("blockedPatterns must be an array")
        if not isinstance(raw.get("nonMainReadOnly"), bool):
            raise ValueError("nonMainReadOnly must be a boolean")

        # Merge with default blocked patterns
        merged_blocked = list(set(DEFAULT_BLOCKED_PATTERNS + raw["blockedPatterns"]))

        allowlist = MountAllowlist(
            allowed_roots=[
                AllowedRoot(
                    path=r["path"],
                    allow_read_write=r["allowReadWrite"],
                    description=r.get("description"),
                )
                for r in raw["allowedRoots"]
            ],
            blocked_patterns=merged_blocked,
            non_main_read_only=raw["nonMainReadOnly"],
        )

        _cached_allowlist = allowlist
        logger.info(
            "Mount allowlist loaded successfully",
            path=str(MOUNT_ALLOWLIST_PATH),
            allowed_roots=len(allowlist.allowed_roots),
            blocked_patterns=len(allowlist.blocked_patterns),
        )
        return _cached_allowlist

    except Exception as err:
        _allowlist_load_error = str(err)
        logger.error(
            "Failed to load mount allowlist - additional mounts will be BLOCKED",
            path=str(MOUNT_ALLOWLIST_PATH),
            error=_allowlist_load_error,
        )
        return None


def _expand_path(p: str) -> str:
    home_dir = os.environ.get("HOME") or "/Users/user"
    if p.startswith("~/"):
        return os.path.join(home_dir, p[2:])
    if p == "~":
        return home_dir
    return os.path.abspath(p)


def _get_real_path(p: str) -> Optional[str]:
    try:
        return os.path.realpath(p)
    except Exception:
        return None


def _matches_blocked_pattern(real_path: str, blocked_patterns: list[str]) -> Optional[str]:
    path_parts = real_path.split(os.sep)

    for pattern in blocked_patterns:
        for part in path_parts:
            if part == pattern or pattern in part:
                return pattern
        if pattern in real_path:
            return pattern

    return None


def _find_allowed_root(
    real_path: str, allowed_roots: list[AllowedRoot]
) -> Optional[AllowedRoot]:
    for root in allowed_roots:
        expanded_root = _expand_path(root.path)
        real_root = _get_real_path(expanded_root)

        if real_root is None:
            continue

        relative = os.path.relpath(real_path, real_root)
        if not relative.startswith("..") and not os.path.isabs(relative):
            return root

    return None


def _is_valid_container_path(container_path: str) -> bool:
    if ".." in container_path:
        return False
    if container_path.startswith("/"):
        return False
    if not container_path or not container_path.strip():
        return False
    return True


@dataclass
class MountValidationResult:
    allowed: bool
    reason: str
    real_host_path: Optional[str] = None
    resolved_container_path: Optional[str] = None
    effective_readonly: Optional[bool] = None


def validate_mount(mount: AdditionalMount, is_main: bool) -> MountValidationResult:
    allowlist = load_mount_allowlist()

    if allowlist is None:
        return MountValidationResult(
            allowed=False,
            reason=f"No mount allowlist configured at {MOUNT_ALLOWLIST_PATH}",
        )

    container_path = mount.container_path or os.path.basename(mount.host_path)

    if not _is_valid_container_path(container_path):
        return MountValidationResult(
            allowed=False,
            reason=f'Invalid container path: "{container_path}" - must be relative, non-empty, and not contain ".."',
        )

    expanded_path = _expand_path(mount.host_path)
    real_path = _get_real_path(expanded_path)

    if real_path is None:
        return MountValidationResult(
            allowed=False,
            reason=f'Host path does not exist: "{mount.host_path}" (expanded: "{expanded_path}")',
        )

    blocked_match = _matches_blocked_pattern(real_path, allowlist.blocked_patterns)
    if blocked_match is not None:
        return MountValidationResult(
            allowed=False,
            reason=f'Path matches blocked pattern "{blocked_match}": "{real_path}"',
        )

    allowed_root = _find_allowed_root(real_path, allowlist.allowed_roots)
    if allowed_root is None:
        root_paths = ", ".join(_expand_path(r.path) for r in allowlist.allowed_roots)
        return MountValidationResult(
            allowed=False,
            reason=f'Path "{real_path}" is not under any allowed root. Allowed roots: {root_paths}',
        )

    requested_read_write = mount.readonly is False
    effective_readonly = True

    if requested_read_write:
        if not is_main and allowlist.non_main_read_only:
            effective_readonly = True
            logger.info("Mount forced to read-only for non-main group", mount=mount.host_path)
        elif not allowed_root.allow_read_write:
            effective_readonly = True
            logger.info(
                "Mount forced to read-only - root does not allow read-write",
                mount=mount.host_path,
                root=allowed_root.path,
            )
        else:
            effective_readonly = False

    desc = f' ({allowed_root.description})' if allowed_root.description else ""
    return MountValidationResult(
        allowed=True,
        reason=f'Allowed under root "{allowed_root.path}"{desc}',
        real_host_path=real_path,
        resolved_container_path=container_path,
        effective_readonly=effective_readonly,
    )


@dataclass
class ValidatedMount:
    host_path: str
    container_path: str
    readonly: bool


def validate_additional_mounts(
    mounts: list[AdditionalMount],
    group_name: str,
    is_main: bool,
) -> list[ValidatedMount]:
    validated: list[ValidatedMount] = []

    for mount in mounts:
        result = validate_mount(mount, is_main)

        if result.allowed:
            validated.append(
                ValidatedMount(
                    host_path=result.real_host_path,  # type: ignore[arg-type]
                    container_path=f"/workspace/extra/{result.resolved_container_path}",
                    readonly=result.effective_readonly,  # type: ignore[arg-type]
                )
            )
            logger.debug(
                "Mount validated successfully",
                group=group_name,
                host_path=result.real_host_path,
                container_path=result.resolved_container_path,
                readonly=result.effective_readonly,
                reason=result.reason,
            )
        else:
            logger.warning(
                "Additional mount REJECTED",
                group=group_name,
                requested_path=mount.host_path,
                container_path=mount.container_path,
                reason=result.reason,
            )

    return validated


def generate_allowlist_template() -> str:
    template = {
        "allowedRoots": [
            {
                "path": "~/projects",
                "allowReadWrite": True,
                "description": "Development projects",
            },
            {
                "path": "~/repos",
                "allowReadWrite": True,
                "description": "Git repositories",
            },
            {
                "path": "~/Documents/work",
                "allowReadWrite": False,
                "description": "Work documents (read-only)",
            },
        ],
        "blockedPatterns": ["password", "secret", "token"],
        "nonMainReadOnly": True,
    }
    return json.dumps(template, indent=2)
