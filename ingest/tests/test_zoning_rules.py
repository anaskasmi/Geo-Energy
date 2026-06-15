"""GEO-5 zoning_rules: curated reference loads cleanly, covers all Kern districts, and the
effective-rows builder fills gaps with a flagged conservative default."""

import csv

import pytest

from pipeline import config, zoning_rules

# The 27 base district codes the normalizer produces from the Kern County zoning layer.
KERN_BASE_CODES = {
    "A", "A-1", "C-1", "C-2", "CH", "CO", "DI", "E", "FPP", "GI", "KRC", "M-1", "M-2", "M-3",
    "MP", "MS", "NR", "OS", "OTHER", "P", "PL", "R-1", "R-2", "R-3", "RF", "SP", "WE",
}


def test_reference_loads_and_is_internally_valid():
    rules, names = zoning_rules.load_rules()
    # Every (code, use) permission is a valid token and every code has a human name.
    for (code, use), rule in rules.items():
        assert use in config.ZONING_USE_CASES
        assert rule["permission"] in config.ZONING_PERMISSIONS
        assert names.get(code)


def test_reference_covers_every_kern_base_code_and_use():
    rules, names = zoning_rules.load_rules()
    rows, missing = zoning_rules.effective_rows(KERN_BASE_CODES, rules, names)
    assert missing == []  # no gaps for any real Kern district
    assert len(rows) == len(KERN_BASE_CODES) * len(config.ZONING_USE_CASES)


def test_reference_matches_known_ordinance_facts():
    rules, _ = zoning_rules.load_rules()
    # Spot-checks traceable to Title 19 (see zoning_rules.README.md / basis column).
    assert rules[("M-1", "data_center")]["permission"] == "by_right"   # industrial similar-use
    assert rules[("WE", "wind")]["permission"] == "by_right"           # 19.64.020.B
    assert rules[("E", "solar")]["permission"] == "prohibited"         # residential estate
    assert rules[("OS", "data_center")]["permission"] == "prohibited"  # 19.44.040
    assert rules[("A", "solar")]["permission"] == "conditional"        # 19.12.030.G CUP


def test_effective_rows_fills_unknown_code_with_flagged_default():
    rules, names = zoning_rules.load_rules()
    rows, missing = zoning_rules.effective_rows(["A", "ZZZ"], rules, names)
    by_key = {(r["zone_code"], r["use_case"]): r for r in rows}
    # The unmapped code gets the conservative default for every use, flagged in basis.
    for use in config.ZONING_USE_CASES:
        assert ("ZZZ", use) in missing
        assert by_key[("ZZZ", use)]["permission"] == config.ZONING_DEFAULT_PERMISSION
        assert "DEFAULT" in by_key[("ZZZ", use)]["basis"]
    # Only the unmapped code populates `missing` — the curated code "A" never leaks in.
    assert all(c == "ZZZ" for c, _ in missing)
    assert ("A", "solar") not in missing


def _write_rules(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=zoning_rules.FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def _row(**kw):
    base = {"zone_code": "A", "zone_name": "Ag", "use_case": "solar",
            "permission": "conditional", "basis": "x"}
    base.update(kw)
    return base


def test_load_rejects_bad_permission(tmp_path):
    bad = _write_rules(tmp_path / "bad.csv", [_row(permission="maybe")])
    with pytest.raises(ValueError):
        zoning_rules.load_rules(bad)


def test_load_rejects_unknown_use_case(tmp_path):
    bad = _write_rules(tmp_path / "bad.csv", [_row(use_case="parking")])
    with pytest.raises(ValueError):
        zoning_rules.load_rules(bad)


def test_load_rejects_duplicate_code_use(tmp_path):
    bad = _write_rules(tmp_path / "bad.csv", [_row(), _row()])  # same (A, solar) twice
    with pytest.raises(ValueError):
        zoning_rules.load_rules(bad)


def test_load_rejects_empty_zone_code(tmp_path):
    bad = _write_rules(tmp_path / "bad.csv", [_row(zone_code="")])
    with pytest.raises(ValueError):
        zoning_rules.load_rules(bad)


def test_write_csv_roundtrip(tmp_path):
    rules, names = zoning_rules.load_rules()
    rows, _ = zoning_rules.effective_rows(["A", "M-1"], rules, names)
    out = tmp_path / "zoning_rules.csv"
    zoning_rules.write_csv(rows, out)
    back = list(csv.DictReader(open(out)))
    assert len(back) == 2 * len(config.ZONING_USE_CASES)
    assert set(back[0].keys()) == set(zoning_rules.FIELDNAMES)
