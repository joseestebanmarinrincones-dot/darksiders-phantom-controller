"""Genera el icono de Mando Fantasma (ejecutar solo al cambiar el diseno)."""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

MASTER = 2048
BG = "#0b0e13"
BORDER = "#263041"
MINT = "#3ddc97"
MINT_LIGHT = "#7cf0bd"
DARK = "#0b0e13"


def rounded_rect(draw, box, radius, **kwargs):
    draw.rounded_rectangle(box, radius=radius, **kwargs)


def build_emblem(size=MASTER):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = size / 1024.0

    def B(x):
        return int(round(x * u))

    # Cuerpo del mando: parte superior redondeada y base plana para la cola.
    rounded_rect(d, (B(150), B(250), B(874), B(560)), B(190), fill=MINT)
    d.rectangle((B(150), B(440), B(874), B(700)), fill=MINT)
    # Cola ondulada de fantasma.
    wave = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    wd = ImageDraw.Draw(wave)
    points = []
    steps = 240
    for i in range(steps + 1):
        x = B(150) + (B(874) - B(150)) * i / steps
        t = i / steps
        y = B(760) + int((B(60) * math.sin(t * 6 * math.pi)))
        points.append((x, y))
    wd.polygon([(B(150), B(680)), *points, (B(874), B(680))], fill=MINT)
    img = Image.alpha_composite(img, wave)

    d = ImageDraw.Draw(img)
    # Sombra interior del borde superior.
    rounded_rect(d, (B(186), B(286), B(838), B(470)), B(150), outline=MINT_LIGHT, width=max(2, B(8)))

    # Ojos de fantasma.
    eye_y = (B(430), B(530))
    rounded_rect(d, (B(388), eye_y[0], B(468), eye_y[1]), B(40), fill=DARK)
    rounded_rect(d, (B(556), eye_y[0], B(636), eye_y[1]), B(40), fill=DARK)
    # Brillo en los ojos.
    d.ellipse((B(404), B(444), B(430), B(470)), fill="#54c995")
    d.ellipse((B(572), B(444), B(598), B(470)), fill="#54c995")

    # Cruz direccional.
    cx, cy = B(282), B(628)
    arm, thick = B(58), B(24)
    rounded_rect(d, (cx - thick, cy - arm, cx + thick, cy + arm), B(14), fill=DARK)
    rounded_rect(d, (cx - arm, cy - thick, cx + arm, cy + thick), B(14), fill=DARK)

    # Botones A/B/X/Y.
    btn_r = B(27)
    for bx, by in ((B(716), B(582)), (B(772), B(628)), (B(660), B(628)), (B(716), B(674))):
        d.ellipse((bx - btn_r, by - btn_r, bx + btn_r, by + btn_r), fill=DARK)
    d.ellipse((B(716) - B(10), B(582) - B(10), B(716) + B(10), B(582) + B(10)), fill="#54c995")

    # Pequenos detalles de brillo.
    d.arc((B(220), B(300), B(804), B(560)), 200, 320, fill="#68e7ae", width=max(2, B(6)))
    return img


def with_background(emblem):
    canvas = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    rounded_rect(d, (28, 28, MASTER - 28, MASTER - 28), 430, fill=BG, outline=BORDER, width=18)

    # Glow suave.
    glow = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    alpha = emblem.getchannel("A").filter(ImageFilter.GaussianBlur(70))
    glow_layer = Image.new("RGBA", (MASTER, MASTER), (61, 220, 151, 0))
    glow_layer.putalpha(alpha.point(lambda v: int(v * 0.35)))
    canvas = Image.alpha_composite(canvas, glow_layer)
    canvas = Image.alpha_composite(canvas, emblem)
    return canvas


def main():
    emblem = build_emblem()
    full = with_background(emblem)

    # PNG para la cabecera (fondo transparente).
    emblem.resize((256, 256), Image.LANCZOS).save(ASSETS / "icon_256.png")
    for size in (64, 96, 128):
        emblem.resize((size, size), Image.LANCZOS).save(ASSETS / f"icon_{size}.png")

    # ICO para el ejecutable.
    sizes = [16, 24, 32, 48, 64, 128, 256]
    icons = [full.resize((s, s), Image.LANCZOS) for s in sizes]
    icons[-1].save(
        ASSETS / "icon.ico",
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=icons[:-1],
    )
    full.resize((512, 512), Image.LANCZOS).save(ASSETS / "icon_512.png")
    print("Iconos generados en", ASSETS)


if __name__ == "__main__":
    main()
