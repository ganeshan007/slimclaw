"""Tests for the app registry and AppOpts."""
import os
import sys
import types as builtins_types
from unittest.mock import patch

import pytest
from slimclaw.channels.registry import discover_apps, get_enabled_apps
from slimclaw.types import AppOpts, Channel, RegisteredGroup


# --- AppOpts ---


class TestAppOpts:
    def test_creates_with_required_fields(self):
        opts = AppOpts(
            on_message=lambda jid, msg: None,
            on_chat_metadata=lambda jid, ts, name, ch, is_group: None,
            registered_groups=lambda: {},
        )
        assert opts.on_unregistered_trigger is None

    def test_creates_with_all_fields(self):
        opts = AppOpts(
            on_message=lambda jid, msg: None,
            on_chat_metadata=lambda jid, ts, name, ch, is_group: None,
            registered_groups=lambda: {},
            on_unregistered_trigger=lambda jid, name, content: None,
        )
        assert opts.on_unregistered_trigger is not None

    def test_callbacks_are_callable(self):
        calls = []
        opts = AppOpts(
            on_message=lambda jid, msg: calls.append(("msg", jid)),
            on_chat_metadata=lambda jid, ts, name, ch, is_group: calls.append(("meta", jid)),
            registered_groups=lambda: {"test": RegisteredGroup(
                name="test", folder="test", trigger="@bot", added_at="2024-01-01",
            )},
        )
        opts.on_message("jid1", None)
        opts.on_chat_metadata("jid2", "ts", None, None, None)
        groups = opts.registered_groups()
        assert calls == [("msg", "jid1"), ("meta", "jid2")]
        assert "test" in groups


# --- discover_apps ---


class TestDiscoverApps:
    def test_discovers_whatsapp(self):
        apps = discover_apps()
        assert "whatsapp" in apps

    def test_discovered_class_has_correct_name(self):
        apps = discover_apps()
        for name, cls in apps.items():
            assert cls.name == name

    def test_skips_registry_module(self):
        apps = discover_apps()
        assert "registry" not in apps

    def test_skips_init_module(self):
        apps = discover_apps()
        assert "__init__" not in apps

    def test_skips_modules_with_import_errors(self):
        """Modules whose deps are missing should be silently skipped."""
        # Create a fake module that will raise ImportError
        import pkgutil
        from pathlib import Path

        original_iter = pkgutil.iter_modules

        def patched_iter(paths):
            yield from original_iter(paths)
            # Add a fake module that doesn't exist
            info = builtins_types.SimpleNamespace(
                name="fake_missing_app",
                ispkg=False,
            )
            yield info

        with patch("slimclaw.channels.registry.pkgutil.iter_modules", patched_iter):
            apps = discover_apps()
            # Should not raise, and fake module should not appear
            assert "fake_missing_app" not in apps
            # WhatsApp should still be found
            assert "whatsapp" in apps


# --- get_enabled_apps ---


class TestGetEnabledApps:
    def test_returns_all_when_no_config(self):
        apps = discover_apps()
        with patch("slimclaw.config.ENABLED_APPS", None):
            enabled = get_enabled_apps(apps)
            assert enabled == list(apps.keys())

    def test_filters_to_configured_apps(self):
        apps = discover_apps()
        with patch("slimclaw.config.ENABLED_APPS", ["whatsapp"]):
            enabled = get_enabled_apps(apps)
            assert enabled == ["whatsapp"]

    def test_ignores_unknown_app_names(self):
        apps = discover_apps()
        with patch("slimclaw.config.ENABLED_APPS", ["nonexistent"]):
            enabled = get_enabled_apps(apps)
            assert enabled == []

    def test_filters_mix_of_known_and_unknown(self):
        apps = discover_apps()
        with patch("slimclaw.config.ENABLED_APPS", ["whatsapp", "nonexistent"]):
            enabled = get_enabled_apps(apps)
            assert enabled == ["whatsapp"]

    def test_empty_list_enables_nothing(self):
        apps = discover_apps()
        with patch("slimclaw.config.ENABLED_APPS", []):
            enabled = get_enabled_apps(apps)
            assert enabled == []


# --- ENABLED_APPS config parsing ---


class TestEnabledAppsConfig:
    def test_parses_comma_separated(self):
        with patch.dict(os.environ, {"ENABLED_APPS": "whatsapp,telegram"}):
            # Re-import to pick up env var
            from slimclaw import config
            import importlib
            importlib.reload(config)
            assert config.ENABLED_APPS == ["whatsapp", "telegram"]

    def test_strips_whitespace(self):
        with patch.dict(os.environ, {"ENABLED_APPS": " whatsapp , telegram "}):
            from slimclaw import config
            import importlib
            importlib.reload(config)
            assert config.ENABLED_APPS == ["whatsapp", "telegram"]

    def test_unset_returns_none(self):
        env = os.environ.copy()
        env.pop("ENABLED_APPS", None)
        with patch.dict(os.environ, env, clear=True):
            from slimclaw import config
            import importlib
            importlib.reload(config)
            assert config.ENABLED_APPS is None

    def test_empty_string_returns_none(self):
        with patch.dict(os.environ, {"ENABLED_APPS": ""}):
            from slimclaw import config
            import importlib
            importlib.reload(config)
            assert config.ENABLED_APPS is None


# --- WhatsApp accepts AppOpts ---


class TestWhatsAppAcceptsAppOpts:
    def test_whatsapp_channel_accepts_app_opts(self):
        from slimclaw.channels.whatsapp import WhatsAppChannel
        opts = AppOpts(
            on_message=lambda jid, msg: None,
            on_chat_metadata=lambda jid, ts, name, ch, is_group: None,
            registered_groups=lambda: {},
        )
        ch = WhatsAppChannel(opts)
        assert ch.name == "whatsapp"
        assert ch.opts is opts

    def test_legacy_alias_still_works(self):
        from slimclaw.channels.whatsapp import WhatsAppChannelOpts
        assert WhatsAppChannelOpts is AppOpts
