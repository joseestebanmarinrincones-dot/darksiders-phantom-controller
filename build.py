"""Compila MandoFantasma.exe en un unico archivo para Windows."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = (ROOT / "version.txt").read_text(encoding="utf-8").strip()
VIGEM_INSTALLER = "ViGEmBus_1.22.0_x64_x86_arm64.exe"
VIGEM_URL = (
    "https://github.com/nefarius/ViGEmBus/releases/download/"
    f"v1.22.0/{VIGEM_INSTALLER}"
)
VIGEM_SHA256 = "89220a7865076b342892f98865f3499fb7c4cfd673159e89d352c360fd014c6a"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_installer() -> Path:
    vendor = ROOT / "vendor"
    vendor.mkdir(exist_ok=True)
    installer = vendor / VIGEM_INSTALLER
    if installer.is_file() and sha256(installer).lower() == VIGEM_SHA256:
        return installer
    if installer.exists():
        installer.unlink()
    print("Descargando instalador de ViGEmBus...")
    request = urllib.request.Request(
        VIGEM_URL, headers={"User-Agent": "MandoFantasma-build"}
    )
    with urllib.request.urlopen(request, timeout=60) as response, installer.open(
        "wb"
    ) as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    actual = sha256(installer).lower()
    if actual != VIGEM_SHA256:
        raise SystemExit(
            f"El instalador de ViGEmBus no coincide con el hash esperado.\n"
            f"Esperado: {VIGEM_SHA256}\nObtenido: {actual}"
        )
    return installer


def write_version_info() -> Path:
    build_dir = ROOT / "build"
    build_dir.mkdir(exist_ok=True)
    parts = [int(part) for part in VERSION.split(".")]
    if len(parts) != 3:
        raise SystemExit("version.txt debe tener el formato X.Y.Z")
    file_version = tuple((parts + [0])[:4])
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={file_version},
    prodvers={file_version},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '0c0a',
        [
          StringStruct('CompanyName', 'Mando Fantasma'),
          StringStruct('FileDescription', 'Mando Fantasma - Controlador virtual para Steam'),
          StringStruct('FileVersion', '{VERSION}.0'),
          StringStruct('InternalName', 'MandoFantasma'),
          StringStruct('LegalCopyright', 'Copyright (c) 2026 - Licencia MIT'),
          StringStruct('OriginalFilename', 'MandoFantasma.exe'),
          StringStruct('ProductName', 'Mando Fantasma'),
          StringStruct('ProductVersion', '{VERSION}'),
          StringStruct('Comments', 'Controlador Xbox 360 virtual para Steam con actualizaciones automaticas')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [3082, 1200])])
  ]
)
"""
    path = build_dir / "version_info.txt"
    path.write_text(content, encoding="utf-8")
    return path


def main() -> None:
    installer = ensure_installer()
    version_info = write_version_info()
    separator = os.pathsep

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--noupx",
        "--name",
        "MandoFantasma",
        "--icon",
        str(ROOT / "assets" / "icon.ico"),
        "--add-data",
        f"{installer}{separator}vendor",
        "--add-data",
        f"{ROOT / 'assets'}{separator}assets",
        "--add-data",
        f"{ROOT / 'version.txt'}{separator}.",
        "--version-file",
        str(version_info),
        "--collect-all",
        "vgamepad",
        "--log-level",
        "WARN",
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build"),
        "--specpath",
        str(ROOT / "build"),
        str(ROOT / "app.py"),
    ]
    print("Compilando MandoFantasma", VERSION, "...")
    subprocess.run(command, check=True, cwd=ROOT)

    exe = ROOT / "dist" / "MandoFantasma.exe"
    if not exe.is_file():
        raise SystemExit("La compilacion termino sin generar el ejecutable.")
    size_mb = exe.stat().st_size / (1024 * 1024)
    print()
    print(f"Ejecutable: {exe}")
    print(f"Version:    {VERSION}")
    print(f"Tamano:     {size_mb:.1f} MB")
    print(f"SHA256:     {sha256(exe).upper()}")


if __name__ == "__main__":
    main()
