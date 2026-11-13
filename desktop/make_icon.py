"""Draws the CutLab app icon (desktop/cutlab.ico + desktop/ui/icon.png). Needs Pillow:  python desktop/make_icon.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
S = 1024                                   # drawn large, downsampled for each icon size
A, B = (124, 92, 255), (91, 140, 255)      # theme --accent / --accent-2


def draw() -> Image.Image:
    grad = Image.new("RGBA", (S, S))
    px = grad.load()
    for y in range(S):
        for x in range(S):
            t = (x + y) / (2 * S - 2)
            px[x, y] = tuple(round(A[i] + (B[i] - A[i]) * t) for i in range(3)) + (255,)
    mask = Image.new("L", (S, S), 0)
    m = 56
    ImageDraw.Draw(mask).rounded_rectangle((m, m, S - m, S - m), radius=220, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    # white "play" triangle, cut by a diagonal slit (the edit)
    tri = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(tri)
    d.polygon([(372, 282), (372, 742), (772, 512)], fill=255)
    d.line([(300, 640), (760, 300)], fill=0, width=54)
    shadow = tri.filter(ImageFilter.GaussianBlur(18))
    img.paste(Image.new("RGBA", (S, S), (20, 10, 60, 90)), (0, 14), shadow)
    img.paste(Image.new("RGBA", (S, S), (255, 255, 255, 255)), (0, 0), tri)
    return img


if __name__ == "__main__":
    big = draw()
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    big.resize((256, 256), Image.LANCZOS).save(HERE / "cutlab.ico", sizes=[(s, s) for s in sizes])
    (HERE / "ui").mkdir(exist_ok=True)
    big.resize((128, 128), Image.LANCZOS).save(HERE / "ui" / "icon.png")
    print("wrote", HERE / "cutlab.ico")
