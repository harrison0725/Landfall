from landfall.sections import cell_bbox, cells, label


def test_label_from_plss_id():
    assert label({"PLSSID": "FL290150S0130E0", "FRSTDIVLAB": "29"}) == "Sec 29 · T15S R13E"
    assert label({"PLSSID": "", "FRSTDIVNO": "05"}) == "Sec 5"


def test_cells_are_fixed():
    # the same spot falls in the same cell however wide the view is
    assert (-831, 291) in cells(-83.05, 29.12, -83.02, 29.15)
    assert (-831, 291) in cells(-83.4, 28.8, -82.6, 29.6)
    w, s, e, n = cell_bbox(-831, 291)
    assert (w, s, e, n) == (-83.1, 29.1, -83.0, 29.2)
