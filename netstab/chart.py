"""Live latency chart drawn directly with QPainter.

Replaces a plotting library (and the numpy + OpenGL it pulled in) for one
simple chart, which makes the portable exe much smaller and faster to start.
Draws every packet's ping, a 1-second average, lost packets and the median.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget


def nice_ticks(max_value: float, target: int = 6) -> list[float]:
    """Round tick values from 0 to at least max_value, about `target` of them."""
    if max_value <= 0:
        return [0.0, 1.0]
    raw = max_value / target
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    n = math.ceil(max_value / step - 1e-9)
    return [i * step for i in range(n + 1)]


class ColumnMinMax:
    """Lowest and highest point per pixel column, built incrementally.

    Thousands of packets draw fast without hiding any spike, and each redraw
    only processes the packets added since the last one. Assumes x grows
    (packets arrive in time order), which the live test guarantees.
    """

    def __init__(self, cols: int, x_max: float):
        self.cols, self.x_max = cols, x_max
        self.lo: list[tuple[float, float] | None] = [None] * (cols + 1)
        self.hi: list[tuple[float, float] | None] = [None] * (cols + 1)
        self.done = 0
        self.dirty_from = 0  # first column changed since the caller last looked

    def add(self, xs: list[float], ys: list[float]) -> None:
        cols, x_max, lo, hi = self.cols, self.x_max, self.lo, self.hi
        first = None
        for i in range(self.done, len(xs)):
            x, y = xs[i], ys[i]
            c = min(cols, max(0, int(x / x_max * cols)))
            if first is None:
                first = c
            if lo[c] is None:
                lo[c] = hi[c] = (x, y)
            elif y < lo[c][1]:
                lo[c] = (x, y)
            elif y > hi[c][1]:
                hi[c] = (x, y)
        if first is not None:
            self.dirty_from = min(self.dirty_from, first)
        self.done = len(xs)

    def column(self, c: int) -> list[tuple[float, float]]:
        lo, hi = self.lo[c], self.hi[c]
        if lo is None:
            return []
        return [lo] if lo is hi else sorted((lo, hi))

    def points(self) -> list[tuple[float, float]]:
        return [pt for c in range(self.cols + 1) for pt in self.column(c)]


def decimate(xs: list[float], ys: list[float], x_max: float, max_cols: int):
    """All points if few, else the min/max of each pixel column in time order."""
    if len(xs) <= max_cols * 2:
        return list(zip(xs, ys))
    cm = ColumnMinMax(max_cols, x_max)
    cm.add(xs, ys)
    return cm.points()


class LatencyChart(QWidget):
    def __init__(self, palette, parent=None):
        """`palette` is read at paint time (attributes PANEL, BORDER, MUTED,
        LINE, ACCENT, BAD), so a theme change only needs update()."""
        super().__init__(parent)
        self.c = palette
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.reset(60)

    # -- data ------------------------------------------------------------------
    def reset(self, x_max: float) -> None:
        self.x_max = max(1.0, float(x_max))
        self.xs: list[float] = []
        self.ys: list[float] = []
        self.avg: list[tuple[float, float]] = []
        self.loss_x: list[float] = []
        self.median: float | None = None
        self._cols: ColumnMinMax | None = None
        self._mapped: list[list[QPointF]] = []
        self._map_key = None
        self.update()

    def set_data(self, xs, ys, avg, loss_x) -> None:
        self.xs, self.ys, self.avg, self.loss_x = xs, ys, avg, loss_x
        self.update()

    def set_median(self, value: float | None) -> None:
        self.median = value
        self.update()

    # -- drawing ---------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        c = self.c
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(c.PANEL))
        fm = p.fontMetrics()

        top_val = max(self.ys, default=0.0)
        top_val = max([top_val] + [y for _, y in self.avg] + [self.median or 0])
        y_ticks = nice_ticks(top_val * 1.1 if top_val else 100)
        y_max = y_ticks[-1]
        x_ticks = nice_ticks(self.x_max, 8)
        x_ticks = [t for t in x_ticks if t <= self.x_max + 1e-9]

        def label(v: float) -> str:
            return f"{v:.0f}" if float(v).is_integer() else f"{v:g}"

        left = max(fm.horizontalAdvance(label(t)) for t in y_ticks) + 14
        bottom_pad = fm.height() * 2 + 10
        plot = QRectF(left, fm.height() + 8, self.width() - left - 12,
                      self.height() - bottom_pad - fm.height() - 8)
        if plot.width() < 20 or plot.height() < 20:
            p.end()
            return

        def X(x: float) -> float:
            return plot.left() + x / self.x_max * plot.width()

        def Y(y: float) -> float:
            return plot.bottom() - y / y_max * plot.height()

        muted = QColor(c.MUTED)
        grid = QColor(c.BORDER)
        p.setPen(muted)
        p.drawText(QRectF(0, 0, left - 6, fm.height() + 4),
                   Qt.AlignRight | Qt.AlignVCenter, "ms")
        for t in y_ticks:
            y = Y(t)
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(muted)
            p.drawText(QRectF(0, y - fm.height() / 2, left - 8, fm.height()),
                       Qt.AlignRight | Qt.AlignVCenter, label(t))
        for t in x_ticks:
            x = X(t)
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(x, plot.bottom()), QPointF(x, plot.bottom() + 4))
            p.setPen(muted)
            p.drawText(QRectF(x - 30, plot.bottom() + 6, 60, fm.height()),
                       Qt.AlignHCenter | Qt.AlignTop, label(t))
        p.drawText(QRectF(plot.left(), plot.bottom() + fm.height() + 8, plot.width(),
                          fm.height()), Qt.AlignHCenter | Qt.AlignTop, "seconds")

        p.setClipRect(plot.adjusted(-1, -8, 1, 1))
        if self.ys:
            pen = QPen(QColor(c.LINE), 1)
            pen.setCosmetic(True)
            p.setPen(pen)
            # A 1 px line looks the same without antialiasing and draws ~6x faster.
            p.setRenderHint(QPainter.Antialiasing, False)
            p.drawPolyline(self._series_polygon(plot, y_max, X, Y))
            p.setRenderHint(QPainter.Antialiasing, True)
        if len(self.avg) > 1:
            path = QPainterPath(QPointF(X(self.avg[0][0]), Y(self.avg[0][1])))
            for x, y in self.avg[1:]:
                path.lineTo(X(x), Y(y))
            p.setPen(QPen(QColor(c.ACCENT), 2))
            p.drawPath(path)
        if self.median is not None:
            p.setPen(QPen(muted, 1, Qt.DashLine))
            p.drawLine(QPointF(plot.left(), Y(self.median)),
                       QPointF(plot.right(), Y(self.median)))
        if self.loss_x:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c.BAD))
            yt = plot.top() + 1
            for x in self.loss_x:
                xp = X(x)
                p.drawPolygon(QPolygonF([QPointF(xp - 4, yt - 6), QPointF(xp + 4, yt - 6),
                                         QPointF(xp, yt + 1)]))
        p.setClipping(False)
        p.setPen(QPen(grid, 1))
        p.drawLine(QPointF(plot.left(), plot.top()), QPointF(plot.left(), plot.bottom()))
        p.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
        p.end()

    def _series_polygon(self, plot: QRectF, y_max: float, X, Y) -> QPolygonF:
        """Per-packet line as screen points, reusing everything already mapped:
        only columns touched by new packets are recomputed each frame."""
        cols = max(1, int(plot.width()))
        if len(self.xs) <= cols * 2:  # few packets: draw them all
            return QPolygonF([QPointF(X(x), Y(y)) for x, y in zip(self.xs, self.ys)])
        if (self._cols is None or self._cols.cols != cols
                or self._cols.done > len(self.xs)):
            self._cols = ColumnMinMax(cols, self.x_max)
            self._map_key = None
        self._cols.add(self.xs, self.ys)
        key = (plot.left(), plot.top(), plot.width(), plot.height(), y_max)
        if key != self._map_key:  # resized or rescaled: map every column again
            self._map_key = key
            self._mapped = [[] for _ in range(cols + 1)]
            start = 0
        else:
            start = self._cols.dirty_from
        for col in range(start, cols + 1):
            self._mapped[col] = [QPointF(X(x), Y(y)) for x, y in self._cols.column(col)]
        self._cols.dirty_from = cols + 1
        return QPolygonF([pt for col in self._mapped for pt in col])
