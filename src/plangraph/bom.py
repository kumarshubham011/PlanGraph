"""Bill-of-materials helpers shared by the ETL, the scenarios and the inventory model."""

import pandas as pd


def explode_bom(bom: pd.DataFrame, max_levels: int = 6) -> pd.DataFrame:
    """Flatten a multi-level BOM.

    Input: one row per direct link (assembly_id, component_id, qty).
    Output: one row per (top_id, component_id) with `qty` = how many units of the
    component are needed to build ONE unit of top_id, at any depth.

    Example: a bike needs 2 wheels and each wheel needs 36 spokes, so the output
    has (bike, wheel, 2) and (bike, spoke, 72).
    """
    direct = bom[["assembly_id", "component_id", "qty"]].astype({"qty": float})
    level = direct.rename(columns={"assembly_id": "top_id"})
    levels = [level]
    for _ in range(max_levels - 1):
        # Go one level deeper: the components of the current level's components.
        deeper = level.merge(
            direct, left_on="component_id", right_on="assembly_id", suffixes=("", "_child")
        )
        if deeper.empty:
            break
        deeper["qty"] = deeper["qty"] * deeper["qty_child"]
        level = deeper[["top_id", "component_id_child", "qty"]].rename(
            columns={"component_id_child": "component_id"}
        )
        levels.append(level)
    flat = pd.concat(levels, ignore_index=True)
    return flat.groupby(["top_id", "component_id"], as_index=False)["qty"].sum()


def component_requirements(flat_bom: pd.DataFrame, demand: pd.Series) -> pd.Series:
    """Turn demand for products into demand for every component underneath them.

    `demand` is indexed by product_id. A product's own demand is included too,
    because some purchased items (helmets, tyres) are sold directly.
    """
    demand = demand[demand > 0]
    needs = flat_bom.merge(demand.rename("demand"), left_on="top_id", right_index=True)
    dependent = (needs["qty"] * needs["demand"]).groupby(needs["component_id"]).sum()
    return dependent.add(demand, fill_value=0)
