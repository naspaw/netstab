"""PySide6 desktop UI."""

from __future__ import annotations

import ctypes
import json
import math
import statistics
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication, QBoxLayout, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLayout, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QRadioButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from . import PREVIEW, __version__, advice, compare, metered, profiles, servers, settings, splash
from .chart import LatencyChart
from .detailed import (LOAD_MODES, RAMP_SECONDS, DetailedOptions, DetailedResult,
                       DetailedTest, estimate_mb)
from .engine import LoadTest
from .stats import Summary, summarize

THEMES = {
    "dark": dict(
        BG="#0f1115", PANEL="#171a21", BORDER="#262b36", TEXT="#e6e8ee",
        MUTED="#8a92a6", INPUT="#222733", HOVER="#1e222b", SEL="#2a2410",
        CARD="#1c2029", DANGER_BG="#3a1d1f", DISABLED="#555c6b", ACCENT="#f5a524",
        ACCENT_HOVER="#ffb93d", ACCENT_DIM="#4a3d1e", ON_ACCENT="#111111",
        GOOD="#3ccf6e", OK="#a3d65c", WARN="#f5c542", ORANGE="#f0883e",
        BAD="#ef5350", LINE="#5b8def",
    ),
    "light": dict(
        BG="#f3f4f7", PANEL="#ffffff", BORDER="#dde1e8", TEXT="#1b1f27",
        MUTED="#667085", INPUT="#eef0f4", HOVER="#f0f2f6", SEL="#fff3d6",
        CARD="#f7f8fa", DANGER_BG="#fdecea", DISABLED="#a9afbb", ACCENT="#e08e00",
        ACCENT_HOVER="#f59f0b", ACCENT_DIM="#f3dcae", ON_ACCENT="#ffffff",
        GOOD="#1e9e4f", OK="#5f9a12", WARN="#b97f00", ORANGE="#d9661f",
        BAD="#d93025", LINE="#2f6fde",
    ),
}
THEME_CHOICES = [("system", "System"), ("dark", "Dark"), ("light", "Light")]


class _Palette:
    """Current colours; swapped in place when the theme changes."""

    def use(self, name: str) -> None:
        self.name = name
        self.__dict__.update(THEMES[name])


C = _Palette()
C.use("dark")


def grade_color(g: str) -> str:
    return {"A": C.GOOD, "B": C.OK, "C": C.WARN, "D": C.ORANGE, "F": C.BAD}[g]


def level_color(level: str) -> str:
    return {advice.GOOD: C.GOOD, advice.OK: C.WARN, advice.BAD: C.BAD}[level]


def make_style() -> str:
    return f"""
* {{ font-family: 'Segoe UI'; font-size: 10pt; color: {C.TEXT}; }}
QMainWindow, QWidget#root {{ background: {C.BG}; }}
QFrame#panel {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 10px; }}
QLabel#h {{ font-size: 9pt; font-weight: 600; color: {C.MUTED}; letter-spacing: 1px; }}
QLabel#muted {{ color: {C.MUTED}; }}
QLabel#title {{ font-size: 22pt; font-weight: 700; }}
QLabel#badge {{ font-size: 7.5pt; font-weight: 700; color: {C.WARN}; border: 1px solid {C.WARN}; border-radius: 4px; padding: 1px 5px; margin-left: 6px; }}
QLabel#status {{ font-size: 11pt; font-weight: 600; }}
QLabel#tileValue {{ font-size: 20pt; font-weight: 700; }}
QLabel#verdict {{ font-size: 13pt; font-weight: 700; }}
QLabel#aspectHead {{ font-size: 10.5pt; font-weight: 700; }}
QLabel#warn {{ color: {C.WARN}; }}
QFrame#aspect {{ background: {C.CARD}; border: 1px solid {C.BORDER}; border-radius: 8px; }}
QDialog {{ background: {C.PANEL}; }}
QCheckBox {{ spacing: 8px; }}
QPushButton#link {{ background: transparent; border: none; color: {C.ACCENT}; padding: 2px 0; }}
QPushButton#link:hover {{ text-decoration: underline; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ padding: 8px 10px; border-radius: 6px; margin: 1px 0; }}
QListWidget::item:selected {{ background: {C.SEL}; color: {C.ACCENT}; border: 1px solid {C.ACCENT}; }}
QListWidget::item:hover:!selected {{ background: {C.HOVER}; }}
QPushButton {{ background: {C.INPUT}; border: 1px solid {C.BORDER}; border-radius: 6px; padding: 7px 12px; }}
QPushButton:hover {{ border-color: {C.MUTED}; }}
QPushButton:checked {{ background: {C.SEL}; border-color: {C.ACCENT}; color: {C.ACCENT}; }}
QPushButton:disabled {{ color: #555c6b; }}
QPushButton#primary {{ background: {C.ACCENT}; color: {C.ON_ACCENT}; font-weight: 700; font-size: 11pt; border: none; padding: 11px; }}
QPushButton#primary:hover {{ background: {C.ACCENT_HOVER}; }}
QPushButton#primary:disabled {{ background: {C.ACCENT_DIM}; color: {C.MUTED}; }}
QPushButton#game {{ text-align: left; padding: 8px 10px; background: transparent; border: 1px solid transparent; }}
QPushButton#game:hover:!checked {{ background: {C.HOVER}; }}
QPushButton#game:checked {{ background: {C.SEL}; color: {C.ACCENT}; border-color: {C.ACCENT}; }}
QPushButton#game:disabled {{ color: {C.DISABLED}; }}
QPushButton#danger {{ background: {C.DANGER_BG}; border-color: {C.BAD}; color: {C.BAD}; }}
QRadioButton {{ spacing: 8px; padding: 3px 0; }}
QSpinBox, QComboBox {{ background: {C.INPUT}; border: 1px solid {C.BORDER}; border-radius: 6px; padding: 5px 8px; }}
QComboBox QAbstractItemView {{ background: {C.PANEL}; selection-background-color: {C.SEL}; }}
QProgressBar {{ background: {C.INPUT}; border: none; border-radius: 4px; height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {C.ACCENT}; border-radius: 4px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {C.MUTED}; padding: 8px 16px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {C.TEXT}; border-bottom-color: {C.ACCENT}; }}
QTableWidget {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 8px; gridline-color: {C.BORDER}; }}
QHeaderView {{ background: {C.PANEL}; }}
QHeaderView::section {{ background: {C.PANEL}; color: {C.MUTED}; border: none; border-bottom: 1px solid {C.BORDER}; padding: 6px; }}
QScrollArea {{ background: transparent; border: none; }}
QWidget#page {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {C.BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:horizontal {{ background: {C.BORDER}; border-radius: 5px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: {C.MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QTableCornerButton::section {{ background: {C.PANEL}; border: none; }}
"""


DEFAULT_SPEEDS = (100.0, 30.0)  # Mbps guess for data estimates before a measurement


def _fmt(v: float | None, unit: str = " ms", nd: int = 0) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{v:.{nd}f}{unit}"


def _ping_color(ms: float | None) -> str:
    if ms is None:
        return C.MUTED
    return C.GOOD if ms < 50 else C.OK if ms < 80 else C.WARN if ms < 120 else C.BAD


class Bridge(QObject):
    """Carries results from worker threads to the GUI thread."""
    scan_progress = Signal(int, int)
    scan_done = Signal(object, object)  # servers list, error str
    cost_ready = Signal(object)
    detail_progress = Signal(str, float, str)
    detail_done = Signal(object)


