# Levisol "Balancing Act" — Monthly Planning Tool

A decision-support tool that produces the cost-optimal January production and
distribution plan for Levisol's 3-plant → 2-hub → 10-CFA network, with a
planner-friendly web interface. Optimization is a true mixed-integer program
(PuLP + CBC solver), not a heuristic.

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app opens at http://localhost:8501. The case data workbook is bundled in
`data/` — nothing else to configure.

## Using the tool (no code needed)

1. **Inputs tab** — every table (demand, capacities, production cost, both
   transport matrices, penalties, hub safety stock) is directly editable.
   Or upload a replacement workbook in the sidebar.
2. Press **▶ Run plan** in the sidebar. Solves in ~15–30 seconds.
3. **Plan & Results tab** — cost summary, production plan (25 kL multiples),
   both routing legs, hub safety stock, a network flow map, and an explicit
   list of any demand not met with the reason (capacity shortfall vs.
   deliberate deprioritisation). Download the full plan as Excel.
4. **Inventory Norms tab** — reorder point, safety stock and days of cover
   per SKU × CFA at 98% service level (Component 1 reference).

The first completed run is stored as the baseline; every later run shows the
cost delta against it — ideal for live what-if demos.

## Project layout

```
app.py               Streamlit front-end
core/data_loader.py  Parses all exhibits (A–J) from the workbook
core/norms.py        Inventory norms (ROP / safety stock / days of cover)
core/optimizer.py    The MIP model (PuLP + CBC)
data/                Bundled case data workbook
Methodology_Note.md  Objective, constraints and assumptions (half-page note)
```

## Validated scenarios

| Scenario | Result |
|---|---|
| Baseline (Jan net demand 5,136 kL) | Optimal, ₹8.93 Cr, 99.94% fill |
| Tier-A demand +40% | Optimal, ₹11.03 Cr, 99.95% fill |
| Kolkata→MHE freight ×5 | Optimal, ₹9.72 Cr — flows reroute |
| Kolkata plant offline | Optimal, ₹10.38 Cr, 99.76% fill; **zero Tier-A demand cut** |

All runs respect 25 kL batches, pack-size line capacities, and hub flow
conservation; each solves in ~20 seconds.
