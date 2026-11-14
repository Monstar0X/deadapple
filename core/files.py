"""Image normalisation, watermark, PDF / CBZ building (all blocking – run in an executor)."""
import zipfile
from io import BytesIO
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

Image.MAX_IMAGE_PIXELS = None  # webtoon strips can be huge

PageInfo = Tuple[Path, int, int]  # path, width, height


def _watermark(img: Image.Image, wm: dict) -> Image.Image:
    text = (wm.get("text") or "").strip()
    if not text:
        return img
    size_pct = float(wm.get("size", 3))
    px = max(12, int(img.width * size_pct / 100))
    try:
        font = ImageFont.load_default(size=px)
    except TypeError:  # very old Pillow
        font = ImageFont.load_default()
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    tw, th = r - l, b - t
    pad = max(8, px // 2)
    pos = wm.get("position", "bottom-right")
    if "left" in pos:
        x = pad
    elif "right" in pos:
        x = img.width - tw - pad
    else:
        x = (img.width - tw) // 2
    if "top" in pos:
        y = pad
    elif "bottom" in pos:
        y = img.height - th - pad
    else:
        y = (img.height - th) // 2
    color = wm.get("color", "#ffffff").lstrip("#")
    try:
        rgb = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        rgb = (255, 255, 255)
    alpha = max(0, min(255, int(wm.get("opacity", 128))))
    draw.text((x - l, y - t), text, font=font, fill=rgb + (alpha,),
              stroke_width=max(1, px // 14), stroke_fill=(0, 0, 0, alpha))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def save_jpeg(raw: bytes, out: Path, wm: Optional[dict] = None) -> Tuple[int, int]:
    """Decode any image, write a JPEG, return (width, height)."""
    img = Image.open(BytesIO(raw))
    fmt, mode = img.format, img.mode
    w, h = img.size
    if fmt == "JPEG" and mode == "RGB" and not (wm and wm.get("text")):
        out.write_bytes(raw)  # lossless fast path
        return w, h
    img.load()
    if mode in ("RGBA", "LA") or (mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    elif mode != "RGB":
        img = img.convert("RGB")
    if wm and wm.get("text"):
        img = _watermark(img, wm)
    img.save(out, "JPEG", quality=90)
    return img.size


def make_thumb(first: Path, out: Path) -> Path:
    with Image.open(first) as im:
        im = im.convert("RGB")
        w, h = im.size
        if h > w * 1.5:
            im = im.crop((0, 0, w, int(w * 1.45)))
        im.thumbnail((320, 320))
        q = 85
        while True:
            im.save(out, "JPEG", quality=q)
            if out.stat().st_size < 200 * 1024 or q <= 40:
                break
            q -= 15
    return out


def write_cbz(pages: List[PageInfo], out: Path) -> Path:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for i, (p, _, _) in enumerate(pages, 1):
            z.write(p, f"{i:04d}.jpg")
    return out


def _pdf_text(s: str) -> bytes:
    return b"<FEFF" + s.encode("utf-16-be").hex().upper().encode() + b">"


def write_pdf(pages: List[PageInfo], out: Path, title: str = "") -> Path:
    """Minimal streaming PDF writer: embeds the JPEG bytes untouched
    (no re-encoding, tiny memory use). Page size = image size in points,
    scaled down when it would exceed the 14400pt PDF limit."""
    n = len(pages)
    offsets = {}
    with open(out, "wb") as f:
        def obj(num: int, body: bytes):
            offsets[num] = f.tell()
            f.write(b"%d 0 obj\n" % num + body + b"\nendobj\n")

        f.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        kids = b" ".join(b"%d 0 R" % (6 + 3 * i) for i in range(n))
        obj(2, b"<< /Type /Pages /Count %d /Kids [%s] >>" % (n, kids))
        obj(3, b"<< /Title " + _pdf_text(title) + b" /Producer (MangaBot) >>")
        for i, (path, w, h) in enumerate(pages):
            data = Path(path).read_bytes()
            img_id, cont_id, page_id = 4 + 3 * i, 5 + 3 * i, 6 + 3 * i
            offsets[img_id] = f.tell()
            f.write(b"%d 0 obj\n<< /Type /XObject /Subtype /Image /Width %d /Height %d "
                    b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
                    b"/Length %d >>\nstream\n" % (img_id, w, h, len(data)))
            f.write(data)
            f.write(b"\nendstream\nendobj\n")
            scale = min(1.0, 14000.0 / max(w, h))
            pw, ph = round(w * scale, 2), round(h * scale, 2)
            content = b"q %s 0 0 %s 0 0 cm /Im0 Do Q" % (str(pw).encode(), str(ph).encode())
            obj(cont_id, b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
            obj(page_id, (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] "
                          b"/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>")
                % (str(pw).encode(), str(ph).encode(), img_id, cont_id))
        total = 4 + 3 * n
        xref = f.tell()
        f.write(b"xref\n0 %d\n0000000000 65535 f \n" % total)
        for num in range(1, total):
            f.write(b"%010d 00000 n \n" % offsets[num])
        f.write(b"trailer\n<< /Size %d /Root 1 0 R /Info 3 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                % (total, xref))
    return out
