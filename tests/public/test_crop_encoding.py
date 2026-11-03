"""A crop is kept as WebP: lossy when that takes at most half its lossless size -- a photograph
shrinks several times -- and lossless otherwise, where text and rules come out smaller and
exact. Whatever it is kept as, it is read back and sent to the model under its own type.
Invented pictures."""
from __future__ import annotations

import base64
import random
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from graphme.adapters.chat import image_part
from graphme.adapters.storage.assets import LocalAssetStore
from graphme.application.visual_crops import WEBP_LONGEST, put_crop, read_crop


def _png(picture: Image.Image) -> bytes:
    out = BytesIO()
    picture.save(out, "PNG")
    return out.getvalue()


def _photograph() -> bytes:
    """Noise over a gradient: what a photograph is to a compressor."""
    rng = random.Random(7)
    picture = Image.new("RGB", (400, 300))
    picture.putdata([(x * 255 // 400 + rng.randint(-30, 30), y * 255 // 300, rng.randint(0, 255))
                     for y in range(300) for x in range(400)])
    return _png(picture)


def _ruled_table() -> Image.Image:
    picture = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(picture)
    for y in range(0, 300, 30):
        draw.line((0, y, 400, y), fill="black")
        draw.text((10, y + 8), f"Pump {y // 30}   2,000 h", fill="black")
    return picture


def _is_webp(data: bytes) -> bool:
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def test_a_photograph_is_kept_lossy_and_a_ruled_table_exact(tmp_path: Path) -> None:
    store = LocalAssetStore(tmp_path)
    photo, table = _photograph(), _ruled_table()

    kept_photo, kept_table = put_crop(store, photo), put_crop(store, _png(table))

    back_photo, back_table = read_crop(kept_photo, store), read_crop(kept_table, store)
    assert back_photo is not None and back_table is not None
    assert _is_webp(back_photo) and _is_webp(back_table)
    assert store.path_for(kept_photo, ".webp").is_file()
    assert len(back_photo) * 4 <= len(photo)
    assert list(Image.open(BytesIO(back_table)).convert("RGB").getdata()) == list(
        table.getdata())  # lossless: every pixel as it was


def test_a_crop_longer_than_webp_holds_is_kept_shrunk_to_fit(tmp_path: Path) -> None:
    store = LocalAssetStore(tmp_path)
    tall = _png(Image.new("RGB", (300, WEBP_LONGEST + 2000), "white"))

    kept = read_crop(put_crop(store, tall), store)

    assert kept is not None and _is_webp(kept)
    assert Image.open(BytesIO(kept)).size[1] == WEBP_LONGEST


def test_a_webp_is_sent_to_the_model_as_a_webp() -> None:
    out = BytesIO()
    Image.new("RGB", (40, 30), "gray").save(out, "WEBP")

    url = image_part(out.getvalue())["image_url"]["url"]

    assert url.startswith("data:image/webp;base64,")
    assert _is_webp(base64.b64decode(url.split(",", 1)[1]))
