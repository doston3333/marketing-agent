# Image types for the AI Station agent (MiniMax stays the generator for concept art).
# Per platform, spec[p]["art"] picks the background:
#   "minimax" (default when image_prompt is set)  concept illustration, several candidates possible
#   "photo"   a real photo: spec[p]["photo"] = article URL (its og:image is used), image URL, "tg:<file_id>"
#             (a photo a lead sent to the bot) or a local path
#   "card"    no background art: the brand template, best for a big number / deadline / quote
# spec[p]["art_path"] pins an already-rendered background (chosen by Claude after `agent.py render`).
# post["carousel"] = {"instagram": [slides], "linkedin": [slides]} adds swipeable slides; slide =
#   {"title": "... [[highlight]] ...", "body": "...", "stat": "optional"}. LinkedIn slides also go out as a PDF.
import io, os, re, time
import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageOps
import agent_lib as A
import sources as S

SLIDE = (1080, 1350)


def _open_bytes(b):
    return Image.open(io.BytesIO(b)).convert("RGB")


def tg_file(file_id):
    d = A.tg("getFile", {"file_id": file_id})
    path = d["result"]["file_path"]
    tok = A.secrets()["telegram_bot_token"]
    r = requests.get(f"https://api.telegram.org/file/bot{tok}/{path}", timeout=60)
    r.raise_for_status()
    return r.content


def photo_art(ref):
    """Resolve a photo reference to a PIL image (or raise)."""
    ref = str(ref).strip()
    if ref.startswith("tg:"):
        return _open_bytes(tg_file(ref[3:]))
    if os.path.exists(ref):
        return Image.open(ref).convert("RGB")
    if re.search(r"\.(jpe?g|png|webp)(\?|$)", ref, re.I):
        url = ref
    else:
        url = S.og_image(ref)
        if not url:
            raise RuntimeError(f"no og:image on {ref}")
    r = requests.get(url, headers=S.UA, timeout=40)
    r.raise_for_status()
    img = _open_bytes(r.content)
    if min(img.size) < 400:
        raise RuntimeError(f"photo too small {img.size}")
    return img


def brand_tint(img, amount=0.55):
    """Duotone a real photo towards the brand indigo/cyan so text stays readable and photos look like one family."""
    g = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    duo = ImageOps.colorize(g, black=A.BG, white=(196, 214, 255), mid=(70, 90, 190)).convert("RGB")
    out = Image.blend(img.convert("RGB"), duo, amount)
    return ImageEnhance.Brightness(out).enhance(0.82)


def background(platform, sp, seed, candidate=0):
    """Returns (art_or_None, source_label, error)."""
    if sp.get("art_path") and os.path.exists(sp["art_path"]):
        return Image.open(sp["art_path"]).convert("RGB"), "pinned", None
    kind = sp.get("art") or ("minimax" if sp.get("image_prompt") else "card")
    try:
        if kind == "photo" and sp.get("photo"):
            art = photo_art(sp["photo"])
            return (art if sp.get("tint") is False else brand_tint(art)), "photo", None
        if kind == "minimax" and sp.get("image_prompt"):
            prompt = sp["image_prompt"] if candidate == 0 else sp["image_prompt"] + f" Alternative composition #{candidate + 1}."
            return A.minimax(prompt, platform), "minimax", None
        return None, "card", None
    except Exception as ex:
        return None, "card", f"{kind}: {str(ex)[:160]}"


def render_candidates(spec, post_id, n=2, platforms=("instagram", "telegram", "linkedin")):
    """For MiniMax platforms, make n backgrounds each and compose previews so Claude can look and pick.
    Returns {platform: [{"art_path", "preview", "source", "error"}]}."""
    from concurrent.futures import ThreadPoolExecutor
    outdir = f"{A.WORK}/art/{post_id}"
    os.makedirs(outdir, exist_ok=True)
    jobs = []
    for p in platforms:
        sp = dict(spec[p])
        sp.pop("art_path", None)
        kind = sp.get("art") or ("minimax" if sp.get("image_prompt") else "card")
        for i in range(n if kind == "minimax" else 1):
            jobs.append((p, i, sp))

    def one(j):
        p, i, sp = j
        art, src, err = background(p, sp, spec.get("seed", 7) + i, candidate=i)
        ap = None
        if art is not None:
            ap = f"{outdir}/{p}_{i}.png"
            art.save(ap)
        prev = f"{outdir}/{p}_{i}_preview.jpg"
        A.compose(p, sp, spec.get("seed", 7) + i, art).save(prev, "JPEG", quality=85)
        return p, {"art_path": ap, "preview": prev, "source": src, "error": err}

    A.secrets()
    A._font_path()
    out = {}
    with ThreadPoolExecutor(4) as ex:
        for p, r in ex.map(one, jobs):
            out.setdefault(p, []).append(r)
    return out


