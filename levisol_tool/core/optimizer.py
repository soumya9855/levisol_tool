"""Levisol monthly production & distribution MIP.

Decision variables
  batches[s,p]  integer >= 0        production batches of SKU s at plant p (1 batch = 25 kL)
  x[s,p,h]      continuous >= 0     kL of SKU s shipped plant p -> hub h
  y[s,h,c]      continuous >= 0     kL of SKU s shipped hub h -> CFA c
  unmet[s,c]    continuous >= 0     kL of net demand for (s,c) not served
  ss_short[h]   continuous >= 0     kL shortfall vs. hub safety-stock requirement

Constraints
  1. sum_h x[s,p,h] <= 25 * batches[s,p]              (ship no more than produced; leftover stays at plant)
  2. sum_{s in band} 25*batches[s,p] <= cap[p,band]   (pack-size line capacity per plant)
  3. sum_c y[s,h,c] <= open_hub[s,h] + sum_p x[s,p,h] (hub per-SKU balance; excess is retained)
  4. sum_h y[s,h,c] + unmet[s,c] = net_demand[s,c]    (serve or explicitly under-serve)
  5. hub retained stock + ss_short[h] >= ss_req[h]    (hub safety stock, soft)

Objective: minimize production + plant->hub transport + hub->CFA transport
           + effective penalty * unmet + ss_penalty * ss_short
Effective penalty = Exhibit-D penalty x tier weight x contractual multiplier,
which makes tier-A and contractual SKUs the last to be cut when capacity binds.
"""
import time
import pulp
import pandas as pd

from .data_loader import BANDS, HUBS, PLANTS


