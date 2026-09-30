"""Regression coverage for public fixes integrated into the local Agent branch."""

import json

import pytest


@pytest.mark.parametrize("cache", ["deepdive", "backtest"])
def test_first_cache_write_creates_directory_and_remains_replaceable(tmp_path, cache):
    import server

    path = tmp_path / "new-user" / cache / "result.json"
    assert not path.parent.exists()
    server._atomic_write(str(path), {"generation": 1, "description": "首次缓存"})
    assert json.loads(path.read_text()) == {"generation": 1, "description": "首次缓存"}
    server._atomic_write(str(path), {"generation": 2})
    assert json.loads(path.read_text()) == {"generation": 2}
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("field", ["MIMO_QUICK_MODEL", "MIMO_MODEL", "MIMO_BASE_URL"])
def test_explicit_model_config_wins_when_key_comes_from_file(tmp_path, monkeypatch, field):
    import duanxian.config as config

    for key in ("MIMO_API_KEY", "MIMO_BASE_URL", "MIMO_MODEL", "MIMO_QUICK_MODEL"):
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / "mimo.env"
    env_file.write_text(f"MIMO_API_KEY=test-only-key\n{field}=file-value\n")
    monkeypatch.setattr(config, "_MIMO_ENV", env_file)
    monkeypatch.setattr(config, "_CREDS", None)
    monkeypatch.setenv(field, "explicit-value")
    config._ensure_mimo_loaded()
    assert config._CREDS["MIMO_API_KEY"] == "test-only-key"
    assert config._CREDS[field] == "explicit-value"
