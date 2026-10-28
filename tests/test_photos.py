import io

import numpy as np
from PIL import Image

from landfall.photos import box_window, clean, lay, register


def _scene(seed=0, size=(600, 800)):
    """A made-up town from above: roofs of different shades on noisy ground."""
    rng = np.random.default_rng(seed)
    img = rng.integers(60, 110, (*size, 3)).astype(np.uint8)
    for _ in range(40):
        y, x = rng.integers(0, size[0] - 60), rng.integers(0, size[1] - 60)
        h, w = rng.integers(20, 60, 2)
        img[y : y + h, x : x + w] = rng.integers(120, 255, 3)
    return img


def test_register_finds_a_rotated_before_photo():
    after = _scene()
    before = np.asarray(Image.fromarray(after).rotate(7, resample=Image.BICUBIC).resize((700, 520)))
    warped, ok = register(before, after)
    inside = ok[100:-100, 100:-100]
    assert inside.mean() > 0.95
    diff = np.abs(warped.astype(int) - after.astype(int))[100:-100, 100:-100][inside]
    assert np.median(diff) < 12


def test_register_gives_up_on_a_different_place():
    assert register(_scene(1), _scene(2)) is None


def test_clean_drops_metadata_and_shrinks():
    im = Image.fromarray(np.zeros((5000, 3000, 3), np.uint8))
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x8825] = {2: (29.0, 8.0, 0.0)}  # a GPS block
    im.save(buf, "JPEG", exif=exif)
    out = Image.open(io.BytesIO(clean(buf.getvalue())))
    assert max(out.size) == 4000
    assert 0x8825 not in out.getexif()


def test_lay_without_a_before_photo_uses_grey(tmp_path):
    Image.fromarray(_scene()).save(tmp_path / "upload_after.jpg")
    bbox = (-83.0364, 29.1369, -83.0346, 29.1381)
    b = box_window(bbox, 19)
    from landfall.geo import Window

    area = Window(b.x0 - 10, b.y0 - 10, b.x1 + 10, b.y1 + 10)
    post, post_ok, pre, pre_ok, note = lay(tmp_path, bbox, 19, area)
    assert note == "no before photo"
    assert post.shape == pre.shape == (area.height, area.width, 3)
    assert not post_ok[:10].any() and post_ok[10:-10, 10:-10].all()
    assert (pre[post_ok] == 128).all()
