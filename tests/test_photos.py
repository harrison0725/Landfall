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


def test_locate_finds_a_turned_darker_photo_in_the_archive():
    from landfall.photos import locate

    archive = _scene(3, (900, 1200))
    photo = (archive[200:600, 300:900] * 0.5).astype(np.uint8)  # a darker camera
    photo = np.ascontiguousarray(np.rot90(photo, 1))  # and not north-up
    hit = locate(photo, archive, (560, 380))  # the user's box: a bit small
    assert hit is not None and hit["rot"] == 3
    assert abs(hit["x"] - 300) <= 12 and abs(hit["y"] - 200) <= 12 and abs(hit["w"] - 600) <= 30


def test_locate_gives_up_on_another_place():
    from landfall.photos import locate

    assert locate(_scene(4, (400, 600)), _scene(5, (900, 1200)), (600, 400)) is None


def test_a_new_disaster_described_by_the_uploader():
    import pytest
    from fastapi import HTTPException

    from landfall.app import _event
    from landfall.pipeline import AreaSpec, event_kind, event_name

    ev = _event({"kind": "flood", "date": "2026-10-20", "name": "  Big   river flood "})
    assert ev == {"kind": "flood", "date": "2026-10-20", "exact": True, "name": "Big river flood"}
    # only the year known: the before photo must predate the year
    yr = _event({"kind": "hurricane", "date": "2025"})
    assert (yr["date"], yr["exact"]) == ("2025-01-01", False)
    spec = AreaSpec.for_photos("upload_y", (10.0, 45.0, 10.01, 45.01), "", 20, event=yr)
    assert (spec.landfall, spec.gauges, event_name(spec)) == ("2025-01-01", [], "the 2025 hurricane")
    with pytest.raises(HTTPException):
        _event({"kind": "volcano", "date": "2026-10-20"})
    with pytest.raises(HTTPException):
        _event({"kind": "flood", "date": "2999-01-01"})
    spec = AreaSpec.for_photos("upload_x", (10.0, 45.0, 10.01, 45.01), "", 20, event={**ev, "name": ""})
    assert (spec.storm, spec.landfall, event_kind(spec), event_name(spec)) == ("", "2026-10-20", "flood", "the flood of 2026-10-20")
