from pathlib import Path

from landfall.area import Area
from landfall.surroundings import classify, label

import numpy as np

DEMO = Path(__file__).resolve().parents[1] / "demo"


def test_group_whole_area_and_square():
    a = Area.load(DEMO, "cedar_key")
    every = a.group_buildings()
    assert every["buildings"] == len(a.buildings)
    assert sum(g["buildings"] for g in every["groups"]) == every["buildings"]
    # a square around a few buildings keeps only those
    xs = sorted(a.buildings.values(), key=lambda x: x["lon"])[:5]
    w, e = xs[0]["lon"] - 1e-5, xs[-1]["lon"] + 1e-5
    a.cell = {"label": "test", "polygon": [[w, 29.0], [e, 29.0], [e, 29.3], [w, 29.3], [w, 29.0]]}
    some = a.group_buildings(by=["green"])
    assert some["square"] == "test" and some["buildings"] == 5
    assert set(some["by_each_factor"]) == {"green"}


def test_green_label():
    grass = np.zeros((40, 40, 3), np.uint8) + np.array([120, 150, 60], np.uint8)
    s = classify(grass, np.ones((40, 40), bool))
    assert s["lawn"] > 0.9 and label(s) == "mostly lawn or grass"
    concrete = np.zeros((40, 40, 3), np.uint8) + 180
    assert label(classify(concrete, np.ones((40, 40), bool))).startswith("little green")
