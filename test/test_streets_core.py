# -*- coding: utf-8 -*-
"""Pure tests for master street-name logic."""

from nena_addressing_and_rcl_tools.core.streets import names as N


def P(**kw):
    return N.parts_from(kw)


def test_street_types_standardize_and_round_trip():
    from nena_addressing_and_rcl_tools.core.project_config import ProjectConfig
    from nena_addressing_and_rcl_tools.core.streets import street_types as ST

    assert ST.display_type("ST") == "Street"
    assert ST.display_type("glen") == "Glen"
    assert ST.display_type("CO RD") == "County Road"
    assert ST.display_type("Loop") == "Loop"
    assert ST.display_type("Not A Type") == "Not A Type"
    assert "Glen" in ST.OFFICIAL_TYPES and "Path" in ST.OFFICIAL_TYPES and "Court" in ST.OFFICIAL_TYPES
    abbr = ST.merged_abbreviations({"PVT LN": "Lane"})
    assert N.name_key(P(St_Name="Oak", St_PosTyp="Glen")) == N.name_key(P(St_Name="Oak", St_PosTyp="GLN"))
    assert N.name_key(P(St_Name="Oak", St_PosTyp="Pvt Ln"), abbreviations=abbr) == \
        N.name_key(P(St_Name="Oak", St_PosTyp="Lane"), abbreviations=abbr)
    hits = N.find_conflicts(P(St_Name="Oak", St_PosTyp="GLN"), "",
                            [_rec("1", St_Name="Oak", St_PosTyp="Glen")], N.UniquenessRule())
    assert hits and hits[0].kind == "duplicate"
    cfg = ProjectConfig()
    assert cfg.addresses.create_driveway is False
    assert cfg.street_types.custom is False
    cfg.street_types.custom = True
    cfg.street_types.types = ["Street", "Glen"]
    cfg.street_types.abbreviations = {"GLN": "Glen"}
    again = ProjectConfig.from_json(cfg.to_json())
    assert again.street_types.custom is True
    assert again.street_types.types == ["Street", "Glen"]
    assert again.street_types.abbreviations["GLN"] == "Glen"
    legacy = ProjectConfig.from_dict({"addresses": {"create_driveway": True}})
    assert legacy.addresses.create_driveway is True
    assert legacy.street_types.types == []


def test_full_name_and_keys():
    p = P(St_PreDir="N", St_Name="Main", St_PosTyp="St")
    assert N.full_name(p) == "N Main St"
    assert N.name_key(p) == N.name_key(P(St_PreDir="North", St_Name="MAIN", St_PosTyp="Street"))
    assert N.name_key(p) != N.name_key(P(St_PreDir="S", St_Name="Main", St_PosTyp="St"))
    assert N.name_key(P(St_Name="Oak", St_PosTyp="St"), N.STRICT_IGNORE_TYPE) == \
        N.name_key(P(St_Name="Oak", St_PosTyp="Ave"), N.STRICT_IGNORE_TYPE)
    assert N.name_key(P(St_PreTyp="CO RD", St_Name="12")) == N.name_key(P(St_PreTyp="County Road", St_Name="12"))


def test_soundex():
    assert N.soundex("Robert") == "R163" and N.soundex("Rupert") == "R163"
    assert N.soundex("Smith") == N.soundex("Smyth")
    assert N.soundex("Ashcraft") == "A261"


def _rec(sid, scope="", status=N.STATUS_ACTIVE, alias_of="", **parts):
    return N.StreetRecord(sid, P(**parts), scope=scope, status=status, alias_of=alias_of)


def test_find_conflicts_rules():
    recs = [_rec("1", St_Name="Main", St_PosTyp="St"), _rec("2", St_Name="Smyth", St_PosTyp="Rd"),
            _rec("3", St_Name="Oak", St_PosTyp="Ave", scope="TOWN A"),
            _rec("4", St_Name="Elm", St_PosTyp="St", status=N.STATUS_RETIRED)]
    rule = N.UniquenessRule()
    c = N.find_conflicts(P(St_Name="MAIN", St_PosTyp="Street"), "", recs, rule)
    assert [(x.kind, x.other.street_id, x.blocking) for x in c] == [("duplicate", "1", True)]
    # renaming record 1 itself is not a conflict
    assert N.find_conflicts(P(St_Name="Main", St_PosTyp="St"), "", recs, rule, exclude_ids=["1"]) == []
    # sound-alike warns, never blocks
    c = N.find_conflicts(P(St_Name="Smith", St_PosTyp="Rd"), "", recs, rule)
    assert [(x.kind, x.blocking) for x in c] == [("soundalike", False)]
    # county-wide: Oak Ave in TOWN A collides with Oak Ave anywhere; per-field scope does not
    assert N.find_conflicts(P(St_Name="Oak", St_PosTyp="Ave"), "TOWN B", recs, rule)
    assert not N.find_conflicts(P(St_Name="Oak", St_PosTyp="Ave"), "TOWN B", recs,
                                N.UniquenessRule(scope_mode="field"))
    # ignore street type
    assert N.find_conflicts(P(St_Name="Main", St_PosTyp="Ave"), "", recs,
                            N.UniquenessRule(strictness=N.STRICT_IGNORE_TYPE))[0].kind == "duplicate"
    # retired names do not block reuse; warn mode never blocks
    assert not N.find_conflicts(P(St_Name="Elm", St_PosTyp="St"), "", recs, rule)
    assert not N.find_conflicts(P(St_Name="Main", St_PosTyp="St"), "", recs, N.UniquenessRule(action="warn"))[0].blocking


def test_variant_groups():
    feats = [(P(St_Name="Main", St_PosTyp="St"), "")] * 5 + [(P(St_Name="MAIN", St_PosTyp="ST"), "")] * 2 + \
            [(P(St_Name="Main", St_PosTyp="Street"), "")] + [(P(St_Name="Smith", St_PosTyp="Rd"), ""),
                                                              (P(St_Name="Smyth", St_PosTyp="Rd"), "")] + \
            [(P(), "")]
    groups = N.group_variants(feats)
    main = [g for g in groups if g.key == "MAIN STREET"][0]
    assert main.is_variant and len(main.spellings) == 3 and N.full_name(main.chosen_parts()) == "Main St"
    assert len(groups) == 3                     # unnamed ignored
    sa = N.soundalike_groups(groups)
    assert len(sa) == 1 and {N.full_name(g.chosen_parts()) for g in sa[0]} == {"Smith Rd", "Smyth Rd"}


def test_duplicate_groups_and_ids():
    recs = [_rec("1", St_Name="Main", St_PosTyp="St"), _rec("2", St_Name="MAIN", St_PosTyp="STREET"),
            _rec("3", St_Name="Oak")]
    assert [sorted(r.street_id for r in g) for g in N.duplicate_groups(recs, N.UniquenessRule())] == [["1", "2"]]
    sid = N.new_street_id()
    assert sid.startswith("{") and len(sid) == 38
