import json
import time
from pathlib import Path
import pytest
import cache


@pytest.fixture(autouse=True)
def clean_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_FILE", tmp_path / "cache.json")


def test_load_returns_none_when_no_file():
    assert cache.load_cache() is None


def test_save_and_load_returns_payload():
    cache.save_cache({"tasks": [1, 2, 3]})
    result = cache.load_cache()
    assert result == {"tasks": [1, 2, 3]}


def test_load_returns_none_when_expired(monkeypatch):
    cache.save_cache({"tasks": []})
    monkeypatch.setattr(cache, "CACHE_TTL", -1)
    assert cache.load_cache() is None


def test_clear_removes_file():
    cache.save_cache({"tasks": []})
    cache.clear_cache()
    assert cache.load_cache() is None


def test_load_returns_none_when_corrupt():
    cache.CACHE_FILE.write_text("not valid json")
    assert cache.load_cache() is None
