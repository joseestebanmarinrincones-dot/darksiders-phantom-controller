"""
Mando Fantasma
Controlador Xbox 360 virtual para Steam, con actualizaciones automaticas.

Hecho para Darksiders Genesis y cualquier juego que necesite detectar un mando.
"""

from __future__ import annotations

import ctypes
import gc
import hashlib
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

APP_NAME = "Mando Fantasma"
REPO = "joseestebanmarinrincones-dot/darksiders-phantom-controller"
REPO_URL = f"https://github.com/{REPO}"
RELEASES_URL = f"{REPO_URL}/releases"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
VIGEM_INSTALLER = "ViGEmBus_1.22.0_x64_x86_arm64.exe"
VIGEM_URL = (
    "https://github.com/nefarius/ViGEmBus/releases/download/"
    f"v1.22.0/{VIGEM_INSTALLER}"
)
VIGEM_SHA256 = "89220a7865076b342892f98865f3499fb7c4cfd673159e89d352c360fd014c6a"

WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000

BG = "#0b0e13"
CARD = "#141922"
CARD2 = "#1a2130"
BORDER = "#263041"
TEXT = "#edf4ff"
MUTED = "#8b98ab"
GREEN = "#3ddc97"
GREEN_LIGHT = "#63eab2"
GREEN_DARK = "#0a2a1f"
RED = "#ff7b7b"
RED_SOFT = "#2d171b"
YELLOW = "#f5c451"
BLUE = "#6ab2ff"

CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "MandoFantasma"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "error.log"

DEFAULT_CONFIG = {
    "controllers": 1,
    "auto_connect": False,
    "skipped_version": None,
}

_MUTEX_HANDLE = None
_ERROR_DIALOG_SHOWN = False


# ---------------------------------------------------------------------------
# Utilidades generales
# ---------------------------------------------------------------------------


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def read_version() -> str:
    try:
        value = resource_path("version.txt").read_text(encoding="utf-8").strip()
        if re.fullmatch(r"\d+\.\d+\.\d+", value):
            return value
    except Exception:
        pass
    return "1.0.0"


APP_VERSION = read_version()


def log_error(message: str) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
            handle.write(message.rstrip() + "\n")
    except Exception:
        pass


def load_config() -> dict:
    data = dict(DEFAULT_CONFIG)
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            controllers = raw.get("controllers", 1)
            if isinstance(controllers, int) and 1 <= controllers <= 4:
                data["controllers"] = controllers
            data["auto_connect"] = bool(raw.get("auto_connect", False))
            skipped = raw.get("skipped_version")
            if isinstance(skipped, str) or skipped is None:
                data["skipped_version"] = skipped
    except Exception:
        pass
    return data


