# Methodology & Assumptions Note — Levisol Monthly Planning Tool

## Objective function and why it is appropriate

The model minimizes **total monthly cost = production cost + plant→hub freight +
hub→CFA freight + penalty cost of unmet demand + penalty for hub safety-stock
shortfall**. Pricing unserved demand — rather than forcing it to be served — is what
makes the model appropriate for a capacity-constrained business: when everything
cannot be supplied, the solver automatically sacrifices the volume that hurts least.
Penalties come from Exhibit D, multiplied by a service-tier weight (A 1.5×, B 1.2×,
C 1.0×, D 0.8×) and by 4× for contractual SKUs, so flagship and contractual
commitments are the last to be cut. All weights are editable in the interface. The
problem is solved as a mixed-integer linear program (PuLP/CBC), so the plan is
provably cost-minimizing within a 0.01% optimality tolerance.

## Constraints, in business language

1. **Batch production** — every SKU-plant quantity is an integer multiple of 25 kL.
2. **Line capacity** — total production at each plant, per pack-size band
   (≤1.5 L / 3–5 L / 7–20 L / 50 L / 180–210 L), cannot exceed Exhibit A capacity.
3. **Network structure** — all volume travels plant → hub → CFA; any plant may
   supply either hub and any hub may supply any CFA (per Exhibits B/C).
4. **Hub conservation** — a hub can only dispatch what it received this month plus
   its opening stock; anything retained counts toward its safety-stock buffer.
5. **Demand balance** — each SKU-CFA's net demand is either shipped or explicitly
   recorded as unmet (never silently dropped), at its penalty price.
6. **Hub safety stock** — each hub must retain a buffer (default: 5 days of cover of
   its natural region's demand, editable). The buffer is held in Tier A/B SKUs
   pro-rata to their demand, since a buffer of slow-movers protects nothing. This is
   a soft constraint: shortfalls are permitted but penalized and reported.

## Assumptions where the case was ambiguous

- **Net demand**: January forecast minus CFA opening inventory (Exhibit I), floored
  at zero — the tool has a toggle to plan against gross forecast instead.
- **Hub opening inventory** (the Mother Hub rows in Exhibit I) is available for
  dispatch and counts toward the safety-stock buffer.
- **Service tiers** are not tagged in the data, so SKUs were classified by ranking
  6-month sales volume and applying Exhibit F's cumulative slabs (top 50% of volume
  = Tier A, next 30% = B, next 15% = C, rest = D); the assignment is editable.
- **Pack-size bands**: a SKU is mapped by its unit container size (e.g., 20 × 900 mL
  → ≤1.5 L line); 180 kg drums are treated as the 180–210 L band.
- **Inventory norms** (reference tab) use the standard reorder-point model at 98%
  service (z = 2.05): daily demand statistics from Jul–Dec 2025 sales, total lead
  time = production + plant→hub + hub→CFA, and combined lead-time variability from
  Exhibit E. ROP = d·LT + z·√(LT·σd² + d²·σLT²).
- Two kinds of shortfall are reported distinctly: **capacity shortfall** (the
  pack-size line is full at all plants) and **deliberate deprioritisation** (serving
  the last few kL would require a whole extra 25 kL batch costing more than the
  penalty — an honest economic trade-off the tool surfaces rather than hides).
