"""Signs an exe with signtool, using a certificate from the Windows store.

    python tools/sign_exe.py --thumbprint <SHA1> [--timestamp URL|none]
                             [--allow-untrusted] [file]   (default dist/netstab.exe)

Works with any code signing certificate Windows can see in the user's
certificate store, e.g. a Certum SimplySign cloud certificate (it appears as a
virtual smart card) or another CA's OV certificate on a hardware token.

Needs signtool.exe from the Windows SDK.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMESTAMP = "http://time.certum.pl"  # any RFC 3161 server works
PROJECT_URL = "https://github.com/naspaw/netstab"


def find_signtool() -> Path | None:
    found = shutil.which("signtool")
    if found:
        return Path(found)
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) \
        / "Windows Kits" / "10" / "bin"
    candidates = sorted(kits.glob("10.*/x64/signtool.exe"), reverse=True)
    return candidates[0] if candidates else None


def sign(exe: Path, thumbprint: str, timestamp: str | None, signtool: Path) -> None:
    cmd = [str(signtool), "sign", "/sha1", thumbprint, "/fd", "SHA256",
           "/d", "netstab", "/du", PROJECT_URL]
    if timestamp:
        # Timestamped signatures stay valid after the certificate expires.
        cmd += ["/tr", timestamp, "/td", "SHA256"]
    subprocess.run(cmd + [str(exe)], check=True)


def is_trusted(exe: Path, signtool: Path) -> bool:
    """True if Windows trusts the signature (chain to a trusted root CA)."""
    return subprocess.run([str(signtool), "verify", "/pa", str(exe)],
                          capture_output=True).returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", nargs="?", default=str(ROOT / "dist" / "netstab.exe"))
    ap.add_argument("--thumbprint", required=True, help="SHA1 thumbprint of the cert")
    ap.add_argument("--timestamp", default=DEFAULT_TIMESTAMP,
                    help="RFC 3161 timestamp URL, or 'none'")
    ap.add_argument("--allow-untrusted", action="store_true",
                    help="don't fail on an untrusted (e.g. test) certificate")
    a = ap.parse_args()

    signtool = find_signtool()
    if signtool is None:
        print("signtool.exe not found: install the Windows SDK (signing tools).")
        return 2
    exe = Path(a.file)
    sign(exe, a.thumbprint.replace(" ", ""),
         None if a.timestamp.lower() == "none" else a.timestamp, signtool)
    if is_trusted(exe, signtool):
        print(f"Signed {exe}: signature is trusted by Windows.")
        return 0
    print(f"Signed {exe}, but Windows does NOT trust the certificate"
          " (expected for a test certificate).")
    return 0 if a.allow_untrusted else 1


if __name__ == "__main__":
    sys.exit(main())
