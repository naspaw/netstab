"""Live monitor UI: the "Live monitor" tab, the pop-up mini window and the
tray icon that keeps monitoring in the background.

One MonitorController owns the running PingMonitor; the tab, the mini window
and the tray all show its data, so they always agree.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QActionGroup, QColor, QFont, QIcon, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QHBoxLayout, QLabel,
                               QMenu, QPushButton, QRadioButton, QSpinBox,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

from . import monitor, profiles, servers, settings
from .chart import LatencyChart
from .gui import C, ResponsiveRow, Tile, _fmt, _heading, _panel, _ping_color
from .monitor import AlertTracker, PingMonitor, TargetError

WINDOWS = [(60, "1 min"), (300, "5 min"), (600, "10 min")]
CLOSE_MODES = [
    ("taskbar", "Keep monitoring on the taskbar (minimise)"),
    ("tray", "Keep monitoring in the tray (hide the window)"),
    ("quit", "Stop monitoring and quit"),
]


# ----------------------------------------------------------------- controller --
class MonitorController(QObject):
    """Starts/stops the monitor and turns its data into alerts."""

    changed = Signal()  # started, stopped, paused, resumed or target changed
    failed = Signal(str)
    alert = Signal(str)
    _resolved = Signal(object, object)  # (ip, label) or None, error text

    def __init__(self, window):
        super().__init__()
        self.win = window  # MainWindow: reuses its server list when it has one
        s = settings.load()
        self.interval: float = s.get("monitor_interval", 0.5)
        if self.interval not in monitor.INTERVALS:
            self.interval = 0.5
        self.alerts_on: bool = s.get("monitor_alerts", False)
        self.limit_ms: int = s.get("monitor_limit", 90)
        self.mon: PingMonitor | None = None
        self.label = ""
        self.starting = False
        self.user_paused = False
        self.test_paused = False
        self.tracker = AlertTracker(self.limit_ms)
        self._resolved.connect(self._on_resolved)
        self._alert_timer = QTimer(self)
        self._alert_timer.setInterval(1000)
        self._alert_timer.timeout.connect(self._check_alerts)

    # -- state -------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.mon is not None

    @property
    def paused(self) -> bool:
        return self.user_paused or self.test_paused

    def status_text(self) -> str:
        if self.starting:
            return "Starting..."
        if not self.mon:
            return "Not monitoring"
        rate = f"{1 / self.interval:.0f} ping{'s' if self.interval < 1 else ''}/s"
        if self.test_paused:
            return f"{self.label} - paused while a test runs"
        if self.user_paused:
            return f"{self.label} - paused"
        return f"{self.label} ({self.mon.ip}) - {rate}"

    # -- control -----------------------------------------------------------------
    def start(self, spec: dict) -> None:
        """spec: {"kind": "game", "game": key, "server": code|"auto"}
        or {"kind": "host", "host": text}. Resolving can take a few seconds."""
        self.stop()
        self.starting = True
        settings.update(monitor_target=spec)
        self.changed.emit()

        def work():
            try:
                self._resolved.emit(self._resolve(spec), None)
            except (TargetError, NotImplementedError, OSError) as e:
                self._resolved.emit(None, str(e))
            except Exception as e:  # noqa: BLE001 - shown to the user
                self._resolved.emit(None, f"Couldn't start: {e}")

        threading.Thread(target=work, daemon=True, name="netstab-monitor-resolve").start()

    def _resolve(self, spec: dict) -> tuple[str, str]:
        if spec.get("kind") == "game":
            prof = profiles.get(spec.get("game", "cs2"))
            ss = self.win.servers if self.win.servers_for == prof.key else []
            if not ss:
                ss = servers.probe_servers(servers.list_servers(prof))
            code = spec.get("server", "auto")
            srv = (servers.auto_select(ss) if code == "auto" else
                   next((s for s in ss if s.code == code and s.ping_ms is not None), None))
            if srv is None:
                raise TargetError("That server didn't answer. Try 'Best ping'.")
            return srv.best_relay, f"{prof.name} · {srv.name}"
        host = spec.get("host", "").strip()
        ip = monitor.resolve(host)
        return ip, ("Your router" if host.lower() == monitor.ROUTER else host)

    def _on_resolved(self, result, err) -> None:
        if not self.starting:
            return  # stopped while resolving
        self.starting = False
        if err:
            self.failed.emit(err)
            self.changed.emit()
            return
        ip, self.label = result
        self.mon = PingMonitor(ip, self.interval)
        self.user_paused = False
        self.tracker = AlertTracker(self.limit_ms)
        if self.test_paused:
            self.mon.pause()
        self.mon.start()
        self._alert_timer.start()
        self.changed.emit()

    def stop(self) -> None:
        self.starting = False
        if self.mon:
            self.mon.stop()
            self.mon = None
        self._alert_timer.stop()
        self.changed.emit()

    def toggle_pause(self) -> None:
        if not self.mon:
            return
        self.user_paused = not self.user_paused
        self._apply_pause()

    def pause_for_test(self) -> None:
        """The stability/detailed test needs the line to itself."""
        self.test_paused = True
        self._apply_pause()

    def resume_after_test(self) -> None:
        self.test_paused = False
        self._apply_pause()

    def _apply_pause(self) -> None:
        if self.mon:
            self.mon.pause() if self.paused else self.mon.resume()
        self.changed.emit()

    def set_interval(self, seconds: float) -> None:
        self.interval = seconds
        settings.update(monitor_interval=seconds)
        if self.mon:
            self.mon.interval = seconds
        self.changed.emit()

    def set_alerts(self, on: bool, limit_ms: int) -> None:
        self.alerts_on, self.limit_ms = on, limit_ms
        self.tracker.limit_ms = limit_ms
        settings.update(monitor_alerts=on, monitor_limit=limit_ms)

    def _check_alerts(self) -> None:
        if not (self.alerts_on and self.mon) or self.paused:
            return
        recent = self.mon.stats(self.tracker.hold_s)
        msg = self.tracker.check(recent, time.monotonic(), self.interval)
        if msg:
            self.alert.emit(f"{self.label}: {msg}")


# --------------------------------------------------------------------- drawing --
def draw_sparkline(p: QPainter, rect: QRectF, samples, window_s: float) -> None:
    """Tiny ping history: a line for replies, red ticks for lost pings."""
    if not samples:
        return
    end = samples[-1][0]
    rtts = [r for _, r in samples if r is not None]
    top = max(rtts, default=50.0) * 1.15 or 1.0
    pts = [QPointF(rect.right() - (end - t) / window_s * rect.width(),
                   rect.bottom() - r / top * rect.height())
           for t, r in samples if r is not None and end - t <= window_s]
    if len(pts) > 1:
        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        p.setPen(QPen(QColor(C.LINE), 1.4))
        p.drawPath(path)
    p.setPen(QPen(QColor(C.BAD), 1.6))
    for t, r in samples:
        if r is None and end - t <= window_s:
            x = rect.right() - (end - t) / window_s * rect.width()
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.top() + 6))


def tray_icon_for(ms: float | None, state: str) -> QIcon:
    """Tray icon showing the current ping number on a coloured square."""
    icon = QIcon()
    for size in (16, 20, 24, 32, 48):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        color = (QColor(C.MUTED) if state != "live" else
                 QColor(C.BAD) if ms is None else QColor(_ping_color(ms)))
        p.setBrush(color)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
        text = ("II" if state == "paused" else "-" if state != "live" else
                "×" if ms is None else ("999" if ms >= 999 else f"{ms:.0f}"))
        f = QFont("Segoe UI")
        f.setBold(True)
        f.setPixelSize(int(size * (0.62 if len(text) <= 2 else 0.46)))
        p.setFont(f)
        p.setPen(QColor("#111111") if state == "live" and ms is not None and ms < 120
                 else QColor("#ffffff"))
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, text)
        p.end()
        icon.addPixmap(pm)
    return icon


# ------------------------------------------------------------------------- tab --
class MonitorTab(QWidget):
    def __init__(self, ctl: MonitorController, window):
        super().__init__()
        self.ctl, self.win = ctl, window
        self.window_s = 60
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(12)

        intro = QLabel("Keeps pinging one server or website, lightly (1-4 pings a "
                       "second), so you can watch your connection while you play. "
                       "Use the mini window or the tray icon to keep it on screen.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        f, fl = _panel()
        fl.addWidget(_heading("What to ping"))
        saved = settings.load().get("monitor_target", {"kind": "game", "game": "cs2",
                                                       "server": "auto"})
        self.rb_game = QRadioButton("Game server")
        self.rb_host = QRadioButton("Website or IP")
        grp = QButtonGroup(self)
        grp.addButton(self.rb_game)
        grp.addButton(self.rb_host)
        (self.rb_host if saved.get("kind") == "host" else self.rb_game).setChecked(True)

        row = QHBoxLayout()
        row.addWidget(self.rb_game)
        self.game = QComboBox()
        self.game.setAccessibleName("Game")
        for p in profiles.PROFILES:
            if p.available:
                self.game.addItem(p.name, p.key)
        self.game.setCurrentIndex(max(0, self.game.findData(saved.get("game", "cs2"))))
        self.game.currentIndexChanged.connect(lambda *_: self._fill_servers())
        row.addWidget(self.game)
        self.server = QComboBox()
        self.server.setAccessibleName("Server")
        self.server.setMinimumWidth(220)
        row.addWidget(self.server, 1)
        fl.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(self.rb_host)
        self.host = QComboBox()
        self.host.setEditable(True)
        self.host.setAccessibleName("Website or IP address")
        self.host.setInsertPolicy(QComboBox.NoInsert)
        for host, name in monitor.PRESET_HOSTS:
            self.host.addItem(f"{name}" if host == monitor.ROUTER else host, host)
        self.host.setToolTip("Type any website (google.com) or IP address, or pick one")
        if saved.get("kind") == "host":
            h = saved.get("host", "google.com")
            i = self.host.findData(h)
            self.host.setCurrentIndex(i) if i >= 0 else self.host.setEditText(h)
        row.addWidget(self.host, 1)
        fl.addLayout(row)
        for w in (self.game, self.server):
            w.activated.connect(lambda *_: self.rb_game.setChecked(True))
        self.host.activated.connect(lambda *_: self.rb_host.setChecked(True))
        self.host.lineEdit().textEdited.connect(lambda *_: self.rb_host.setChecked(True))

        row = QHBoxLayout()
        row.addWidget(QLabel("Pings per second"))
        self.rate = QButtonGroup(self)
        for secs in monitor.INTERVALS:
            b = QPushButton(f"{1 / secs:.0f}")
            b.setCheckable(True)
            b.setProperty("secs", secs)
            b.setChecked(secs == ctl.interval)
            b.setFixedWidth(48)
            self.rate.addButton(b)
            row.addWidget(b)
        self.rate.buttonClicked.connect(lambda b: ctl.set_interval(b.property("secs")))
        row.addStretch(1)
        fl.addLayout(row)

        row = QHBoxLayout()
        self.alerts = QCheckBox("Alert me when ping goes above")
        self.alerts.setChecked(ctl.alerts_on)
        self.limit = QSpinBox()
        self.limit.setAccessibleName("Alert limit")
        self.limit.setRange(20, 500)
        self.limit.setSingleStep(10)
        self.limit.setSuffix(" ms")
        self.limit.setValue(ctl.limit_ms)
        tail = QLabel("or the connection drops")
        for w in (self.alerts, self.limit, tail):
            row.addWidget(w)
        row.addStretch(1)
        self.alerts.toggled.connect(lambda *_: self._alerts_changed())
        self.limit.valueChanged.connect(lambda *_: self._alerts_changed())
        fl.addLayout(row)

        s = settings.load()
        self.on_taskbar = QCheckBox("Show the live ping on netstab's taskbar button "
                                    "(next to your pinned apps)")
        self.on_taskbar.setChecked(s.get("monitor_taskbar", True))
        self.on_taskbar.toggled.connect(lambda on: (settings.update(monitor_taskbar=on),
                                                    ctl.changed.emit()))
        fl.addWidget(self.on_taskbar)

        row = QHBoxLayout()
        row.addWidget(QLabel("When I close the window while monitoring"))
        self.close_mode = QComboBox()
        self.close_mode.setAccessibleName("When I close the window while monitoring")
        for key, text in CLOSE_MODES:
            self.close_mode.addItem(text, key)
        # Older versions stored a yes/no "keep in tray" setting.
        saved_mode = s.get("monitor_close") or (
            "taskbar" if "monitor_tray" not in s else
            "tray" if s["monitor_tray"] else "quit")
        self.close_mode.setCurrentIndex(max(0, self.close_mode.findData(saved_mode)))
        self.close_mode.currentIndexChanged.connect(
            lambda *_: (settings.update(monitor_close=self.close_mode.currentData()),
                        ctl.changed.emit()))
        row.addWidget(self.close_mode, 1)
        fl.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("When netstab opens:"))
        self.autostart = QCheckBox("start monitoring this target")
        self.autostart.setChecked(s.get("monitor_autostart", False))
        self.autostart.toggled.connect(lambda on: settings.update(monitor_autostart=on))
        self.start_min = QCheckBox("start minimised")
        self.start_min.setToolTip("Opens straight onto the taskbar (or into the tray, "
                                  "if you chose the tray above)")
        self.start_min.setChecked(s.get("start_minimized", False))
        self.start_min.toggled.connect(lambda on: settings.update(start_minimized=on))
        row.addWidget(self.autostart)
        row.addWidget(self.start_min)
        row.addStretch(1)
        fl.addLayout(row)

        row = QHBoxLayout()
        self.btn_start = QPushButton("START MONITOR")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self._start_or_stop)
        row.addWidget(self.btn_start, 2)
        self.btn_pause = QPushButton("Pause")
        self.btn_pause.clicked.connect(ctl.toggle_pause)
        row.addWidget(self.btn_pause, 1)
        self.btn_mini = QPushButton("Mini window")
        self.btn_mini.setToolTip("A small window that stays on top of other windows")
        self.btn_mini.clicked.connect(lambda: window.toggle_mini())
        row.addWidget(self.btn_mini, 1)
        fl.addLayout(row)
        lay.addWidget(f)

        self.status = QLabel()
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        self.tiles = ResponsiveRow(min_item_width=150)
        self.t_now = Tile("Ping now")
        self.t_avg = Tile("Average")
        self.t_jit = Tile("Jitter")
        self.t_loss = Tile("Packet loss")
        self.t_range = Tile("Best / worst")
        for t in (self.t_now, self.t_avg, self.t_jit, self.t_loss, self.t_range):
            self.tiles.add(t)
        lay.addWidget(self.tiles)

        f, fl = _panel()
        row = QHBoxLayout()
        row.addWidget(_heading("Live ping"), 1)
        self.win_group = QButtonGroup(self)
        for secs, text in WINDOWS:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(secs == self.window_s)
            b.setProperty("secs", secs)
            self.win_group.addButton(b)
            row.addWidget(b)
        self.win_group.buttonClicked.connect(self._window_changed)
        fl.addLayout(row)
        self.chart = LatencyChart(C)
        self.chart.set_rolling(self.window_s)
        self.chart.setMinimumHeight(200)
        fl.addWidget(self.chart, 1)
        note = QLabel("The mini window stays on top of other windows. Over a game it "
                      "shows in borderless or windowed mode, not exclusive fullscreen. "
                      "The monitor pauses by itself while a stability or detailed "
                      "test runs.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        fl.addWidget(note)
        lay.addWidget(f, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)
        ctl.changed.connect(self._state_changed)
        ctl.failed.connect(lambda msg: self.status.setText(f"⚠ {msg}"))
        self._fill_servers()
        self._state_changed()

    # -- helpers -------------------------------------------------------------------
    def _fill_servers(self) -> None:
        key = self.game.currentData()
        keep = self.server.currentData() or settings.load().get(
            "monitor_target", {}).get("server", "auto")
        self.server.clear()
        self.server.addItem("Best ping (auto)", "auto")
        if self.win.servers_for == key:
            for s in self.win.servers:
                if s.ping_ms is not None:
                    self.server.addItem(s.label, s.code)
        else:
            self.server.setToolTip("Other servers appear here after a scan on the "
                                   "Stability test tab")
        i = self.server.findData(keep)
        self.server.setCurrentIndex(max(0, i))

    def servers_scanned(self) -> None:
        """Called by the main window after it scans a game's servers."""
        self._fill_servers()

    def spec(self) -> dict:
        if self.rb_game.isChecked():
            return {"kind": "game", "game": self.game.currentData(),
                    "server": self.server.currentData() or "auto"}
        i = self.host.findText(self.host.currentText())
        host = self.host.itemData(i) if i >= 0 else self.host.currentText()
        return {"kind": "host", "host": host or ""}

    def _start_or_stop(self) -> None:
        if self.ctl.running or self.ctl.starting:
            self.ctl.stop()
        else:
            self.status.setText("Starting...")
            self.ctl.start(self.spec())

    def _alerts_changed(self) -> None:
        self.ctl.set_alerts(self.alerts.isChecked(), self.limit.value())

    def _window_changed(self, b) -> None:
        self.window_s = b.property("secs")
        self.chart.set_rolling(self.window_s)
        self.refresh()

    def _state_changed(self) -> None:
        live = self.ctl.running or self.ctl.starting
        self.btn_start.setText("STOP MONITOR" if live else "START MONITOR")
        self.btn_pause.setEnabled(self.ctl.running and not self.ctl.test_paused)
        self.btn_pause.setText("Resume" if self.ctl.user_paused else "Pause")
        for w in (self.rb_game, self.rb_host, self.game, self.server, self.host):
            w.setEnabled(not live)
        if self.ctl.running and not self.timer.isActive():
            self.timer.start()
            self.chart.set_rolling(self.window_s)
        elif not self.ctl.running:
            self.timer.stop()
        if not self.ctl.starting and not self.status.text().startswith("⚠") \
                or self.ctl.running:
            self.status.setText(self.ctl.status_text())
        self.refresh()

    def apply_theme(self) -> None:
        self.chart.update()
        self.refresh()

    def refresh(self) -> None:
        mon = self.ctl.mon
        if not mon:
            for t in (self.t_now, self.t_avg, self.t_jit, self.t_loss, self.t_range):
                t.set("-")
            return
        if not self.isVisible():
            return
        hist = mon.history(self.window_s)
        st = monitor.summarize(hist)
        self.t_now.set("lost" if st.last_ms is None and st.count else _fmt(st.last_ms),
                       "PAUSED" if self.ctl.paused else "",
                       C.BAD if st.last_ms is None and st.count else _ping_color(st.last_ms))
        span = next(t for s, t in WINDOWS if s == self.window_s)
        self.t_avg.set(_fmt(st.avg_ms), f"last {span}", _ping_color(st.avg_ms))
        self.t_jit.set(_fmt(st.jitter_ms, nd=1), "",
                       None if st.jitter_ms is None else
                       C.GOOD if st.jitter_ms < 4 else C.WARN if st.jitter_ms < 10 else C.BAD)
        self.t_loss.set(_fmt(st.loss_pct, "%", 1), f"{st.count} pings",
                        C.GOOD if st.loss_pct == 0 else C.WARN if st.loss_pct < 2 else C.BAD)
        self.t_range.set(f"{_fmt(st.min_ms, '')} / {_fmt(st.max_ms, '')}", "ms")
        if hist:
            start = hist[-1][0] - self.window_s
            xs = [t - start for t, r in hist if r is not None]
            ys = [r for _, r in hist if r is not None]
            loss = [t - start for t, r in hist if r is None]
            sec: dict[int, list[float]] = {}
            for x, y in zip(xs, ys):
                sec.setdefault(int(x / 5), []).append(y)  # 5 s average
            avg = [(k * 5 + 2.5, sum(v) / len(v)) for k, v in sorted(sec.items())]
            self.chart.set_data(xs, ys, avg, loss)
            self.chart.set_median(st.median_ms)


