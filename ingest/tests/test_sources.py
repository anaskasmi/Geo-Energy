"""sources: local override resolution + HTTP download with retry (offline via MockTransport)."""

from pathlib import Path

import httpx
import pytest

from pipeline import sources
from pipeline.sources import SourceError


def test_local_override_unset_returns_none(monkeypatch):
    monkeypatch.delenv("GEO_X_SOURCE", raising=False)
    assert sources.local_override("GEO_X_SOURCE") is None


def test_local_override_blank_returns_none(monkeypatch):
    monkeypatch.setenv("GEO_X_SOURCE", "   ")
    assert sources.local_override("GEO_X_SOURCE") is None


def test_local_override_present(monkeypatch, tmp_path):
    f = tmp_path / "src.geojson"
    f.write_text("{}")
    monkeypatch.setenv("GEO_X_SOURCE", str(f))
    assert sources.local_override("GEO_X_SOURCE") == f


def test_local_override_missing_path_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("GEO_X_SOURCE", str(tmp_path / "nope.geojson"))
    with pytest.raises(SourceError):
        sources.local_override("GEO_X_SOURCE")


def test_http_download_writes_body_atomically(tmp_path):
    def handler(request):
        return httpx.Response(200, content=b"hello-bytes")

    dest = tmp_path / "out.bin"
    out = sources.http_download(
        "https://example/x", dest, transport=httpx.MockTransport(handler), retries=1
    )
    assert out == dest
    assert dest.read_bytes() == b"hello-bytes"
    assert not (tmp_path / "out.bin.part").exists()  # temp cleaned up


def test_http_download_retries_then_succeeds(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, content=b"ok")

    dest = tmp_path / "out.bin"
    sources.http_download(
        "https://example/x", dest, transport=httpx.MockTransport(handler),
        retries=3, backoff=0.0,
    )
    assert calls["n"] == 3
    assert dest.read_bytes() == b"ok"


def test_http_download_all_attempts_fail_raises(tmp_path):
    def handler(request):
        return httpx.Response(500)

    with pytest.raises(SourceError):
        sources.http_download(
            "https://example/x", tmp_path / "out.bin",
            transport=httpx.MockTransport(handler), retries=2, backoff=0.0,
        )
    assert not (tmp_path / "out.bin").exists()
    assert not (tmp_path / "out.bin.part").exists()


def test_http_download_cleans_up_part_after_post_write_failure(tmp_path, monkeypatch):
    """A failure *after* the .part file is written (here: os.replace) must not leave it behind."""

    def handler(request):
        return httpx.Response(200, content=b"partial-bytes")  # body writes the .part file

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(sources.os, "replace", boom)
    dest = tmp_path / "out.bin"
    with pytest.raises(SourceError):
        sources.http_download(
            "https://example/x", dest,
            transport=httpx.MockTransport(handler), retries=2, backoff=0.0,
        )
    assert not dest.exists()
    assert not (tmp_path / "out.bin.part").exists()  # the half-written staging file is gone
