from plangraph.eval.run_eval import contains, grade, numbers_in


def test_numbers_are_parsed_with_commas_and_units():
    assert numbers_in("about $319,813 or 0.32 million, roughly 320k") == [
        319813.0,
        320000.0,
        320000.0,
    ]


def test_contains_matches_numbers_within_tolerance():
    assert contains("Revenue at risk is $320k.", 319813.22)
    assert not contains("Revenue at risk is $250k.", 319813.22)
    assert contains("The vendor is custom frames, inc.", "Custom Frames, Inc.")


def test_grade_all_vs_any():
    answer = "Advanced Bicycles can supply it."
    expected = ["Advanced Bicycles", "WestAmerica Bicycle Co."]
    assert not grade(answer, expected, "all")
    assert grade(answer, expected, "any")
    assert not grade(answer, [], "any")  # no ground truth means not correct
