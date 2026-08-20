# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec (AGENT_SPEC.md §7).

Produces standalone single-file executables so the agent can be deployed to a
server with no Python runtime installed:

    pip install pyinstaller
    pyinstaller build_pyinstaller.spec

Outputs (in ./dist):
  - monitoring-agent            the agent (all platforms)
  - monitoring-agent-service    the Windows Service host (Windows builds only)

Build on each target OS to get a native binary — PyInstaller does not
cross-compile.
"""

import sys

block_cipher = None

# --- agent binary (all platforms) ----------------------------------------- #
agent_a = Analysis(
    ["agent/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=["psutil", "requests"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
agent_pyz = PYZ(agent_a.pure, cipher=block_cipher)
agent_exe = EXE(
    agent_pyz,
    agent_a.scripts,
    agent_a.binaries,
    agent_a.datas,
    [],
    name="monitoring-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

# --- Windows Service host (Windows only) ----------------------------------- #
if sys.platform == "win32":
    svc_a = Analysis(
        ["agent/service/windows_service.py"],
        pathex=["."],
        binaries=[],
        datas=[],
        hiddenimports=[
            "psutil",
            "requests",
            "servicemanager",
            "win32event",
            "win32service",
            "win32serviceutil",
            "win32timezone",
        ],
        hookspath=[],
        runtime_hooks=[],
        excludes=[],
        cipher=block_cipher,
        noarchive=False,
    )
    svc_pyz = PYZ(svc_a.pure, cipher=block_cipher)
    svc_exe = EXE(
        svc_pyz,
        svc_a.scripts,
        svc_a.binaries,
        svc_a.datas,
        [],
        name="monitoring-agent-service",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
    )
