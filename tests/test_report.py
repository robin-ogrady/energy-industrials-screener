"""
Tests for make_report.py: helpers on made-up data, and a smoke test that
builds the real PDF from the cache (skipped if the cache is not built yet).

Run:  .venv/bin/python -m pytest -q
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import loader  # noqa: E402
import make_report as mr  # noqa: E402


def test_nice_name_keeps_acronyms():
    assert mr.nice_name("EOG RESOURCES INC") == "EOG Resources Inc"
    assert mr.nice_name("GE VERNOVA INC") == "GE Vernova Inc"
    assert mr.nice_name("SLB LTD") == "SLB Ltd"


def test_filter_table():
    t = pd.DataFrame({"chain": ["A", None], "sector": ["Energy", "Industrials"],
                      "industry": ["Oil", "Machinery"], "is_core": [True, False]}, index=["x", "y"])
    assert list(mr.filter_table(t, {"core_only": True}).index) == ["x"]
    assert list(mr.filter_table(t, {"sector": ["Industrials"]}).index) == ["y"]
    assert len(mr.filter_table(t, None)) == 2


def test_caveat_badge():
    assert mr.caveat_badge("") == ""
    assert mr.caveat_badge("extreme value: P/E 500x") == "extreme ratio"


@pytest.mark.skipif(not loader.config.data_path().exists(), reason="needs your own Bloomberg workbook (see README)")
def test_report_builds_four_letter_pages_with_readable_text():
    fitz = pytest.importorskip("fitz")
    d, feats, extras, result = mr.compute()
    for filters in (None, {"core_only": True}):
        pdf = mr.build_pdf(d, feats, result, filters)
        doc = fitz.open(stream=pdf, filetype="pdf")
        assert doc.page_count == 4
        assert round(doc[0].rect.width) == 612 and round(doc[0].rect.height) == 792   # US Letter
        sizes = [s["size"] for p in doc for b in p.get_text("dict")["blocks"]
                 for l in b.get("lines", []) for s in l["spans"]]
        assert min(sizes) >= 8
        assert "Not validated. A screening tool, not a trading signal." in doc[0].get_text()
