# -*- coding: utf-8 -*-
"""Pure tests for address-number calculation."""

from nena_addressing_and_rcl_tools.core.addressing import numbering as A

FT = 0.3048


def S(**kw):
    return A.NumberingSettings(**kw)


def test_side_of():
    assert A.side_of(1, 0, 0, 5) == "L" and A.side_of(1, 0, 0, -5) == "R"
    assert A.side_of(0, 1, -1, 0) == "L"      # heading north, point to the west
    assert A.side_of(1, 0, 3, 0) is None


def test_distance_method_default_1000_per_mile():
    s = S()
    # 528 ft from the origin = 100 numbers -> 200; left odd -> 201 or 199 (nearest: equidistant -> lower)
    r = A.calculate(s, "O", [], 528 * FT)
    assert r.raw == 200.0 and r.number in (199, 201)
    assert A.calculate(s, "E", [], 528 * FT).number == 200
    assert A.calculate(S(rounding="down"), "O", [], 530 * FT).number == 199
    assert A.calculate(s, "E", [], 0.0).number == 100       # road start: 100 even / 101 odd
    assert A.calculate(s, "O", [], 0.0).number in (99, 101)
    assert A.calculate(S(rounding="down"), "O", [], 6 * FT).number == 101


def test_interpolate_and_frontage():
    r = A.calculate(S(method=A.M_INTERPOLATE), "O", [], 30.0, frac=0.5, seg_from=101, seg_to=199)
    assert r.number in (149, 151)
    assert not A.calculate(S(method=A.M_INTERPOLATE), "O", [], 30.0, frac=0.5).ok
    f = S(method=A.M_FRONTAGE, frontage_m=50 * FT, frontage_step=2)
    assert A.calculate(f, "E", [], 149 * FT).number == 104      # 2 whole lots
    assert A.calculate(f, "O", [], 149 * FT).number in (103, 105)


def test_order_check_keeps_numbers_increasing():
    s = S()
    nb = [A.Neighbor(10.0, 121), A.Neighbor(40.0, 125)]
    r = A.resolve(101, "O", 20.0, nb, s)          # raw says 101 but it sits after 121
    assert r.number == 123 and "raised" in r.message
    r = A.resolve(131, "O", 20.0, nb, s)          # too high -> below 125
    assert r.number == 123
    assert A.resolve(101, "O", 20.0, nb, S(order_check=False)).number == 101


def test_collisions():
    nb = [A.Neighbor(10.0, 121), A.Neighbor(30.0, 123)]
    # no odd number between 121 and 123 -> suffix on the previous address
    r = A.resolve(121, "O", 20.0, nb, S())
    assert (r.number, r.suffix) == (121, "A")
    # an earlier 125 means the new address simply follows it
    nb2 = [A.Neighbor(10.0, 121), A.Neighbor(15.0, 125), A.Neighbor(50.0, 131)]
    assert A.resolve(125, "O", 20.0, nb2, S()).number == 127
    # without the order check a taken number triggers the collision rule
    off = dict(order_check=False)
    assert A.resolve(125, "O", 20.0, nb2, S(**off)).number == 127
    r = A.resolve(125, "O", 20.0, nb2, S(collision=A.C_SUFFIX, **off))
    assert (r.number, r.suffix) == (125, "A")
    r = A.resolve(125, "O", 20.0, nb2 + [A.Neighbor(15.0, 125, "A")], S(collision=A.C_SUFFIX, **off))
    assert r.suffix == "B"
    r = A.resolve(125, "O", 20.0, nb2, S(collision=A.C_ASK, **off))
    assert r.needs_choice and r.options == ["127", "125A"] and r.number is None


def test_db_integrity_sql():
    from nena_addressing_and_rcl_tools.core.streets.dbsql import build_sql
    parts = ["", "St_PreDir", "", "", "St_Name", "St_PosTyp", "St_PosDir", ""]
    sql = build_sql(("gis", "street_names", "StreetID", ["St_PreMod", "St_PreDir", "St_PreTyp", "St_PreSep",
                                                          "St_Name", "St_PosTyp", "St_PosDir", "St_PosMod"]),
                    [("gis", "rcl", "StreetID", parts), ("gis", "ssap", "streetid", parts)])
    assert 'UNIQUE ("StreetID")' in sql
    assert 'FOREIGN KEY ("streetid") REFERENCES "gis"."street_names" ("StreetID")' in sql
    assert 'UPDATE "gis"."rcl" SET "St_PreDir" = NEW."St_PreDir", "St_Name" = NEW."St_Name"' in sql
    assert sql.count("CREATE TRIGGER") == 3 and sql.strip().endswith("COMMIT;")
