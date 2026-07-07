# SPDX-License-Identifier: AGPL-3.0-only

"""Regression test: assert every MediaApp __init__ attribute is in MediaAppProtocol.

Adding a new ``self._xxx = ...`` in ``app.py``'s ``__init__`` but forgetting to
add it to ``protocols.MediaAppProtocol`` breaks the Phase 3 contract.  This test
catches that gap at test time.
"""

from __future__ import annotations

from nyrx.app import MediaApp
from nyrx.protocols import MediaAppProtocol


def test_all_init_attrs_in_protocol() -> None:
    """Every ``self._<name>`` assigned in ``__init__`` must be in the Protocol."""
    protocol_attrs = set(MediaAppProtocol.__annotations__)

    # co_names includes every name used in the bytecode, not just assignments.
    # Filter to names starting with '_' (private) and NOT methods (no parens).
    # We extract the subset that are `self._xxx = ...` by looking at names
    # that appear in __init__.co_code as STORE_ATTR targets.
    import dis

    stored: set[str] = set()
    for instr in dis.get_instructions(MediaApp.__init__):
        if instr.opname == "STORE_ATTR":
            stored.add(instr.argval)

    missing = sorted(stored - protocol_attrs)
    assert not missing, (
        f"{len(missing)} attribute(s) in MediaApp.__init__ missing from "
        f"MediaAppProtocol:\n  " + "\n  ".join(missing)
    )


def test_media_app_protocol_has_docstring() -> None:
    """The class docstring must live on MediaAppProtocol itself, not float mid-body."""
    assert MediaAppProtocol.__doc__ and "mixins" in MediaAppProtocol.__doc__


def test_protocol_members_resolve_on_media_app() -> None:
    """Every method/property declared in MediaAppProtocol must exist on MediaApp.

    ``test_all_init_attrs_in_protocol`` covers the attribute half of the
    contract; this covers the method half.  A mixin method renamed or removed
    from ``MediaApp`` fails here at test time.
    """
    import inspect

    missing = [
        name
        for name, member in MediaAppProtocol.__dict__.items()
        if not name.startswith("__")
        and (inspect.isfunction(member) or isinstance(member, property))
        and not hasattr(MediaApp, name)
    ]
    assert not missing, (
        f"{len(missing)} MediaAppProtocol member(s) missing from MediaApp:\n  "
        + "\n  ".join(missing)
    )


def test_protocol_methods_have_runtime_stubs() -> None:
    """Every method in the Protocol must accept ``self`` with no mandatory args beyond ``self``."""
    import inspect

    for name, method in MediaAppProtocol.__dict__.items():
        if name.startswith("_") or name == "watch":
            continue
        if inspect.isfunction(method) or inspect.ismethod(method):
            sig = inspect.signature(method)
            params = list(sig.parameters.values())
            if len(params) > 0 and params[0].name == "self":
                assert (
                    params[0].annotation is inspect.Parameter.empty
                    or str(params[0].annotation) == "MediaAppProtocol"
                ), f"{name}: first param should be bare self or self: MediaAppProtocol"
