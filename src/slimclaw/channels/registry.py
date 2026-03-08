"""Auto-discovery registry for app channels.

Scans the channels/ package for modules that export a class with a ``name``
attribute matching the module filename.  Apps whose dependencies are missing
(e.g. neonize not installed) are silently skipped.
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import Type

from slimclaw.types import Channel


def discover_apps() -> dict[str, Type[Channel]]:
    """Return ``{name: class}`` for every valid channel module in this package."""
    apps: dict[str, Type[Channel]] = {}
    package_dir = Path(__file__).resolve().parent

    for module_info in pkgutil.iter_modules([str(package_dir)]):
        if module_info.name.startswith("_") or module_info.name == "registry":
            continue
        try:
            mod = importlib.import_module(f"slimclaw.channels.{module_info.name}")
        except ImportError:
            # Missing optional dependency (e.g. neonize) — skip silently
            continue

        # Find the channel class: look for a class whose ``name`` attribute
        # matches the module filename.
        for attr_name in dir(mod):
            obj = getattr(mod, attr_name)
            if (
                isinstance(obj, type)
                and getattr(obj, "name", None) == module_info.name
                and obj is not Channel
            ):
                apps[module_info.name] = obj
                break

    return apps


def get_enabled_apps(available: dict[str, Type[Channel]] | None = None) -> list[str]:
    """Return the list of app names to load.

    Reads ``ENABLED_APPS`` from the environment / .env.  When unset, returns
    all discovered app names (backwards-compatible default).
    """
    from slimclaw.config import ENABLED_APPS

    if available is None:
        available = discover_apps()

    if ENABLED_APPS is not None:
        return [name for name in ENABLED_APPS if name in available]

    return list(available.keys())
