"""persistence.load_state / save_state 单元测试。"""
from taskline.persistence import load_state, save_state


def test_load_missing_file_returns_empty(tmp_path):
    assert load_state(str(tmp_path / "nope.json")) == []


def test_save_then_load_roundtrip(tmp_path):
    p = str(tmp_path / "state.json")
    data = {"version": 1, "nodes": [{"id": "a#0", "result": 1}]}
    save_state(p, data)
    assert load_state(p) == [{"id": "a#0", "result": 1}]


def test_save_leaves_no_tmp_file(tmp_path):
    save_state(str(tmp_path / "state.json"), {"version": 1, "nodes": []})
    assert (tmp_path / "state.json").exists()
    assert not (tmp_path / "state.json.tmp").exists()


def test_save_overwrites_existing(tmp_path):
    p = str(tmp_path / "state.json")
    save_state(p, {"version": 1, "nodes": [{"id": "a#0", "result": 1}]})
    save_state(p, {"version": 1,
                   "nodes": [{"id": "a#0", "result": 1},
                             {"id": "b#0", "result": 2}]})
    assert len(load_state(p)) == 2


def test_load_returns_nodes_list_only(tmp_path):
    p = str(tmp_path / "state.json")
    save_state(p, {"version": 1, "nodes": [{"id": "x#0", "result": "v"}]})
    loaded = load_state(p)
    assert isinstance(loaded, list)
    assert loaded[0]["id"] == "x#0"
