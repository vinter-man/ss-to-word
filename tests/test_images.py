from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from ss_to_word.images import HEIF_SUPPORTED, find_images, load_image, natural_key


def test_natural_key_orders_numbers_numerically_and_ignores_case():
    names = ["IMG_10.jpg", "img_2.JPG", "IMG_1.png", "b.heic", "A.png", "IMG_2 (1).jpg"]
    assert sorted(names, key=natural_key) == [
        "A.png", "b.heic", "IMG_1.png", "img_2.JPG", "IMG_2 (1).jpg", "IMG_10.jpg",
    ]


def test_find_images_filters_and_sorts(tmp_path: Path):
    for name in ["10.png", "2.jpg", "1.jpeg", "notes.txt", ".hidden.png", "photo.HEIC", "x.webp"]:
        (tmp_path / name).write_bytes(b"")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "3.png").write_bytes(b"")
    assert [p.name for p in find_images(tmp_path)] == ["1.jpeg", "2.jpg", "10.png", "photo.HEIC", "x.webp"]


def test_find_images_rejects_non_directory(tmp_path: Path):
    with pytest.raises(NotADirectoryError):
        find_images(tmp_path / "missing")


def test_load_image_applies_exif_orientation(tmp_path: Path):
    im = Image.new("RGB", (40, 20), "white")
    im.paste((255, 0, 0), (0, 0, 8, 8))         # red square top-left
    exif = im.getexif()
    exif[0x0112] = 6                            # orientation: rotate 90° CW
    path = tmp_path / "rot.jpg"
    im.save(path, exif=exif.tobytes(), quality=100)
    loaded = load_image(path)
    assert loaded.size == (20, 40)
    assert loaded.mode == "RGB"
    red, green, blue = loaded.getpixel((17, 2))   # the red square moved to the top-right
    assert red > 200 and green < 80 and blue < 80


@pytest.mark.skipif(not HEIF_SUPPORTED, reason="pillow-heif not installed")
def test_load_heic_roundtrip(tmp_path: Path):
    im = Image.new("RGB", (64, 32), (10, 200, 30))
    path = tmp_path / "IMG_0001.HEIC"
    im.save(path, format="HEIF", quality=90)
    loaded = load_image(path)
    assert loaded.size == (64, 32)
    red, green, _blue = loaded.getpixel((10, 10))
    assert green > 150 and red < 60
    assert [p.name for p in find_images(tmp_path)] == ["IMG_0001.HEIC"]
