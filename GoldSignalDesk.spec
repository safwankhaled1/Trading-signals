# Build an onedir package so Qt libraries remain separately replaceable.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH)
datas = [(str(root / "signaldesk" / "qml"), "signaldesk/qml"),
         (str(root / "README.md"), "."), (str(root / "THIRD_PARTY_NOTICES.md"), ".")]
datas += copy_metadata("PySide6") + copy_metadata("Telethon")
a = Analysis([str(root / "main.py")], pathex=[str(root)], binaries=[], datas=datas,
             hiddenimports=["MetaTrader5", "telethon", "telethon.sessions", "psutil", "openpyxl", "PySide6.QtPrintSupport"],
             excludes=["pytest", "tkinter"], noarchive=False)
# Qt uses Windows' ICU API (unversioned exports). A different ICU on the build
# machine's PATH can shadow it and make QtCore fail to import on launch.
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() not in {"icuuc.dll", "icudt78.dll"}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="GoldSignalDesk", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          version=str(root / "installer" / "version.txt"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="GoldSignalDesk-0.1.1")
