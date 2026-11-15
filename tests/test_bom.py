import pandas as pd

from plangraph.bom import component_requirements, explode_bom

# A bike needs 2 wheels; a wheel needs 36 spokes and 1 tyre.
BOM = pd.DataFrame(
    {
        "assembly_id": [1, 2, 2],
        "component_id": [2, 3, 4],
        "qty": [2.0, 36.0, 1.0],
    }
)


def test_explode_bom_multiplies_quantities_down_the_levels():
    flat = explode_bom(BOM).set_index(["top_id", "component_id"])["qty"]
    assert flat[(1, 2)] == 2  # bike -> wheel
    assert flat[(1, 3)] == 72  # bike -> spoke = 2 wheels x 36 spokes
    assert flat[(2, 3)] == 36  # wheel -> spoke


def test_component_requirements_includes_direct_demand():
    demand = pd.Series({1: 10.0, 4: 5.0})  # 10 bikes, plus 5 tyres sold on their own
    needs = component_requirements(explode_bom(BOM), demand)
    assert needs[2] == 20  # wheels
    assert needs[3] == 720  # spokes
    assert needs[4] == 25  # 20 tyres for bikes + 5 sold directly
