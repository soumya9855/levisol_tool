"""Load and tidy all exhibits from the Levisol case-study workbook."""
import re
import pandas as pd

BANDS = ["<=1.5 LT", "3-5 LT", "7-20 LT", "50 LT", "180-210 LT"]
HUBS = ["MHW", "MHE"]
HUB_LABELS = {"MHW": "Mother Hub West", "MHE": "Mother Hub East"}
PLANTS = ["BOM", "AHM", "KOL"]


def unit_size_litres(pack: str) -> float:
    """'20 X 900 ML' -> 0.9 ; '1 X 180 KG' -> 180 (kg treated as litres)."""
    m = re.match(r"\s*(\d+)\s*X\s*([\d.]+)\s*(ML|LT|KG|L)\s*", str(pack), re.I)
    if not m:
        raise ValueError(f"Unparseable pack size: {pack}")
    qty = float(m.group(2))
    unit = m.group(3).upper()
    return qty / 1000.0 if unit == "ML" else qty


def pack_band(pack: str) -> str:
    u = unit_size_litres(pack)
    if u <= 1.5:
        return BANDS[0]
    if 3 <= u <= 5:
        return BANDS[1]
    if 7 <= u <= 20:
        return BANDS[2]
    if u == 50:
        return BANDS[3]
    if 180 <= u <= 210:
        return BANDS[4]
    raise ValueError(f"Pack size {pack} ({u} L) fits no capacity band")


