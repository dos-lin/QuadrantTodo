# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['entry.py'],
    pathex=[],
    binaries=[],
    datas=[('quadrant_todo/styles.qss', 'quadrant_todo'), ('quadrant_todo/styles/dark.qss', 'quadrant_todo/styles'), ('assets/app.ico', 'assets')],
    hiddenimports=['PySide6.QtNetwork', 'PySide6.QtSvg'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6.QtWebEngine', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebChannel', 'PySide6.Qt3D', 'PySide6.QtCharts', 'PySide6.QtMultimedia', 'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtDesigner', 'PySide6.QtLocation', 'PySide6.QtBluetooth', 'PySide6.QtSerialPort', 'PySide6.QtPositioning', 'PySide6.QtNfc', 'PySide6.QtScxml', 'PySide6.QtSpeech', 'PySide6.QtSvgWidgets', 'PySide6.QtVirtualKeyboard', 'PySide6.QtRemoteObjects', 'PySide6.QtPdf'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='QuadrantTodo',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/app.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='QuadrantTodo',
)