# ----------------------------------------------------------------- mini window --
class MiniMonitor(QWidget):
    """Small always-on-top window with the live ping. Drag to move,
    right-click for options, double-click to open netstab."""

    W, H = 260, 112

    def __init__(self, ctl: MonitorController, window):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.ctl, self.win = ctl, window
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("netstab mini")
        self.setFixedSize(self.W, self.H)
        s = settings.load()
        self.setWindowOpacity(s.get("mini_opacity", 0.92))
        pos = s.get("mini_pos")
        if pos:
            self.move(QPoint(*pos))
        else:  # first time: top-right corner, out of the way of the game
            area = window.screen().availableGeometry()
            self.move(area.right() - self.W - 24, area.top() + 24)
        self._drag: QPoint | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.update)
        ctl.changed.connect(self.update)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._keep_on_screen()
        self.timer.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self.timer.stop()

    def _keep_on_screen(self) -> None:
        screen = self.screen() or self.win.screen()
        area = screen.availableGeometry()
        if not area.contains(self.frameGeometry()):
            self.move(area.right() - self.W - 24, area.top() + 24)

    # -- painting --------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(C.BORDER), 1))
        p.setBrush(QColor(C.PANEL))
        p.drawRoundedRect(r, 10, 10)

        mon = self.ctl.mon
        st = monitor.summarize(mon.history(60)) if mon else None
        f = QFont("Segoe UI")
        f.setPixelSize(11)
        p.setFont(f)
        p.setPen(QColor(C.MUTED))
        title = self.ctl.label if mon else "netstab live monitor"
        p.drawText(QRectF(12, 6, self.W - 24, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(title, Qt.ElideRight, self.W - 24))

        if not mon:
            p.setPen(QColor(C.TEXT))
            p.drawText(QRectF(12, 26, self.W - 24, 60), Qt.AlignLeft | Qt.TextWordWrap,
                       "Starting..." if self.ctl.starting else
                       "Not monitoring. Right-click, or open netstab to start.")
            p.end()
            return

        lost = st.count and st.last_ms is None
        big = "LOST" if lost else _fmt(st.last_ms, "")
        f.setPixelSize(30)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(C.BAD if lost else _ping_color(st.last_ms)))
        p.drawText(QRectF(12, 22, 140, 38), Qt.AlignLeft | Qt.AlignVCenter, big)
        if not lost:
            bw = p.fontMetrics().horizontalAdvance(big)
            f.setPixelSize(13)
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor(C.MUTED))
            p.drawText(QRectF(16 + bw, 34, 30, 22), Qt.AlignLeft | Qt.AlignVCenter, "ms")

        f.setPixelSize(11)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(C.WARN if self.ctl.paused else C.MUTED))
        right = ("PAUSED" if self.ctl.paused else
                 f"avg {_fmt(st.avg_ms, '')}  jit {_fmt(st.jitter_ms, '', 1)}\n"
                 f"loss {_fmt(st.loss_pct, '%', 1)} (1 min)")
        p.drawText(QRectF(130, 24, self.W - 142, 36), Qt.AlignRight | Qt.AlignVCenter, right)

        draw_sparkline(p, QRectF(12, 66, self.W - 24, self.H - 76), mon.history(60), 60)
        p.end()

    # -- interaction -----------------------------------------------------------
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e) -> None:
        if self._drag is not None:
            self._drag = None
            settings.update(mini_pos=[self.x(), self.y()])

    def mouseDoubleClickEvent(self, e) -> None:
        self.win.show_from_background()

    def contextMenuEvent(self, e) -> None:
        self.build_menu().exec(e.globalPos())

    def build_menu(self) -> QMenu:
        m = QMenu(self)
        m.addAction("Open netstab", self.win.show_from_background)
        if self.ctl.running:
            m.addAction("Resume" if self.ctl.user_paused else "Pause",
                        self.ctl.toggle_pause).setEnabled(not self.ctl.test_paused)
            m.addAction("Stop monitoring", self.ctl.stop)
        else:
            m.addAction("Start monitoring", self.win.start_monitor_from_saved)
        see = m.addMenu("See-through")
        grp = QActionGroup(see)
        for pct in (100, 90, 75, 60, 45):
            a = QAction(f"{100 - pct}%" if pct < 100 else "Off", see, checkable=True)
            a.setChecked(abs(self.windowOpacity() - pct / 100) < 0.02)
            a.triggered.connect(lambda _=False, v=pct / 100: self._set_opacity(v))
            grp.addAction(a)
            see.addAction(a)
        m.addSeparator()
        m.addAction("Close mini window", self.win.toggle_mini)
        return m

    def _set_opacity(self, v: float) -> None:
        self.setWindowOpacity(v)
        settings.update(mini_opacity=v)