def load_workbook(path: str) -> dict:
    xl = pd.ExcelFile(path)

    # --- Exhibit A: plants ---
    A = xl.parse("A - Plants & Production", header=2).dropna(how="all")
    A = A[A["Plant Code"].isin(PLANTS)].set_index("Plant Code")
    cap_cols = [c for c in A.columns if "Line Capacity" in str(c)]
    capacity = A[cap_cols].copy()
    capacity.columns = BANDS
    capacity = capacity.astype(float)
    prod_cost = A["Production Cost (₹/kl)"].astype(float)

    # --- Exhibit B: plant->hub transport ---
    B = xl.parse("B - Plant-Hub Transport", header=2).dropna(how="all")
    B = B[B["From Plant"].notna()].iloc[:3]
    name2code = {"Mumbai": "BOM", "Ahmedabad": "AHM", "Kolkata": "KOL"}
    ph_cost = pd.DataFrame(
        {"MHW": B.iloc[:, 1].values, "MHE": B.iloc[:, 2].values},
        index=[name2code[n] for n in B["From Plant"]],
    ).astype(float)

    # --- Exhibit C: hub->CFA transport ---
    C = xl.parse("C -Hub-CFA Transport", header=2).dropna(how="all")
    C = C[C["CFA"].notna() & (C["CFA"] != "CFA")]
    C = C[~C["CFA"].astype(str).str.contains("supplied", na=False)]
    hc_cost = pd.DataFrame(
        {"MHW": C.iloc[:, 2].values, "MHE": C.iloc[:, 3].values},
        index=[str(c).strip() for c in C["CFA"]],
    ).astype(float)
    cfa_region = dict(zip([str(c).strip() for c in C["CFA"]], C["Region"]))

    # --- Exhibit D: SKU portfolio ---
    D = xl.parse("D -SKU Portfolio+Penalty matrix", header=2).dropna(how="all")
    D = D[D["Product Name"].astype(str).str.startswith("SKU")]
    sku = D.rename(
        columns={
            "Product Name": "SKU",
            "Pack size": "Pack",
            "Penalty cost (per kL)": "Penalty",
            "Contractual?": "Contractual",
        }
    )[["SKU", "Pack", "Penalty", "Contractual"]].copy()
    sku["Contractual"] = sku["Contractual"].astype(str).str.upper().str.contains("YES")
    sku["Band"] = sku["Pack"].map(pack_band)
    sku = sku.set_index("SKU")

    def norm_cfa(s):  # 'Kolkata CFA' -> 'Kolkata'
        return str(s).replace(" CFA", "").strip()

    # --- Exhibit E: sourcing + lead times ---
    E = xl.parse("E - Source + LT data", header=2).dropna(how="all")
    E = E[E["Product Name"].astype(str).str.startswith("SKU")].copy()
    E["CFA"] = E["CFA"].map(norm_cfa)
    E = E.rename(
        columns={
            "Product Name": "SKU",
            "LT (Plant to Hub)(in  days)": "LT_plant_hub",
            "LT (Hub to CFA ) (in  days)": "LT_hub_cfa",
            "Production lead time (in  days)": "LT_prod",
            "Production variability (in  days)": "Var_prod",
            "Transit lead variability (in  days)": "Var_transit",
        }
    )
    E["SourceHub"] = E["Source"].map(lambda s: "MHE" if str(s).strip().lower() == "east" else "MHW")
    lt = E[["SKU", "CFA", "SourceHub", "LT_plant_hub", "LT_hub_cfa", "LT_prod", "Var_prod", "Var_transit"]]

    # --- Exhibits G/H: sales & forecast history ---
    def hist(sheet, header):
        d = xl.parse(sheet, header=header).dropna(how="all")
        d = d[d["Product Name"].astype(str).str.startswith("SKU")].copy()
        d["CFA"] = d["CFA"].map(norm_cfa)
        d = d.rename(columns={"Product Name": "SKU"})
        mcols = [c for c in d.columns if "-25" in str(c)]
        return d[["SKU", "CFA"] + mcols], mcols

    sales, sales_cols = hist("G - Sales History", 2)
    fcst, fcst_cols = hist("H - Forecast History", 3)

    # --- Exhibit I: opening inventory (CFA rows + hub rows) ---
    I = xl.parse("I - Expected opening Inventory", header=3).dropna(how="all")
    I = I[I["Product Name"].astype(str).str.startswith("SKU")].copy()
    val = [c for c in I.columns if "Jan" in str(c)][0]
    hub_mask = I["CFA"].astype(str).str.contains("Mother Hub")
    open_hub = I[hub_mask].copy()
    open_hub["Hub"] = open_hub["CFA"].map(lambda s: "MHE" if "East" in s else "MHW")
    open_hub = open_hub.rename(columns={"Product Name": "SKU", val: "OpenKL"})[["SKU", "Hub", "OpenKL"]]
    open_cfa = I[~hub_mask].copy()
    open_cfa["CFA"] = open_cfa["CFA"].map(norm_cfa)
    open_cfa = open_cfa.rename(columns={"Product Name": "SKU", val: "OpenKL"})[["SKU", "CFA", "OpenKL"]]

    # --- Exhibit J: January forecast ---
    J = xl.parse("J - Jan Forecast", header=3).dropna(how="all")
    J = J[J["Product Name"].astype(str).str.startswith("SKU")].copy()
    valj = [c for c in J.columns if "Jan" in str(c)][0]
    J["CFA"] = J["CFA"].map(norm_cfa)
    demand = J.rename(columns={"Product Name": "SKU", valj: "DemandKL"})[["SKU", "CFA", "DemandKL"]]

    # --- Derive service tiers from 6-month sales volume (Exhibit F slabs) ---
    vol = sales.set_index(["SKU", "CFA"])[sales_cols].sum(axis=1).groupby("SKU").sum()
    vol = vol.reindex(sku.index).fillna(0.0).sort_values(ascending=False)
    cum = vol.cumsum() / vol.sum()
    tier = pd.Series("D", index=vol.index)
    tier[cum <= 0.95] = "C"
    tier[cum <= 0.80] = "B"
    tier[cum <= 0.50] = "A"
    sku["Tier"] = tier.reindex(sku.index)
    sku["Vol6m"] = vol.reindex(sku.index)

    return dict(
        capacity=capacity, prod_cost=prod_cost, ph_cost=ph_cost, hc_cost=hc_cost,
        cfa_region=cfa_region, sku=sku, lt=lt, sales=sales, sales_cols=sales_cols,
        fcst=fcst, fcst_cols=fcst_cols, open_cfa=open_cfa, open_hub=open_hub,
        demand=demand,
    )
