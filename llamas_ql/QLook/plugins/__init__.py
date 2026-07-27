"""Plugin system for the Quick Look GUI.

External tools (e.g. PyAPS) attach without modifying this package by declaring a
``llamas_ql.qlook_plugins`` entry point that returns a
:class:`~llamas_ql.QLook.plugins.base.QLToolPlugin`.  Tools shipped in-repo are
listed in :data:`~llamas_ql.QLook.plugins.registry.BUILTIN_PLUGINS`.
"""
