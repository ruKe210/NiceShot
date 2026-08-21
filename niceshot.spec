# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

project = Path(SPECPATH)

datas = [(str(project / "app" / "assets"), "app/assets")]
binaries = []
hiddenimports = [
    "rapidocr.main",
    "rapidocr.cal_rec_boxes",
    "rapidocr.ch_ppocr_cls",
    "rapidocr.ch_ppocr_det",
    "rapidocr.ch_ppocr_rec",
    "rapidocr.inference_engine.onnxruntime",
    "rapidocr.inference_engine.onnxruntime.main",
    "win32com.client",
    "pythoncom",
    "pywintypes",
    "PIL",
    "PIL.Image",
    "numpy",
    "requests",
    "mss",
    "cv2",
    "omegaconf",
    "yaml",
]

for pkg in ("rapidocr", "onnxruntime", "cv2"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

binaries += collect_dynamic_libs("onnxruntime")

for base in (Path(sys.base_prefix), Path(sys.prefix)):
    for folder in (base / "Library" / "bin", base / "DLLs", base / "bin"):
        for name in ("libssl-1_1-x64.dll", "libcrypto-1_1-x64.dll"):
            dll = folder / name
            if dll.is_file() and not any(Path(item[0]).name == name for item in binaries):
                binaries.append((str(dll), "."))

a = Analysis(
    [str(project / "main.py")],
    pathex=[str(project)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "accelerate",
        "pandas",
        "scipy",
        "sklearn",
        "skimage",
        "numba",
        "llvmlite",
        "matplotlib",
        "IPython",
        "jupyter",
        "xformers",
        "timm",
        "uvicorn",
        "fsspec",
        "paddle",
        "paddlepaddle",
        "tensorrt",
        "openvino",
        "tkinter",
        "rapidocr.inference_engine.pytorch",
        "rapidocr.inference_engine.paddle",
        "rapidocr.inference_engine.tensorrt",
        "rapidocr.inference_engine.openvino",
        "rapidocr.inference_engine.mnn",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NiceShot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(project / "app" / "assets" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="NiceShot",
)
