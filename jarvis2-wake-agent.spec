# -*- mode: python ; coding: utf-8 -*-
# JARVIS2 全局后台唤醒 agent 打包（PyInstaller）
# 产物：desktop/src-tauri/binaries/jarvis2-wake-agent.exe
# 运行后由 Tauri 构建脚本重命名为 jarvis2-wake-agent-<triple>.exe 接入 externalBin。
from PyInstaller.utils.hooks import collect_all

distpath = 'desktop/src-tauri/binaries'
workpath = 'build/wake-agent'

datas = [('desktop/package/.env', '.')]
binaries = []
hiddenimports = [
    'backend.config',
    'backend.voice.edge',
    'backend.voice.kws',
    'backend.voice.local',
    'sounddevice',
    'sherpa_onnx',
]

for pkg in ('sherpa_onnx', 'sounddevice', 'edge_tts', 'numpy'):
    tmp_ret = collect_all(pkg)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]


a = Analysis(
    ['backend/wake_agent.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='jarvis2-wake-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 后台常驻，无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