def save_config(config: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        log_error(traceback.format_exc())


def parse_version(value: str) -> tuple[int, int, int]:
    match = re.match(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(value or ""))
    if not match:
        return (0, 0, 0)
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def enable_dpi_awareness() -> None:
    if not WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def get_ui_scale() -> float:
    if not WINDOWS:
        return 1.0
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return max(1.0, round(dpi / 96.0, 2))
    except Exception:
        return 1.0


def center_window(window: tk.Toplevel | tk.Tk, width: int, height: int, parent=None) -> None:
    window.update_idletasks()
    if parent is not None and parent.winfo_viewable():
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    else:
        x = (window.winfo_screenwidth() - width) // 2
        y = (window.winfo_screenheight() - height) // 3
    x = max(0, x)
    y = max(0, y)
    window.geometry(f"{width}x{height}+{x}+{y}")


def driver_installed() -> bool:
    if not WINDOWS:
        return False
    try:
        result = subprocess.run(
            ["sc.exe", "query", "ViGEmBus"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
            timeout=5,
        )
        if result.returncode == 0:
            return True
    except Exception:
        pass
    try:
        driver_file = (
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32"
            / "drivers"
            / "ViGEmBus.sys"
        )
        return driver_file.is_file()
    except Exception:
        return False


def acquire_single_instance():
    if not WINDOWS:
        return None
    global _MUTEX_HANDLE
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        handle = kernel32.CreateMutexW(None, False, "Local\\MandoFantasma_SingleInstance")
        error = kernel32.GetLastError()
        if error == 183:  # ERROR_ALREADY_EXISTS
            if handle:
                kernel32.CloseHandle(handle)
            return False
        _MUTEX_HANDLE = handle
        return True
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Actualizaciones
# ---------------------------------------------------------------------------


def fetch_latest_release() -> dict | None:
    request = urllib.request.Request(
        API_LATEST,
        headers={
            "User-Agent": f"MandoFantasma/{APP_VERSION}",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise

    assets = data.get("assets") or []
    asset = None
    for candidate in assets:
        if str(candidate.get("name", "")).lower() == "mandofantasma.exe":
            asset = candidate
            break
    if asset is None:
        for candidate in assets:
            name = str(candidate.get("name", "")).lower()
            if name.endswith(".exe") and not name.endswith(".sha256"):
                asset = candidate
                break

    return {
        "version": str(data.get("tag_name", "")).lstrip("vV"),
        "notes": data.get("body") or "",
        "url": asset.get("browser_download_url") if asset else None,
        "size": asset.get("size") if asset else None,
        "digest": asset.get("digest") if asset else None,
        "page": data.get("html_url") or RELEASES_URL,
    }


class UpdateCancelled(Exception):
    pass


def download_update(info: dict, progress, cancel_event: threading.Event) -> Path:
    url = info.get("url")
    if not url:
        raise RuntimeError("La versión nueva no tiene un archivo ejecutable adjunto.")

    target_dir = Path(tempfile.gettempdir()) / "MandoFantasma"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"MandoFantasma-{info['version']}.exe"
    partial = target.with_suffix(".part")
    if partial.exists():
        try:
            partial.unlink()
        except Exception:
            pass

    request = urllib.request.Request(
        url, headers={"User-Agent": f"MandoFantasma/{APP_VERSION}"}
    )
    written = 0
    total = int(info.get("size") or 0)
    with urllib.request.urlopen(request, timeout=30) as response:
        try:
            total = int(response.headers.get("Content-Length") or total)
        except Exception:
            pass
        with partial.open("wb") as handle:
            while True:
                if cancel_event.is_set():
                    raise UpdateCancelled()
                chunk = response.read(128 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
                if total:
                    progress(min(written / total, 1.0), written, total)
                else:
                    progress(-1.0, written, 0)

    if cancel_event.is_set():
        try:
            partial.unlink()
        except Exception:
            pass
        raise UpdateCancelled()

    if total and written != total:
        raise RuntimeError(f"Descarga incompleta ({written} de {total} bytes).")
    if written < 2_000_000:
        raise RuntimeError("El archivo descargado es demasiado pequeno o no es valido.")

    with partial.open("rb") as handle:
        if handle.read(2) != b"MZ":
            raise RuntimeError("El archivo descargado no es un ejecutable de Windows.")

    digest = str(info.get("digest") or "")
    if digest.lower().startswith("sha256:"):
        expected = digest.split(":", 1)[1].strip().lower()
        actual = sha256_file(partial)
        if actual != expected:
            raise RuntimeError("La verificación de seguridad de la actualización falló.")

    if target.exists():
        try:
            target.unlink()
        except Exception:
            pass
    partial.replace(target)
    return target


def ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def create_updater_script(current_exe: Path, new_exe: Path, version: str) -> Path:
    temp_dir = Path(tempfile.gettempdir()) / "MandoFantasma"
    temp_dir.mkdir(parents=True, exist_ok=True)
    script = temp_dir / f"update_{re.sub(r'[^0-9A-Za-z._-]', '', version)}.ps1"
    update_log = CONFIG_DIR / "update.log"

    content = f"""$ErrorActionPreference = 'Stop'
$oldPid = {os.getpid()}
$src = {ps_quote(str(new_exe))}
$dst = {ps_quote(str(current_exe))}
$log = {ps_quote(str(update_log))}
$staged = "$dst.new"
try {{
    for ($i = 0; $i -lt 120; $i++) {{
        if (-not (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {{ break }}
        Start-Sleep -Milliseconds 250
    }}
    Start-Sleep -Milliseconds 600
    Copy-Item -LiteralPath $src -Destination $staged -Force
    $ok = $false
    for ($i = 0; $i -lt 80; $i++) {{
        try {{
            Move-Item -LiteralPath $staged -Destination $dst -Force
            $ok = $true
            break
        }} catch {{
            Start-Sleep -Milliseconds 400
        }}
    }}
    if (-not $ok) {{
        throw "No se pudo reemplazar el ejecutable. Cierra Mando Fantasma y vuelve a intentarlo."
    }}
    Start-Process -FilePath $dst
}} catch {{
    Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date -Format s), $_.Exception.Message)
    $shell = New-Object -ComObject WScript.Shell
    [void]$shell.Popup("No se pudo aplicar la actualización.`n`n" + $_.Exception.Message, 20, "Mando Fantasma", 16)
}} finally {{
    Remove-Item -LiteralPath $src -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
}}
"""
    script.write_text(content, encoding="utf-8-sig")
    return script


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class RoundedButton(tk.Canvas):
    def __init__(
        self,
        parent,
        text,
        command,
        *,
        width,
        height,
        bg,
        fill,
        hover,
        text_color,
        font,
        outline="",
        hover_outline="",
        radius=12,
        disabled_fill="#1b2230",
        disabled_text="#697487",
    ):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._text = text
        self._command = command
        self._radius = radius
        self._font = font
        self._fill = fill
        self._hover = hover
        self._text_color = text_color
        self._outline = outline
        self._hover_outline = hover_outline or outline
        self._disabled_fill = disabled_fill
        self._disabled_text = disabled_text
        self._enabled = True
        self._locked = False
        self._hovered = False
        self._pressed = False
        self._draw()

        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _rounded_polygon(self, x1, y1, x2, y2, radius, **kwargs):
        radius = max(1, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
        points = [
            x1 + radius,
            y1,
            x2 - radius,
            y1,
            x2,
            y1,
            x2,
            y1 + radius,
            x2,
            y2 - radius,
            x2,
            y2,
            x2 - radius,
            y2,
            x1 + radius,
            y2,
            x1,
            y2,
            x1,
            y2 - radius,
            x1,
            y1 + radius,
            x1,
            y1,
        ]
        return self.create_polygon(
            points, smooth=True, splinesteps=24, **kwargs
        )

    def _draw(self):
        if not self.winfo_exists():
            return
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        if width <= 2 or height <= 2:
            width = int(self.cget("width"))
            height = int(self.cget("height"))
        self.delete("all")

        if not self._enabled:
            fill, text_color, outline = (
                self._disabled_fill,
                self._disabled_text,
                self._disabled_fill,
            )
        elif self._hovered:
            fill, text_color, outline = (
                self._hover,
                self._text_color,
                self._hover_outline,
            )
        else:
            fill, text_color, outline = self._fill, self._text_color, self._outline

        pad = 1
        self._rounded_polygon(
            pad,
            pad,
            width - pad,
            height - pad,
            self._radius,
            fill=fill,
            outline=outline,
            width=1,
        )
        self.create_text(
            width / 2,
            height / 2 + 1,
            text=self._text,
            fill=text_color,
            font=self._font,
        )

    def _on_enter(self, _event):
        if self._enabled and not self._locked:
            self._hovered = True
            self._draw()

    def _on_leave(self, _event):
        self._hovered = False
        self._pressed = False
        self._draw()

    def _on_press(self, _event):
        if self._enabled and not self._locked:
            self._pressed = True

    def _on_release(self, event):
        if not (self._enabled and not self._locked and self._pressed):
            self._pressed = False
            return
        self._pressed = False
        if 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self._command()

    def set_text(self, text: str):
        if text != self._text:
            self._text = text
            self._draw()

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self._pressed = False
        self.configure(cursor="hand2" if enabled and not self._locked else "arrow")
        self._draw()

    def set_locked(self, locked: bool):
        self._locked = bool(locked)
        self._pressed = False
        self._hovered = False
        self.configure(cursor="arrow" if locked or not self._enabled else "hand2")
        self._draw()

    def set_style(self, *, fill, hover, text_color, outline="", hover_outline=""):
        self._fill = fill
        self._hover = hover
        self._text_color = text_color
        self._outline = outline
        self._hover_outline = hover_outline or outline
        self._draw()


class LinkLabel(tk.Label):
    def __init__(self, parent, text, command, *, bg, fg, hover_fg, font):
        super().__init__(parent, text=text, bg=bg, fg=fg, font=font, cursor="hand2")
        self._command = command
        self._normal = fg
        self._hover = hover_fg
        self._enabled = True
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _on_enter(self, _event):
        if self._enabled:
            self.configure(fg=self._hover)

    def _on_leave(self, _event):
        if self._enabled:
            self.configure(fg=self._normal)

    def _on_click(self, _event):
        if self._enabled:
            self._command()

    def set_enabled(self, enabled: bool, text: str | None = None):
        self._enabled = bool(enabled)
        if text is not None:
            self.configure(text=text)
        self.configure(
            fg=self._normal if enabled else "#5b6679",
            cursor="hand2" if enabled else "arrow",
        )


class StatusDot(tk.Canvas):
    def __init__(self, parent, size: int, bg: str):
        super().__init__(
            parent, width=size, height=size, bg=bg, highlightthickness=0, bd=0
        )
        self._size = size
        self._on = False
        self._busy = False
        self._job = None
        self._phase = 0.0
        self.set_state(on=False)

    def set_state(self, on: bool, busy: bool = False):
        self._on = bool(on)
        self._busy = bool(busy)
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        if self._busy:
            self._draw(YELLOW, "#4b3d16", 8)
        elif self._on:
            self._phase = 0.0
            self._pulse()
        else:
            self._draw("#394657", "#1d2532", 8)

    def _draw(self, color: str, ring: str, radius: float):
        self.delete("all")
        center = self._size / 2
        self.create_oval(
            center - radius - 8,
            center - radius - 8,
            center + radius + 8,
            center + radius + 8,
            outline=ring,
            width=2,
        )
        self.create_oval(
            center - radius,
            center - radius,
            center + radius,
            center + radius,
            fill=color,
            outline="",
        )
        self.create_oval(
            center - radius + 2,
            center - radius + 2,
            center - radius + 6,
            center - radius + 6,
            fill="#baffdf" if color == GREEN else "",
            outline="",
        )

    def _pulse(self):
        if not self._on:
            return
        self._phase += 0.28
        radius = 8 + math.sin(self._phase) * 1.4
        self._draw(GREEN, "#1d5c43", radius)
        self._job = self.after(70, self._pulse)

    def destroy(self):
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        super().destroy()


# ---------------------------------------------------------------------------
# Ventana de ayuda
# ---------------------------------------------------------------------------


class HelpDialog(tk.Toplevel):
    def __init__(self, app: "PhantomApp"):
        super().__init__(app.root)
        self.app = app
        self.title("Ayuda - Mando Fantasma")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app.root)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close)

        S = app.S
        width, height = S(600), S(560)
        center_window(self, width, height, app.root)

        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=S(24), pady=(S(20), S(10)))
        tk.Label(
            header,
            text="Cómo usar Mando Fantasma",
            bg=BG,
            fg=TEXT,
            font=app.F(16, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            header,
            text="Todo lo que necesitas para que Steam detecte el mando.",
            bg=BG,
            fg=MUTED,
            font=app.F(9),
            anchor="w",
        ).pack(fill="x", pady=(S(2), 0))

        card = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="both", expand=True, padx=S(24), pady=(0, S(14)))
        text = tk.Text(
            card,
            bg=CARD,
            fg=TEXT,
            bd=0,
            highlightthickness=0,
            padx=S(16),
            pady=S(12),
            wrap="word",
            font=app.F(9),
            cursor="arrow",
        )
        scrollbar = tk.Scrollbar(card, command=text.yview, bg=CARD2, troughcolor=CARD)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)

        text.tag_configure(
            "h",
            font=app.F(10, "bold"),
            foreground=GREEN,
            spacing1=S(10),
            spacing3=S(4),
        )
        text.tag_configure(
            "p", font=app.F(9), foreground=MUTED, spacing3=S(5), lmargin1=S(2)
        )
        text.tag_configure("b", font=app.F(9, "bold"), foreground=TEXT)
        text.tag_configure("link", foreground=BLUE)

        help_text = [
            ("h", "PRIMER USO\n"),
            ("p", "1. Pulsa \"Instalar\" en la tarjeta del controlador ViGEmBus. Solo hace falta la primera vez.\n"),
            ("p", "2. Acepta el aviso de administrador y completa el instalador. Si Windows pide reiniciar, reinicia.\n"),
            ("p", "3. Pulsa \"Encender mando\". Steam lo detectara como un mando Xbox 360.\n"),
            ("p", "4. Abre Darksiders Genesis. Si el juego ya estaba abierto, cierralo y vuelve a abrirlo.\n"),
            ("h", "COOPERATIVO LOCAL\n"),
            ("p", "Antes de encender, elige 2 mandos. Steam verá dos mandos Xbox 360 y podrás jugar la campaña cooperativa. Puedes crear hasta 4.\n"),
            ("h", "SI STEAM NO LO DETECTA\n"),
            ("p", "Enciende el mando antes de abrir Steam, o reinicia Steam con el mando ya encendido. Tambien puedes pulsar \"Panel de mandos\" y comprobar que Windows lo reconoce.\n"),
            ("p", "Si acabas de instalar ViGEmBus y falla, reinicia Windows.\n"),
            ("h", "ACTUALIZACIONES\n"),
            ("p", "El programa consulta GitHub al abrirse. Cuando hay una versión nueva muestra un aviso; pulsa \"Actualizar\" y se descargará y reiniciará solo. Tus ajustes se conservan.\n"),
            ("h", "NOTAS\n"),
            ("p", "El ejecutable no está firmado digitalmente, así que Windows SmartScreen puede pedir confirmación la primera vez. Es normal: pulsa \"Más información\" y \"Ejecutar de todas formas\".\n"),
            ("p", "Mando Fantasma no está afiliado a Valve, Steam, THQ Nordic ni Nefarius Software Solutions.\n"),
            ("h", "CREDITOS\n"),
            ("p", "ViGEmBus, de Nefarius Software Solutions (BSD-3-Clause). vgamepad, de Yann Bouteiller (MIT). Hecho con Python, tkinter y PyInstaller.\n"),
        ]
        for tag, content in help_text:
            text.insert("end", content, tag)
        text.configure(state="disabled")

        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill="x", padx=S(24), pady=(0, S(20)))
        repo_button = RoundedButton(
            buttons,
            "Abrir GitHub",
            lambda: webbrowser.open(REPO_URL),
            width=S(180),
            height=S(40),
            bg=BG,
            fill=CARD2,
            hover="#202a3b",
            text_color=TEXT,
            outline=BORDER,
            hover_outline="#38465c",
            font=app.F(9, "bold"),
            radius=S(11),
        )
        repo_button.pack(side="left")
        close_button = RoundedButton(
            buttons,
            "Cerrar",
            self._close,
            width=S(140),
            height=S(40),
            bg=BG,
            fill=GREEN,
            hover=GREEN_LIGHT,
            text_color=GREEN_DARK,
            outline=GREEN,
            font=app.F(9, "bold"),
            radius=S(11),
        )
        close_button.pack(side="right")
        self.bind("<Escape>", lambda _event: self._close())

    def _close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


# ---------------------------------------------------------------------------
# Ventana de actualizacion
# ---------------------------------------------------------------------------


class UpdateDialog(tk.Toplevel):
    def __init__(self, app: "PhantomApp", info: dict):
        super().__init__(app.root)
        self.app = app
        self.info = info
        self._cancel_event = threading.Event()
        self._downloading = False
        self._queue: queue.Queue = queue.Queue()
        self._poll_job = None

        self.title("Actualización disponible - Mando Fantasma")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app.root)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        S = app.S
        width, height = S(580), S(500)
        center_window(self, width, height, app.root)

        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=S(24), pady=(S(20), S(8)))
        tk.Label(
            header,
            text="Actualización disponible",
            bg=BG,
            fg=TEXT,
            font=app.F(16, "bold"),
            anchor="w",
        ).pack(fill="x")
        self.version_label = tk.Label(
            header,
            text=f"Nueva versión v{info['version']}  ·  Tienes v{APP_VERSION}",
            bg=BG,
            fg=GREEN,
            font=app.F(9, "bold"),
            anchor="w",
        )
        self.version_label.pack(fill="x", pady=(S(3), 0))

        notes_card = tk.Frame(
            self, bg=CARD, highlightbackground=BORDER, highlightthickness=1
        )
        notes_card.pack(fill="both", expand=True, padx=S(24), pady=(S(6), S(10)))
        notes = tk.Text(
            notes_card,
            bg=CARD,
            fg=MUTED,
            bd=0,
            highlightthickness=0,
            padx=S(14),
            pady=S(12),
            wrap="word",
            font=app.F(9),
            height=10,
            cursor="arrow",
        )
        notes_scroll = tk.Scrollbar(
            notes_card, command=notes.yview, bg=CARD2, troughcolor=CARD
        )
        notes.configure(yscrollcommand=notes_scroll.set)
        notes_scroll.pack(side="right", fill="y")
        notes.pack(side="left", fill="both", expand=True)
        notes.insert(
            "1.0",
            info.get("notes")
            or "Esta versión incluye mejoras y correcciones. Pulsa Actualizar para instalarla.",
        )
        notes.configure(state="disabled")

        self.progress_frame = tk.Frame(self, bg=BG)
        self.progress_frame.pack(fill="x", padx=S(24))
        self.progress_label = tk.Label(
            self.progress_frame,
            text="",
            bg=BG,
            fg=MUTED,
            font=app.F(8),
            anchor="w",
        )
        self.progress_label.pack(fill="x", pady=(0, S(4)))
        self.progress_bar = tk.Canvas(
            self.progress_frame,
            height=S(6),
            bg=CARD2,
            highlightthickness=0,
            bd=0,
        )
        self.progress_bar.pack(fill="x")
        self._bar_item = self.progress_bar.create_rectangle(
            0, 0, 0, S(6), fill=GREEN, outline=""
        )
        self.progress_bar.bind("<Configure>", lambda _event: self._render_progress_bar())
        self._progress_value = 0.0
        self.progress_frame.pack_forget()

        self.buttons = tk.Frame(self, bg=BG)
        self.buttons.pack(fill="x", padx=S(24), pady=(S(12), S(20)))
        self.later_button = RoundedButton(
            self.buttons,
            "Más tarde",
            self._on_close,
            width=S(170),
            height=S(42),
            bg=BG,
            fill=CARD2,
            hover="#202a3b",
            text_color=TEXT,
            outline=BORDER,
            hover_outline="#38465c",
            font=app.F(9, "bold"),
            radius=S(11),
        )
        self.later_button.pack(side="left")
        self.update_button = RoundedButton(
            self.buttons,
            "Actualizar ahora",
            self._start_download,
            width=S(230),
            height=S(42),
            bg=BG,
            fill=GREEN,
            hover=GREEN_LIGHT,
            text_color=GREEN_DARK,
            outline=GREEN,
            font=app.F(9, "bold"),
            radius=S(11),
        )
        self.update_button.pack(side="right")

        self.bind("<Escape>", lambda _event: self._on_close())
        self._poll_job = self.after(80, self._poll)

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "progress":
                    fraction, written, total = payload
                    self._show_progress(fraction, written, total)
                elif kind == "done":
                    self._on_download_done(payload)
                elif kind == "error":
                    self._on_download_error(payload)
                elif kind == "cancelled":
                    self._on_cancelled()
        except queue.Empty:
            pass
        self._poll_job = self.after(80, self._poll)

    def _show_progress(self, fraction: float, written: int, total: int):
        self.progress_frame.pack(fill="x", padx=self.app.S(24), before=self.buttons)
        if fraction < 0:
            self.progress_label.configure(
                text=f"Descargando... {written / (1024 * 1024):.1f} MB"
            )
            fraction = 0.03
        else:
            if total:
                self.progress_label.configure(
                    text=(
                        f"Descargando... {written / (1024 * 1024):.1f} de "
                        f"{total / (1024 * 1024):.1f} MB"
                    )
                )
            else:
                self.progress_label.configure(
                    text=f"Descargando... {written / (1024 * 1024):.1f} MB"
                )
        self._progress_value = max(0.01, min(1.0, fraction))
        self._render_progress_bar()

    def _render_progress_bar(self):
        width = max(1, self.progress_bar.winfo_width())
        self.progress_bar.coords(
            self._bar_item, 0, 0, int(width * self._progress_value), self.app.S(6)
        )

    def _start_download(self):
        if self._downloading:
            return
        if not self.info.get("url"):
            webbrowser.open(self.info.get("page") or RELEASES_URL)
            self._on_close()
            return

        self._downloading = True
        self.update_button.set_enabled(False)
        self.update_button.set_text("Descargando...")
        self.later_button.set_enabled(False)
        self.later_button.set_text("Cancelar")
        self.later_button.set_enabled(True)
        self.version_label.configure(fg=YELLOW)
        self._show_progress(-1.0, 0, 0)

        def worker():
            try:
                path = download_update(
                    self.info,
                    lambda fraction, written, total: self._queue.put(
                        ("progress", (fraction, written, total))
                    ),
                    self._cancel_event,
                )
                self._queue.put(("done", path))
            except UpdateCancelled:
                self._queue.put(("cancelled", None))
            except Exception as error:
                log_error(traceback.format_exc())
                self._queue.put(("error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_download_done(self, path: Path):
        self._downloading = False
        self._progress_value = 1.0
        self._render_progress_bar()
        self.progress_label.configure(text="Descarga completada. Reiniciando...")
        self.update_button.set_text("Instalando...")
        self.update_button.set_enabled(False)
        self.later_button.set_enabled(False)
        self.app.apply_update(path, self.info["version"])

    def _on_download_error(self, message: str):
        self._downloading = False
        self.version_label.configure(fg=RED)
        self.progress_label.configure(
            text="No se pudo descargar. Revisa tu conexión e inténtalo de nuevo."
        )
        self.update_button.set_text("Reintentar")
        self.update_button.set_enabled(True)
        self.later_button.set_text("Cerrar")
        self.later_button.set_enabled(True)
        log_error(f"Error de actualización: {message}")

    def _on_cancelled(self):
        self._downloading = False
        self.update_button.set_text("Actualizar ahora")
        self.update_button.set_enabled(True)
        self.later_button.set_text("Más tarde")
        self.later_button.set_enabled(True)
        self.version_label.configure(fg=GREEN)
        self.progress_frame.pack_forget()
        self._cancel_event.clear()

    def _on_close(self):
        if self._downloading:
            self._cancel_event.set()
        self.app.note_update_skipped(self.info.get("version"))
        try:
            self.grab_release()
        except Exception:
            pass
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
        self.destroy()


# ---------------------------------------------------------------------------
# Aplicacion principal
# ---------------------------------------------------------------------------


class PhantomApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.scale = get_ui_scale()
        self.cfg = load_config()
        self.pads: list = []
        self.vg = None
        self.update_info: dict | None = None
        self._dialog: tk.Toplevel | None = None
        self._queue: queue.Queue = queue.Queue()
        self._closing = False
        self._updating = False
        self._checking = False
        self._banner_visible = False
        self._driver_installed = False
        self._driver_installing = False
        self._driver_poll_job = None
        self._poll_job = None
        self._busy = False
        self._auto_prompted = False

        self.root.title(f"{APP_NAME} - Controlador virtual para Steam")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.report_callback_exception = self._report_exception
        self._set_window_icon()

        self._build_ui()
        self._set_window_height()
        center_window(self.root, self.S(600), self.S(575))
        self.root.update_idletasks()

        self._driver_installed = driver_installed()
        self._refresh_driver_ui()
        self._set_idle_status()
        self._refresh_segments()
        self._refresh_power_button()

        self._poll_job = self.root.after(80, self._poll_queue)
        self.root.after(1400, self._start_background_update_check)
        if self.cfg.get("auto_connect") and self._driver_installed:
            self.root.after(650, self.toggle_power)

    # -- helpers ---------------------------------------------------------

    def S(self, value: float) -> int:
        return int(round(value * self.scale))

    def F(self, size: int, weight: str = "normal"):
        return ("Segoe UI", size, weight)

    def _set_window_height(self):
        height = 615 if self._banner_visible else 575
        self.root.geometry(f"{self.S(600)}x{self.S(height)}")

    def _set_window_icon(self):
        try:
            icon_path = resource_path("assets/icon_64.png")
            self._icon_image = tk.PhotoImage(file=str(icon_path))
            self.root.iconphoto(True, self._icon_image)
        except Exception:
            self._icon_image = None
        try:
            if self.scale >= 1.9:
                image_name = "icon_128.png"
            elif self.scale >= 1.35:
                image_name = "icon_96.png"
            else:
                image_name = "icon_64.png"
            self._header_image = tk.PhotoImage(
                file=str(resource_path(f"assets/{image_name}"))
            )
        except Exception:
            self._header_image = None

    def _report_exception(self, exc_type, value, tb):
        global _ERROR_DIALOG_SHOWN
        message = "".join(traceback.format_exception(exc_type, value, tb))
        log_error(message)
        if not _ERROR_DIALOG_SHOWN and not self._closing:
            _ERROR_DIALOG_SHOWN = True
            messagebox.showerror(
                APP_NAME,
                "Ha ocurrido un error inesperado.\n\n"
                f"Se guardó un registro en:\n{LOG_FILE}",
                parent=self.root,
            )

    def _build_ui(self):
        self.container = tk.Frame(self.root, bg=BG)
        self.container.pack(fill="both", expand=True, padx=self.S(24), pady=self.S(18))

        header = tk.Frame(self.container, bg=BG)
        header.pack(fill="x")
        if self._header_image is not None:
            icon_label = tk.Label(header, image=self._header_image, bg=BG, bd=0)
            icon_label.pack(side="left", padx=(0, self.S(12)))
        title_box = tk.Frame(header, bg=BG)
        title_box.pack(side="left", fill="y", pady=self.S(2))
        tk.Label(
            title_box,
            text="MANDO FANTASMA",
            bg=BG,
            fg=TEXT,
            font=self.F(18, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="Controlador Xbox 360 virtual para Steam",
            bg=BG,
            fg=MUTED,
            font=self.F(9),
            anchor="w",
        ).pack(anchor="w", pady=(self.S(1), 0))

        self.banner = tk.Frame(
            self.container,
            bg="#123024",
            highlightbackground="#2b6b4f",
            highlightthickness=1,
        )
        banner_inner = tk.Frame(self.banner, bg="#123024")
        banner_inner.pack(fill="x", padx=self.S(12), pady=self.S(6))
        self.banner_text = tk.Label(
            banner_inner,
            text="",
            bg="#123024",
            fg=GREEN,
            font=self.F(9, "bold"),
            anchor="w",
        )
        self.banner_text.pack(side="left")
        close_banner = tk.Label(
            banner_inner,
            text="×",
            bg="#123024",
            fg=MUTED,
            font=self.F(12),
            cursor="hand2",
        )
        close_banner.pack(side="right", padx=(self.S(8), 0))
        close_banner.bind("<Button-1>", lambda _event: self._hide_banner())
        self.banner_link = LinkLabel(
            banner_inner,
            "Actualizar",
            self._open_update_dialog,
            bg="#123024",
            fg=GREEN,
            hover_fg=GREEN_LIGHT,
            font=self.F(9, "bold"),
        )
        self.banner_link.pack(side="right")

        self.status_card = tk.Frame(
            self.container, bg=CARD, highlightbackground=BORDER, highlightthickness=1
        )
        self.status_card.pack(fill="x", pady=(self.S(14), self.S(12)))
        status_inner = tk.Frame(self.status_card, bg=CARD)
        status_inner.pack(fill="x", padx=self.S(16), pady=self.S(14))
        self.status_dot = StatusDot(status_inner, self.S(42), CARD)
        self.status_dot.pack(side="left", padx=(0, self.S(14)))
        status_text = tk.Frame(status_inner, bg=CARD)
        status_text.pack(side="left", fill="x", expand=True)
        self.status_title = tk.Label(
            status_text,
            text="Mando apagado",
            bg=CARD,
            fg=TEXT,
            font=self.F(15, "bold"),
            anchor="w",
        )
        self.status_title.pack(fill="x")
        self.status_sub = tk.Label(
            status_text,
            text="",
            bg=CARD,
            fg=MUTED,
            font=self.F(9),
            anchor="w",
            justify="left",
            wraplength=self.S(420),
        )
        self.status_sub.pack(fill="x", pady=(self.S(2), 0))

        self.power_btn = RoundedButton(
            self.container,
            "Encender mando",
            self.toggle_power,
            width=self.S(552),
            height=self.S(54),
            bg=BG,
            fill=GREEN,
            hover=GREEN_LIGHT,
            text_color=GREEN_DARK,
            outline=GREEN,
            font=self.F(11, "bold"),
            radius=self.S(14),
        )
        self.power_btn.pack(fill="x", pady=(0, self.S(12)))

        options = tk.Frame(self.container, bg=BG)
        options.pack(fill="x")
        tk.Label(
            options,
            text="Mandos virtuales",
            bg=BG,
            fg=TEXT,
            font=self.F(9),
        ).pack(side="left", padx=(0, self.S(10)))
        self.segment_buttons = []
        for number in range(1, 5):
            button = RoundedButton(
                options,
                str(number),
                lambda chosen=number: self.set_controller_count(chosen),
                width=self.S(40),
                height=self.S(32),
                bg=BG,
                fill=CARD2,
                hover="#202a3b",
                text_color=MUTED,
                outline=BORDER,
                hover_outline="#38465c",
                font=self.F(9, "bold"),
                radius=self.S(9),
            )
            button.pack(side="left", padx=(0, self.S(6)))
            self.segment_buttons.append(button)

        self.auto_var = tk.BooleanVar(value=bool(self.cfg.get("auto_connect")))
        self.auto_check = tk.Checkbutton(
            options,
            text="Encender al abrir",
            variable=self.auto_var,
            command=self._on_auto_toggle,
            bg=BG,
            fg=MUTED,
            activebackground=BG,
            activeforeground=TEXT,
            selectcolor=BG,
            font=self.F(9),
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self.auto_check.pack(side="right")

        self.hint_label = tk.Label(
            self.container,
            text="Enciende el mando antes de abrir el juego. Para cooperativo local, usa 2 mandos.",
            bg=BG,
            fg="#6f7d91",
            font=self.F(8),
            anchor="w",
            justify="left",
            wraplength=self.S(552),
        )
        self.hint_label.pack(fill="x", pady=(self.S(7), self.S(12)))

        self.driver_card = tk.Frame(
            self.container, bg=CARD, highlightbackground=BORDER, highlightthickness=1
        )
        self.driver_card.pack(fill="x", pady=(0, self.S(12)))
        driver_inner = tk.Frame(self.driver_card, bg=CARD)
        driver_inner.pack(fill="x", padx=self.S(16), pady=self.S(11))
        driver_text = tk.Frame(driver_inner, bg=CARD)
        driver_text.pack(side="left", fill="x", expand=True)
        self.driver_title = tk.Label(
            driver_text,
            text="Controlador ViGEmBus",
            bg=CARD,
            fg=TEXT,
            font=self.F(10, "bold"),
            anchor="w",
        )
        self.driver_title.pack(fill="x")
        self.driver_sub = tk.Label(
            driver_text,
            text="Comprobando...",
            bg=CARD,
            fg=MUTED,
            font=self.F(8),
            anchor="w",
            justify="left",
            wraplength=self.S(360),
        )
        self.driver_sub.pack(fill="x", pady=(self.S(1), 0))

        driver_action = tk.Frame(driver_inner, bg=CARD)
        driver_action.pack(side="right", padx=(self.S(10), 0))
        self.driver_status = tk.Label(
            driver_action,
            text="...",
            bg=CARD,
            fg=MUTED,
            font=self.F(8, "bold"),
        )
        self.driver_status.pack(side="left", padx=(0, self.S(10)))
        self.driver_btn = RoundedButton(
            driver_action,
            "Comprobar",
            self.on_driver_action,
            width=self.S(94),
            height=self.S(32),
            bg=CARD,
            fill=CARD2,
            hover="#202a3b",
            text_color=TEXT,
            outline=BORDER,
            hover_outline="#38465c",
            font=self.F(8, "bold"),
            radius=self.S(9),
        )
        self.driver_btn.pack(side="left")

        tests = tk.Frame(self.container, bg=BG)
        tests.pack(fill="x")
        self.test_a_btn = RoundedButton(
            tests,
            "Probar botón A",
            self.test_button_a,
            width=self.S(270),
            height=self.S(38),
            bg=BG,
            fill=CARD2,
            hover="#202a3b",
            text_color=MUTED,
            outline=BORDER,
            hover_outline="#38465c",
            font=self.F(9),
            radius=self.S(10),
        )
        self.test_a_btn.pack(side="left")
        self.test_a_btn.set_enabled(False)
        self.joy_btn = RoundedButton(
            tests,
            "Panel de mandos de Windows",
            self.open_joy_panel,
            width=self.S(270),
            height=self.S(38),
            bg=BG,
            fill=CARD2,
            hover="#202a3b",
            text_color=MUTED,
            outline=BORDER,
            hover_outline="#38465c",
            font=self.F(9),
            radius=self.S(10),
        )
        self.joy_btn.pack(side="right")

        footer = tk.Frame(self.container, bg=BG)
        footer.pack(side="bottom", fill="x", pady=(self.S(12), 0))
        separator = tk.Frame(footer, bg=BORDER, height=1)
        separator.pack(fill="x", pady=(0, self.S(9)))
        footer_row = tk.Frame(footer, bg=BG)
        footer_row.pack(fill="x")
        tk.Label(
            footer_row,
            text=f"v{APP_VERSION}",
            bg=BG,
            fg="#5b6679",
            font=self.F(8),
        ).pack(side="left")
        self.help_link = LinkLabel(
            footer_row,
            "Ayuda",
            self.open_help,
            bg=BG,
            fg=MUTED,
            hover_fg=TEXT,
            font=self.F(8),
        )
        self.help_link.pack(side="right")
        tk.Label(
            footer_row, text="  ·  ", bg=BG, fg="#3a4557", font=self.F(8)
        ).pack(side="right")
        self.update_link = LinkLabel(
            footer_row,
            "Buscar actualizaciones",
            lambda: self._start_update_check(manual=True),
            bg=BG,
            fg=MUTED,
            hover_fg=TEXT,
            font=self.F(8),
        )
        self.update_link.pack(side="right")

    # -- estado ----------------------------------------------------------

    def _set_idle_status(self):
        if self.pads:
            return
        self.status_title.configure(text="Mando apagado")
        if self._driver_installed:
            self.status_sub.configure(
                text="Enciéndelo para que Steam detecte el mando virtual."
            )
        else:
            self.status_sub.configure(
                text="Falta el controlador ViGEmBus para poder crear el mando."
            )
        self.status_dot.set_state(on=False)

    def _refresh_power_button(self):
        if self._busy:
            self.power_btn.set_enabled(False)
            return
        self.power_btn.set_enabled(True)
        if self.pads:
            self.power_btn.set_text("Apagar mando")
            self.power_btn.set_style(
                fill=RED_SOFT,
                hover="#3b1d23",
                text_color=RED,
                outline="#6b343b",
                hover_outline="#8a454d",
            )
        else:
            self.power_btn.set_text("Encender mando")
            self.power_btn.set_style(
                fill=GREEN,
                hover=GREEN_LIGHT,
                text_color=GREEN_DARK,
                outline=GREEN,
                hover_outline=GREEN_LIGHT,
            )

    def _refresh_segments(self):
        count = int(self.cfg.get("controllers", 1))
        for index, button in enumerate(self.segment_buttons, start=1):
            selected = index == count
            if selected:
                button.set_style(
                    fill=GREEN,
                    hover=GREEN,
                    text_color=GREEN_DARK,
                    outline=GREEN,
                )
            else:
                button.set_style(
                    fill=CARD2,
                    hover="#202a3b",
                    text_color=MUTED,
                    outline=BORDER,
                    hover_outline="#38465c",
                )
            button.set_locked(bool(self.pads))

    def _refresh_driver_ui(self):
        if self._driver_installing and not self._driver_installed:
            self.driver_status.configure(text="Instalando...", fg=YELLOW)
            self.driver_sub.configure(
                text="Termina el instalador y vuelve aquí. Si Windows lo pide, reinicia."
            )
            self.driver_btn.set_text("Comprobar")
            self.driver_btn.set_style(
                fill=CARD2,
                hover="#202a3b",
                text_color=TEXT,
                outline=BORDER,
                hover_outline="#38465c",
            )
        elif self._driver_installed:
            self.driver_status.configure(text="Instalado", fg=GREEN)
            self.driver_sub.configure(text="Listo para crear mandos virtuales.")
            self.driver_btn.set_text("Comprobar")
            self.driver_btn.set_style(
                fill=CARD2,
                hover="#202a3b",
                text_color=TEXT,
                outline=BORDER,
                hover_outline="#38465c",
            )
        else:
            self.driver_status.configure(text="No instalado", fg=RED)
            self.driver_sub.configure(
                text="Se instala una sola vez. El instalador va incluido en este programa."
            )
            self.driver_btn.set_text("Instalar")
            self.driver_btn.set_style(
                fill=GREEN,
                hover=GREEN_LIGHT,
                text_color=GREEN_DARK,
                outline=GREEN,
                hover_outline=GREEN_LIGHT,
            )
        if not self.pads:
            self._set_idle_status()

    def _set_connected_status(self):
        count = len(self.pads)
        self.status_title.configure(
            text=f"{count} mando conectado" if count == 1 else f"{count} mandos conectados"
        )
        if count == 1:
            self.status_sub.configure(
                text="Steam ya puede detectarlo como Xbox 360 Controller."
            )
        else:
            self.status_sub.configure(
                text="Steam ya puede detectarlos como mandos Xbox 360 Controller."
            )

    # -- acciones --------------------------------------------------------

    def set_controller_count(self, number: int):
        if self.pads or self._busy:
            return
        self.cfg["controllers"] = max(1, min(4, int(number)))
        save_config(self.cfg)
        self._refresh_segments()

    def _on_auto_toggle(self):
        self.cfg["auto_connect"] = bool(self.auto_var.get())
        save_config(self.cfg)

    def toggle_power(self):
        if self._busy or self._updating:
            return
        if self.pads:
            self.disconnect()
        else:
            self.connect()

    def _ensure_driver(self) -> bool:
        if driver_installed():
            self._driver_installed = True
            self._driver_installing = False
            self._refresh_driver_ui()
            return True
        self._driver_installed = False
        self._refresh_driver_ui()
        answer = messagebox.askyesno(
            "Controlador necesario",
            "Para crear el mando virtual hace falta ViGEmBus.\n\n"
            "El instalador ya viene incluido en Mando Fantasma. "
            "¿Quieres abrirlo ahora?",
            parent=self.root,
        )
        if answer:
            self.install_driver()
        return False

    def connect(self):
        if not self._ensure_driver():
            return
        count = int(self.cfg.get("controllers", 1))
        count = max(1, min(4, count))
        self._busy = True
        self.power_btn.set_enabled(False)
        self.power_btn.set_text("Conectando...")
        self.status_title.configure(
            text="Conectando mando..." if count == 1 else "Conectando mandos..."
        )
        self.status_sub.configure(
            text="Creando el dispositivo virtual. Esto puede tardar un momento."
        )
        self.status_dot.set_state(on=False, busy=True)
        self.root.update_idletasks()

        created = []
        try:
            import vgamepad as vg

            for _ in range(count):
                pad = vg.VX360Gamepad()
                pad.reset()
                pad.update()
                created.append(pad)
            self.vg = vg
            self.pads = created
        except Exception as error:
            for pad in created:
                try:
                    pad.reset()
                    pad.update()
                except Exception:
                    pass
            created.clear()
            gc.collect()
            self._busy = False
            self._driver_installed = driver_installed()
            self._refresh_driver_ui()
            self._set_idle_status()
            self._refresh_power_button()
            log_error(traceback.format_exc())

            detail = str(error)
            if "BUS_NOT_FOUND" in detail or "0x" in detail:
                hint = (
                    "ViGEmBus no está respondiendo. Comprueba que esté instalado "
                    "y reinicia Windows si acabas de instalarlo."
                )
            elif "DLL" in detail or "WinError 126" in detail:
                hint = (
                    "No se pudo cargar el controlador. Reinstala ViGEmBus y "
                    "reinicia Windows."
                )
            else:
                hint = "No se pudo crear el mando virtual."
            messagebox.showerror(
                "No se pudo encender el mando",
                f"{hint}\n\nDetalle: {detail}",
                parent=self.root,
            )
            return

        self._busy = False
        self._driver_installing = False
        self._refresh_driver_ui()
        self._set_connected_status()
        self._refresh_power_button()
        self.status_dot.set_state(on=True)
        self._refresh_segments()
        self.test_a_btn.set_enabled(True)
        self.test_a_btn.set_style(
            fill=CARD2,
            hover="#25334a",
            text_color=TEXT,
            outline=BORDER,
            hover_outline="#455773",
        )

    def disconnect(self):
        pads = self.pads
        self.pads = []
        for pad in pads:
            try:
                pad.reset()
                pad.update()
            except Exception:
                pass
        del pads
        gc.collect()
        self.vg = None
        self._busy = False
        self._set_idle_status()
        self._refresh_power_button()
        self._refresh_segments()
        self.test_a_btn.set_enabled(False)
        self.test_a_btn.set_text("Probar botón A")
        self.test_a_btn.set_style(
            fill="#171d28",
            hover="#171d28",
            text_color="#566174",
            outline="#202836",
        )

    def test_button_a(self):
        if not self.pads or self.vg is None:
            return
        pad = self.pads[0]
        try:
            pad.press_button(self.vg.XUSB_BUTTON.XUSB_GAMEPAD_A)
            pad.update()
        except Exception:
            log_error(traceback.format_exc())
            return
        self.test_a_btn.set_text("Botón A pulsado")
        self.root.after(900, lambda: self.test_a_btn.set_text("Probar botón A"))

        def release():
            if not self.pads or self.vg is None:
                return
            try:
                self.pads[0].release_button(self.vg.XUSB_BUTTON.XUSB_GAMEPAD_A)
                self.pads[0].update()
            except Exception:
                pass

        self.root.after(280, release)

    def open_joy_panel(self):
        try:
            subprocess.Popen(
                ["control.exe", "joy.cpl"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            log_error(traceback.format_exc())
            messagebox.showerror(
                "Panel de mandos",
                "No se pudo abrir el panel de mandos de Windows.",
                parent=self.root,
            )

    def open_help(self):
        HelpDialog(self)

    # -- driver ----------------------------------------------------------

    def on_driver_action(self):
        if self._driver_installing:
            self._refresh_driver_ui()
            if driver_installed():
                self._driver_installed = True
                self._driver_installing = False
                self._refresh_driver_ui()
                messagebox.showinfo(
                    "Controlador instalado",
                    "ViGEmBus ya está instalado. Ya puedes encender el mando.",
                    parent=self.root,
                )
            return
        if driver_installed():
            self._driver_installed = True
            self._refresh_driver_ui()
            messagebox.showinfo(
                "Controlador ViGEmBus",
                "El controlador está instalado y listo para usarse.",
                parent=self.root,
            )
            return
        self.install_driver()

    def install_driver(self):
        installer = resource_path(f"vendor/{VIGEM_INSTALLER}")
        if not installer.is_file():
            webbrowser.open(VIGEM_URL)
            messagebox.showinfo(
                "Instalar ViGEmBus",
                "No se encontró el instalador incluido.\n\n"
                "Se abrió la página oficial de ViGEmBus en tu navegador. "
                "Descarga e instala la versión 1.22.0 y vuelve a abrir Mando Fantasma.",
                parent=self.root,
            )
            self._driver_installing = True
            self._refresh_driver_ui()
            self._poll_driver(0)
            return

        try:
            if sha256_file(installer).lower() != VIGEM_SHA256:
                messagebox.showerror(
                    "Archivo no valido",
                    "El instalador de ViGEmBus no supero la comprobación de seguridad. "
                    "Descarga una copia nueva de Mando Fantasma.",
                    parent=self.root,
                )
                return
            temp_dir = Path(tempfile.gettempdir()) / "MandoFantasma"
            temp_dir.mkdir(parents=True, exist_ok=True)
            installer_copy = temp_dir / "ViGEmBusSetup.exe"
            shutil.copy2(installer, installer_copy)
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", str(installer_copy), None, str(temp_dir), 1
            )
            if result <= 32:
                messagebox.showwarning(
                    "Instalación cancelada",
                    "No se pudo abrir el instalador. Acepta el aviso de administrador "
                    "e inténtalo de nuevo.",
                    parent=self.root,
                )
                return
        except Exception:
            log_error(traceback.format_exc())
            messagebox.showerror(
                "Error al instalar",
                f"No se pudo iniciar el instalador de ViGEmBus.\n\nDetalle: {traceback.format_exc(limit=1)}",
                parent=self.root,
            )
            return

        self._driver_installing = True
        self._refresh_driver_ui()
        messagebox.showinfo(
            "Instalador abierto",
            "Sigue los pasos del instalador de ViGEmBus.\n\n"
            "Cuando termine, Mando Fantasma lo detectará automáticamente. "
            "Si Windows pide reiniciar, hazlo antes de encender el mando.",
            parent=self.root,
        )
        self._poll_driver(0)

    def _poll_driver(self, attempts: int):
        if self._closing:
            return
        if driver_installed():
            self._driver_installed = True
            self._driver_installing = False
            self._refresh_driver_ui()
            if self.pads:
                self._set_connected_status()
            return
        if attempts < 240:
            self._driver_poll_job = self.root.after(
                1500, lambda: self._poll_driver(attempts + 1)
            )

    # -- actualizaciones -------------------------------------------------

    def _poll_queue(self):
        if self._closing:
            return
        try:
            while True:
                callback, args = self._queue.get_nowait()
                try:
                    callback(*args)
                except Exception:
                    log_error(traceback.format_exc())
        except queue.Empty:
            pass
        self._poll_job = self.root.after(80, self._poll_queue)

    def post(self, callback, *args):
        self._queue.put((callback, args))

    def _start_background_update_check(self):
        self._start_update_check(manual=False)

    def _start_update_check(self, manual: bool):
        if self._checking:
            return
        self._checking = True
        if manual:
            self.update_link.set_text("Buscando...")
            self.update_link.set_enabled(False)

        def worker():
            try:
                info = fetch_latest_release()
                self.post(self._on_update_check_result, info, manual, None)
            except Exception as error:
                log_error(traceback.format_exc())
                self.post(self._on_update_check_result, None, manual, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_check_result(self, info, manual, error):
        self._checking = False
        self.update_link.set_enabled(True, "Buscar actualizaciones")
        if error is not None:
            if manual:
                messagebox.showwarning(
                    "Actualizaciones",
                    "No se pudo comprobar si hay una versión nueva.\n\n"
                    "Revisa tu conexión a Internet e inténtalo de nuevo.",
                    parent=self.root,
                )
            return
        if info and is_newer(info.get("version", ""), APP_VERSION):
            self.update_info = info
            self._show_banner(info)
            skipped = self.cfg.get("skipped_version")
            if manual or skipped != info.get("version"):
                if not self._auto_prompted or manual:
                    self._auto_prompted = True
                    self._open_update_dialog()
        elif manual:
            if info is None:
                messagebox.showinfo(
                    "Actualizaciones",
                    "Todavía no hay versiones publicadas en GitHub.",
                    parent=self.root,
                )
            else:
                messagebox.showinfo(
                    "Actualizaciones",
                    f"Ya tienes la última versión (v{APP_VERSION}).",
                    parent=self.root,
                )

    def _show_banner(self, info: dict):
        self.banner_text.configure(
            text=f"Nueva versión v{info.get('version', '')} disponible"
        )
        if not self._banner_visible:
            self.banner.pack(
                fill="x",
                pady=(0, self.S(10)),
                before=self.status_card,
            )
            self._banner_visible = True
            self._set_window_height()

    def _hide_banner(self):
        if self._banner_visible:
            self.banner.pack_forget()
            self._banner_visible = False
            self._set_window_height()

    def _open_update_dialog(self):
        if not self.update_info:
            if self._checking:
                return
            self._start_update_check(manual=True)
            return
        if self._dialog is not None and self._dialog.winfo_exists():
            self._dialog.lift()
            return
        self._dialog = UpdateDialog(self, self.update_info)

    def note_update_skipped(self, version: str | None):
        if version:
            self.cfg["skipped_version"] = version
            save_config(self.cfg)

    def apply_update(self, new_exe: Path, version: str):
        if not getattr(sys, "frozen", False):
            messagebox.showinfo(
                "Actualizacion",
                "La actualización automática solo funciona desde el ejecutable "
                "distribuido de Mando Fantasma.",
                parent=self.root,
            )
            return
        current_exe = Path(sys.executable).resolve()
        try:
            script = create_updater_script(current_exe, new_exe, version)
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-WindowStyle",
                    "Hidden",
                    "-File",
                    str(script),
                ],
                creationflags=CREATE_NO_WINDOW,
                close_fds=True,
            )
        except Exception:
            log_error(traceback.format_exc())
            messagebox.showerror(
                "No se pudo actualizar",
                "No se pudo iniciar el actualizador. Descarga la nueva versión "
                "desde la página de GitHub.",
                parent=self.root,
            )
            return

        self._updating = True
        self._closing = True
        save_config(self.cfg)
        self.disconnect()
        self.root.after(700, self.root.destroy)

    # -- cierre ----------------------------------------------------------

    def on_close(self):
        if self._closing:
            return
        self._closing = True
        save_config(self.cfg)
        self.disconnect()
        try:
            self.root.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


def main():
    enable_dpi_awareness()
    if WINDOWS:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "MandoFantasma.PhantomController"
            )
        except Exception:
            pass

    root = tk.Tk()
    root.withdraw()

    if not acquire_single_instance():
        messagebox.showinfo(
            APP_NAME,
            "Mando Fantasma ya está abierto.",
            parent=root,
        )
        root.destroy()
        return

    try:
        PhantomApp(root)
        root.deiconify()
        root.mainloop()
    except Exception:
        log_error(traceback.format_exc())
        try:
            messagebox.showerror(
                APP_NAME,
                "Mando Fantasma no pudo iniciarse.\n\n"
                f"Detalle guardado en:\n{LOG_FILE}",
                parent=root,
            )
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    main()
