"""Inventory norms per SKU x CFA: safety stock, reorder point, days of cover.

Method (standard reorder-point model, 98% hub service level, z = 2.054):
  daily demand      d  = mean(6-month sales) / 30
  daily demand sd   sd = std(monthly sales) / sqrt(30)
  lead time         LT = production LT + plant->hub LT + hub->CFA LT   (days)
  lead time sd      sLT = sqrt(prod_var^2 + transit_var^2)
  safety stock      SS  = z * sqrt(LT * sd^2 + d^2 * sLT^2)
  reorder point     ROP = d * LT + SS
  days of cover     DOC = ROP / d
"""
import numpy as np
import pandas as pd

Z_98 = 2.0537


def compute_norms(data: dict) -> pd.DataFrame:
    sales = data["sales"].set_index(["SKU", "CFA"])[data["sales_cols"]]
    d_mean = sales.mean(axis=1) / 30.0
    d_std = sales.std(axis=1, ddof=1) / np.sqrt(30.0)

    lt = data["lt"].set_index(["SKU", "CFA"])
    total_lt = (lt["LT_prod"] + lt["LT_plant_hub"] + lt["LT_hub_cfa"]).astype(float)
    lt_std = np.sqrt(lt["Var_prod"].astype(float) ** 2 + lt["Var_transit"].astype(float) ** 2)

    idx = sales.index.intersection(lt.index)
    d, sd = d_mean.loc[idx], d_std.loc[idx]
    L, sL = total_lt.loc[idx], lt_std.loc[idx]

    ss = Z_98 * np.sqrt(L * sd**2 + (d**2) * (sL**2))
    rop = d * L + ss
    doc = np.where(d > 0, rop / d, 0.0)

    out = pd.DataFrame(
        {
            "Source Hub": lt.loc[idx, "SourceHub"],
            "Avg daily demand (kL)": d.round(3),
            "Lead time (days)": L,
            "Safety stock (kL)": ss.round(2),
            "Reorder point (kL)": rop.round(2),
            "Days of cover": np.round(doc, 1),
        },
        index=idx,
    ).reset_index()
    return out