# ------------------------------------------------------------------------ tray --
class TrayController(QObject):
    """Tray icon with the live ping; menu to open netstab, mini window, quit."""

    def __init__(self, ctl: MonitorController, window):
        super().__init__()
        self.ctl, self.win = ctl, window
        self.tray = QSystemTrayIcon(tray_icon_for(None, "off"))
        self.tray.setToolTip("netstab")
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.menu.aboutToShow.connect(self._build_menu)
        self.tray.activated.connect(self._activated)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        ctl.changed.connect(self.sync)
        ctl.alert.connect(self.notify)

    def _build_menu(self) -> None:
        m = self.menu
        m.clear()
        m.addAction("Open netstab", self.win.show_from_background)
        m.addAction("Hide mini window" if self.win.mini_visible() else "Show mini window",
                    self.win.toggle_mini)
        if self.ctl.running:
            m.addAction("Resume" if self.ctl.user_paused else "Pause",
                        self.ctl.toggle_pause).setEnabled(not self.ctl.test_paused)
            m.addAction("Stop monitoring", self.ctl.stop)
        m.addSeparator()
        m.addAction("Quit netstab", self.win.quit_app)

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.win.show_from_background()

    def sync(self) -> None:
        """Visible while monitoring or while the window is hidden."""
        # While monitoring, the tray is used when chosen; it's also the way back
        # whenever the window is hidden.
        mode = self.win.monitor_tab.close_mode.currentData()
        want = ((self.ctl.running or self.ctl.starting) and mode == "tray")             or not self.win.isVisible()
        self.tray.setVisible(want and QSystemTrayIcon.isSystemTrayAvailable())
        if self.ctl.running and not self.timer.isActive():
            self.timer.start()
        elif not self.ctl.running:
            self.timer.stop()
        self.refresh()

    def refresh(self) -> None:
        mon = self.ctl.mon
        if not mon:
            self.tray.setIcon(tray_icon_for(None, "off"))
            self.tray.setToolTip("netstab - not monitoring")
            return
        st = mon.stats(10)
        state = "paused" if self.ctl.paused else "live"
        self.tray.setIcon(tray_icon_for(st.last_ms, state))
        now = "lost" if st.count and st.last_ms is None else _fmt(st.last_ms)
        self.tray.setToolTip(f"netstab - {self.ctl.label}\n"
                             f"{'Paused' if self.ctl.paused else 'Ping ' + now}, "
                             f"loss {_fmt(mon.stats(60).loss_pct, '%', 1)} (1 min)")

    def notify(self, text: str) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.alert(self.win)  # flashes the taskbar button too
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if not self.tray.isVisible():
            # Notifications need a tray icon: show it just for this one.
            self.tray.setVisible(True)
            QTimer.singleShot(9000, self.sync)
        self.tray.showMessage("netstab", text, QSystemTrayIcon.Warning, 6000)

    def message(self, text: str) -> None:
        if self.tray.isVisible():
            self.tray.showMessage("netstab", text, tray_icon_for(None, "off"), 6000)


