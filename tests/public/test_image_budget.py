"""What an image costs: every picture sent to a model is held to the largest one a model reads
whole, and a picture inside an Office file is held to what a page can show before the viewer,
which refuses one past 2^27 pixels, ever decodes it. Invented images and files."""
from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path

from PIL import Image

from graphme.adapters.chat import MODEL_IMAGE_LONG_EDGE, MODEL_IMAGE_PIXELS, image_part
from graphme.adapters.rendering.embedded_images import EMBEDDED_LONG_EDGE, lightened


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _sent(part: dict[str, object]) -> tuple[int, int]:
    url = part["image_url"]["url"]  # type: ignore[index]
    data = base64.b64decode(str(url).split(",", 1)[1])
    size: tuple[int, int] = Image.open(io.BytesIO(data)).size
    return size


def test_a_page_larger_than_a_model_reads_whole_is_sent_at_that_size() -> None:
    width, height = _sent(image_part(_png(2380, 3368)))  # an A4 page drawn large

    assert max(width, height) <= MODEL_IMAGE_LONG_EDGE
    assert width * height <= MODEL_IMAGE_PIXELS
    assert abs(width / height - 2380 / 3368) < 0.01  # the page's shape kept


def test_a_wide_sheet_is_held_by_its_long_edge() -> None:
    width, height = _sent(image_part(_png(6000, 900)))

    assert width == MODEL_IMAGE_LONG_EDGE and height <= 900


def test_a_small_picture_is_sent_as_it_is() -> None:
    small = _png(640, 480)

    assert image_part(small)["image_url"]["url"].endswith(  # type: ignore[index]
        base64.b64encode(small).decode("ascii"))


def _office(path: Path, images: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/slides/slide1.xml", "<slide/>")
        for name, data in images.items():
            archive.writestr(name, data)
    return path


def test_a_picture_past_what_a_page_shows_is_scaled_down_in_a_copy(tmp_path: Path) -> None:
    source = _office(tmp_path / "deck.pptx", {
        "ppt/media/image1.png": _png(12000, 12000),  # 144 million pixels: the viewer refuses it
        "ppt/media/image2.png": _png(800, 600)})

    light = lightened(source, tmp_path / "work")

    assert light != source and source.read_bytes() == source.read_bytes()
    with zipfile.ZipFile(light) as archive:
        big = Image.open(io.BytesIO(archive.read("ppt/media/image1.png")))
        assert max(big.size) == EMBEDDED_LONG_EDGE
        assert archive.read("ppt/media/image2.png") == _png(800, 600)
        assert archive.read("ppt/slides/slide1.xml") == b"<slide/>"
    with zipfile.ZipFile(source) as original:  # the original is never touched
        assert Image.open(io.BytesIO(original.read("ppt/media/image1.png"))).size == (12000, 12000)


def test_a_file_whose_pictures_all_fit_is_drawn_from_itself(tmp_path: Path) -> None:
    source = _office(tmp_path / "deck.pptx", {"ppt/media/image1.png": _png(800, 600)})

    assert lightened(source, tmp_path / "work") == source


def test_a_pdf_is_not_opened_as_an_office_file(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    assert lightened(pdf, tmp_path / "work") == pdf


def test_a_picture_past_pillows_own_guard_is_still_measured_and_scaled(tmp_path: Path) -> None:
    # Pillow refuses to open a picture past ~179 million pixels unless told to: the very size
    # this is for. Without a warning either.
    import warnings

    from PIL import Image as PILImage

    buffer = io.BytesIO()
    PILImage.new("1", (15000, 13000)).save(buffer, format="PNG")  # 195 million pixels, small file
    source = _office(tmp_path / "deck.pptx", {"ppt/media/image1.png": buffer.getvalue()})

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        light = lightened(source, tmp_path / "work")

    with zipfile.ZipFile(light) as archive:
        assert max(PILImage.open(io.BytesIO(archive.read("ppt/media/image1.png"))).size) == \
            EMBEDDED_LONG_EDGE