def solve_plan(
    capacity: pd.DataFrame,          # plant x band  (kL/month)
    prod_cost: pd.Series,            # plant -> ₹/kL
    ph_cost: pd.DataFrame,           # plant x hub   ₹/kL
    hc_cost: pd.DataFrame,           # CFA x hub     ₹/kL
    sku: pd.DataFrame,               # index SKU: Band, Penalty, Contractual, Tier
    demand: pd.DataFrame,            # SKU, CFA, DemandKL (already netted if desired)
    open_hub: pd.DataFrame,          # SKU, Hub, OpenKL
    ss_req: dict,                    # hub -> required safety stock kL
    tier_weight: dict,               # {'A':..,'B':..,'C':..,'D':..}
    contract_mult: float = 4.0,
    ss_penalty: float = 50000.0,
    batch_kl: float = 25.0,
    time_limit: int = 60,
):
    t0 = time.time()
    dem = demand[demand["DemandKL"] > 1e-9].copy()
    cfas = list(hc_cost.index)
    skus_active = sorted(dem["SKU"].unique())
    d = {(r.SKU, r.CFA): float(r.DemandKL) for r in dem.itertuples()}
    oh = {(r.SKU, r.Hub): float(r.OpenKL) for r in open_hub.itertuples()}

    eff_pen = {
        s: float(sku.loc[s, "Penalty"])
        * tier_weight.get(sku.loc[s, "Tier"], 1.0)
        * (contract_mult if sku.loc[s, "Contractual"] else 1.0)
        for s in sku.index
    }

    m = pulp.LpProblem("Levisol_Plan", pulp.LpMinimize)

    # cap batches per SKU-plant by band capacity AND by what could ever be useful:
    # the SKU's own total demand plus the full hub safety-stock requirement
    # (tight bounds make the MIP dramatically faster)
    import math
    sku_dem = dem.groupby("SKU")["DemandKL"].sum().to_dict()
    ss_total = sum(max(v_, 0.0) for v_ in ss_req.values())

    def max_batches(s, p):
        cap_b = int(capacity.loc[p, sku.loc[s, "Band"]] // batch_kl)
        need_b = math.ceil((sku_dem.get(s, 0.0) + ss_total) / batch_kl)
        return min(cap_b, need_b)

    b = {(s, p): pulp.LpVariable(f"b_{s}_{p}", 0, max_batches(s, p), cat="Integer")
         for s in skus_active for p in PLANTS}
    x = {(s, p, h): pulp.LpVariable(f"x_{s}_{p}_{h}", lowBound=0)
         for s in skus_active for p in PLANTS for h in HUBS}
    dem_keys = list(d.keys())
    y = {(s, h, c): pulp.LpVariable(f"y_{s}_{h}_{c}", lowBound=0)
         for (s, c) in dem_keys for h in HUBS}
    u = {(s, c): pulp.LpVariable(f"u_{s}_{c}", lowBound=0, upBound=d[(s, c)])
         for (s, c) in dem_keys}

    # Hub safety stock is held in high-velocity Tier A/B SKUs, allocated pro-rata
    # to their demand: the buffer protects the products that matter most, avoids
    # degenerate "hold the cheapest SKU" solutions, and avoids fragmenting the
    # buffer into tiny per-SKU quantities that would each force an extra 25 kL batch.
    ab = [s for s in skus_active if sku.loc[s, "Tier"] in ("A", "B") and sku_dem.get(s, 0) > 0]
    ab_dem = sum(sku_dem[s] for s in ab) or 1.0
    ss_tgt = {(s, h): 0.0 for s in skus_active for h in HUBS}
    for s in ab:
        for h in HUBS:
            ss_tgt[s, h] = max(ss_req.get(h, 0.0), 0.0) * sku_dem[s] / ab_dem
    ss_short = {(s, h): pulp.LpVariable(f"ssshort_{s}_{h}", 0, ss_tgt[s, h])
                for s in skus_active for h in HUBS}

    # 1. ship <= produced
    for s in skus_active:
        for p in PLANTS:
            m += pulp.lpSum(x[s, p, h] for h in HUBS) <= batch_kl * b[s, p]

    # 2. band capacity
    for p in PLANTS:
        for band in BANDS:
            members = [s for s in skus_active if sku.loc[s, "Band"] == band]
            if members:
                m += pulp.lpSum(batch_kl * b[s, p] for s in members) <= float(capacity.loc[p, band])

    # 3. hub per-SKU balance (outbound <= opening + inbound)
    cfa_of = {}
    for (s, c) in dem_keys:
        cfa_of.setdefault(s, []).append(c)
    for s in skus_active:
        for h in HUBS:
            m += (
                pulp.lpSum(y[s, h, c] for c in cfa_of[s])
                <= oh.get((s, h), 0.0) + pulp.lpSum(x[s, p, h] for p in PLANTS)
            )

    # 4. demand balance
    for (s, c) in dem_keys:
        m += pulp.lpSum(y[s, h, c] for h in HUBS) + u[s, c] == d[(s, c)]

    # 5. hub safety stock per SKU (retained = opening + inbound - outbound)
    for s in skus_active:
        for h in HUBS:
            if ss_tgt[s, h] > 1e-9:
                retained = (
                    oh.get((s, h), 0.0)
                    + pulp.lpSum(x[s, p, h] for p in PLANTS)
                    - pulp.lpSum(y[s, h, c] for c in cfa_of[s])
                )
                m += retained + ss_short[s, h] >= ss_tgt[s, h]

    m += (
        pulp.lpSum(batch_kl * b[s, p] * float(prod_cost[p]) for s in skus_active for p in PLANTS)
        + pulp.lpSum(x[s, p, h] * float(ph_cost.loc[p, h]) for s in skus_active for p in PLANTS for h in HUBS)
        + pulp.lpSum(y[s, h, c] * float(hc_cost.loc[c, h]) for (s, c) in dem_keys for h in HUBS)
        + pulp.lpSum(u[s, c] * eff_pen[s] for (s, c) in dem_keys)
        + pulp.lpSum(ss_short[s, h] * ss_penalty for s in skus_active for h in HUBS)
    )

    m.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit, gapRel=1e-4))
    if pulp.LpStatus[m.status] != "Optimal":
        # no proven-good integer solution yet — give CBC one longer attempt
        m.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit * 3, gapRel=5e-3))
    solve_secs = time.time() - t0
    if pulp.LpStatus[m.status] != "Optimal":
        return {"status": pulp.LpStatus[m.status], "solve_seconds": round(solve_secs, 1),
                "error": "Solver could not find a proven solution in the time allowed. "
                         "Increase the time limit in Settings and re-run."}

    # ---------- extract results ----------
    v = lambda var: max(0.0, var.value() or 0.0)

    prod_rows = [
        {"SKU": s, "Plant": p, "Band": sku.loc[s, "Band"], "Tier": sku.loc[s, "Tier"],
         "Batches": int(round(v(b[s, p]))), "Production (kL)": batch_kl * int(round(v(b[s, p])))}
        for s in skus_active for p in PLANTS if v(b[s, p]) > 0.5
    ]
    prod_df = pd.DataFrame(prod_rows)

    ph_rows = [
        {"SKU": s, "Plant": p, "Hub": h, "Volume (kL)": round(v(x[s, p, h]), 2)}
        for s in skus_active for p in PLANTS for h in HUBS if v(x[s, p, h]) > 1e-6
    ]
    ph_df = pd.DataFrame(ph_rows)

    hc_rows = [
        {"SKU": s, "Hub": h, "CFA": c, "Volume (kL)": round(v(y[s, h, c]), 2)}
        for (s, c) in dem_keys for h in HUBS if v(y[s, h, c]) > 1e-6
    ]
    hc_df = pd.DataFrame(hc_rows)

    # costs
    c_prod = sum(batch_kl * v(b[s, p]) * float(prod_cost[p]) for s in skus_active for p in PLANTS)
    c_ph = sum(v(x[s, p, h]) * float(ph_cost.loc[p, h]) for s in skus_active for p in PLANTS for h in HUBS)
    c_hc = sum(v(y[s, h, c]) * float(hc_cost.loc[c, h]) for (s, c) in dem_keys for h in HUBS)
    c_pen = sum(v(u[s, c]) * eff_pen[s] for (s, c) in dem_keys)
    c_ss = sum(v(ss_short[s, h]) * ss_penalty for s in skus_active for h in HUBS)

    # band utilisation, for shortfall diagnosis
    band_used = {
        (p, band): sum(batch_kl * v(b[s, p]) for s in skus_active if sku.loc[s, "Band"] == band)
        for p in PLANTS for band in BANDS
    }
    band_binding = {
        band: all(band_used[(p, band)] >= float(capacity.loc[p, band]) - batch_kl + 1e-6 for p in PLANTS)
        for band in BANDS
    }

    unmet_rows = []
    for (s, c) in dem_keys:
        q = v(u[s, c])
        if q > 1e-4:
            band = sku.loc[s, "Band"]
            reason = (
                f"Capacity shortfall — '{band}' lines effectively full at all plants"
                if band_binding[band]
                else "Deliberate deprioritisation — cost to serve exceeds weighted penalty"
            )
            unmet_rows.append({
                "SKU": s, "CFA": c, "Tier": sku.loc[s, "Tier"],
                "Contractual": "Yes" if sku.loc[s, "Contractual"] else "No",
                "Demand (kL)": round(d[(s, c)], 2), "Unmet (kL)": round(q, 2),
                "Unmet %": round(100 * q / d[(s, c)], 1),
                "Penalty applied (₹)": round(q * eff_pen[s]),
                "Reason": reason,
            })
    unmet_df = pd.DataFrame(unmet_rows)

    hub_ss = {}
    for h in HUBS:
        retained = sum(
            oh.get((s, h), 0.0) + sum(v(x[s, p, h]) for p in PLANTS)
            - sum(v(y[s, h, c]) for c in cfa_of[s])
            for s in skus_active
        )
        hub_ss[h] = {
            "Required (kL)": round(float(ss_req.get(h, 0.0)), 1),
            "Retained (kL)": round(retained, 1),
            "Shortfall (kL)": round(sum(v(ss_short[s, h]) for s in skus_active), 1),
        }

    total_dem = sum(d.values())
    total_unmet = sum(v(u[s, c]) for (s, c) in dem_keys)

    return {
        "status": pulp.LpStatus[m.status],
        "solve_seconds": round(solve_secs, 1),
        "production": prod_df,
        "plant_hub": ph_df,
        "hub_cfa": hc_df,
        "unmet": unmet_df,
        "hub_ss": hub_ss,
        "costs": {
            "Production": c_prod, "Plant→Hub transport": c_ph,
            "Hub→CFA transport": c_hc, "Unmet-demand penalty": c_pen,
            "Hub safety-stock shortfall": c_ss,
            "Grand total": c_prod + c_ph + c_hc + c_pen + c_ss,
        },
        "service": {
            "Total net demand (kL)": round(total_dem, 1),
            "Served (kL)": round(total_dem - total_unmet, 1),
            "Fill rate %": round(100 * (total_dem - total_unmet) / total_dem, 2) if total_dem else 100.0,
        },
        "band_used": band_used,
    }
