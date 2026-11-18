# -*- coding: utf-8 -*-
"""Reversing a centerline keeps every house on its side; label expressions for the map styles."""

from nena_addressing_and_rcl_tools.core.direction import flip_side, reversal_values
from nena_addressing_and_rcl_tools.core.styles import (address_label_expression, full_name_expression, quote_field,
                                        range_expression)


def test_reversing_keeps_each_side_with_its_numbers():
    before = {"FromAddr_L": 101, "ToAddr_L": 199, "FromAddr_R": 100, "ToAddr_R": 198,
              "Parity_L": "O", "Parity_R": "E", "ESN_L": "101", "ESN_R": "202",
              "PostCode_L": "49001", "PostCode_R": "49001", "OneWay": "FT", "St_Name": "Main"}
    after = dict(before, **reversal_values(before))
    # The old right side (even, 100 at the start, 198 at the end) is now on the left, running 198 -> 100.
    assert (after["FromAddr_L"], after["ToAddr_L"], after["Parity_L"], after["ESN_L"]) == (198, 100, "E", "202")
    assert (after["FromAddr_R"], after["ToAddr_R"], after["Parity_R"], after["ESN_R"]) == (199, 101, "O", "101")
    assert after["OneWay"] == "TF"
    assert after["St_Name"] == "Main"
    assert "PostCode_L" not in reversal_values(before)          # same on both sides: nothing to write
    again = dict(after, **reversal_values(after))
    assert again == before                                      # reversing twice is a no-op


def test_one_way_both_and_missing_pairs_are_left_alone():
    assert reversal_values({"OneWay": "B"}) == {}
    assert reversal_values({"OneWay": None}) == {}
    assert reversal_values({"ESN_L": "1"}) == {}                # no right-hand field to swap with
    assert reversal_values({"FromAddr_L": 1, "ToAddr_L": 9}) == {}


def test_address_side_flips():
    assert flip_side("L") == "R"
    assert flip_side(" r ") == "L"
    assert flip_side("") == ""
    assert flip_side(None) is None


def test_road_label_uses_full_name_then_parts():
    parts = full_name_expression({"St_PreDir": "PREDIR", "St_Name": "STNAME", "St_PosTyp": "SUFFIX"})
    assert '"PREDIR"' in parts and '"STNAME"' in parts and '"SUFFIX"' in parts
    assert "regexp_replace" in parts
    full = full_name_expression({"FullName": "FULL_NAME", "St_Name": "STNAME"})
    assert full.startswith('coalesce(nullif(trim("FULL_NAME")')
    assert full_name_expression({}) == "''"
    assert quote_field('we"ird') == '"we""ird"'


def test_range_labels_name_the_end_and_each_side():
    fields = {"FromAddr_L": "L_F_ADD", "FromAddr_R": "R_F_ADD", "ToAddr_L": "L_T_ADD", "ToAddr_R": "R_T_ADD"}
    start = range_expression(fields, "From")
    end = range_expression(fields, "To")
    assert start.startswith("'From'") and '"L_F_ADD"' in start and '"R_F_ADD"' in start
    assert end.startswith("'To'") and '"L_T_ADD"' in end
    assert range_expression({}, "From") is None


def test_address_label_has_number_suffix_and_unit():
    text = address_label_expression({"Add_Number": "Add_Number", "AddNum_Suf": "AddNum_Suf", "Unit": "Unit"})
    assert text.startswith('concat("Add_Number", "AddNum_Suf")')
    assert "' #'" in text
    assert address_label_expression({}) is None



def test_one_way_values_and_arrow_expressions():
    from nena_addressing_and_rcl_tools.core.direction import ONE_WAY_CHOICES, normalize_one_way
    from nena_addressing_and_rcl_tools.core.styles import one_way_expressions
    assert [c[0] for c in ONE_WAY_CHOICES] == ["B", "FT", "TF"]
    assert normalize_one_way(" ft ") == "FT" and normalize_one_way("x") is None and normalize_one_way(None) is None
    is_one_way, angle = one_way_expressions({"OneWay": "ONEWAY"})
    assert "\"ONEWAY\"" in is_one_way and "'FT', 'TF'" in is_one_way
    assert "'TF', 180, 0" in angle
    assert one_way_expressions({}) is None


def test_reversing_flips_the_direction_of_travel():
    assert reversal_values({"OneWay": "FT"}) == {"OneWay": "TF"}
    assert reversal_values({"OneWay": "tf"}) == {"OneWay": "FT"}
