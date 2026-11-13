# PyInstaller build of Hey Buddy: a folder (dist/HeyBuddy) with HeyBuddy.exe and everything it
# needs, Python included. Build with:
#     python -m PyInstaller --noconfirm HeyBuddy.spec

a = Analysis(
    ["task_reminder.py"],
    datas=[(folder, folder) for folder in ("figure", "figure_minnal", "fonts", "sounds", "ui")],
    hiddenimports=["pystray._win32"],       # pystray picks its backend at run time
    excludes=["numpy", "pytest", "setuptools", "pip"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="HeyBuddy",
    icon="ui/app.ico",
    console=False,                          # no black console window
)
coll = COLLECT(exe, a.binaries, a.datas, name="HeyBuddy")
