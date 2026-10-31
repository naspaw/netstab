import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from netstab import chart, gui  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("top,expected", [
    (95, [0, 20, 40, 60, 80, 100]),
    (30, [0, 5, 10, 15, 20, 25, 30]),
    (0.4, [0, 0.1, 0.2, 0.30000000000000004, 0.4]),
    (0, [0.0, 1.0]),
])
def test_nice_ticks(top, expected):
    assert chart.nice_ticks(top) == pytest.approx(expected)


def test_nice_ticks_always_reach_max():
    for v in (7, 61, 119, 299, 1234):
        t = chart.nice_ticks(v)
        assert t[0] == 0 and t[-1] >= v and len(t) <= 12


def test_decimate_keeps_spikes_and_order():
    xs = [i / 64 for i in range(64 * 60)]  # 60 s at 64 Hz
    ys = [80.0] * len(xs)
    ys[1000] = 400.0  # one spike
    ys[2000] = 10.0  # one dip
    pts = chart.decimate(xs, ys, 60, 300)
    assert len(pts) <= 602
    assert max(y for _, y in pts) == 400.0 and min(y for _, y in pts) == 10.0
    assert [x for x, _ in pts] == sorted(x for x, _ in pts)


def test_decimate_passes_small_series_through():
    assert chart.decimate([0, 1], [5, 6], 1, 100) == [(0, 5), (1, 6)]


def test_column_minmax_incremental_matches_one_shot():
    import random
    rnd = random.Random(1)
    xs = [i / 64 for i in range(64 * 120)]
    ys = [rnd.gauss(80, 8) for _ in xs]
    one = chart.ColumnMinMax(500, 120)
    one.add(xs, ys)
    inc = chart.ColumnMinMax(500, 120)
    for end in range(0, len(xs) + 1, 37):  # arrives in small batches, like live data
        inc.add(xs[:end], ys[:end])
    inc.add(xs, ys)
    assert inc.points() == one.points()


def test_single_instance_mutex(monkeypatch):
    monkeypatch.setattr(gui, "_MUTEX_NAME", "Local\\netstab-test-single-instance")
    assert gui.claim_single_instance() is True  # first claim wins
    assert gui.claim_single_instance() is False  # any later one sees it taken


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_chart_renders(app, theme):
    gui.C.use(theme)
    c = chart.LatencyChart(gui.C)
    c.resize(900, 320)
    img = QImage(c.size(), QImage.Format_ARGB32)

    c.reset(30)
    c.render(img)  # empty chart

    xs = [i / 64 for i in range(64 * 30)]
    ys = [70 + (i % 13) for i in range(len(xs))]
    c.set_data(xs, ys, [(k + 0.5, 76.0) for k in range(30)], [3.0, 3.1, 17.5])
    c.set_median(76.0)
    c.render(img)
    # The panel background is painted and the line colour appears somewhere.
    assert img.pixelColor(2, 2).name() == gui.C.PANEL
    line = gui.C.LINE.lower()
    assert any(img.pixelColor(x, y).name() == line
               for x in range(0, 900, 3) for y in range(0, 320, 3))
    gui.C.use("dark")
