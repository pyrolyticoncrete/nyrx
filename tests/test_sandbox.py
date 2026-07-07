# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the Lua sandbox (2.4).

Covers ``_deep_from_lua`` table conversion, ``call_probe`` error handling and
Lua-table-to-dict conversion, the ``crypto.aes_gcm`` stub, the sandbox RCE
fix (``python``/``loadfile``/``dofile`` nulled), per-config environment
isolation, retained stdlib flexibility, the playwright http/https URL guard,
and per-config secrets scoping.  Creating a Lua runtime also executes every
``_register_*`` table-building function; those lines are covered incidentally
(no browser or network flow is exercised).
"""

from __future__ import annotations

import pytest

from nyrx.sources.tv_movies.sandbox import Sandbox, _deep_from_lua


def _load(tmp_path, lua_src: str):
    script = tmp_path / "probe.lua"
    script.write_text(lua_src)
    sb = Sandbox.create()
    return sb, sb.load_script(str(script))


class TestDeepFromLua:
    def test_sequential_int_keys_become_list(self) -> None:
        assert _deep_from_lua({1: "a", 2: "b", 3: "c"}) == ["a", "b", "c"]

    def test_string_keys_stay_dict(self) -> None:
        assert _deep_from_lua({"a": 1, "b": 2}) == {"a": 1, "b": 2}

    def test_scalar_passthrough(self) -> None:
        assert _deep_from_lua(42) == 42
        assert _deep_from_lua("x") == "x"
        assert _deep_from_lua(None) is None

    def test_nested_conversion(self) -> None:
        assert _deep_from_lua({"x": {1: "p", 2: "q"}, "y": {"z": 1}}) == {
            "x": ["p", "q"],
            "y": {"z": 1},
        }

    def test_empty_dict_stays_dict(self) -> None:
        assert _deep_from_lua({}) == {}

    def test_gap_in_keys_stays_dict(self) -> None:
        assert _deep_from_lua({1: "a", 3: "c"}) == {1: "a", 3: "c"}

    def test_zero_index_stays_dict(self) -> None:
        assert _deep_from_lua({0: "a", 1: "b"}) == {0: "a", 1: "b"}


class TestCallProbe:
    def test_missing_probe_returns_none(self, tmp_path) -> None:
        sb, g = _load(tmp_path, "probe = nil")
        assert sb.call_probe(g, {}) is None

    def test_raising_probe_returns_none(self, tmp_path) -> None:
        sb, g = _load(tmp_path, "function probe(p) error('boom') end")
        assert sb.call_probe(g, {"id": "1"}) is None

    def test_table_result_converted(self, tmp_path) -> None:
        sb, g = _load(
            tmp_path,
            "function probe(p) return {id = p.id, tags = {'a', 'b'}} end",
        )
        assert sb.call_probe(g, {"id": "abc"}) == {
            "id": "abc",
            "tags": ["a", "b"],
        }

    def test_nil_result_returns_none(self, tmp_path) -> None:
        sb, g = _load(tmp_path, "function probe(p) end")
        assert sb.call_probe(g, {"id": "1"}) is None


class TestCryptoAesGcmStub:
    def test_direct_call_raises_runtime_error(self, tmp_path) -> None:
        sb, g = _load(tmp_path, "function probe(p) return 'ok' end")
        aes_gcm = g["crypto"]["aes_gcm"]
        with pytest.raises(RuntimeError, match="pycryptodomex"):
            aes_gcm("data")

    def test_inside_probe_is_swallowed(self, tmp_path) -> None:
        sb, g = _load(tmp_path, "function probe(p) return crypto.aes_gcm('x') end")
        assert sb.call_probe(g, {}) is None


class TestPythonBridgeNulled:
    def test_python_global_is_nil(self) -> None:
        from nyrx.sources.tv_movies.sandbox import _get_lua

        g = _get_lua().globals()
        assert g["python"] is None
        assert g["loadfile"] is None
        assert g["dofile"] is None

    def test_python_unreachable_from_probe(self, tmp_path) -> None:
        sb, g = _load(tmp_path, "function probe(p) return python end")
        assert sb.call_probe(g, {}) is None

    def test_python_eval_swallowed(self, tmp_path) -> None:
        sb, g = _load(tmp_path, "function probe(p) return python.eval('1+1') end")
        assert sb.call_probe(g, {}) is None

    def test_python_builtins_swallowed(self, tmp_path) -> None:
        sb, g = _load(
            tmp_path,
            "function probe(p) "
            "return python.builtins.__import__('os').system('echo nope') end",
        )
        assert sb.call_probe(g, {}) is None

    def test_file_loaders_nil_in_config_env(self, tmp_path) -> None:
        sb, g = _load(
            tmp_path,
            "function probe(p) return {load ~= nil, loadfile ~= nil, dofile ~= nil} end",
        )
        assert sb.call_probe(g, {}) == [False, False, False]


class TestPerConfigIsolation:
    def test_globals_do_not_leak_between_configs(self, tmp_path) -> None:
        a = tmp_path / "a.lua"
        a.write_text("LEAKED = 'fromA'; function probe() return LEAKED end")
        b = tmp_path / "b.lua"
        b.write_text("function probe() return 'b' end")
        sb = Sandbox.create()
        sb.load_script(str(a))
        gb = sb.load_script(str(b))
        assert "LEAKED" not in dict(gb)
        assert gb["probe"]() == "b"

    def test_server_is_per_config(self, tmp_path) -> None:
        a = tmp_path / "a.lua"
        a.write_text("SERVER = {name='alpha'}")
        b = tmp_path / "b.lua"
        b.write_text("SERVER = {name='beta'}")
        sb = Sandbox.create()
        ga = sb.load_script(str(a))
        gb = sb.load_script(str(b))
        assert dict(ga["SERVER"])["name"] == "alpha"
        assert dict(gb["SERVER"])["name"] == "beta"

    def test_missing_probe_is_not_inherited(self, tmp_path) -> None:
        a = tmp_path / "a.lua"
        a.write_text("function probe(p) return 'A-stream' end")
        b = tmp_path / "b.lua"
        b.write_text("SERVER = {name='lonely'}")
        sb = Sandbox.create()
        ga = sb.load_script(str(a))
        gb = sb.load_script(str(b))
        assert sb.call_probe(ga, {}) == "A-stream"
        assert sb.call_probe(gb, {}) is None

    def test_module_tamper_isolated(self, tmp_path) -> None:
        a = tmp_path / "a.lua"
        a.write_text("function probe() end")
        b = tmp_path / "b.lua"
        b.write_text("function probe() end")
        sb = Sandbox.create()
        ga = sb.load_script(str(a))
        gb = sb.load_script(str(b))
        orig = gb["http"]["get"]
        ga["http"]["get"] = lambda u: "HACKED"
        assert gb["http"]["get"] is orig

    def test_stdlib_tamper_isolated(self, tmp_path) -> None:
        a = tmp_path / "a.lua"
        a.write_text("function probe() end")
        b = tmp_path / "b.lua"
        b.write_text("function probe() return string.rep('x', 2) end")
        sb = Sandbox.create()
        ga = sb.load_script(str(a))
        gb = sb.load_script(str(b))
        ga["string"]["rep"] = lambda *a: "HACKED"
        assert gb["probe"]() == "xx"

    def test_global_write_stays_in_own_env(self, tmp_path) -> None:
        from nyrx.sources.tv_movies.sandbox import _get_lua

        script = tmp_path / "g.lua"
        script.write_text("_G.OWNGLOBAL = 'mine'")
        sb = Sandbox.create()
        env = sb.load_script(str(script))
        assert env["OWNGLOBAL"] == "mine"
        assert "OWNGLOBAL" not in dict(_get_lua().globals())


class TestStdlibFlexibility:
    def test_utf8_and_coroutine_available(self, tmp_path) -> None:
        sb, g = _load(
            tmp_path,
            "function probe(p) return {utf8.len('héllo'), type(coroutine), _VERSION} end",
        )
        result = sb.call_probe(g, {})
        assert result[0] == 5
        assert result[1] == "table"
        assert isinstance(result[2], str)

    def test_future_module_registered_in_python_visible(self, tmp_path) -> None:
        from nyrx.sources.tv_movies.sandbox import _get_lua

        g = _get_lua().globals()
        demo = _get_lua().table()
        demo["magic"] = lambda: 42
        g["nyrx_test_future_module"] = demo
        try:
            sb, env = _load(
                tmp_path, "function probe(p) return nyrx_test_future_module.magic() end"
            )
            assert sb.call_probe(env, {}) == 42
        finally:
            g["nyrx_test_future_module"] = None


class TestPlaywrightUrlGuard:
    def test_file_scheme_rejected(self, tmp_path) -> None:
        sb, g = _load(
            tmp_path,
            "function probe(p) "
            "local ok = pcall(function() playwright.navigate('file:///etc/passwd') end) "
            "return ok end",
        )
        assert sb.call_probe(g, {}) is False

    def test_data_scheme_rejected(self, tmp_path) -> None:
        sb, g = _load(
            tmp_path,
            "function probe(p) "
            "local ok = pcall(function() playwright.navigate('data:text/html,x') end) "
            "return ok end",
        )
        assert sb.call_probe(g, {}) is False


class TestSecretsScoping:
    def test_own_prefix_read_write_clear(self, tmp_path, monkeypatch) -> None:
        from nyrx.sources.tv_movies import sandbox as sb_mod

        lua = sb_mod.LuaRuntime(unpack_returned_tuples=True)
        sb_mod._register_secrets(lua, tmp_path / "secrets.json")
        secrets = lua.globals()["secrets"]
        try:
            sb_mod._current_server.name = "mapple"
            assert secrets["load"]("mapple_apikey") is None
            secrets["store"]("mapple_apikey", "v1")
            assert secrets["load"]("mapple_apikey") == "v1"
            secrets["clear"]("mapple_apikey")
            assert secrets["load"]("mapple_apikey") is None
        finally:
            sb_mod._current_server.name = None

    def test_cross_config_key_denied(self, tmp_path, monkeypatch) -> None:
        from nyrx.sources.tv_movies import sandbox as sb_mod

        lua = sb_mod.LuaRuntime(unpack_returned_tuples=True)
        sb_mod._register_secrets(lua, tmp_path / "secrets.json")
        secrets = lua.globals()["secrets"]
        try:
            sb_mod._current_server.name = "mapple"
            secrets["store"]("mapple_apikey", "v1")
            assert secrets["load"]("mapple_apikey") == "v1"
        finally:
            sb_mod._current_server.name = None
        try:
            sb_mod._current_server.name = "other"
            assert secrets["load"]("mapple_apikey") is None
            secrets["clear"]("mapple_apikey")
        finally:
            sb_mod._current_server.name = None
        try:
            sb_mod._current_server.name = "mapple"
            assert secrets["load"]("mapple_apikey") == "v1"
        finally:
            sb_mod._current_server.name = None

    def test_no_server_denied(self, tmp_path, monkeypatch) -> None:
        from nyrx.sources.tv_movies import sandbox as sb_mod

        lua = sb_mod.LuaRuntime(unpack_returned_tuples=True)
        sb_mod._register_secrets(lua, tmp_path / "secrets.json")
        secrets = lua.globals()["secrets"]
        sb_mod._current_server.name = None
        assert secrets["load"]("mapple_apikey") is None
        secrets["store"]("mapple_apikey", "v1")
        assert secrets["load"]("mapple_apikey") is None
