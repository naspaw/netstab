"""Builds the portable single-file app: dist/netstab.exe.

    python tools/build_exe.py [--no-ping] [--sign-thumbprint SHA1]

Everything (Python, Qt, libraries, icon) is packed into one .exe that runs
without installing anything. The packing rules live in tools/netstab.spec.
After building (and signing, if a certificate is given) the exe's own
--selftest is run against the real network. --no-ping skips the ping-based
checks, for cloud build machines that block ICMP.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from netstab import __version__  # noqa: E402

BUILD = ROOT / "build"
EXE = ROOT / "dist" / "netstab.exe"

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=({v}, 0), prodvers=({v}, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'netstab contributors'),
      StringStruct('FileDescription', 'netstab - game network stability test'),
      StringStruct('FileVersion', '{s}'),
      StringStruct('InternalName', 'netstab'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'netstab.exe'),
      StringStruct('ProductName', 'netstab'),
      StringStruct('ProductVersion', '{s}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def selftest(no_ping: bool = False) -> bool:
    """Run the built exe from an empty folder with nothing but Windows on PATH."""
    out = Path(tempfile.mkdtemp(prefix="netstab-selftest-"))
    # A normal Windows session's variables, minus any Python/dev tools on PATH;
    # app data goes to a throwaway folder so the user's history is untouched.
    keep = ("SYSTEMROOT", "WINDIR", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
            "USERNAME", "COMPUTERNAME", "PROGRAMDATA", "NUMBER_OF_PROCESSORS")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update(PATH=r"C:\Windows\System32;C:\Windows", LOCALAPPDATA=str(out / "appdata"),
               TEMP=str(out / "tmp"), TMP=str(out / "tmp"))
    (out / "tmp").mkdir()
    try:
        subprocess.run([str(EXE), "--selftest", str(out)] + (["--no-ping"] if no_ping else []),
                       cwd=out, env=env, timeout=300)
    except OSError as e:
        if getattr(e, "winerror", None) == 4551:
            # Smart App Control / WDAC refused the unsigned exe. Not a build
            # bug, but users with it switched on would be blocked the same way.
            print("  BLOCKED by Windows Application Control (Smart App Control is on).\n"
                  "  Windows refused to run this unsigned build, so it can't be tested\n"
                  "  here. Rebuild to get a different file, or sign the exe.")
            return False
        raise
    report = json.loads((out / "selftest.json").read_text(encoding="utf-8"))
    for r in report["results"]:
        print(f"  {'PASS' if r['ok'] else 'FAIL'} {r['check']}: {r['info'][:100]}")
    print(f"  screenshots: {out}")
    return report["ok"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Build and self-test dist/netstab.exe")
    ap.add_argument("--no-ping", action="store_true",
                    help="skip ping-based self-test checks (cloud build machines)")
    ap.add_argument("--sign-thumbprint", metavar="SHA1",
                    help="sign with this certificate (see tools/sign_exe.py)")
    args = ap.parse_args()

    BUILD.mkdir(exist_ok=True)
    # Always redraw: the splash carries the version number.
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)
    parts = [int(x) for x in __version__.split(".")] + [0] * 3
    (BUILD / "version_info.txt").write_text(
        VERSION_INFO.format(v=", ".join(map(str, parts[:3])), s=__version__),
        encoding="utf-8")

    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(ROOT / "dist"),
                    "--workpath", str(BUILD / "pyinstaller"),
                    str(ROOT / "tools" / "netstab.spec")], check=True, cwd=ROOT)
    print(f"\nBuilt {EXE} ({EXE.stat().st_size / 1e6:.1f} MB).")
    if args.sign_thumbprint:
        # Sign first, so the self-test runs on exactly the file that ships.
        subprocess.run([sys.executable, str(ROOT / "tools" / "sign_exe.py"),
                        "--thumbprint", args.sign_thumbprint, str(EXE)], check=True)
    print("Self-test:")
    ok = selftest(no_ping=args.no_ping)
    print("Self-test passed." if ok else "SELF-TEST FAILED - do not ship this build.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
