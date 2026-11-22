import json

from slipstream import cleancache


def _write(path, obj):
    path.write_text(json.dumps(obj))


def test_deletes_only_built_races_and_keeps_season_files(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _write(cache / "built.json", [{"date": "x", "session_key": 11420, "speed": 300}])
    _write(cache / "notbuilt.json", [{"date": "x", "session_key": 99999, "speed": 300}])
    _write(cache / "calendar.json", [{"session_key": 11420, "session_name": "Race", "session_type": "Race"}])
    index = tmp_path / "index.json"
    _write(index, {"races": [{"slug": "2026-21-las-vegas", "session_key": 11420}]})

    n, freed = cleancache.clean(cache_dir=cache, index_path=index, dry_run=True)
    assert n == 1 and freed > 0
    assert (cache / "built.json").exists()  # dry run deletes nothing

    n, _ = cleancache.clean(cache_dir=cache, index_path=index)
    assert n == 1
    assert not (cache / "built.json").exists()
    assert (cache / "notbuilt.json").exists()
    assert (cache / "calendar.json").exists()


def test_all_deletes_everything(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _write(cache / "a.json", [{"session_key": 1}])
    _write(cache / "b.json", [{"session_name": "Race", "session_type": "Race"}])
    n, _ = cleancache.clean(cache_dir=cache, index_path=tmp_path / "missing.json", everything=True)
    assert n == 2 and not list(cache.glob("*.json"))


def test_missing_index_deletes_nothing(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _write(cache / "a.json", [{"session_key": 1}])
    n, _ = cleancache.clean(cache_dir=cache, index_path=tmp_path / "missing.json")
    assert n == 0
