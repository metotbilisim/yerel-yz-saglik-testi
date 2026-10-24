"""HTML -> PNG (sistem Chrome'u, playwright) + tarama efekti (PIL/numpy). Hukuk g6'daki yöntemin genel hâli.

Kullanım (sektör gen modülünde):
    from render import page, render_documents
    docs = [{"html": page(body), "kalite": "normal"|"dusuk"}, ...]
    render_documents(docs, out_dir, rng)   # out_dir/evrak1.png ... yazar
"""
import os
import tempfile
from pathlib import Path

CHROME_CANDIDATES = [
    os.environ.get("CHROME_PATH", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

BASE_CSS = """
@page { size: 794px 1123px; margin: 0; }
html, body { margin: 0; padding: 0; background: #fff; }
body { font-family: 'Times New Roman', Times, serif; color: #111; width: 794px; height: 1123px; box-sizing: border-box; padding: 60px 64px; font-size: 15.5px; line-height: 1.5; position: relative; }
h1 { font-size: 19px; text-align: center; margin: 0 0 4px 0; letter-spacing: 0.5px; }
h2 { font-size: 15px; text-align: center; margin: 0 0 18px 0; font-weight: normal; }
.hdr { text-align: center; border-bottom: 2px solid #111; padding-bottom: 8px; margin-bottom: 18px; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; }
td, th { border: 1px solid #222; padding: 5px 8px; vertical-align: top; text-align: left; }
th { background: #eee; width: 32%; font-weight: bold; }
table.liste th { width: auto; }
.small { font-size: 12.5px; color: #333; }
.right { text-align: right; }
.stamp { position: absolute; right: 70px; bottom: 150px; border: 3px double #1a2a6c; color: #1a2a6c; padding: 8px 14px; transform: rotate(-8deg); font-weight: bold; font-size: 14px; text-align: center; opacity: .8; }
.sign { margin-top: 36px; display: flex; justify-content: space-between; }
.sign div { text-align: center; width: 45%; }
p { margin: 8px 0; text-align: justify; }
"""


def page(body, css=BASE_CSS):
    return f"<!doctype html><html lang='tr'><head><meta charset='utf-8'><style>{css}</style></head><body>{body}</body></html>"


def find_chrome():
    for c in CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    raise FileNotFoundError("Chrome bulunamadı; CHROME_PATH ortam değişkenini ayarlayın.")


def html_to_png(html_list, out_paths, width=794, height=1123, scale=1.4):
    """Her HTML dizgesini ilgili yola PNG olarak yazar (tarama efekti yok)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=find_chrome(), headless=True)
        ctx = b.new_context(viewport={"width": width, "height": height}, device_scale_factor=scale)
        pg = ctx.new_page()
        for html, path in zip(html_list, out_paths):
            pg.set_content(html)
            pg.wait_for_timeout(200)
            pg.screenshot(path=str(path), full_page=False)
        b.close()


def scan_effect(src, dst, rng, kalite="normal", angle=None):
    """Taranmış belge görünümü: ±1,5° döndürme, gri ton, soluk kâğıt, gürültü, kenar gölgesi; kalite='dusuk' ise bulanıklık."""
    from PIL import Image, ImageFilter
    import numpy as np
    nrng = np.random.default_rng(rng.randint(0, 2 ** 31 - 1))
    im = Image.open(src).convert("L")
    ang = angle if angle is not None else rng.choice([-1.5, 1.2, -0.8, 1.5, -1.0])
    im = im.rotate(ang, resample=Image.BICUBIC, expand=False, fillcolor=235)
    arr = np.asarray(im).astype(np.float32)
    arr = arr * 0.93 + 8
    arr += nrng.normal(0, 7.0, arr.shape)
    h, w = arr.shape
    yy, xx = np.mgrid[0:h, 0:w]
    vign = 1 - 0.07 * (((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / 2
    arr = arr * vign
    im = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
    if kalite == "dusuk":
        im = im.resize((int(w * 0.62), int(h * 0.62)), Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.1)).resize((w, h), Image.BILINEAR)
        im = im.filter(ImageFilter.GaussianBlur(0.8))
    im.save(dst, optimize=True)


def render_documents(docs, out_dir, rng, prefix="evrak"):
    """docs: [{"html":..., "kalite": "normal"|"dusuk"}]. out_dir/<prefix><i>.png yazar; yazılan dosya adlarını döndürür."""
    out_dir = Path(out_dir)
    names = []
    with tempfile.TemporaryDirectory() as tmp:
        raw = [Path(tmp) / f"{prefix}{i}.png" for i in range(1, len(docs) + 1)]
        html_to_png([d["html"] for d in docs], raw)
        for i, (d, r) in enumerate(zip(docs, raw), 1):
            name = f"{prefix}{i}.png"
            scan_effect(r, out_dir / name, rng, d.get("kalite", "normal"))
            names.append(name)
    return names
