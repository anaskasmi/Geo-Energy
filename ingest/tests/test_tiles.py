"""GEO-14 parcels.pmtiles tiling. The command-builder test is pure; the end-to-end test runs
the real tippecanoe binary (skipped if it isn't installed) and inspects the PMTiles v3 header
to confirm the zoom range was baked in."""

import shutil
import struct

import pytest

from pipeline import config, tiles
from pipeline.sources import SourceError

_PARCELS_GEOJSON = (
    '{"type":"FeatureCollection","features":['
    '{"type":"Feature","properties":{"id":1,"apn":"123-456-78","apn_norm":"12345678","acres":12.5},'
    '"geometry":{"type":"Polygon","coordinates":[[[-119.02,35.38],[-119.00,35.38],[-119.00,35.40],[-119.02,35.40],[-119.02,35.38]]]}},'
    '{"type":"Feature","properties":{"id":2,"apn":"123-456-79","apn_norm":"12345679","acres":7.25},'
    '"geometry":{"type":"Polygon","coordinates":[[[-119.00,35.38],[-118.98,35.38],[-118.98,35.40],[-119.00,35.40],[-119.00,35.38]]]}}'
    ']}'
)


def test_build_command_has_zoom_simplification_and_base_attrs():
    cmd = tiles.build_command("parcels.geojson", "parcels.pmtiles", tippecanoe="tippecanoe")
    assert cmd[0] == "tippecanoe"
    assert "-o" in cmd and "parcels.pmtiles" in cmd
    assert ["-l", config.PARCELS_TILE_LAYER] == [cmd[cmd.index("-l")], cmd[cmd.index("-l") + 1]]
    assert f"-Z{config.PARCELS_TILE_MINZOOM}" in cmd and f"-z{config.PARCELS_TILE_MAXZOOM}" in cmd
    assert "--simplification" in cmd and "--drop-densest-as-needed" in cmd
    # only the base attributes are kept (-y id -y apn -y acres) — and apn_norm is NOT
    y_values = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-y"]
    assert y_values == list(config.PARCELS_TILE_ATTRS) == ["id", "apn", "acres"]
    assert "apn_norm" not in y_values
    assert cmd[-1] == "parcels.geojson"


def test_build_parcels_pmtiles_missing_geojson_raises(tmp_path):
    with pytest.raises(SourceError):
        tiles.build_parcels_pmtiles(tmp_path / "nope.geojson", tmp_path / "out.pmtiles")


def test_build_parcels_pmtiles_missing_binary_raises(tmp_path):
    gj = tmp_path / "parcels.geojson"
    gj.write_text(_PARCELS_GEOJSON)
    with pytest.raises(SourceError):
        tiles.build_parcels_pmtiles(gj, tmp_path / "out.pmtiles", tippecanoe="tippecanoe-does-not-exist")


@pytest.mark.skipif(shutil.which("tippecanoe") is None, reason="tippecanoe not installed")
def test_build_parcels_pmtiles_produces_valid_archive(tmp_path):
    gj = tmp_path / "parcels.geojson"
    gj.write_text(_PARCELS_GEOJSON)
    out = tmp_path / "parcels.pmtiles"

    result = tiles.build_parcels_pmtiles(gj, out)

    assert result == out and out.exists() and out.stat().st_size > 0
    header = out.read_bytes()[:128]
    # PMTiles v3 header: 7-byte magic + version byte, then (offset 100/101) min/max zoom.
    assert header[:7] == b"PMTiles", "output is not a PMTiles archive"
    assert header[7] == 3, "expected PMTiles spec version 3"
    min_zoom = struct.unpack_from("<B", header, 100)[0]
    max_zoom = struct.unpack_from("<B", header, 101)[0]
    assert min_zoom == config.PARCELS_TILE_MINZOOM
    assert max_zoom == config.PARCELS_TILE_MAXZOOM
