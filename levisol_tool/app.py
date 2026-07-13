"""Levisol Balancing Act — Monthly Production & Distribution Planner.

Run with:  streamlit run app.py
"""
import io

import pandas as pd
import streamlit as st

from core.data_loader import load_workbook, BANDS, PLANTS, HUBS, HUB_LABELS
from core.norms import compute_norms
from core.optimizer import solve_plan

st.set_page_config(page_title="Levisol Planner", page_icon="🛢️", layout="wide")

EAST_CFAS = {"Guwahati", "Kolkata", "Jamshedpur", "Kanpur"}


# ---------------------------------------------------------------- data
@st.cache_data(show_spinner="Reading data workbook…")
def get_data(file_bytes: bytes | None):
    if file_bytes:
        return load_workbook(io.BytesIO(file_bytes))
    return load_workbook("data/Supply_Chain_Data.xlsx")


def init_inputs(data):
    """Put editable copies of every input into session state (once)."""
    ss = st.session_state
    if "demand" in ss:
        return
    def first_col(df, name):
        df = df.reset_index()
        df.columns = [name] + list(df.columns[1:])
        return df

    ss.demand = data["demand"].copy()
    ss.capacity = first_col(data["capacity"], "Plant")
    ss.prod_cost = first_col(data["prod_cost"].to_frame(), "Plant")
    ss.prod_cost.columns = ["Plant", "Production cost (₹/kL)"]
    ss.ph_cost = first_col(data["ph_cost"], "Plant")
    ss.hc_cost = first_col(data["hc_cost"], "CFA")
    pen = data["sku"].reset_index()[["SKU", "Pack", "Band", "Tier", "Contractual", "Penalty"]]
    ss.penalties = pen.rename(columns={"Penalty": "Penalty (₹/kL unmet)"})
    # default hub safety stock = 5 days of cover of net demand in each hub's natural region
    net = net_demand(data)
    e = net[net["CFA"].isin(EAST_CFAS)]["DemandKL"].sum()
    w = net["DemandKL"].sum() - e
    ss.ss_req = pd.DataFrame(
        {"Hub": ["Mother Hub West (MHW)", "Mother Hub East (MHE)"],
         "Safety stock required (kL)": [round(w / 30 * 5), round(e / 30 * 5)]}
    )


def net_demand(data, use_net=True):
    if not use_net:
        return st.session_state.get("demand", data["demand"])[["SKU", "CFA", "DemandKL"]]
    dem = st.session_state.get("demand", data["demand"]).merge(
        data["open_cfa"], on=["SKU", "CFA"], how="left"
    ).fillna({"OpenKL": 0})
    dem["DemandKL"] = (dem["DemandKL"] - dem["OpenKL"]).clip(lower=0)
    return dem[["SKU", "CFA", "DemandKL"]]


# ---------------------------------------------------------------- header / sidebar
st.title("🛢️ Levisol Monthly Planner")
st.caption(
    "Decides what to produce at each plant, how to route it through the hubs to the 10 CFAs, "
    "and what to protect when capacity is tight — at the lowest total cost. "
    "Edit any input in the **Inputs** tab, then press **Run plan**."
)

with st.sidebar:
    st.header("⚙️ Run controls")
    up = st.file_uploader("Replace data workbook (optional)", type=["xlsx"],
                          help="Upload a modified 'Supply Chain Case Study — Data.xlsx'. "
                               "All tables below refresh from it.")
    if up is not None and st.session_state.get("_upname") != up.name:
        st.session_state.clear()
        st.session_state["_upname"] = up.name
        st.session_state["_upbytes"] = up.getvalue()

    data = get_data(st.session_state.get("_upbytes"))
    init_inputs(data)

    use_net = st.toggle("Net off CFA opening inventory", value=True,
                        help="Plan against January forecast minus stock already sitting at each CFA.")
    st.subheader("Service protection")
    contract_mult = st.slider("Contractual SKU penalty multiplier", 1.0, 6.0, 4.0, 0.5,
                              help="Scales Exhibit-D penalties for contractual SKUs to reflect "
                                   "reputational and financial exposure beyond lost margin.")
    c1, c2 = st.columns(2)
    twA = c1.number_input("Tier A weight", 1.0, 5.0, 1.5, 0.1)
    twB = c2.number_input("Tier B weight", 0.5, 5.0, 1.2, 0.1)
    twC = c1.number_input("Tier C weight", 0.5, 5.0, 1.0, 0.1)
    twD = c2.number_input("Tier D weight", 0.1, 5.0, 0.8, 0.1)
    ss_pen = st.number_input("Hub safety-stock shortfall penalty (₹/kL)", 0, 500000, 50000, 5000)
    tl = st.slider("Solver time limit (seconds)", 10, 120, 20, 5)
    run = st.button("▶ Run plan", type="primary", use_container_width=True)

