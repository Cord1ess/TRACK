# TRACK

**T**raffic **R**oute optimization using **A**\* search, **C**lustering, and **K**NN
*A Data-Driven Approach to Congestion-Aware Traffic Optimization in Dhaka City*

---

## 1. Elevator Pitch

Traffic congestion in Dhaka is a persistent challenge, with limited real-time data infrastructure to support smart routing decisions. TRACK is an AI system that learns *where and when* congestion will occur from historical traffic data, then *diverts and redistributes* traffic across the road network — turning a map of dark-red jams and empty green side roads into smooth, evenly flowing traffic everywhere. Instead of optimizing one driver's route, TRACK optimizes the **whole city at once**: thousands of simulated trips are routed and re-routed by A\* search inside an iterative traffic-assignment loop, and the result is visualized live as the network "thins out" from red to yellow to green.

---

## 2. Problem Statement

- Dhaka suffers from severe, recurring congestion: some corridors run at or beyond capacity (red / dark red on Google Maps) while nearby alternative roads sit nearly empty (green).
- Google provides **no downloadable traffic dataset** — traffic information exists only as a visual overlay or per-query travel-time snapshots. Bangladesh also lacks a public real-time traffic data infrastructure.
- Naive routing does not solve this: if every driver is sent to the same "least congested" road, the jam simply moves to a different street (a known effect related to Braess's paradox). The real problem is **network-level load balancing**, not individual shortest paths.

**Goal:** Using historical congestion patterns and a graph of Dhaka's real road network, predict congestion by time and location, then compute a citywide traffic assignment that spreads demand across underused roads — and demonstrate the before/after effect in a live simulation.

---

## 3. Core Idea

The system works at the **network level**, not the per-user level:

1. **Predict** — From historical data, learn recurring congestion patterns and predict how congested each road segment will be at a given time.
2. **Estimate volume** — Convert Google Maps congestion colors into estimated traffic volume as a fraction of each road's capacity (capacity inferred from OSM road-type tags):

   | Color | Estimated volume / capacity |
   |---|---|
   | Green | ~30% |
   | Yellow | ~60% |
   | Orange | ~80% |
   | Red | ~100% (at capacity) |
   | Dark red | ~120%+ (over capacity) |
   | Closed | 0 (blocked) |

   This volume-to-capacity ratio is exactly what traffic engineers use when no sensor data is available — no individual vehicle data is needed.
3. **Divert and spread** — Run thousands of simulated trips through the network using A\* search inside an **iterative traffic-assignment loop**: route a batch of demand, update road costs to reflect the new load, route the next batch. Congested roads become expensive, so later trips naturally flow onto free roads — spreading and thinning the traffic.
4. **Visualize** — Render each loop iteration as a frame: the live demo shows the city's congestion map smoothing out in real time, from "some roads dark red, some empty green" to "flowing, mostly-yellow traffic everywhere."

---

## 4. System Architecture

**Pipeline:**

```
OSM road network ──► Graph (nodes = intersections, edges = road segments)
                          │
Historical traffic data ──► K-means clustering ──► Traffic pattern scenarios
                          │
                    KNN / Logistic regression ──► Per-segment congestion prediction
                          │
              Color → volume estimate + BPR cost function ──► Traffic-aware edge weights
                          │
        ┌─────────────────▼──────────────────┐
        │  ASSIGNMENT LOOP (repeat per batch)│
        │  1. Generate/take next demand batch│
        │  2. A* routes the batch            │
        │  3. Update volumes & edge costs    │
        │  4. Render frame (visualization)   │
        └────────────────────────────────────┘
                          │
              Spread-out, balanced network state (before/after comparison)
```

### What each component does

- **Road network graph (OpenStreetMap)** — Pure structure, no algorithm. Dhaka's road network pulled from OSM (Overpass API / SUMO `netconvert`) becomes the graph everything else operates on: nodes are intersections, edges are road segments.
- **K-means clustering** — Pattern discovery on historical data, *before* any prediction. Groups time-slots/segments into recurring traffic scenarios ("typical weekday rush hour," "weekend afternoon," "off-peak"). This is the same approach used in published Dhaka congestion research, and it defines the scenarios the simulation runs against.
- **KNN / Logistic regression** — The prediction step. Given a road segment, a time, and its cluster/scenario, predicts its congestion level (KNN: low/medium/high; logistic regression: congested vs. not). Comparing the two classifiers is a natural evaluation angle for the report. Their output is what turns static road distance into a dynamic, traffic-aware cost.
- **BPR cost function** — The Bureau of Public Roads formula converts volume and capacity into travel time:

  `travel_time = free_flow_time × (1 + 0.15 × (volume/capacity)⁴)`

  Cost rises *sharply* as a road approaches capacity, which is precisely why moving a few vehicles off a red road produces a large improvement — the mathematical engine behind the "spreading" effect.
- **A\* search** — The workhorse router. Every single "best route from X to Y under current costs" computation is an A\* call, guided by a geographic-distance heuristic. It is not replaced by the assignment method — it is *wrapped* by it, and called thousands of times.
- **Traffic assignment loop (incremental assignment / MSA)** — The outer control structure that produces the spreading. Total estimated demand is split into small batches (~10–20%); each batch is routed by A\* on current costs, then volumes and BPR costs are updated before the next batch. This approximates **Wardrop's user equilibrium** — the stable state where no trip can improve its travel time by switching routes. (The Frank–Wolfe algorithm is the formal academic method for this; incremental/MSA is the simple, explainable approximation used here.)
- **Synthetic demand generation** — Since no per-vehicle trip data exists, the system generates origin–destination (OD) pairs itself: divide Dhaka into zones (as done in published Dhaka research) and generate trips between zones weighted by observed congestion — busier zones produce and attract more trips. A **gravity model** (used in existing Dhaka-specific research) is the citable method for this. Alternatively, a simpler corridor-based version generates OD pairs between major arterial endpoints scaled by color-derived volumes.

---

## 5. Features

### Feature 1 — Network-level traffic diversion (core)

The main demo: a live visualizer showing the whole city being optimized at once.

- Input: a snapshot of current Dhaka congestion (color-coded network state).
- The system generates thousands of synthetic trips, routes them through the assignment loop — genuinely thousands of A\* searches, each on slightly different costs — and renders every iteration.
- Output: a **before/after comparison** — the initial red/green imbalanced map versus the final spread-out, balanced network — plus the live animation of the transition.
- No user picks start/end points here; the system generates demand itself.

### Feature 2 — Personal congestion-aware routing (secondary)

An interactive layer on the *same* engine: the user clicks two points on the map (e.g., Mirpur → Motijheel), and a single A\* query — weighted by the KNN/logistic-regression congestion prediction for the relevant future time window — returns a route that avoids where traffic *will* be. Presenting both features makes a strong pitch: *"Here's what the system does for the whole city, and here's what it does for you personally."*

### Stretch goal — CSP-based signal timing (optional)

If time permits: model traffic-signal timing at a few key intersections as a constraint satisfaction problem (green phases must not conflict; minimize total wait across approaches). This optimizes the *infrastructure* rather than just the routes through it, and uses an additional syllabus topic.

---

## 6. Data Acquisition Plan (Combined Strategy)

There is no bulk Google traffic export, so the project combines three sources that reinforce each other:

### 6.1 Road network — OpenStreetMap *(foundation, do first)*

Detailed, free Dhaka road network via the Overpass API, convertible with SUMO's `netconvert`. Provides the graph plus road-type tags used to estimate segment capacity.

### 6.2 Existing datasets & research (Option C)

- **Two research papers effectively already collected the needed congestion time-series** by capturing Google Maps traffic-layer snapshots over time and converting color intensity to congestion scores:
  - *"Unraveling Urban Traffic Congestion Patterns in Bangladesh"* (Hasan & Sarker, VEHITS 2025) — 139,008 snapshots at 15-minute intervals across Bangladesh, Jan–Jun 2023.
  - *"Inferring Traffic Patterns of Dhaka City: A Spatio-Temporal Analysis Over a Year"* (Rahman & Naushin, 2024) — 350,400 records over a full year, Dhaka divided into ten zones.
  - **Action:** email both author groups requesting the datasets for an academic AI-lab project.
  - **Fallback:** their methodology is fully replicable — screenshot the traffic layer at fixed intervals, grid it, map colors to congestion scores. A legitimate, citable collection approach if the authors don't respond.
- **BUET Accident Research Institute** — Dhaka road accident data 2007–2021 (also on IEEE DataPort), if a safety-aware routing angle is added.
- **Related groundwork:** a Dhaka-specific study combining data mining and microsimulation to reduce congestion at signalized intersections (MDPI, Urban Sci. 2019) — nearly the same project shape, useful as a template.

### 6.3 Fresh self-collected data (Option B)

- Pick 5–10 fixed origin–destination pairs on major Dhaka corridors (e.g., Mirpur–Farmgate, Gulshan–Motijheel) that overlap with the network graph — the overlap is what enables calibration.
- Poll the Google Directions/Routes API every 15–30 minutes for 1–2 weeks (simple cron script), logging travel time vs. free-flow time.
- This serves as the **validation set**: if the historical data says a corridor is congested at 6 pm, the freshly polled data should agree.
- Cost: expected to stay within Google's monthly free API credit (verify current pricing before scheduling).

### 6.4 Fusion

Historical/replicated data sets baseline congestion across the whole network; self-collected API data calibrates and validates specific corridors. The fused result feeds the prediction models and the simulation's demand parameters. Even in the worst case (no author response), the project stands on self-collected data plus a replicated, citable methodology.

---

## 7. Syllabus Alignment

| Syllabus topic | Role in TRACK |
|---|---|
| A\* search (Class 4) | Core routing engine — every path computation |
| CSP (Class 5) | Optional signal-timing stretch goal |
| K-means clustering (Class 8) | Traffic pattern/scenario discovery |
| KNN (Class 8) | Congestion-level classification per segment/time |
| Logistic regression (Class 10) | Binary congested/not prediction; compared against KNN |

Beyond-syllabus techniques (BPR cost function, incremental/MSA traffic assignment, gravity-model demand generation) are standard, citable transportation-engineering methods, included because they are what makes the network-level "spreading" claim actually work.

> **To confirm with faculty:** whether the final project may use the full syllabus or only topics covered before the deadline, and whether beyond-syllabus supporting techniques are acceptable.

---

## 8. Presentation & Pitch Notes

### The visual story

- **Before:** static congestion map from the K-means/KNN predictions — some roads dark red, some empty green.
- **During:** the assignment loop animated live — each iteration is one frame; the city visibly thins out.
- **After:** balanced, mostly-yellow flowing network. Side-by-side before/after comparison closes the demo.

### How to phrase the core claim (defensible version)

- ✅ *"We spread congestion so that no single route becomes a severe bottleneck — the network reaches a stable state where no trip has an obviously better alternative route available."* (This is Wardrop's user equilibrium — congestion becomes evenly distributed and manageable.)
- ❌ Avoid: *"Every vehicle gets an optimal path and never encounters a jam."* This overclaims what the method guarantees and invites pushback from a technical evaluator.

### Framing A\*'s role

*"Our core algorithm is A\* search, extended with an iterative traffic-assignment procedure so it accounts for the effect of its own routing decisions on congestion."* — accurate, and shows understanding of *why* single-shot A\* alone would just move the jam elsewhere.

### Anticipated faculty questions — prepared answers

1. **"Where does your traffic data come from if Google doesn't provide it?"** → The three-source combined strategy (§6), with a citable screenshot-methodology fallback.
2. **"Won't rerouting everyone just create a new jam?"** → Yes, if done naively (Braess's paradox) — which is exactly why the system uses batch assignment with BPR cost feedback instead of one-shot routing.
3. **"How do you simulate demand without vehicle data?"** → Zone-based synthetic OD generation weighted by observed congestion; gravity model as the citable method — a documented design decision, not an oversight.
4. **"Why A\* and not Dijkstra?"** → Every query is origin→destination, so A\*'s goal-directed heuristic avoids Dijkstra's wasted one-to-all expansion — an advantage that *grows* with graph size. Dijkstra remains appropriate if a one-to-all computation (e.g., hub-to-all-zones baselines for clustering) is ever needed.
5. **"KNN or logistic regression — which and why?"** → Both, compared empirically; the comparison is part of the evaluation.

---

## 9. Timeline / Milestones (skeleton — adjust to actual deadline)

| Phase | Work | Notes |
|---|---|---|
| 1. Foundation | Pull Dhaka OSM network, build graph | ~an afternoon; unblocks everything |
| 2. Data outreach | Email paper authors; start Directions API polling script | Run both in parallel from day one; polling needs 1–2 weeks of wall-clock time |
| 3. Data prep | Fuse/calibrate datasets; replicate screenshot methodology if needed | Fallback path activates here if no author response |
| 4. ML models | K-means scenarios; train & compare KNN vs. logistic regression | Feeds edge weights |
| 5. Assignment engine | BPR cost function; A\* + incremental/MSA loop; synthetic OD generation | The core system |
| 6. Visualization & demo | Live thinning-out visualizer; before/after comparison; Feature 2 UI | The presentation centerpiece |
| 7. (Stretch) CSP signals | Signal timing at key intersections | Only if ahead of schedule |
| 8. Report & presentation | Evaluation, write-up, slides | — |

---

## 10. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Paper authors never respond with datasets | Replicate their published screenshot→congestion-score methodology (citable); self-collected API data stands on its own |
| Google API costs exceed free credit | Small corridor set at 15–30 min intervals is expected to fit within free credit; verify current pricing before scheduling automated calls |
| Color→volume estimates are rough | Standard practice when sensors are unavailable; calibrated/validated against self-collected travel-time data on overlapping corridors |
| Faculty restricts project to syllabus-covered topics | Core pipeline (A\*, K-means, KNN, logistic regression) is fully in-syllabus; BPR/MSA can be framed as "iterative A\* with a congestion-sensitive cost update" — confirm early |
| "Roads Management Insights" (Google's enterprise traffic product) | Enterprise sales-gated, not viable for a student timeline; access form submitted as a zero-cost bonus, but nothing depends on it |
| Overclaiming in the pitch | Use the Wardrop-equilibrium phrasing (§8) — "spread and balanced," not "jam-free" |

---

## 11. Key References & Citable Concepts

- **Wardrop's user equilibrium** — formal target state of the assignment loop
- **BPR (Bureau of Public Roads) function** — congestion-sensitive travel-time cost
- **Incremental assignment / Method of Successive Averages (MSA); Frank–Wolfe** — traffic assignment methods ("we approximate user equilibrium traffic assignment")
- **Braess's paradox** — why naive rerouting fails; motivates the assignment approach
- **Gravity model** — synthetic OD demand generation (used in Dhaka-specific research)
- Hasan & Sarker, *Unraveling Urban Traffic Congestion Patterns in Bangladesh*, VEHITS 2025
- Rahman & Naushin, *Inferring Traffic Patterns of Dhaka City: A Spatio-Temporal Analysis Over a Year*, 2024
- MDPI Urban Sci. 2019 — Dhaka data mining + microsimulation congestion study
- OpenStreetMap (road network); SUMO (simulation tooling); BUET ARI accident data (optional safety angle)
