"""Plugin discovery: built-in tools plus external entry-point plugins.

A failing plugin is logged and skipped — one bad tool must never take down the
quick-look GUI during observing.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points

from llamas_ql.QLook.plugins.base import ENTRY_POINT_GROUP, QLToolPlugin
from llamas_ql.QLook.plugins.builtin.header_inspector import HeaderInspectorPlugin

logger = logging.getLogger(__name__)

#: Tools shipped in this repo (instantiated fresh on each discovery).
BUILTIN_PLUGIN_CLASSES = (HeaderInspectorPlugin,)


def discover_plugins() -> list[QLToolPlugin]:
    """Return instantiated plugins from built-ins and entry points."""
    plugins: list[QLToolPlugin] = []
    for cls in BUILTIN_PLUGIN_CLASSES:
        try:
            plugins.append(cls())
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Built-in plugin %s failed to load: %s", cls.__name__, exc)

    try:
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:  # Python <3.10 selection API
        eps = entry_points().get(ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]
    for ep in eps:
        try:
            plugins.append(ep.load()())
        except Exception as exc:
            logger.warning("Plugin %s failed to load: %s", getattr(ep, "name", ep), exc)
    return plugins
