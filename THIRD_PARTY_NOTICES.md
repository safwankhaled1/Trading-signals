# Third-party dependencies

Gold Signal Desk uses Python, Qt for Python / PySide6, Telethon, MetaTrader5,
psutil, openpyxl and their dependencies. This file is an attribution notice,
not a grant of rights to the application's own source code.

- PySide6 / Qt: LGPLv3, GPLv3 or commercial licensing, depending on the module.
  The application uses Qt Core, Gui, Qml, Quick, Quick Controls, Widgets and
  PrintSupport. Qt DLLs are shipped separately in the onedir distribution.
  https://doc.qt.io/qtforpython-6/licenses.html
  https://www.qt.io/licensing/open-source-lgpl-obligations
- Telethon: MIT. https://github.com/LonamiWebs/Telethon
- MetaTrader5 Python package: MetaQuotes' package license and broker/platform terms.
  https://pypi.org/project/MetaTrader5/
- psutil: BSD-3-Clause. https://github.com/giampaolo/psutil
- openpyxl: MIT. https://openpyxl.readthedocs.io/
- Python: PSF license. https://docs.python.org/3/license.html
- PyInstaller: GPL with bootloader exception. https://pyinstaller.org/en/stable/license.html

Exact installed versions are recorded in requirements-lock.txt. Bundled license
files are retained by the packaging hooks. Verify applicable redistribution
obligations before publicly distributing a commercial release.

Windows Segoe UI fonts are loaded from the user's Windows installation; they
are not copied or redistributed with this application.