tab_in, tab_out, tab_norm, tab_help = st.tabs(
    ["📥 Inputs", "📊 Plan & Results", "📦 Inventory Norms", "ℹ️ How it works"]
)

# ---------------------------------------------------------------- inputs tab
with tab_in:
    st.info("Every table below is **directly editable** — click a cell, type, press Enter. "
            "Then hit **Run plan** in the sidebar. No code required.", icon="✏️")

    st.subheader("January demand per SKU × CFA (kL)")
    fc1, fc2 = st.columns(2)
    f_sku = fc1.multiselect("Filter SKU", sorted(st.session_state.demand["SKU"].unique()))
    f_cfa = fc2.multiselect("Filter CFA", sorted(st.session_state.demand["CFA"].unique()))
    view = st.session_state.demand.copy()
    mask = pd.Series(True, index=view.index)
    if f_sku:
        mask &= view["SKU"].isin(f_sku)
    if f_cfa:
        mask &= view["CFA"].isin(f_cfa)
    filt_key = f"demand_edit_{hash((tuple(sorted(f_sku)), tuple(sorted(f_cfa))))}"
    edited = st.data_editor(view[mask], key=filt_key, num_rows="fixed",
                            use_container_width=True, height=280,
                            column_config={"DemandKL": st.column_config.NumberColumn(
                                "Demand (kL)", min_value=0.0, format="%.2f")})
    new_demand = st.session_state.demand.copy()
    new_demand.loc[edited.index, "DemandKL"] = edited["DemandKL"]
    st.session_state.demand = new_demand

    cA, cB = st.columns(2)
    with cA:
        st.subheader("Plant line capacities (kL/month)")
        st.session_state.capacity = st.data_editor(
            st.session_state.capacity, key="cap_edit", use_container_width=True, disabled=["Plant"])
        st.subheader("Production cost")
        st.session_state.prod_cost = st.data_editor(
            st.session_state.prod_cost, key="pc_edit", use_container_width=True, disabled=["Plant"])
        st.subheader("Hub safety stock requirement")
        st.session_state.ss_req = st.data_editor(
            st.session_state.ss_req, key="ss_edit", use_container_width=True, disabled=["Hub"])
    with cB:
        st.subheader("Plant → Hub transport (₹/kL)")
        st.session_state.ph_cost = st.data_editor(
            st.session_state.ph_cost, key="ph_edit", use_container_width=True, disabled=["Plant"])
        st.subheader("Hub → CFA transport (₹/kL)")
        st.session_state.hc_cost = st.data_editor(
            st.session_state.hc_cost, key="hc_edit", use_container_width=True,
            height=250, disabled=["CFA"])

    st.subheader("SKU penalties & flags")
    st.session_state.penalties = st.data_editor(
        st.session_state.penalties, key="pen_edit", use_container_width=True, height=250,
        disabled=["SKU", "Pack", "Band"],
        column_config={
            "Tier": st.column_config.SelectboxColumn(options=["A", "B", "C", "D"]),
            "Contractual": st.column_config.CheckboxColumn(),
        })

# ---------------------------------------------------------------- solve
if run:
    cap = st.session_state.capacity.set_index("Plant")[BANDS].astype(float)
    pc = st.session_state.prod_cost.set_index("Plant").iloc[:, 0].astype(float)
    ph = st.session_state.ph_cost.set_index("Plant")[HUBS].astype(float)
    hc = st.session_state.hc_cost.set_index("CFA")[HUBS].astype(float)
    skudf = st.session_state.penalties.set_index("SKU").rename(
        columns={"Penalty (₹/kL unmet)": "Penalty"})
    ssr = {"MHW": float(st.session_state.ss_req.iloc[0, 1]),
           "MHE": float(st.session_state.ss_req.iloc[1, 1])}
    dem_run = net_demand(data, use_net)

    with st.spinner("Optimizing — mixed-integer program with CBC…"):
        res = solve_plan(
            capacity=cap, prod_cost=pc, ph_cost=ph, hc_cost=hc, sku=skudf,
            demand=dem_run, open_hub=data["open_hub"], ss_req=ssr,
            tier_weight={"A": twA, "B": twB, "C": twC, "D": twD},
            contract_mult=contract_mult, ss_penalty=ss_pen, time_limit=tl,
        )
    st.session_state.result = res
    if "baseline" not in st.session_state and "costs" in res:
        st.session_state.baseline = res