class Tile(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("panel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        t = QLabel(title.upper())
        t.setObjectName("h")
        self.value = QLabel("-")
        self.value.setObjectName("tileValue")
        self.sub = QLabel("")
        self.sub.setObjectName("muted")
        lay.addWidget(t)
        lay.addWidget(self.value)
        lay.addWidget(self.sub)

    def set(self, value: str, sub: str = "", color: str | None = None) -> None:
        self.value.setText(value)
        # No colour = follow the theme's text colour.
        self.value.setStyleSheet(f"color: {color};" if color else "")
        self.sub.setText(sub)


class VerdictPanel(QFrame):
    """'What to expect in game': a headline, one card per aspect, and tips."""

    def __init__(self, action: QPushButton):
        super().__init__()
        self.setObjectName("panel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        row = QHBoxLayout()
        self.title = _heading("What to expect in game")
        self.title.setWordWrap(True)  # so a narrow window doesn't cut the button off
        row.addWidget(self.title, 1)
        row.addWidget(action, 0, Qt.AlignTop)
        lay.addLayout(row)
        self.head = QLabel("Results appear here when the test finishes.")
        self.head.setObjectName("muted")
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        self.cards = ResponsiveRow(min_item_width=190, spacing=10)
        lay.addWidget(self.cards)
        self.tips = QLabel()
        self.tips.setWordWrap(True)
        self.tips.setTextFormat(Qt.RichText)
        self.tips.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.tips.setVisible(False)
        lay.addWidget(self.tips)

    def _clear_cards(self) -> None:
        self.cards.clear()

    def _restyle(self, w: QWidget, name: str, color: str = "") -> None:
        # Object-name selectors only re-apply after a re-polish.
        w.setObjectName(name)
        w.setStyleSheet(f"color: {color};" if color else "")
        w.style().unpolish(w)
        w.style().polish(w)

    def message(self, text: str, game: str = "") -> None:
        if game:
            self.title.setText(f"WHAT TO EXPECT IN {game.upper()}")
        self._clear_cards()
        self._restyle(self.head, "muted")
        self.head.setText(text)
        self.tips.setVisible(False)

    def show_verdict(self, v: advice.Verdict) -> None:
        self._clear_cards()
        self._restyle(self.head, "verdict", level_color(v.level))
        self.head.setText(v.headline)
        for a in v.aspects:
            card = QFrame()
            card.setObjectName("aspect")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(10, 8, 10, 8)
            cl.setSpacing(3)
            name = QLabel(f"<span style='color:{level_color(a.level)}'>●</span> "
                          f"{a.name.upper()}")
            name.setObjectName("h")
            head = QLabel(a.headline)
            head.setObjectName("aspectHead")
            head.setStyleSheet(f"color: {level_color(a.level)};")
            body = QLabel(a.comment)
            body.setObjectName("muted")
            body.setWordWrap(True)
            body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            # Wrapped labels under-report their height in a row of cards.
            body.setMinimumHeight(body.fontMetrics().lineSpacing() * 4 + 4)
            cl.addWidget(name)
            cl.addWidget(head)
            cl.addWidget(body, 1)
            self.cards.add(card)
        if v.tips:
            self.tips.setText(f"<b style='color:{C.ACCENT}'>TIPS</b><br>"
                              + "<br>".join(f"→ {t}" for t in v.tips))
            self.tips.setVisible(True)
        else:
            self.tips.setVisible(False)


class DetailOptionsDialog(QDialog):
    """Lets the user pick which detailed checks to run, with a data estimate."""

    def __init__(self, parent, server_name: str, cost: metered.ConnectionCost,
                 speeds: tuple[float, float], measured: bool, saved: dict):
        super().__init__(parent)
        self.setWindowTitle("Detailed network test")
        self.setMinimumWidth(500)
        self.cost, self.speeds, self.measured = cost, speeds, measured
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        t = QLabel(f"Detailed test for {server_name}")
        t.setObjectName("status")
        lay.addWidget(t)

        if cost.cost_type == "Unknown":
            conn = QLabel("Couldn't tell whether this connection is metered.")
        else:
            conn = QLabel(("⚠ " if cost.metered else "") + cost.description)
        conn.setObjectName("warn" if cost.metered else "muted")
        conn.setWordWrap(True)
        lay.addWidget(conn)

        lay.addWidget(_heading("Route & per-hop latency"))
        self.route = QComboBox()
        for secs, label in ((0, "Off"), (30, "30 s (recommended)"),
                            (60, "60 s - more precise")):
            self.route.addItem(label, secs)
        self.route.setCurrentIndex(max(0, self.route.findData(saved.get("route_seconds", 30))))
        lay.addWidget(self.route)

        self.mtu = QCheckBox("Find path MTU (largest packet that fits, a few KB)")
        self.mtu.setChecked(saved.get("mtu", True))
        lay.addWidget(self.mtu)

        lay.addWidget(_heading("Download / upload load test"))
        hint = QLabel("Fills your line to see how ping behaves when it's busy "
                      "(bufferbloat) and measures your speed. Uses data.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.load_group = QButtonGroup(self)
        # On a metered line nothing that burns data is pre-selected.
        default_load = "off" if cost.metered else saved.get("load", "standard")
        for i, (key, mode) in enumerate(LOAD_MODES.items()):
            rb = QRadioButton(mode.label)
            rb.setProperty("key", key)
            rb.setChecked(key == default_load)
            self.load_group.addButton(rb, i)
            lay.addWidget(rb)

        row = QHBoxLayout()
        self.cap_on = QCheckBox("Limit data to")
        self.cap_on.setChecked(cost.metered)
        self.cap = QSpinBox()
        self.cap.setRange(10, 5000)
        self.cap.setSingleStep(10)
        self.cap.setSuffix(" MB")
        self.cap.setValue(saved.get("max_mb") or 100)
        row.addWidget(self.cap_on)
        row.addWidget(self.cap)
        row.addStretch(1)
        lay.addLayout(row)

        self.estimate = QLabel()
        self.estimate.setWordWrap(True)
        lay.addWidget(self.estimate)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Start detailed test")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

        for sig in (self.route.currentIndexChanged, self.mtu.toggled,
                    self.load_group.idToggled, self.cap_on.toggled,
                    self.cap.valueChanged):
            sig.connect(lambda *_: self._update())
        self._update()

    def options(self) -> DetailedOptions:
        return DetailedOptions(
            route_seconds=self.route.currentData(),
            mtu=self.mtu.isChecked(),
            load=self.load_group.checkedButton().property("key"),
            max_mb=self.cap.value() if self.cap_on.isChecked() else None,
        )

    def data_mb(self) -> float:
        o = self.options()
        down, up = self.speeds
        boost = 1.15 if o.load == "hard" else 1.0  # more streams usually go faster
        return estimate_mb(o, down * boost, up * boost)

    def seconds(self) -> float:
        o = self.options()
        t = (o.route_seconds + 10) if o.route_seconds else 0
        t += 4 if o.mtu else 0
        mode = LOAD_MODES[o.load]
        if mode.seconds:
            t += 5 + 2 * (mode.seconds + RAMP_SECONDS)
        return t

    def _update(self) -> None:
        o = self.options()
        self.cap_on.setEnabled(o.uses_data)
        self.cap.setEnabled(self.cap_on.isChecked() and o.uses_data)
        basis = ("your last measured speed" if self.measured else
                 f"a typical {self.speeds[0]:.0f}/{self.speeds[1]:.0f} Mbps line")
        data = (f"about {self.data_mb():.0f} MB of data (based on {basis})"
                if o.uses_data else "almost no data (under 1 MB)")
        self.estimate.setText(f"Takes ~{self.seconds() / 60:.1f} min and uses {data}.")
        self.estimate.setObjectName("warn" if self.cost.metered and o.uses_data else "muted")
        self.estimate.style().unpolish(self.estimate)
        self.estimate.style().polish(self.estimate)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(
            bool(o.route_seconds or o.mtu or o.uses_data))


PRIVACY_HTML = """
<h3>netstab {version} - open source, MIT licensed</h3>
<p>Every line of code is public, so anyone can check exactly what it does.</p>

<p><b>What it never does</b></p>
<ul>
<li>No account, no ads, no telemetry or analytics. Results stay on your PC.</li>
<li>Doesn't need administrator rights and doesn't change Windows, firewall or
router settings.</li>
<li>Doesn't touch your games: it never reads game memory or files, so it can't
trigger anti-cheat (VAC, Vanguard, EAC, ...).</li>
</ul>

<p><b>What it sends over the network</b></p>
<ul>
<li><b>Steam Web API</b> (api.steampowered.com) - downloads Valve's public list of
server locations. Nothing about you is sent.</li>
<li><b>Ping (ICMP echo)</b> to the game relay you test - the same kind of
packets Windows' <i>ping</i> command sends.</li>
<li><b>Only if you turn on the download/upload test:</b> dummy data to and from
Cloudflare's speed test (speed.cloudflare.com). On a metered connection the
app asks for your approval first and can cap the data used.</li>
</ul>

<p><b>What it stores</b></p>
<ul>
<li>%LOCALAPPDATA%\\netstab - the cached server list, your settings (theme,
last measured speed), your saved tests for <i>Compare networks</i> (results
and Wi-Fi/network name) and, only after an error, crash.log. Delete the folder
any time.</li>
<li>Reports are saved only when you click <i>Export report</i>, where you choose.</li>
</ul>
<p>Third-party components and their licenses: see THIRD_PARTY_NOTICES.md.</p>
"""


class AboutDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Privacy & safety")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        html = PRIVACY_HTML.format(version=__version__)
        if PREVIEW:
            html = (f"<p style='color:{C.WARN}'><b>Preview build:</b> checked by "
                    "automated tests only, not yet tested by people. Results may "
                    "have rough edges - please report problems on GitHub.</p>" + html)
        text = QLabel(html)
        text.setTextFormat(Qt.RichText)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(text)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class ResponsiveRow(QWidget):
    """Items side by side when there's room, wrapped onto more rows when the
    window is narrow (5 tiles become 3 + 2, then 2 + 2 + 1, ...)."""

    def __init__(self, min_item_width: int, spacing: int = 12):
        super().__init__()
        self.min_item = min_item_width
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(spacing)
        # The grid must not force the row as wide as all items side by side.
        self.grid.setSizeConstraint(QLayout.SetNoConstraint)
        self.items: list[QWidget] = []
        self._cols = 0

    def add(self, w: QWidget) -> None:
        self.items.append(w)
        self._cols = 0
        self._arrange()

    def clear(self) -> None:
        for w in self.items:
            self.grid.removeWidget(w)
            # Hide and detach now: deleteLater alone leaves old items painted
            # until the event loop gets to them.
            w.hide()
            w.setParent(None)
            w.deleteLater()
        self.items = []
        self._cols = 0
        self.updateGeometry()

    def _item_width(self) -> int:
        return max([self.min_item] + [w.minimumSizeHint().width() for w in self.items])

    def minimumSizeHint(self) -> QSize:
        return QSize(self._item_width(), self.grid.minimumSize().height())

    def sizeHint(self) -> QSize:
        sp = self.grid.horizontalSpacing()
        n = max(1, len(self.items))
        return QSize(n * self._item_width() + (n - 1) * sp, self.grid.sizeHint().height())

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._arrange()

    def _arrange(self) -> None:
        n = len(self.items)
        if not n:
            return
        sp = self.grid.horizontalSpacing()
        fit = (self.width() + sp) // (self._item_width() + sp) if self.width() > 0 else n
        cols = max(1, min(n, fit))
        rows = -(-n // cols)
        cols = -(-n // rows)  # balance the rows: 3 + 2 rather than 4 + 1
        if cols == self._cols:
            return
        self._cols = cols
        for w in self.items:
            self.grid.removeWidget(w)
        for c in range(self.grid.columnCount()):
            self.grid.setColumnStretch(c, 0)
        for i, w in enumerate(self.items):
            self.grid.addWidget(w, i // cols, i % cols)
            w.show()
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)
        self.updateGeometry()


class SideBySide(QWidget):
    """Two panels side by side; when narrow, the first goes above the second."""

    def __init__(self, threshold: int, first_width: tuple[int, int]):
        super().__init__()
        self.threshold = threshold
        self.first_width = first_width  # (min, max) while side by side
        self.box = QBoxLayout(QBoxLayout.LeftToRight, self)
        self.box.setContentsMargins(0, 8, 0, 0)
        self.box.setSpacing(12)
        self.first: QWidget | None = None

    def set_panels(self, first: QWidget, second: QWidget) -> None:
        self.first = first
        first.setMinimumWidth(self.first_width[0])
        first.setMaximumWidth(self.first_width[1])
        self.box.addWidget(first)
        self.box.addWidget(second, 1)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        narrow = self.width() < self.threshold
        want = QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight
        if self.first is not None and self.box.direction() != want:
            self.box.setDirection(want)
            self.first.setMinimumWidth(0 if narrow else self.first_width[0])
            self.first.setMaximumWidth(16777215 if narrow else self.first_width[1])


def _scrollable(content: QWidget, horizontal: bool = True) -> QScrollArea:
    """Lets a page scroll when the window is smaller than its content."""
    area = QScrollArea()
    content.setObjectName("page")
    area.setWidget(content)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded if horizontal
                                      else Qt.ScrollBarAlwaysOff)
    area.viewport().setAutoFillBackground(False)
    return area


def _panel() -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("panel")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)
    return f, lay


def _heading(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("h")
    return lbl


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(720, 480)
        self._fit_to_screen()

        self.bridge = Bridge()
        self.bridge.scan_progress.connect(self._on_scan_progress)
        self.bridge.scan_done.connect(self._on_scan_done)
        self.bridge.detail_progress.connect(self._on_detail_progress)
        self.bridge.detail_done.connect(self._on_detail_done)

        self.servers: list[servers.Server] = []
        self.servers_for: str | None = None  # profile key the list belongs to
        self.server: servers.Server | None = None
        self.test: LoadTest | None = None
        self.test_done_at: float | None = None
        self.summary: Summary | None = None
        self.detail: DetailedTest | None = None
        self.detail_result: DetailedResult | None = None
        self.tested: profiles.GameProfile | None = None  # game of the last test
        self.verdict_data: advice.Verdict | None = None
        self.cost: metered.ConnectionCost | None = None
        self.detail_opts: DetailedOptions | None = None
        self.runs: list[compare.Run] = compare.load()
        self.compare_ids: list[str] = []
        self.current_run: compare.Run | None = None
        self._start_after_scan = False

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(16)
        outer.addWidget(self._build_sidebar(), 0)
        outer.addWidget(self._build_main(), 1)

        self._on_game_changed()
        self.compare_ids = self._default_compare_ids()
        self._refresh_runs()
        self.apply_theme()
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            lambda *_: self.apply_theme())
        # Know up front whether the line is metered (for tips and the report).
        self.bridge.cost_ready.connect(lambda c: setattr(self, "cost", c))
        threading.Thread(target=lambda: self.bridge.cost_ready.emit(metered.check()),
                         daemon=True).start()

    def _fit_to_screen(self) -> None:
        """Open at a comfortable size that fits the usable screen area (it is
        much smaller with 125-150% display scaling), centred."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1280, 800)
            return
        avail = screen.availableGeometry()
        self.resize(min(1400, int(avail.width() * 0.92)),
                    min(900, int(avail.height() * 0.92)))
        self.move(avail.center() - self.rect().center())

    # --------------------------------------------------------------- theme --
    def _theme_changed(self) -> None:
        settings.update(theme=self.theme_combo.currentData())
        self.apply_theme()

    def apply_theme(self) -> None:
        choice = self.theme_combo.currentData()
        if choice == "system":
            dark = QGuiApplication.styleHints().colorScheme() != Qt.ColorScheme.Light
            choice = "dark" if dark else "light"
        C.use(choice)
        QApplication.instance().setStyleSheet(make_style())
        self.title.setText(f"net<span style='color:{C.ACCENT}'>stab</span>")
        self.plot.update()  # the chart reads C when it paints
        self.legend.setText(
            f"<span style='color:{C.LINE}'>━</span> each packet &nbsp;&nbsp;"
            f"<span style='color:{C.ACCENT}'>━</span> 1 s average &nbsp;&nbsp;"
            f"<span style='color:{C.BAD}'>▼</span> lost packet")
        # Re-draw anything already shown with the new semantic colours.
        if self.summary is not None and self.test_done_at is not None:
            self._fill_tiles(self.summary)
        if self.verdict_data is not None:
            self.verdict.show_verdict(self.verdict_data)
        if self.detail_result is not None:
            self._render_detail(self.detail_result)
        self._render_compare()

    # ------------------------------------------------------------------ UI --
    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setFixedWidth(300)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        row = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("title")
        row.addWidget(self.title)
        if PREVIEW:
            badge = QLabel("PREVIEW")
            badge.setObjectName("badge")
            badge.setToolTip("Checked by automated tests only - not yet tested by "
                             "people. See Privacy & safety.")
            row.addWidget(badge, 0, Qt.AlignVCenter)
        row.addStretch(1)
        self.theme_combo = QComboBox()
        for key, label in THEME_CHOICES:
            self.theme_combo.addItem(label, key)
        self.theme_combo.setToolTip("Theme (System follows your Windows setting)")
        self.theme_combo.setCurrentIndex(
            max(0, self.theme_combo.findData(settings.load().get("theme", "system"))))
        self.theme_combo.currentIndexChanged.connect(lambda *_: self._theme_changed())
        row.addWidget(self.theme_combo)
        lay.addLayout(row)
        row = QHBoxLayout()
        sub = QLabel("Test before you queue.")
        sub.setObjectName("muted")
        row.addWidget(sub, 1)
        about = QPushButton("Privacy && safety")
        about.setObjectName("link")
        about.clicked.connect(lambda: AboutDialog(self).exec())
        row.addWidget(about)
        lay.addLayout(row)

        # The setup panels scroll on short screens; the title stays on top and
        # START TEST stays visible at the bottom.
        panels = QWidget()
        pl = QVBoxLayout(panels)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(12)

        # Game
        f, fl = _panel()
        fl.addWidget(_heading("1  Game"))
        self.games = QWidget()  # disabling it disables every game button
        gl = QVBoxLayout(self.games)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(2)
        self.game_group = QButtonGroup(self)
        for i, p in enumerate(profiles.PROFILES):
            b = QPushButton(p.name if p.available else f"{p.name}   · soon")
            b.setObjectName("game")
            b.setCheckable(True)
            b.setProperty("key", p.key)
            b.setEnabled(p.available)
            self.game_group.addButton(b, i)
            gl.addWidget(b)
        self.game_group.button(0).setChecked(True)
        self.game_group.idToggled.connect(lambda _, on: on and self._on_game_changed())
        fl.addWidget(self.games)
        self.game_note = QLabel()
        self.game_note.setObjectName("muted")
        self.game_note.setWordWrap(True)
        fl.addWidget(self.game_note)
        pl.addWidget(f)

        # Server
        f, fl = _panel()
        fl.addWidget(_heading("2  Server"))
        self.rb_auto = QRadioButton("Auto - best ping")
        self.rb_max = QRadioButton("Max ping limit")
        self.rb_max.setToolTip("Only servers under this ping, like CS2's "
                               "mm_dedicated_search_maxping")
        self.rb_manual = QRadioButton("Choose server")
        self.rb_auto.setChecked(True)
        self.server_mode = QButtonGroup(self)
        for i, rb in enumerate((self.rb_auto, self.rb_max, self.rb_manual)):
            self.server_mode.addButton(rb, i)
        self.max_ping = QSpinBox()
        self.max_ping.setRange(25, 350)
        self.max_ping.setSingleStep(5)
        self.max_ping.setSuffix(" ms")
        self.max_ping.setValue(90)
        self.server_combo = QComboBox()
        self.server_combo.setPlaceholderText("Scan to list servers")
        self.btn_scan = QPushButton("Scan servers")
        self.btn_scan.clicked.connect(lambda: self._scan(start_after=False))
        self.server_info = QLabel("")
        self.server_info.setObjectName("muted")
        self.server_info.setWordWrap(True)

        fl.addWidget(self.rb_auto)
        row = QHBoxLayout()
        row.addWidget(self.rb_max, 1)
        row.addWidget(self.max_ping)
        fl.addLayout(row)
        fl.addWidget(self.rb_manual)
        fl.addWidget(self.server_combo)
        fl.addWidget(self.btn_scan)
        fl.addWidget(self.server_info)
        self.server_mode.idToggled.connect(lambda *_: self._sync_server_controls())
        self._sync_server_controls()
        pl.addWidget(f)

        # Duration
        f, fl = _panel()
        fl.addWidget(_heading("3  Test duration"))
        row = QHBoxLayout()
        self.dur_group = QButtonGroup(self)
        for d in profiles.DURATIONS:
            b = QPushButton(f"{d}s" if d < 60 else f"{d // 60} min")
            b.setCheckable(True)
            self.dur_group.addButton(b, d)
            row.addWidget(b)
        self.dur_group.button(60).setChecked(True)
        fl.addLayout(row)
        pl.addWidget(f)
        pl.addStretch(1)
        lay.addWidget(_scrollable(panels, horizontal=False), 1)

        self.btn_start = QPushButton("START TEST")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self._start_or_stop)
        lay.addWidget(self.btn_start)
        return side

    def _build_main(self) -> QWidget:
        self.tabs = QTabWidget()
        # Each page scrolls when the window is smaller than it (small screens,
        # 125-150% display scaling) instead of forcing the window to grow.
        self.tabs.addTab(_scrollable(self._build_live_tab()), "Stability test")
        self.tabs.addTab(_scrollable(self._build_detail_tab()), "Detailed test")
        self.tabs.addTab(_scrollable(self._build_compare_tab()), "Compare networks")
        return self.tabs

    def _build_live_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(12)

        top = QHBoxLayout()
        self.status = QLabel("Pick a game and press START TEST.")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)  # long results must not force the window wide
        top.addWidget(self.status, 1)
        self.btn_export = QPushButton("Export report")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._export)
        top.addWidget(self.btn_export)
        lay.addLayout(top)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        lay.addWidget(self.progress)

        tiles = ResponsiveRow(min_item_width=150)
        self.t_ping = Tile("Ping")
        self.t_jitter = Tile("Jitter")
        self.t_loss = Tile("Packet loss")
        self.t_spikes = Tile("Spikes")
        self.t_grade = Tile("Stability")
        for t in (self.t_ping, self.t_jitter, self.t_loss, self.t_spikes, self.t_grade):
            tiles.add(t)
        lay.addWidget(tiles)

        f, fl = _panel()
        fl.addWidget(_heading("Latency over time"))
        self.plot = LatencyChart(C)
        self.legend = QLabel()
        self.legend.setObjectName("muted")
        fl.addWidget(self.plot, 1)
        fl.addWidget(self.legend)
        lay.addWidget(f, 1)

        self.btn_detail = QPushButton("Detailed test for this server...")
        self.btn_detail.setEnabled(False)
        self.btn_detail.clicked.connect(self._ask_detail)
        self.verdict = VerdictPanel(self.btn_detail)
        lay.addWidget(self.verdict)
        return w

    def _build_detail_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(12)

        top = QHBoxLayout()
        self.d_status = QLabel("Run a stability test first, then start the detailed "
                               "test for the chosen server.")
        self.d_status.setObjectName("status")
        self.d_status.setWordWrap(True)
        top.addWidget(self.d_status, 1)
        self.btn_d_stop = QPushButton("Stop")
        self.btn_d_stop.setObjectName("danger")
        self.btn_d_stop.setVisible(False)
        self.btn_d_stop.clicked.connect(lambda: self.detail and self.detail.stop())
        top.addWidget(self.btn_d_stop)
        lay.addLayout(top)
        self.d_progress = QProgressBar()
        self.d_progress.setTextVisible(False)
        self.d_progress.setFixedHeight(8)
        self.d_progress.setRange(0, 1000)
        lay.addWidget(self.d_progress)

        tiles = ResponsiveRow(min_item_width=150)
        self.d_idle = Tile("Idle ping")
        self.d_down = Tile("Ping while downloading")
        self.d_up = Tile("Ping while uploading")
        self.d_bloat = Tile("Bufferbloat")
        self.d_mtu = Tile("Path MTU")
        for t in (self.d_idle, self.d_down, self.d_up, self.d_bloat, self.d_mtu):
            tiles.add(t)
        lay.addWidget(tiles)

        f, fl = _panel()
        fl.addWidget(_heading("Route to server (per-hop)"))
        self.hop_table = QTableWidget(0, 7)
        self.hop_table.setHorizontalHeaderLabels(
            ["Hop", "Address", "Loss", "Avg", "Best", "Worst", "Jitter"])
        self.hop_table.verticalHeader().setVisible(False)
        self.hop_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.hop_table.setSelectionMode(QTableWidget.NoSelection)
        self.hop_table.setShowGrid(False)
        hh = self.hop_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        fl.addWidget(self.hop_table, 1)
        note = QLabel("Loss on a middle hop that doesn't continue to the last hop is "
                      "normal - routers deprioritise answering pings.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        fl.addWidget(note)
        lay.addWidget(f, 1)

        f, fl = _panel()
        fl.addWidget(_heading("Diagnosis"))
        self.d_findings = QLabel("-")
        self.d_findings.setObjectName("muted")
        self.d_findings.setWordWrap(True)
        self.d_findings.setTextFormat(Qt.RichText)
        fl.addWidget(self.d_findings)
        lay.addWidget(f)
        return w

    # ------------------------------------------------------------- helpers --
    def _profile(self) -> profiles.GameProfile:
        return profiles.get(self.game_group.checkedButton().property("key"))

    def _sync_server_controls(self) -> None:
        self.max_ping.setEnabled(self.rb_max.isChecked())
        self.server_combo.setEnabled(self.rb_manual.isChecked())

    def _busy(self) -> bool:
        return (self.test is not None and self.test_done_at is None) or \
            self.detail is not None

    def _set_running(self, running: bool) -> None:
        for w in (self.games, self.rb_auto, self.rb_max, self.rb_manual,
                  self.max_ping, self.server_combo, self.btn_scan):
            w.setEnabled(not running)
        for b in self.dur_group.buttons():
            b.setEnabled(not running)
        if not running:
            self._sync_server_controls()
        self.btn_start.setText("STOP" if running else "START TEST")

    def _on_game_changed(self) -> None:
        p = self._profile()
        self.game_note.setText(f"{p.note}\nLoad: {p.rate_hz} packets/s, "
                               f"{p.payload_bytes} B each.")
        self.max_ping.setValue(p.default_max_ping)
        if self.servers_for != p.key:
            self.servers, self.servers_for = [], None
            self.server_combo.clear()
            self.server_info.setText("")

    # ---------------------------------------------------------------- scan --
    def _scan(self, start_after: bool) -> None:
        prof = self._profile()
        self._start_after_scan = start_after
        self.btn_scan.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.status.setText(f"Finding {prof.name} servers...")
        self.progress.setRange(0, 0)

        def work():
            try:
                ss = servers.list_servers(prof)
                ss = servers.probe_servers(ss, lambda d, n: self.bridge.scan_progress.emit(d, n))
                self.bridge.scan_done.emit((prof.key, ss), None)
            except Exception as e:  # noqa: BLE001 - shown to the user
                self.bridge.scan_done.emit(None, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_scan_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.status.setText(f"Pinging servers... {done}/{total}")

    def _on_scan_done(self, payload, err) -> None:
        self.btn_scan.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        if err:
            self.status.setText(f"Could not get the server list: {err}")
            self._start_after_scan = False
            return
        key, self.servers = payload
        self.servers_for = key
        self.server_combo.clear()
        for s in self.servers:
            if s.ping_ms is not None:
                self.server_combo.addItem(s.label, s.code)
        reachable = [s for s in self.servers if s.ping_ms is not None]
        under = servers.within(self.servers, self.max_ping.value())
        best = reachable[0] if reachable else None
        self.server_info.setText(
            f"{len(reachable)} servers reachable, {len(under)} under "
            f"{self.max_ping.value()} ms. Best: {best.name if best else '-'} "
            f"({_fmt(best.ping_ms) if best else '-'}).")
        self.status.setText("Servers scanned.")
        if self._start_after_scan:
            self._start_after_scan = False
            self._start_test()

    # ---------------------------------------------------------------- test --
    def _start_or_stop(self) -> None:
        if self.test is not None and self.test_done_at is None:
            self.test.stop()
            return
        if self.detail is not None:
            return
        prof = self._profile()
        if not prof.available:
            return
        if self.servers_for != prof.key or not self.servers:
            self._scan(start_after=True)
        else:
            self._start_test()

    def _pick_server(self) -> servers.Server | None:
        if self.rb_manual.isChecked():
            code = self.server_combo.currentData()
            return next((s for s in self.servers if s.code == code), None)
        limit = self.max_ping.value() if self.rb_max.isChecked() else None
        return servers.auto_select(self.servers, limit)

    def _start_test(self) -> None:
        prof = self._profile()
        srv = self._pick_server()
        if srv is None:
            msg = (f"No server answered under {self.max_ping.value()} ms. "
                   "Raise the max ping or use Auto." if self.rb_max.isChecked()
                   else "No reachable server - check your connection.")
            self.status.setText(msg)
            return
        self.server = srv
        self.summary = None
        self.detail_result = None
        self.verdict_data = None
        dur = self.dur_group.checkedId()
        self.test = LoadTest(srv.best_relay, prof.rate_hz, prof.payload_bytes, dur)
        self.test_done_at = None
        self._xs, self._ys, self._loss_x = [], [], []
        self._sec_sum: dict[int, list[float]] = {}  # second -> [sum, count]
        self._consumed = 0
        self._ticks = 0
        self._live: Summary | None = None
        self.plot.reset(dur)
        for t in (self.t_ping, self.t_jitter, self.t_loss, self.t_spikes, self.t_grade):
            t.set("-")
        self.tested = prof
        self.verdict.message("Testing... comments on how the game will feel appear "
                             "when the test finishes.", prof.name)
        self.btn_detail.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.progress.setRange(0, dur * 1000)
        self.status.setText(f"Testing {prof.name} - {srv.name} [{srv.code}] "
                            f"via {srv.best_relay}")
        self._set_running(True)
        self.tabs.setCurrentIndex(0)
        self.test.start()
        self.timer.start()

    def _tick(self) -> None:
        t = self.test
        if t is None:
            return
        # Only the packets that finished since the last tick are processed here,
        # so a 5-minute test (19,200 packets) stays as light as a 30 s one. That
        # matters beyond smoothness: a busy UI thread can delay the measuring
        # threads and add fake jitter.
        snap = t.snapshot()
        step = 1 / t.rate_hz
        for i in range(self._consumed, len(snap)):
            if snap[i] is None:
                self._loss_x.append(i * step)
            else:
                self._xs.append(i * step)
                self._ys.append(snap[i])
                acc = self._sec_sum.setdefault(int(i * step), [0.0, 0])
                acc[0] += snap[i]
                acc[1] += 1
        self._consumed = len(snap)

        # 1-second average for a readable trend line.
        avg = [(k + 0.5, s / n) for k, (s, n) in sorted(self._sec_sum.items())]
        self.plot.set_data(self._xs, self._ys, avg, self._loss_x)

        if snap:
            recent = [r for r in snap[-t.rate_hz:] if r is not None]
            now = statistics.median(recent) if recent else None
            # Full stats sort every packet, so refresh them twice a second.
            if self._ticks % 5 == 0 or self._live is None:
                self._live = summarize(snap, t.rate_hz)
                self._fill_tiles(self._live, ping=False)
            self.t_ping.set(_fmt(now), f"median {_fmt(self._live.median_ms)}",
                            _ping_color(now))
        self._ticks += 1

        self.progress.setValue(int(min(t.elapsed(), t.duration) * 1000))

        if t.finished.is_set():
            if self.test_done_at is None:
                self.test_done_at = time.perf_counter()
            # Wait out the reply timeout so the last packets are counted.
            if time.perf_counter() - self.test_done_at >= t.timeout_ms / 1000 + 0.1:
                self.timer.stop()
                self._finish_test()

    def _fill_tiles(self, s: Summary, ping: bool = True) -> None:
        if ping:
            self.t_ping.set(_fmt(s.median_ms), f"min {_fmt(s.min_ms)} · p99 {_fmt(s.p99_ms)}",
                            _ping_color(s.median_ms))
        self.t_jitter.set(_fmt(s.jitter_ms, nd=1), f"σ {_fmt(s.stddev_ms, nd=1)}",
                          C.GOOD if s.jitter_ms < 4 else C.WARN if s.jitter_ms < 10 else C.BAD)
        self.t_loss.set(_fmt(s.loss_pct, "%", 2), f"{s.sent - s.received} of {s.sent} packets",
                        C.GOOD if s.loss_pct == 0 else C.WARN if s.loss_pct < 1 else C.BAD)
        self.t_spikes.set(str(s.spikes), f"> {_fmt(s.spike_threshold_ms)}",
                          C.GOOD if s.spikes == 0 else C.WARN if s.spikes < s.received * 0.01 else C.BAD)
        self.t_grade.set(f"{s.grade}  {s.score}", "out of 100", grade_color(s.grade))

    def _finish_test(self) -> None:
        t = self.test
        self._set_running(False)
        self.summary = s = t.summary()
        if s.sent == 0:
            self.status.setText("Test stopped.")
            return
        self._fill_tiles(s)
        if not math.isnan(s.median_ms):
            self.plot.set_median(s.median_ms)
        prof = self.tested
        # The status line uses the same verdict as the panel, so they never disagree.
        self._show_verdict()
        verdict = {advice.GOOD: "Ready to play", advice.OK: "Playable, with issues",
                   advice.BAD: "Expect problems"}[self.verdict_data.level]
        stopped = " (stopped early)" if t.cancelled else ""
        self.status.setText(f"{verdict} - {prof.name} on {self.server.name}{stopped}")
        self._save_run(t, s)
        self._show_verdict()  # again, with the network re-checked when saving
        self.btn_detail.setEnabled(True)
        self.btn_export.setEnabled(True)
        self.progress.setValue(self.progress.maximum())
        if t.cancelled:
            return  # the user stopped it; don't push another test on them

        ans = QMessageBox.question(
            self, "Detailed network test",
            f"Run a detailed network test for {self.server.name} [{self.server.code}]?\n\n"
            "It traces the route hop by hop and can check how much your ping rises "
            "while your line is busy. You'll choose what to run next.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ans == QMessageBox.Yes:
            self._ask_detail()

    # ------------------------------------------------------------- compare --
    def _save_run(self, t: LoadTest, s: Summary) -> None:
        """Keep the result for the Compare tab, labelled with the network."""
        self.current_run = None
        if s.received == 0 or t.elapsed() < 10:
            return  # too short to be a fair comparison
        # Re-read the network now: the user may have switched since the app started.
        QGuiApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        try:
            self.cost = metered.check()
        finally:
            QGuiApplication.restoreOverrideCursor()
        net = self.cost.profile or "Unknown network"
        run = compare.Run(
            label=net, network=net, time=datetime.now().isoformat(timespec="seconds"),
            game=self.tested.key, game_name=self.tested.name,
            server_code=self.server.code, server_name=self.server.name,
            duration_s=round(min(t.elapsed(), t.duration)), summary=s.to_dict(),
            rate_hz=t.rate_hz)
        self.runs.append(run)
        self.runs = self.runs[-compare.MAX_RUNS:]
        compare.save(self.runs)
        self.current_run = run
        self.compare_ids = self._default_compare_ids()
        self._refresh_runs()
        others = {r.network for r in self.runs if r.network != net}
        hint = ("open Compare to see which network is better" if others else
                "switch to another network and test again to compare")
        self.status.setText(f"{self.status.text()}  ·  saved as '{net}' - {hint}")

    def _default_compare_ids(self) -> list[str]:
        # Latest run of each network, newest networks first, at most four.
        seen, ids = set(), []
        for r in reversed(self.runs):
            if r.network not in seen:
                seen.add(r.network)
                ids.append(r.id)
            if len(ids) == 4:
                break
        return ids

    def _build_compare_tab(self) -> QWidget:
        # Saved tests beside the comparison; above it when the window is narrow.
        w = SideBySide(threshold=760, first_width=(230, 340))

        f, fl = _panel()
        saved = f
        fl.addWidget(_heading("Saved tests"))
        hint = QLabel("Every test is saved with the network it ran on. Switch Wi-Fi, "
                      "plug in a cable or use a hotspot, test again, then tick the "
                      "tests to compare.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        fl.addWidget(hint)
        self.run_list = QListWidget()
        self.run_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.run_list.setWordWrap(True)
        self.run_list.setMinimumHeight(150)
        self.run_list.itemChanged.connect(self._on_run_checked)
        fl.addWidget(self.run_list, 1)
        row = QHBoxLayout()
        b = QPushButton("Rename")
        b.clicked.connect(self._rename_run)
        row.addWidget(b)
        b = QPushButton("Delete")
        b.clicked.connect(self._delete_run)
        row.addWidget(b)
        fl.addLayout(row)

        f, fl = _panel()
        fl.addWidget(_heading("Comparison"))
        self.cmp_head = QLabel("Run at least two tests to compare.")
        self.cmp_head.setObjectName("muted")
        self.cmp_head.setWordWrap(True)
        fl.addWidget(self.cmp_head)
        self.cmp_reasons = QLabel()
        self.cmp_reasons.setWordWrap(True)
        self.cmp_reasons.setTextFormat(Qt.RichText)
        fl.addWidget(self.cmp_reasons)
        self.cmp_table = QTableWidget(0, 0)
        self.cmp_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.cmp_table.setSelectionMode(QTableWidget.NoSelection)
        self.cmp_table.setShowGrid(False)
        self.cmp_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        # Fixed row height: the ✓ glyph is taller than text and made rows uneven.
        vh = self.cmp_table.verticalHeader()
        vh.setSectionResizeMode(QHeaderView.Fixed)
        vh.setDefaultSectionSize(self.cmp_table.fontMetrics().lineSpacing() * 2 + 6)
        fl.addWidget(self.cmp_table, 1)
        self.cmp_notes = QLabel()
        self.cmp_notes.setObjectName("muted")
        self.cmp_notes.setWordWrap(True)
        fl.addWidget(self.cmp_notes)
        self.cmp_table.setMinimumHeight(220)
        w.set_panels(saved, f)
        return w

    def _refresh_runs(self) -> None:
        self.run_list.blockSignals(True)
        self.run_list.clear()
        for r in reversed(self.runs):
            when = r.time.replace("T", " ")[:16]
            it = QListWidgetItem(
                f"{r.label}\n{r.game_name} · {r.server_name.split(' (')[0]}\n"
                f"{when} · {r.summary.get('grade')} {r.summary.get('score')}/100")
            it.setData(Qt.UserRole, r.id)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            it.setCheckState(Qt.Checked if r.id in self.compare_ids else Qt.Unchecked)
            self.run_list.addItem(it)
        self.run_list.blockSignals(False)
        self._render_compare()

    def _on_run_checked(self, it: QListWidgetItem) -> None:
        rid = it.data(Qt.UserRole)
        if it.checkState() == Qt.Checked and rid not in self.compare_ids:
            self.compare_ids.append(rid)
        elif it.checkState() != Qt.Checked and rid in self.compare_ids:
            self.compare_ids.remove(rid)
        self._render_compare()

    def _selected_run(self) -> compare.Run | None:
        it = self.run_list.currentItem()
        rid = it.data(Qt.UserRole) if it else None
        return next((r for r in self.runs if r.id == rid), None)

    def _rename_run(self) -> None:
        r = self._selected_run()
        if r is None:
            return
        name, ok = QInputDialog.getText(self, "Rename test", "Name:", text=r.label)
        if ok and name.strip():
            r.label = name.strip()
            compare.save(self.runs)
            self._refresh_runs()

    def _delete_run(self) -> None:
        r = self._selected_run()
        if r is None:
            return
        if QMessageBox.question(self, "Delete test", f"Delete the saved test "
                                f"'{r.label}' ({r.time.replace('T', ' ')[:16]})?"
                                ) != QMessageBox.Yes:
            return
        self.runs.remove(r)
        if r.id in self.compare_ids:
            self.compare_ids.remove(r.id)
        compare.save(self.runs)
        self._refresh_runs()

    def _render_compare(self) -> None:
        runs = [r for r in self.runs if r.id in self.compare_ids]  # oldest first
        t = self.cmp_table
        if not runs:
            t.setRowCount(0)
            t.setColumnCount(0)
            self.cmp_head.setText("Tick two or more saved tests to compare them.")
            self.cmp_reasons.setText("")
            self.cmp_notes.setText("")
            return
        cmp = compare.compare(runs)
        t.setColumnCount(len(runs))
        t.setRowCount(len(cmp.rows))
        t.setHorizontalHeaderLabels(
            [f"{r.label}\n{r.time.replace('T', ' ')[5:16]}" for r in runs])
        t.setVerticalHeaderLabels([row.metric.name for row in cmp.rows])
        for ri, row in enumerate(cmp.rows):
            for ci in range(len(runs)):
                win = ci in row.best
                it = QTableWidgetItem(("✓ " if win else "") + row.fmt(ci))
                it.setTextAlignment(Qt.AlignCenter)
                if win:
                    it.setForeground(QColor(C.GOOD))
                    font = it.font()
                    font.setBold(True)
                    it.setFont(font)
                t.setItem(ri, ci, it)
        if cmp.winner is not None:
            self.cmp_head.setText(f"🏆 {cmp.summary}")
            self.cmp_head.setObjectName("verdict")
            self.cmp_head.setStyleSheet(f"color: {C.GOOD};")
        else:
            self.cmp_head.setText(cmp.summary)
            self.cmp_head.setObjectName("verdict" if len(runs) > 1 else "muted")
            self.cmp_head.setStyleSheet("")
        self.cmp_head.style().unpolish(self.cmp_head)
        self.cmp_head.style().polish(self.cmp_head)
        self.cmp_reasons.setText("<br>".join(f"✓ {x}" for x in cmp.reasons))
        self.cmp_notes.setText("  ".join(cmp.notes))

    def _show_verdict(self) -> None:
        if self.summary is None:
            return
        others = [s.ping_ms for s in self.servers
                  if s.ping_ms is not None and s is not self.server]
        self.verdict_data = advice.build(
            self.tested, self.summary, self.server.name, self.detail_result,
            best_other_ping=min(others) if others else None,
            metered=bool(self.cost and self.cost.metered))
        self.verdict.show_verdict(self.verdict_data)

    # -------------------------------------------------------------- detail --
    def _ask_detail(self) -> None:
        """Options dialog, then an explicit approval if the line is metered."""
        if self.server is None or self.detail is not None:
            return
        QGuiApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        try:
            self.cost = metered.check()
        finally:
            QGuiApplication.restoreOverrideCursor()
        saved = settings.load()
        measured = "down_mbps" in saved
        speeds = ((saved["down_mbps"], saved["up_mbps"]) if measured
                  else DEFAULT_SPEEDS)
        dlg = DetailOptionsDialog(self, self.server.name, self.cost, speeds,
                                  measured, saved.get("detail_options", {}))
        if dlg.exec() != QDialog.Accepted:
            return
        opts = dlg.options()
        settings.update(detail_options=opts.__dict__)

        if opts.uses_data and self.cost.metered:
            cap = (f"\nIt will stop at {opts.max_mb} MB." if opts.max_mb else
                   "\nNo data limit is set.")
            ans = QMessageBox.warning(
                self, "Metered connection",
                f"{self.cost.description}.\n\n"
                f"The {LOAD_MODES[opts.load].key} download/upload test will use about "
                f"{dlg.data_mb():.0f} MB of your data allowance.{cap}\n\n"
                "Do you approve using this data?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return
        self._start_detail(opts)

    def _start_detail(self, opts: DetailedOptions) -> None:
        srv = self.server
        self.detail = DetailedTest(srv.best_relay, opts)
        self.detail_opts = opts
        self.detail_result = None
        self.hop_table.setRowCount(0)
        for t in (self.d_idle, self.d_down, self.d_up, self.d_bloat, self.d_mtu):
            t.set("-")
        self.d_findings.setText("Running...")
        self.d_status.setText(f"Detailed test - {srv.name} [{srv.code}] ({srv.best_relay})")
        self.btn_d_stop.setVisible(True)
        self.btn_detail.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.tabs.setCurrentIndex(1)
        det = self.detail

        def work():
            res = det.run(lambda st, f, m: self.bridge.detail_progress.emit(st, f, m))
            self.bridge.detail_done.emit(res)

        threading.Thread(target=work, daemon=True).start()

    # Share of the progress bar each stage takes.
    _STAGES = {"route": (0, 0.15), "mtr": (0.15, 0.45), "mtu": (0.6, 0.05),
               "bloat": (0.65, 0.35), "done": (1, 0)}

    def _on_detail_progress(self, stage: str, frac: float, msg: str) -> None:
        start, span = self._STAGES.get(stage, (0, 0))
        self.d_progress.setValue(int((start + span * frac) * 1000))
        if self.detail:
            self.d_status.setText(f"{msg}")
            self._fill_hops(self.detail.result)
            self._fill_detail_tiles(self.detail.result)

    def _fill_hops(self, r: DetailedResult) -> None:
        hops = list(r.hops)
        self.hop_table.setRowCount(len(hops))
        for row, h in enumerate(hops):
            loss = h.loss_pct if h.sent else None
            cells = [str(h.ttl), h.ip or "* (no reply)",
                     _fmt(loss, "%", 1) if h.sent else "-",
                     _fmt(h.avg, nd=1), _fmt(h.best, nd=1), _fmt(h.worst, nd=1),
                     _fmt(h.jitter, nd=1)]
            for col, text in enumerate(cells):
                it = self.hop_table.item(row, col)
                if it is None:
                    it = QTableWidgetItem()
                    self.hop_table.setItem(row, col, it)
                it.setText(text)
                if col == 2 and loss is not None and h.ip:
                    it.setForeground(QColor(C.GOOD if loss == 0 else C.WARN if loss < 5 else C.BAD))
                if col == 3:
                    it.setForeground(QColor(_ping_color(h.avg)))

    def _fill_detail_tiles(self, r: DetailedResult) -> None:
        def phase(tile: Tile, ph):
            if ph is None:
                return
            sub = f"{ph.mbps:.0f} Mbps" if ph.mbps else ""
            if r.idle and r.idle.median_ms and ph is not r.idle and ph.median_ms:
                sub = f"+{ph.median_ms - r.idle.median_ms:.0f} ms · {sub}"
            tile.set(_fmt(ph.median_ms), sub, _ping_color(ph.median_ms))

        phase(self.d_idle, r.idle)
        phase(self.d_down, r.download)
        phase(self.d_up, r.upload)
        if r.mtu:
            self.d_mtu.set(str(r.mtu), "bytes", C.GOOD if r.mtu >= 1400 else C.WARN)

    def _render_detail(self, r: DetailedResult) -> None:
        opts = self.detail_opts
        self._fill_hops(r)
        self._fill_detail_tiles(r)
        if r.bloat_grade:
            self.d_bloat.set(r.bloat_grade, "A = no bloat", grade_color(r.bloat_grade))
        if opts and not opts.uses_data:
            for t in (self.d_idle, self.d_down, self.d_up, self.d_bloat):
                t.set("-", "skipped")
        if r.mtu is None:
            if opts and not opts.mtu:
                self.d_mtu.set("-", "skipped")
            else:
                self.d_mtu.set("?", "server ignores large pings", C.MUTED)
        self.d_findings.setText("<br>".join(f"• {x}" for x in r.findings))
        self.d_findings.setObjectName("")
        self.d_findings.style().unpolish(self.d_findings)
        self.d_findings.style().polish(self.d_findings)

    def _on_detail_done(self, r: DetailedResult) -> None:
        cancelled = self.detail.cancelled
        self.detail = None
        self.detail_result = r
        self.btn_d_stop.setVisible(False)
        self.btn_detail.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.btn_export.setEnabled(True)
        self.d_progress.setValue(1000)
        self._render_detail(r)
        used = f" - used {r.data_mb:.0f} MB of data" if r.data_mb >= 1 else ""
        self.d_status.setText(("Detailed test stopped" if cancelled else
                               f"Detailed test complete - {self.server.name}") + used)
        if r.download and r.download.mbps and r.upload and r.upload.mbps:
            settings.update(down_mbps=round(r.download.mbps, 1),
                            up_mbps=round(r.upload.mbps, 1))
        if self.current_run is not None:
            self.current_run.detailed = r.to_dict()
            compare.save(self.runs)
            self._render_compare()
        self._show_verdict()  # fold route / bufferbloat results into the advice

    # -------------------------------------------------------------- export --
    def _export(self) -> None:
        if self.summary is None:
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export report", f"netstab-{self._profile().key}-{stamp}.json",
            "JSON (*.json)")
        if not path:
            return
        srv = self.server
        report = {
            "app": f"netstab {__version__}",
            "time": datetime.now().isoformat(timespec="seconds"),
            "game": self.tested.name,
            "connection": self.cost.__dict__ if self.cost else None,
            "server": {"code": srv.code, "name": srv.name, "relay": srv.best_relay,
                       "scan_ping_ms": srv.ping_ms},
            "duration_s": self.test.duration,
            "rate_hz": self.test.rate_hz,
            "summary": self.summary.to_dict(),
            "verdict": self.verdict_data.to_dict() if self.verdict_data else None,
            "rtts_ms": self.test.snapshot(),
            "detailed": self.detail_result.to_dict() if self.detail_result else None,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=lambda o: None)
        self.status.setText(f"Report saved to {path}")

    def closeEvent(self, e) -> None:
        if self.test:
            self.test.stop()
        if self.detail:
            self.detail.stop()
        super().closeEvent(e)


ICON_PATH = Path(__file__).resolve().parent / "assets" / "netstab.ico"
CRASH_LOG = servers.CACHE_DIR / "crash.log"
WINDOW_TITLE = f"netstab {__version__} - game network stability test"
_MUTEX_NAME = "Local\\netstab-single-instance"
_mutex_handles: list[int] = []  # kept open for the life of the process


def claim_single_instance() -> bool:
    """True if this is the only running netstab. Two copies testing at once
    would share the line and skew each other's results."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    handle = k32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        return True  # can't tell; don't block the user
    _mutex_handles.append(handle)
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def focus_running_instance() -> None:
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, WINDOW_TITLE)
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE, in case it's minimised
        user32.SetForegroundWindow(hwnd)


def _install_crash_reporting() -> None:
    """A windowed .exe has no console, so errors are logged and shown instead."""

    def log(exc_type, exc, tb, where: str) -> str:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(CRASH_LOG, "a", encoding="utf-8") as f:
                f.write(f"--- {datetime.now().isoformat(timespec='seconds')} "
                        f"netstab {__version__} ({where})\n{text}\n")
        except OSError:
            pass
        return text

    def on_error(exc_type, exc, tb):
        text = log(exc_type, exc, tb, "main")
        splash.close()  # it stays on top and would hide the error dialog
        if QApplication.instance():
            QMessageBox.critical(None, "netstab - unexpected error",
                                 f"Something went wrong:\n\n{exc}\n\n"
                                 f"Details were saved to {CRASH_LOG}.\n\n{text[-1500:]}")

    def on_thread_error(args):
        log(args.exc_type, args.exc_value, args.exc_traceback,
            f"thread {args.thread.name if args.thread else '?'}")

    sys.excepthook = on_error
    threading.excepthook = on_thread_error


def main() -> int:
    _install_crash_reporting()
    if not claim_single_instance():
        splash.close()
        focus_running_instance()
        return 0
    try:
        # Own taskbar group, so Windows shows netstab's icon rather than Python's.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("netstab.app")
    except (AttributeError, OSError):
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("netstab")
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    app.setStyleSheet(make_style())
    splash.update("Opening window...")
    w = MainWindow()
    w.show()
    app.processEvents()  # paint the window before the splash goes away
    splash.close()
    w.raise_()
    w.activateWindow()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