# --------------------------------------------------------------------- taskbar --
class TaskbarController(QObject):
    """Shows the live ping on netstab's own taskbar button: the number as the
    button's icon and a green/yellow/red strip under it."""

    def __init__(self, ctl: MonitorController, window, app_icon: QIcon):
        super().__init__()
        self.ctl, self.win, self.app_icon = ctl, window, app_icon
        self.button = None  # created once the window has a taskbar button
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        ctl.changed.connect(self.sync)
        self._showing = False

    @property
    def enabled(self) -> bool:
        return self.win.monitor_tab.on_taskbar.isChecked()

    def sync(self) -> None:
        if self.ctl.running and self.enabled:
            if not self.timer.isActive():
                self.timer.start()
            self.refresh()
        else:
            self.timer.stop()
            self.restore()

    def _ensure_button(self):
        if self.button is None and self.win.isVisible():
            from .taskbar import TaskbarButton
            self.button = TaskbarButton(int(self.win.winId()))
        return self.button

    def refresh(self) -> None:
        mon = self.ctl.mon
        if not (mon and self.enabled):
            return self.restore()
        st = mon.stats(5)
        lost = bool(st.count) and st.last_ms is None
        state = "paused" if self.ctl.paused else "live"
        self.win.setWindowIcon(tray_icon_for(None if lost else st.last_ms, state))
        self._showing = True
        from . import taskbar
        b = self._ensure_button()
        if b is not None:
            color = _ping_color(st.last_ms)
            b.set_state(taskbar.NOPROGRESS if self.ctl.paused or not st.count else
                        taskbar.ERROR if lost or color == C.BAD else
                        taskbar.NORMAL if color in (C.GOOD, C.OK) else taskbar.PAUSED)
        tip = (f"{self.ctl.label}: " +
               ("paused" if self.ctl.paused else "lost" if lost else _fmt(st.last_ms)))
        self.win.setWindowTitle(f"{_fmt(st.last_ms) if not lost else 'lost'} - "
                                f"{self.win.base_title}" if not self.ctl.paused
                                else self.win.base_title)
        self.win.setToolTip(tip)

    def restore(self) -> None:
        if not self._showing:
            return
        self._showing = False
        self.win.setWindowIcon(self.app_icon)
        self.win.setWindowTitle(self.win.base_title)
        if self.button is not None:
            self.button.clear()
