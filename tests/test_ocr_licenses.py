import hashlib
import json
from pathlib import Path


def test_leptonica_notice_matches_the_frozen_runtime_version():
    """凍結版使用 Leptonica 1.87.0 時，散布檔須附 BSD 授權全文。"""
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "licenses" / "manifest.json").read_text(encoding="utf-8"))
    leptonica = next(item for item in manifest if item["name"] == "Leptonica")
    notice = (root / "licenses" / "leptonica-license.txt").read_text(encoding="utf-8")

    assert leptonica["version"] == "1.87.0"
    assert leptonica["license"] == "BSD-2-Clause"
    assert leptonica["sha256"] == "4d3065116f182e29760af0c901d5dbb2e1e16c42765dfc24e69b26805e2acb1e"
    assert hashlib.sha256((root / "licenses" / "leptonica-license.txt").read_bytes()).hexdigest() == leptonica["sha256"]
    assert "Redistribution and use in source and binary forms" in notice
