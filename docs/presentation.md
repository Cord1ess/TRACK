# TRACK — Presentation Talking Points

---

## Slide 1 — Title

**TRACK**
Traffic Route optimization using A\* search, Clustering, and KNN

*A data-driven approach to congestion-aware traffic optimization in Dhaka City*

---

## Slide 2 — The Problem

- Dhaka traffic is severely unbalanced: some roads jammed (red), nearby roads empty (green)
- No public real-time traffic data infrastructure in Bangladesh
- Google shows traffic but gives no downloadable dataset
- Rerouting everyone to the "best" road just moves the jam elsewhere

---

## Slide 3 — The Idea

- Learn from historical data: **where** and **when** congestion happens
- Divert traffic from jammed roads to free roads
- Spread it out → smooth, flowing traffic everywhere
- Optimize the **whole city at once**, not one driver

---

## Slide 4 — Before vs. After (visual slide)

- Before: dark red jams + empty green roads
- After: balanced, mostly-yellow, flowing network
- *(Show two map images side by side)*

---

## Slide 5 — How It Works (pipeline)

1. Build Dhaka road network as a graph (OpenStreetMap)
2. **K-means** → find recurring traffic patterns (rush hour, weekend, off-peak)
3. **KNN / Logistic regression** → predict congestion per road, per time
4. Convert map colors → estimated traffic volume
5. **A\*** routes thousands of trips, costs update after each batch
6. Traffic spreads out → visualized live

---

## Slide 6 — What Each Algorithm Does

- **K-means** — groups historical data into traffic scenarios
- **KNN / Logistic regression** — predicts how congested each road will be
- **A\*** — finds the best route under current traffic costs
- **Assignment loop** — calls A\* repeatedly, raising costs on filling roads → spreading effect

---

## Slide 7 — The Key Trick: Spreading

- Colors → volume estimates (Green ~30% capacity … Red ~100% … Dark red 120%+)
- BPR cost formula: road cost rises **sharply** near capacity
- Route demand in small batches; each batch avoids roads the last batch filled
- Result: stable, balanced traffic (Wardrop's user equilibrium)

---

## Slide 8 — Feature 1: Citywide Traffic Diversion (core)

Optimizes the entire city at once — no user input needed. The system takes a snapshot of current Dhaka congestion, generates thousands of synthetic trips based on where activity is concentrated, and routes them through the network so that traffic naturally spreads from jammed roads onto free ones. The result is shown as a live visualizer: the city thinning out from red to yellow to green in real time, with a before/after comparison.

---

## Slide 9 — Feature 2: Personal Future-Aware Routing (secondary)

The user picks any two points on the map, and the system returns a route that avoids where traffic **will** be — not just where it is now. Predicted congestion for the travel time window is used as the road cost, so the route dodges jams before they form. Same engine as Feature 1, applied at personal scale instead of citywide.

---

## Slide 10 — Feature 3: Signal Timing Optimization (stretch goal)

Optimizes the intersections themselves, not the routes through them. Signal timing is modeled as a CSP: predicted traffic per approach decides the green-time splits, under the hard rule that conflicting directions are never green together — producing one optimal signal plan per traffic scenario (rush hour, off-peak, weekend). A third consumer of the same prediction engine, and fully optional: the core pipeline works without it.

---

## Slide 11 — Data Plan

- **Road network:** OpenStreetMap (free, detailed)
- **Historical congestion:** existing Dhaka research datasets (2 published papers; replicable method as fallback)
- **Fresh data:** our own Google Directions API polling on major corridors, 1–2 weeks
- Fresh data validates historical data

---

## Slide 12 — Syllabus Fit

- A\* search — Class 4
- CSP (signal timing, stretch goal) — Class 5
- K-means & KNN — Class 8
- Logistic regression — Class 10
- Plus standard, citable traffic-engineering methods (BPR, traffic assignment)

---

## Slide 13 — Deliverables

- Congestion prediction models (KNN vs. logistic regression, compared)
- Network-level traffic diversion engine (A\* + assignment loop)
- Live before/after visualization demo
- Stretch goal: CSP-based signal timing at key intersections

---

## Slide 14 — Real-World Applications

*The system is a prediction + decision-support tool — here's who uses it:*

- **Traffic police (DMP)** — most intersections are police-run, and police can divert traffic in ways lights can't. Our predictions become advisories: *"Corridor X saturates at 5pm — start diverting to Y at 4:30"* — data-driven timing instead of gut feel
- **Fleets & ride-sharing (Pathao, Uber, delivery, buses)** — fleets control many vehicles centrally, so they can actually follow assigned congestion-aware routes. Most direct user of Feature 2
- **Urban planners (DTCA)** — test interventions in simulation before spending on concrete: one-way schemes, new link roads, U-loops — "what happens to the network if we build this?"
- **Signalization planning (CSP)** — identify which intersections have regular enough flow to justify installing signals, with pre-computed timing plans per traffic scenario

---

## Slide 15 — Conclusion

> "Dhaka doesn't need new roads to move better — it needs smarter use of the roads it already has. TRACK shows that even in a data-scarce city, classical AI can deliver that intelligence today."

---

## Speaker Notes — Tough Questions

- **"Where's the data from?"** → 3 sources: OSM + published datasets + our own API collection
- **"Won't rerouting create new jams?"** → Yes if naive — that's why we use batch assignment with cost feedback
- **"How do you simulate demand without vehicle data?"** → Zone-based synthetic trips weighted by congestion (gravity model, citable)
- **"Why A\* not Dijkstra?"** → We route point-to-point; A\*'s heuristic skips wasted search. Dijkstra is for one-to-all
- **"Does every car avoid jams?"** → Don't overclaim: traffic becomes *balanced*, not eliminated
