# Third-party components

netstab is MIT licensed. It is built on these open-source projects, each under
its own license:

| Component | License | Used for |
|---|---|---|
| [Python](https://www.python.org/) | PSF License | Runtime |
| [Qt 6](https://www.qt.io/) / [PySide6](https://doc.qt.io/qtforpython/) | LGPL-3.0 | User interface |
| [Requests](https://requests.readthedocs.io/) | Apache-2.0 | HTTPS (server list, speed test) |
| [urllib3](https://urllib3.readthedocs.io/) | MIT | Used by Requests |
| [certifi](https://github.com/certifi/python-certifi) | MPL-2.0 | TLS root certificates |
| [idna](https://github.com/kjd/idna) | BSD-3-Clause | Used by Requests |
| [charset-normalizer](https://github.com/jawah/charset_normalizer) | MIT | Used by Requests |
| [PyWinRT](https://github.com/pywinrt/pywinrt) (`winrt-*`) | MIT | Metered-connection check |
| [PyInstaller](https://pyinstaller.org/) bootloader | GPL-2.0 with bootloader exception | Portable .exe |

## Qt / PySide6 (LGPL-3.0)

`netstab.exe` contains unmodified Qt and PySide6 libraries. They stay separate
DLLs: the exe unpacks them to a temporary folder and loads them dynamically at
startup. netstab's complete source code and build script are in this
repository, so you can rebuild the exe against your own modified Qt/PySide6,
as the LGPL requires (`python tools\build_exe.py`, see README).
Qt and PySide6 source code: <https://code.qt.io/> and
<https://code.qt.io/cgit/pyside/pyside-setup.git/>.

## External services

These are contacted at run time; they are not bundled:

- **Steam Web API** (`api.steampowered.com`) - public Valve server relay list.
- **Valve SDR relays** - ICMP echo (ping) only.
- **Cloudflare speed test** (`speed.cloudflare.com`) - only when the optional
  download/upload test is enabled.
