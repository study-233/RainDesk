import asyncio
import io
import hashlib
from urllib.parse import urlsplit

from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from rain import image_bytes, jpeg


async def slide_image(store, owner, lesson, deck, index, slide):
    url = slide.get("cover") or slide.get("coverAlt") or slide.get("thumbnail")
    url_path = urlsplit(url or "")._replace(query="",fragment="").geturl()
    digest = hashlib.sha256("\0".join(map(str,(owner,lesson,deck,index,slide.get("id"),url_path))).encode()).hexdigest()
    cache = store.root / "pages"
    cache.mkdir(exist_ok=True)
    path = cache / (digest+".jpg")
    if path.exists():
        return await asyncio.to_thread(path.read_bytes)
    data = await asyncio.to_thread(jpeg, await image_bytes(url))
    # One event loop owns writes; atomic replacement survives an interrupted export.
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return data


def export_pages(images, fmt):
    out = io.BytesIO()
    if fmt == "pdf":
        pages = []
        try:
            for image in images:
                pages.append(Image.open(io.BytesIO(image)))
            pages[0].save(out, format="PDF", save_all=True, append_images=pages[1:])
        finally:
            for page in pages:
                page.close()
    else:
        ppt = Presentation()
        with Image.open(io.BytesIO(images[0])) as im:
            ppt.slide_width = Inches(12)
            ppt.slide_height = int(ppt.slide_width * im.height / im.width)
        for raw in images:
            slide = ppt.slides.add_slide(ppt.slide_layouts[6])
            slide.shapes.add_picture(io.BytesIO(raw), 0, 0, ppt.slide_width, ppt.slide_height)
        ppt.save(out)
    return out.getvalue()
