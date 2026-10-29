"""Versioned, explicit extension contracts. Plugins are trusted Python code.

Nothing in a subscription JSON can import or install code. The caller must register
plugins in Python or explicitly load modules through --plugin / INFO_RELAY_PLUGINS.
"""
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional

API_VERSION = 1
Item = Dict[str, str]


@dataclass(frozen=True)
class SourcePlugin:
    fetch: Callable[[dict, datetime], List[Item]]
    validate: Callable[[dict], None]
    mode: str = "stream"  # stream: stable item IDs; snapshot: notify every content transition


@dataclass(frozen=True)
class ProcessorPlugin:
    process: Callable[[Item, dict], Optional[Item]]
    validate: Callable[[dict], None]


@dataclass(frozen=True)
class ChannelPlugin:
    send: Callable[[dict, str, str, str], None]  # options, subject, body, stable batch ID
    validate: Callable[[dict], None]
    diagnose: Optional[Callable[[dict], None]] = None


class Registry:
    def __init__(self):
        self.sources, self.processors, self.channels = {}, {}, {}
        self.loaded_modules = set()

    @staticmethod
    def _register(target, name, plugin):
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", name):
            raise ValueError("Plugin names must use lowercase letters, digits, '-' or '_'")
        if name in target:
            raise ValueError(f"Plugin already registered: {name}")
        target[name] = plugin

    def source(self, name, plugin):
        if not isinstance(plugin, SourcePlugin) or plugin.mode not in ("stream", "snapshot", "daily"):
            raise ValueError("Invalid source contract")
        self._register(self.sources, name, plugin)

    def processor(self, name, plugin):
        if not isinstance(plugin, ProcessorPlugin):
            raise ValueError("Invalid processor contract")
        self._register(self.processors, name, plugin)

    def channel(self, name, plugin):
        if not isinstance(plugin, ChannelPlugin):
            raise ValueError("Invalid channel contract")
        self._register(self.channels, name, plugin)

    def load(self, module_name):
        if not re.fullmatch(r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*", module_name):
            raise ValueError("Use an installed Python module name for --plugin")
        if module_name in self.loaded_modules:
            return
        module = importlib.import_module(module_name)
        if getattr(module, "INFO_RELAY_API", None) != API_VERSION:
            raise ValueError(f"{module_name}: expected INFO_RELAY_API = {API_VERSION}")
        # Transactional registration: a broken plugin must not leave half a registry.
        staged = Registry()
        staged.sources, staged.processors, staged.channels = (dict(self.sources), dict(self.processors), dict(self.channels))
        module.register(staged)
        self.sources, self.processors, self.channels = staged.sources, staged.processors, staged.channels
        self.loaded_modules.add(module_name)


_default = None


def default_registry():
    global _default
    if _default is None:
        from .builtins import register
        registry = Registry()
        register(registry)
        _default = registry
    return _default


def process_item(row, pipeline, registry=None):
    from .state import checked_item
    registry = registry or default_registry()
    result = checked_item(row)
    for step in pipeline:
        result = registry.processors[step["type"]].process(dict(result), dict(step.get("options", {})))
        if result is None:
            return None
        result = checked_item(result)
        if result["id"] != row["id"]:
            raise ValueError("Processors must preserve item IDs")
    return result
