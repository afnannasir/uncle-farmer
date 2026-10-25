"""jsonio: the stdlib-json-shaped wrapper over orjson. Text round trips, two-space indent, sorted
keys, int keys, UTF-8 output, the file helpers, the atomic save, and the one shape (int keys plus
sorting) that the wrapper cannot express because orjson forbids those two options together."""

import os

import pytest

from unclefarmer import jsonio


def test_dumps_returns_text_and_loads_round_trips():
    out = jsonio.dumps({"a": 1, "b": [2, 3], "c": None})
    assert isinstance(out, str)
    assert jsonio.loads(out) == {"a": 1, "b": [2, 3], "c": None}


def test_loads_accepts_bytes_too():
    assert jsonio.loads(b'{"x": 1}') == {"x": 1}


def test_indent_writes_two_spaces():
    assert jsonio.dumps({"a": {"b": 1}}, indent=2) == '{\n  "a": {\n    "b": 1\n  }\n}'
    # orjson only offers two spaces, so any truthy indent gives the same output
    assert jsonio.dumps({"a": 1}, indent=1) == '{\n  "a": 1\n}'
    # ... and no indent stays compact, one line
    assert jsonio.dumps({"a": {"b": 1}}) == '{"a":{"b":1}}'


def test_sort_keys_orders_objects():
    assert jsonio.dumps({"b": 1, "a": 2}, sort_keys=True) == '{"a":2,"b":1}'
    assert jsonio.dumps({"b": 1, "a": 2}) == '{"b":1,"a":2}'      # insertion order otherwise


def test_non_string_keys_are_stringified():
    assert jsonio.dumps({1: "x"}) == '{"1":"x"}'
    assert jsonio.loads(jsonio.dumps({2: "y"})) == {"2": "y"}


def test_non_string_keys_with_sort_keys_are_refused():
    # orjson forbids OPT_NON_STR_KEYS together with OPT_SORT_KEYS and jsonio chooses sorting, so
    # sorted int keys - which stdlib json used to accept - raise here instead.
    with pytest.raises(TypeError):
        jsonio.dumps({1: "x"}, sort_keys=True)


def test_utf8_is_kept_and_ensure_ascii_is_accepted():
    assert jsonio.dumps({"é": "ü"}) == '{"é":"ü"}'
    assert jsonio.dumps({"é": "ü"}, ensure_ascii=False) == '{"é":"ü"}'
    assert jsonio.dumps({"é": "ü"}, ensure_ascii=True) == '{"é":"ü"}'   # ignored: orjson is always UTF-8
    assert jsonio.loads(jsonio.dumps({"é": "ü"})) == {"é": "ü"}


def test_default_callback_is_passed_through():
    class Thing:
        pass

    assert jsonio.dumps({"c": Thing()}, default=lambda o: "X") == '{"c":"X"}'


def test_unsupported_type_raises_type_error():
    with pytest.raises(TypeError):
        jsonio.dumps({1, 2})


def test_load_and_dump_with_a_text_file(tmp_path):
    p = tmp_path / "x.json"
    with open(p, "w", encoding="utf-8") as f:
        jsonio.dump({"a": 1, "b": "é"}, f, indent=2)
    text = p.read_text(encoding="utf-8")
    assert '\n  "a": 1' in text and "\n  \"b\": \"é\"" in text
    with open(p, encoding="utf-8") as f:
        assert jsonio.load(f) == {"a": 1, "b": "é"}


def test_load_surfaces_a_broken_file_as_value_error(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with open(p, encoding="utf-8") as f:
        with pytest.raises(ValueError):
            jsonio.load(f)


def test_save_writes_the_new_contents_and_leaves_no_temp_file(tmp_path):
    p = tmp_path / "settings.json"
    jsonio.save(str(p), {"a": 1}, indent=2)
    jsonio.save(str(p), {"a": 2, "b": "ü"}, indent=2)
    assert p.read_text(encoding="utf-8") == '{\n  "a": 2,\n  "b": "ü"\n}'
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_uses_a_fresh_temporary_name_for_every_write(tmp_path, monkeypatch):
    # the old writer used one fixed "<name>.tmp", so two writers (the GUI and a run) could race it
    p = tmp_path / "plan.json"
    used = []
    real = os.replace

    def replace(src, dst):
        used.append(src)
        real(src, dst)

    monkeypatch.setattr(jsonio.os, "replace", replace)
    jsonio.save(str(p), {"n": 1})
    jsonio.save(str(p), {"n": 2})
    assert len(set(used)) == 2
    assert all(not os.path.exists(u) for u in used)


def test_a_failed_save_leaves_the_old_file_alone(tmp_path):
    p = tmp_path / "settings.json"
    jsonio.save(str(p), {"a": 1})
    before = p.read_bytes()
    with pytest.raises(TypeError):
        jsonio.save(str(p), {"a": {1, 2}})      # orjson cannot serialize a set
    assert p.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_failed_replace_keeps_the_old_file_and_cleans_the_temp_file(tmp_path, monkeypatch):
    p = tmp_path / "script.mwf"
    jsonio.save_text(str(p), "say héllo\n")
    assert p.read_text(encoding="utf-8") == "say héllo\n"

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(jsonio.os, "replace", boom)
    with pytest.raises(OSError):
        jsonio.save_text(str(p), "new\n")
    assert p.read_text(encoding="utf-8") == "say héllo\n"
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_creates_a_missing_folder(tmp_path):
    p = tmp_path / "profiles" / "Ann.json"
    jsonio.save(str(p), {"hp": 2})
    assert jsonio.loads(p.read_text(encoding="utf-8")) == {"hp": 2}


def test_plan_save_goes_through_the_atomic_writer(tmp_path, monkeypatch):
    from unclefarmer import paths
    from unclefarmer.engine import plan as plans

    monkeypatch.setattr(paths, "PLANS", str(tmp_path))
    plans.save(dict(plans.PLAN_DEFAULTS, name="Atomic"))
    assert jsonio.loads((tmp_path / "Atomic.json").read_text(encoding="utf-8"))["name"] == "Atomic"
    assert list(tmp_path.glob("*.tmp")) == []


def test_string_keys_stay_on_the_fast_path_and_int_keys_retry(monkeypatch):
    import orjson

    seen = []
    real = orjson.dumps

    def spy(obj, default=None, option=0):
        seen.append(option)
        return real(obj, default=default, option=option)

    monkeypatch.setattr(jsonio.orjson, "dumps", spy)
    assert jsonio.dumps({"a": 1}) == '{"a":1}'
    assert not seen[0] & orjson.OPT_NON_STR_KEYS      # no slowdown for the usual string keys
    assert jsonio.dumps({1: "x"}) == '{"1":"x"}'
    assert len(seen) == 3                            # the first attempt, then the retry
    assert not seen[1] & orjson.OPT_NON_STR_KEYS
    assert seen[2] & orjson.OPT_NON_STR_KEYS