# ---------------------------------------------------------------- carousel slides
def _slide_bg(seed, i):
    W, H = SLIDE
    img = Image.new("RGBA", (W, H), A.BG + (255,))
    A._glow(img, (W - 140 if i % 2 else 140, int(H * 0.16)), 360, A.BLUE, 70)
    A._glow(img, (W // 2, H + 60), 420, A.CYAN, 34)
    return img


def slide(s, i, n, seed=7, lang="uz", last=False):
    W, H = SLIDE
    img = _slide_bg(seed, i)
    d = ImageDraw.Draw(img)
    M = 90
    img.alpha_composite(A._logo(170), (M, M))
    pf = A.F(600, 26)
    pg = f"{i + 1}/{n}"
    d.text((W - M - d.textlength(pg, font=pf), M + 8), pg, font=pf, fill=A.mix(A.WHITE, 0.55))
    toks = A._tokens(A.clean(s.get("title", "")))
    for size in range(92, 48, -4):
        hf = A.F(800, size)
        lines = A._wrap_tokens(toks, hf, W - 2 * M, d)
        if len(lines) <= 4:
            break
    lh = int(size * 1.16)
    bf = A.F(500, 44)
    body = []
    for para in A.clean(s.get("body", "")).split("\n"):
        body += A._wrap(para, bf, W - 2 * M, d) + [""]
    body = body[:-1][:11]
    stat_h = 200 if s.get("stat") else 0
    block = stat_h + len(lines) * lh + 50 + len(body) * 62
    y = max(230, (H - 170 + 200) // 2 - block // 2)
    if s.get("stat"):
        d.text((M - 6, y), A.clean(s["stat"]), font=A.F(800, 160), fill=A.BLUE + (255,))
        y += stat_h
    for line in lines:
        x = M
        for j, (w, hl) in enumerate(line):
            t = w + (" " if j < len(line) - 1 else "")
            d.text((x, y), t, font=hf, fill=(A.CYAN if hl else A.WHITE) + (255,))
            x += d.textlength(t, font=hf)
        y += lh
    y += 50
    d.rectangle([M, y - 26, M + 90, y - 20], fill=A.CYAN + (255,))
    for ln in body:
        if ln:
            d.text((M, y), ln, font=bf, fill=A.mix(A.WHITE, 0.86))
        y += 62 if ln else 24
    ff = A.F(600, 28)
    foot = ("Saqlab qo‘ying va ulashing" if lang == "uz" else "Save this and share it") if last else \
        ("Davomi →" if lang == "uz" else "Swipe →")
    d.line([(M, H - 150), (W - M, H - 150)], fill=A.mix(A.WHITE, 0.18), width=2)
    d.text((M, H - 120), "@aistationuz", font=ff, fill=A.mix(A.WHITE, 0.6))
    d.text((W - M - d.textlength(foot, font=ff), H - 120), foot, font=ff, fill=A.CYAN + (255,))
    return img.convert("RGB")


def render_carousel(post, post_id, cover_paths=None):
    """Returns {"instagram": [jpg...], "linkedin": [jpg...], "linkedin_pdf": path} for platforms that have slides.
    Slide 1 is the platform cover image when given (cover_paths[p])."""
    car = post.get("carousel") or {}
    out = {}
    seed = post.get("spec", {}).get("seed", 7)
    for p, lang in (("instagram", "uz"), ("linkedin", "en")):
        slides = car.get(p) or []
        if not slides:
            continue
        paths = []
        cover = (cover_paths or {}).get(p)
        n = len(slides) + (1 if cover else 0)
        if cover:
            c = Image.open(cover).convert("RGB")
            if p == "linkedin":  # make the 1200x1200 cover fit a 1080x1350 document page
                page = Image.new("RGB", SLIDE, A.BG)
                c = c.resize((1080, 1080), Image.LANCZOS)
                page.paste(c, (0, 135))
                c = page
            path = f"{A.WORK}/out/{post_id}_{p}_slide0.jpg"
            c.save(path, "JPEG", quality=92)
            paths.append(path)
        for i, s in enumerate(slides):
            k = i + (1 if cover else 0)
            path = f"{A.WORK}/out/{post_id}_{p}_slide{k}.jpg"
            slide(s, k, n, seed, lang, last=(i == len(slides) - 1)).save(path, "JPEG", quality=92)
            paths.append(path)
        out[p] = paths
        if p == "linkedin":
            imgs = [Image.open(x).convert("RGB") for x in paths]
            pdf = f"{A.WORK}/out/{post_id}_linkedin_carousel.pdf"
            imgs[0].save(pdf, "PDF", save_all=True, append_images=imgs[1:], resolution=150)
            out["linkedin_pdf"] = pdf
    return out
