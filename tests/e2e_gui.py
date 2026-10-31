"""End-to-end GUI test against the real network (not run by pytest).

    python tests/e2e_gui.py [output_dir] [--no-data]

--no-data skips the download/upload load test (for metered connections).

Drives the real window: a stopped-early run, a full 30 s CS2 test, the
detailed-test options dialog, metered approval (with a 10 MB cap), theme
switching, the Compare tab and report export, then the other server modes
(max ping limit, chosen server), the Steam profile and Compare rename/delete.
Saves screenshots and prints
PASS/FAIL per check. Settings and history go to a temp folder, so the user's
own files are untouched.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QInputDialog,  # noqa: E402
                               QMessageBox)

from netstab import compare, gui, settings  # noqa: E402

NO_DATA = "--no-data" in sys.argv
args = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT = Path(args[0] if args else tempfile.mkdtemp(prefix="netstab-e2e-"))
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="netstab-e2e-data-"))
compare.HISTORY_PATH = TMP / "history.json"
settings._PATH = TMP / "settings.json"
settings.update(theme="light")

# A previous test "on another network", so the Compare tab has two networks.
seed = compare.Run("Phone hotspot", "Phone hotspot", "2026-10-30T09:00:00", "cs2",
                   "Counter-Strike 2", "fra", "Frankfurt (Germany)", 30,
                   {"sent": 1920, "received": 1890, "loss_pct": 1.56, "min_ms": 48,
                    "avg_ms": 71, "median_ms": 66, "p95_ms": 110, "p99_ms": 160,
                    "max_ms": 240, "stddev_ms": 18, "jitter_ms": 14.2, "spikes": 41,
                    "spike_threshold_ms": 96, "longest_loss_burst": 9, "score": 48,
                    "grade": "D", "findings": []})
compare.save([seed])

results: list[tuple[str, bool, str]] = []
log: dict = {"questions": [], "warnings": []}


def check(name: str, ok: bool, info: str = "") -> None:
    results.append((name, bool(ok), info))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


def shot(name: str, widget=None) -> None:
    (widget or w).grab().save(str(OUT / f"{name}.png"))


answer = {"question": QMessageBox.Yes}  # what the patched Yes/No dialogs reply
QMessageBox.question = staticmethod(
    lambda parent, title, text, *a, **k: (log["questions"].append(title),
                                          answer["question"])[1])
QMessageBox.warning = staticmethod(
    lambda parent, title, text, *a, **k: (log["warnings"].append(text),
                                          QMessageBox.Yes)[1])
EXPORT = OUT / "report.json"
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(EXPORT), ""))


def fake_exec(dlg: gui.DetailOptionsDialog) -> int:
    dlg.show()
    QApplication.processEvents()
    check("options dialog: metered connection preselects no data use",
          not dlg.cost.metered or dlg.options().load == "off", dlg.cost.description)
    for b in dlg.load_group.buttons():
        b.setChecked(b.property("key") == ("off" if NO_DATA else "standard"))
    dlg.cap_on.setChecked(True)
    dlg.cap.setValue(10)
    QApplication.processEvents()
    shot("options_dialog", dlg)
    check("options dialog: estimate respects cap", dlg.data_mb() <= 10,
          dlg.estimate.text())
    dlg.hide()
    return QDialog.Accepted


gui.DetailOptionsDialog.exec = fake_exec

app = QApplication(sys.argv)
app.setStyleSheet(gui.make_style())
w = gui.MainWindow()
w.resize(1360, 900)
w.show()

steps = []


def step(fn):
    steps.append(fn)
    return fn


def run_steps():
    deadline = [time.time() + 900]

    def next_step():
        if not steps:
            finish()
            return
        if time.time() > deadline[0]:
            check("finished in time", False)
            finish()
            return
        done = steps[0]()
        if done is not False:
            steps.pop(0)
        QTimer.singleShot(500, next_step)

    QTimer.singleShot(500, next_step)


state = {}


@step
def stopped_early():
    w.dur_group.button(60).setChecked(True)
    w._start_or_stop()
    state["t0"] = time.time()


@step
def wait_running():
    if w.test is None or w.test.sent < 64:
        return False
    w._start_or_stop()  # STOP


@step
def wait_stopped():
    if w.test_done_at is None or w.timer.isActive():
        return False
    check("stop button ends the test", w.test.cancelled, w.status.text())
    check("short stopped run is not saved", len(w.runs) == 1)
    check("stopped test doesn't offer a detailed test", not log["questions"])
    w.dur_group.button(30).setChecked(True)
    w._start_or_stop()


@step
def mid_test():
    if w.test is None or w.test.elapsed() < 12:
        return False
    shot("1_live_light")


@step
def wait_detail_done():
    if w.detail is not None or w.detail_result is None:
        return False
    s = w.summary
    check("30 s test sent 64 packets/s", s.sent >= 64 * 29, f"{s.sent} sent")
    check("summary has a grade", s.grade in "ABCDF", f"{s.grade} {s.score}")
    check("asked about detailed test", "Detailed network test" in log["questions"])
    metered = bool(w.cost and w.cost.metered)
    if NO_DATA:
        check("no approval needed without data use", not log["warnings"])
    else:
        check("metered approval shown when metered", (not metered) or bool(log["warnings"]),
              (log["warnings"] or ["not metered"])[0][:80])
    r = w.detail_result
    check("detailed: route traced", len(r.hops) >= 2, f"{len(r.hops)} hops")
    check("detailed: hop stats collected", any(h.rtts for h in r.hops))
    check("detailed: MTU measured", r.mtu is not None, str(r.mtu))
    check("detailed: data cap respected", r.data_mb <= (0.01 if NO_DATA else 10),
          f"{r.data_mb:.1f} MB")
    if not NO_DATA:
        check("detailed: bufferbloat graded", r.bloat_grade is not None, str(r.bloat_grade))
    v = w.verdict_data
    check("verdict built with aspects", v and len(v.aspects) >= 4,
          v.headline if v else "")
    if not NO_DATA:
        check("verdict includes busy-line aspect after detailed test",
              v and any(a.name == "When the line is busy" for a in v.aspects))
    check("run saved for comparison", len(w.runs) == 2 and w.runs[-1].detailed)
    check("compare shows both networks", len(w.compare_ids) == 2)
    check("theme still light when results show", gui.C.name == "light",
          w.theme_combo.currentText())
    w.tabs.setCurrentIndex(0)
    shot("2_result_light")
    w.tabs.setCurrentIndex(1)
    shot("3_detail_light")
    w.tabs.setCurrentIndex(2)
    shot("4_compare_light")
    cmp = compare.compare([r for r in w.runs if r.id in w.compare_ids])
    check("comparison has a result", bool(cmp.summary), cmp.summary)


@step
def export_and_theme():
    w._export()
    data = json.loads(EXPORT.read_text(encoding="utf-8"))
    check("light theme active before switching", gui.C.name == "light", gui.C.name)
    check("export has summary, verdict and detailed",
          all(data.get(k) for k in ("summary", "verdict", "detailed", "rtts_ms")))
    w.theme_combo.setCurrentIndex(w.theme_combo.findData("dark"))
    QApplication.processEvents()
    check("theme switched to dark", gui.C.name == "dark")
    check("theme saved", settings.load().get("theme") == "dark")
    for i, name in enumerate(("5_result_dark", "6_detail_dark", "7_compare_dark")):
        w.tabs.setCurrentIndex(i)
        QApplication.processEvents()
        shot(name)
    d = gui.AboutDialog(w)
    d.show()
    QApplication.processEvents()
    shot("8_privacy", d)
    d.close()


@step
def max_ping_too_low():
    answer["question"] = QMessageBox.No  # no more detailed tests from here on
    w.tabs.setCurrentIndex(0)
    w.rb_max.setChecked(True)
    w.max_ping.setValue(25)
    before = w.test
    w._start_or_stop()
    check("max ping limit nobody meets: no test, clear message",
          w.test is before and "No server answered under 25 ms" in w.status.text(),
          w.status.text())


@step
def max_ping_ok():
    best = min(s.ping_ms for s in w.servers if s.ping_ms is not None)
    state["limit"] = int(best) + 30
    w.max_ping.setValue(state["limit"])
    w._start_or_stop()
    check("max ping limit picks a server under the limit",
          w.server.ping_ms <= state["limit"], f"{w.server.label} <= {state['limit']} ms")
    w._start_or_stop()  # stop; picking was the point


@step
def manual_server():
    if w.timer.isActive():
        return False
    w.rb_manual.setChecked(True)
    w.server_combo.setCurrentIndex(1)  # the second-best server
    state["manual"] = w.server_combo.currentData()
    w.dur_group.button(30).setChecked(True)
    w._start_or_stop()


@step
def wait_manual():
    if w.timer.isActive() or w.summary is None:
        return False
    check("chosen server is the one tested", w.server.code == state["manual"],
          w.server.label)
    check("chosen-server test completed", w.summary.sent >= 64 * 29, f"{w.summary.sent} sent")


@step
def steam_profile():
    steam = next(b for b in w.game_group.buttons() if b.property("key") == "steam")
    steam.setChecked(True)
    w.rb_auto.setChecked(True)
    w._start_or_stop()  # scans Steam's servers first, then tests


@step
def wait_steam():
    if not (w.tested and w.tested.key == "steam" and w.summary is not None
            and not w.timer.isActive()):
        return False
    check("Steam profile scanned its own server list", w.servers_for == "steam",
          w.server_info.text()[:80])
    check("Steam test ran at 30 packets/s", 29 * 30 <= w.summary.sent <= 31 * 30,
          f"{w.summary.sent} sent")
    check("Steam verdict built", w.verdict_data is not None and "STEAM" in w.verdict.title.text().upper(),
          w.verdict.title.text())
    check("Steam run saved", w.runs[-1].game == "steam")
    w.tabs.setCurrentIndex(0)
    shot("9_steam_result")


@step
def compare_rename_delete():
    w.tabs.setCurrentIndex(2)
    cmp = compare.compare([r for r in w.runs if r.id in w.compare_ids])
    check("compare warns when games differ",
          any("different games" in n for n in cmp.notes) or len({r.game for r in cmp.runs}) == 1,
          " ".join(cmp.notes)[:90])
    QInputDialog.getText = staticmethod(lambda *a, **k: ("Home Wi-Fi (renamed)", True))
    w.run_list.setCurrentRow(0)
    rid = w.run_list.currentItem().data(Qt.UserRole)
    w._rename_run()
    saved = {r.id: r.label for r in compare.load()}
    check("rename shows and is saved", saved.get(rid) == "Home Wi-Fi (renamed)"
          and w.run_list.item(0).text().startswith("Home Wi-Fi (renamed)"))
    shot("10_compare_renamed")
    answer["question"] = QMessageBox.Yes  # confirm the delete
    n = len(w.runs)
    w.run_list.setCurrentRow(0)
    w._delete_run()
    check("delete removes it everywhere", len(w.runs) == n - 1
          and len(compare.load()) == n - 1 and rid not in w.compare_ids)


def finish():
    ok = all(r[1] for r in results)
    print(f"\n{sum(r[1] for r in results)}/{len(results)} checks passed. "
          f"Screenshots: {OUT}", flush=True)
    app.exit(0 if ok else 1)


run_steps()
sys.exit(app.exec())
