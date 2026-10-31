# PyInstaller spec for the portable one-file build. Run via tools/build_exe.py,
# which writes build/version_info.txt and passes the paths below.
# -*- mode: python -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - provided by PyInstaller
BUILD = ROOT / "build"

# Modules installed alongside but never used by the app.
EXCLUDES = [
    "tkinter", "matplotlib", "IPython", "jupyter", "notebook", "pytest", "pyflakes",
    "PyQt5", "PyQt6", "scipy", "pandas", "OpenGL", "PIL", "numpy", "pyqtgraph",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
    "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtPdf", "PySide6.QtNetwork", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtSvg",
    "PySide6.QtSvgWidgets", "PySide6.QtPrintSupport",
]

# Qt files PyInstaller's hooks pull in that this app never loads. Every byte
# here is unpacked on each launch of a one-file exe, so dropping them makes
# startup faster. build_exe.py runs the exe's --selftest, which catches anything
# removed by mistake.
DROP_FILES = {
    "opengl32sw.dll",  # software OpenGL fallback; we draw with QPainter
}
DROP_PREFIXES = ("qt6quick", "qt6qml", "qt6pdf", "qt6network", "qt6virtualkeyboard",
                 "qt6opengl", "qt6svg")
KEEP_PLUGINS = {
    "platforms": {"qwindows.dll"},
    "imageformats": {"qico.dll"},  # window icon; PNG support is built into QtGui
    "styles": None,  # None = keep all (native widget look)
}


def wanted(dest: str) -> bool:
    p = dest.replace("\\", "/").lower()
    name = p.rsplit("/", 1)[-1]
    if name in DROP_FILES or name.startswith(DROP_PREFIXES):
        return False
    if "/translations/" in p and p.endswith(".qm"):
        return False  # the UI is English only
    if "/plugins/" in p:
        group = p.split("/plugins/", 1)[1].split("/", 1)[0]
        if group not in KEEP_PLUGINS:
            return False
        keep = KEEP_PLUGINS[group]
        return keep is None or name in keep
    return True


a = Analysis(  # noqa: F821
    [str(ROOT / "netstab" / "__main__.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "netstab" / "assets"), "netstab/assets"),
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    ],
    hiddenimports=collect_submodules("winrt"),
    excludes=EXCLUDES,
    noarchive=False,
)
a.binaries = [b for b in a.binaries if wanted(b[0])]
a.datas = [d for d in a.datas if wanted(d[0])]

pyz = PYZ(a.pure)  # noqa: F821

# Shown by the launcher before unpacking starts; netstab/splash.py updates and
# closes it. The status line shows what's being unpacked, then app messages.
splash = Splash(  # noqa: F821
    str(BUILD / "splash.png"),
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(32, 276),  # matches SPLASH_TEXT_POS in make_icon.py
    text_size=10,
    text_color="#8a92a6",
    text_default="Starting...",
    minify_script=True,
    always_on_top=True,
)

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    splash,
    splash.binaries,
    a.binaries,
    a.datas,
    [],
    name="netstab",
    console=False,
    icon=str(ROOT / "netstab" / "assets" / "netstab.ico"),
    version=str(BUILD / "version_info.txt"),
    upx=False,
)
