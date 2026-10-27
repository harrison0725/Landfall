from types import SimpleNamespace

import numpy as np
import pytest
from meta_sam_parser import _mask_codec as mc

from landfall import sam


def test_native_decoder_matches_the_parser():
    if not sam._decoder():
        pytest.skip("landfall/native/maskdec.so not built")
    rng = np.random.default_rng(0)
    h, w = 37, 53
    yy, xx = np.mgrid[:h, :w]
    blob = ((yy - 18) ** 2 + (xx - 25) ** 2 < 200) ^ (rng.random((h, w)) < 0.05)
    raster = blob.astype(np.uint8).ravel().tolist()
    payload = mc._encode_raster(raster, w, h)
    mask = SimpleNamespace(payload=payload, width=w, height=h)
    assert sam.decode_mask(mask) == bytes(raster)