# ---------------------------------------------------------------- results tab
with tab_out:
    res = st.session_state.get("result")
    if res is None:
        st.info("No plan yet — press **▶ Run plan** in the sidebar.", icon="👈")
    elif "error" in res:
        st.error(res["error"])
    else:
        cost = res["costs"]
        svc = res["service"]
        base = st.session_state.get("baseline")

        m = st.columns(5)
        d_total = (cost["Grand total"] - base["costs"]["Grand total"]) if base else None
        m[0].metric("Grand total cost", f"₹{cost['Grand total']/1e7:.2f} Cr",
                    delta=f"{d_total/1e5:+,.1f} L vs baseline" if base and abs(d_total) > 1 else None,
                    delta_color="inverse")
        m[1].metric("Fill rate", f"{svc['Fill rate %']:.2f} %")
        m[2].metric("Demand served", f"{svc['Served (kL)']:,.0f} kL",
                    help=f"of {svc['Total net demand (kL)']:,.0f} kL net demand")
        m[3].metric("Unmet demand", f"{svc['Total net demand (kL)'] - svc['Served (kL)']:,.1f} kL")
        m[4].metric("Solve time", f"{res['solve_seconds']} s", help=f"Status: {res['status']}")

        bcol1, bcol2 = st.columns([2, 1])
        with bcol1:
            st.subheader("Cost breakdown")
            cdf = pd.DataFrame(
                {"Cost head": [k for k in cost if k != "Grand total"],
                 "₹": [cost[k] for k in cost if k != "Grand total"]})
            cdf["₹ lakh"] = (cdf["₹"] / 1e5).round(1)
            st.bar_chart(cdf.set_index("Cost head")["₹ lakh"], horizontal=True)
            st.dataframe(cdf.assign(**{"₹": cdf["₹"].map("₹{:,.0f}".format)})[["Cost head", "₹"]],
                         hide_index=True, use_container_width=True)
        with bcol2:
            st.subheader("Hub safety stock")
            hubdf = pd.DataFrame(res["hub_ss"]).T
            hubdf.index = [HUB_LABELS[h] for h in hubdf.index]
            st.dataframe(hubdf, use_container_width=True)
            st.caption("Shortfall is measured against the SKU-level buffer mix "
                       "(held in Tier A/B products pro-rata to demand). Aggregate retained "
                       "stock can exceed the requirement because 25 kL batch rounding "
                       "parks surplus at the hubs.")
            if base:
                if st.button("📌 Set current run as new baseline"):
                    st.session_state.baseline = res
                    st.rerun()

        st.divider()
        st.subheader("🚫 Demand not met — and why")
        if len(res["unmet"]) == 0:
            st.success("All net demand is served in this plan.")
        else:
            st.warning(f"{len(res['unmet'])} SKU–CFA lines under-served "
                       f"({res['unmet']['Unmet (kL)'].sum():.1f} kL total).")
            st.dataframe(res["unmet"].sort_values("Unmet (kL)", ascending=False),
                         hide_index=True, use_container_width=True)

        st.divider()
        p1, p2 = st.tabs(["Production plan (SKU × Plant)", "Line capacity utilisation"])
        with p1:
            prod = res["production"]
            if len(prod):
                pivot = prod.pivot_table(index=["SKU", "Tier", "Band"], columns="Plant",
                                         values="Production (kL)", aggfunc="sum", fill_value=0)
                pivot["Total"] = pivot.sum(axis=1)
                st.dataframe(pivot, use_container_width=True, height=340)
            else:
                st.info("No production planned.")
        with p2:
            util = pd.DataFrame(
                [{"Plant": p, "Band": b,
                  "Planned (kL)": round(res["band_used"][(p, b)], 0),
                  "Capacity (kL)": float(st.session_state.capacity.set_index("Plant").loc[p, b]),
                  } for p in PLANTS for b in BANDS])
            util["Utilisation %"] = [
                round(100 * p_ / c_) if c_ > 0 else 0
                for p_, c_ in zip(util["Planned (kL)"], util["Capacity (kL)"])
            ]
            st.dataframe(util, hide_index=True, use_container_width=True,
                         column_config={"Utilisation %": st.column_config.ProgressColumn(
                             min_value=0, max_value=100, format="%.0f%%")})

        r1, r2 = st.tabs(["Routing: Plant → Hub", "Routing: Hub → CFA"])
        with r1:
            if len(res["plant_hub"]):
                agg = res["plant_hub"].groupby(["Plant", "Hub"])["Volume (kL)"].sum().reset_index()
                agg["Hub"] = agg["Hub"].map(HUB_LABELS)
                st.dataframe(agg, hide_index=True, use_container_width=True)
                with st.expander("SKU-level detail"):
                    st.dataframe(res["plant_hub"], hide_index=True, use_container_width=True, height=300)
        with r2:
            if len(res["hub_cfa"]):
                agg = res["hub_cfa"].groupby(["Hub", "CFA"])["Volume (kL)"].sum().reset_index()
                pivot = agg.pivot_table(index="CFA", columns="Hub", values="Volume (kL)", fill_value=0)
                pivot.columns = [HUB_LABELS[h] for h in pivot.columns]
                st.dataframe(pivot, use_container_width=True)
                with st.expander("SKU-level detail"):
                    st.dataframe(res["hub_cfa"], hide_index=True, use_container_width=True, height=300)

        # network flow diagram
        st.subheader("Network flow map")
        if len(res["plant_hub"]) and len(res["hub_cfa"]):
            dot = ["digraph G { rankdir=LR; node [shape=box, style=filled, fontname=Helvetica];"]
            for p in PLANTS:
                dot.append(f'"{p}" [fillcolor="#c8e6c9"];')
            for h in HUBS:
                dot.append(f'"{HUB_LABELS[h]}" [fillcolor="#fff9c4"];')
            phagg = res["plant_hub"].groupby(["Plant", "Hub"])["Volume (kL)"].sum()
            for (p, h), vol in phagg.items():
                dot.append(f'"{p}" -> "{HUB_LABELS[h]}" [label="{vol:,.0f} kL", penwidth={max(1, vol/400):.1f}];')
            hcagg = res["hub_cfa"].groupby(["Hub", "CFA"])["Volume (kL)"].sum()
            for (h, c), vol in hcagg.items():
                dot.append(f'"{c}" [fillcolor="#bbdefb"]; "{HUB_LABELS[h]}" -> "{c}" '
                           f'[label="{vol:,.0f}", penwidth={max(1, vol/400):.1f}];')
            dot.append("}")
            st.graphviz_chart("\n".join(dot))

        # export
        st.divider()
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            res["production"].to_excel(xw, sheet_name="Production Plan", index=False)
            res["plant_hub"].to_excel(xw, sheet_name="Plant-Hub Routing", index=False)
            res["hub_cfa"].to_excel(xw, sheet_name="Hub-CFA Routing", index=False)
            res["unmet"].to_excel(xw, sheet_name="Unmet Demand", index=False)
            pd.DataFrame([res["costs"]]).T.rename(columns={0: "₹"}).to_excel(xw, sheet_name="Cost Summary")
        st.download_button("⬇️ Download full plan (Excel)", buf.getvalue(),
                           "Levisol_Plan.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---------------------------------------------------------------- norms tab
with tab_norm:
    st.subheader("Inventory norm reference — per SKU × CFA")
    st.caption("Reorder point and days of cover at 98% hub service level, computed from "
               "Jul–Dec 2025 sales variability and Exhibit-E lead times (Component 1).")
    norms = compute_norms(data)
    n1, n2 = st.columns(2)
    nf_sku = n1.multiselect("Filter SKU ", sorted(norms["SKU"].unique()), key="nsku")
    nf_cfa = n2.multiselect("Filter CFA ", sorted(norms["CFA"].unique()), key="ncfa")
    nm = pd.Series(True, index=norms.index)
    if nf_sku:
        nm &= norms["SKU"].isin(nf_sku)
    if nf_cfa:
        nm &= norms["CFA"].isin(nf_cfa)
    st.dataframe(norms[nm], hide_index=True, use_container_width=True, height=420)
    st.download_button("⬇️ Download norms (CSV)", norms.to_csv(index=False),
                       "Inventory_Norms.csv", "text/csv")

# ---------------------------------------------------------------- help tab
with tab_help:
    st.markdown("""
### What the tool optimizes
It minimizes **total cost of the month** = production cost + plant→hub transport +
hub→CFA transport + **penalty for any demand deliberately left unmet** + a penalty for
missing hub safety-stock targets. Because unmet demand is priced (Exhibit D penalties,
scaled up for contractual SKUs and weighted by service tier A>B>C>D), the model never
crashes when demand exceeds capacity — it finds the least damaging plan and tells you
exactly what it chose not to supply, and why.

### Rules the plan always respects
- Production only in **25 kL batches**, within each plant's **pack-size line capacity**.
- Every kL that reaches a CFA travels plant → hub → CFA; hubs can only ship what they
  received plus opening hub stock.
- Each hub retains a **safety-stock buffer** (held in Tier A/B SKUs pro-rata to demand).
- Any SKU–CFA demand can be under-served, but only at its penalty price — so Tier A and
  contractual SKUs are cut last.

### The two kinds of shortfall you may see
1. **Capacity shortfall** — the pack-size line is full at all three plants.
2. **Deliberate deprioritisation** — serving the last few kL would need a whole new
   25 kL batch that costs more than the penalty. The model makes the honest economic call.

### Live-demo checklist (30-minute input change)
1. Change the number directly in **Inputs** (or upload the replacement workbook in the sidebar).
2. Press **▶ Run plan**. Results refresh in ~15–30 s.
3. The baseline comparison on the results page shows the cost delta automatically.
""")
