"""
End-to-end test on the invented sample workbook (make_sample_data.py):
build it, load it, score it, and write the PDF. Needs no Bloomberg data.

Run:  .venv/bin/python -m pytest -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import features  # noqa: E402
import loader  # noqa: E402
import make_report  # noqa: E402
import make_sample_data  # noqa: E402
import scoring  # noqa: E402


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sample")
    path = tmp / "sample_workbook.xlsx"
    make_sample_data.build(path)
    mp = pytest.MonkeyPatch()
    mp.setattr(loader.config, "CACHE_DIR", tmp)        # keep the real cache untouched
    d = loader.load_data(force=True, path=path)
    feats, extras = features.build_features(d)
    result = scoring.score_all(feats, d["links"])
    yield d, feats, result
    mp.undo()


def test_sample_layout_checks_all_pass(sample):
    d, _, _ = sample
    summary = loader.data_summary(d)
    assert (summary["check"] != "CHECK").all(), summary[summary["check"] == "CHECK"]


def test_sample_scores(sample):
    d, feats, result = sample
    s = result["scores"]["score"]
    assert s.notna().sum() == len(d["universe"]) - 2            # the two delisted tickers are excluded
    assert s.between(1, 10).all()
    assert result["group_values"]["Linked companies"].notna().any()   # pass 2 ran
    assert (result["scores"]["peer_caveat"] != "").any()             # extreme values were flagged


def test_sample_report(sample):
    fitz = pytest.importorskip("fitz")
    d, feats, result = sample
    for filters in (None, {"core_only": True}):
        doc = fitz.open(stream=make_report.build_pdf(d, feats, result, filters), filetype="pdf")
        assert doc.page_count == 4
