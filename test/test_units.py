# -*- coding: utf-8 -*-
"""Sequential unit labels for Place units."""

import pytest

from nena_rcl_tools.core.addressing.units import next_unit, preview


def test_increment_letter_and_unit_schemes():
    assert [next_unit("increment", i, 101)[0] for i in range(3)] == [101, 102, 103]
    assert [next_unit("letter", i, 101)[1] for i in range(3)] == ["A", "B", "C"]
    assert next_unit("letter", 0, 101) == (101, "A", "")
    assert next_unit("letter", 25, 101)[1] == "Z"
    assert next_unit("letter", 26, 101)[1] == "AA"
    assert next_unit("letter", 0, 101, start=3)[1] == "C"
    assert next_unit("unit", 0, 101, prefix="", start=1) == (101, "", "1")
    assert next_unit("unit", 1, 101, prefix="Unit ", start=1) == (101, "", "Unit 2")
    assert preview("letter", 101, "A", "") == "101A"
    assert preview("unit", 101, "", "Unit 1") == "Unit 1"
    assert preview("increment", 102, "", "") == "102"


def test_unknown_scheme_is_rejected():
    with pytest.raises(ValueError):
        next_unit("floor", 0, 101)
