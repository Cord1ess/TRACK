# TRACK's algorithms: what each one does, where it lives, when it runs

Every algorithm is one short file under `algorithms/track_algos/`, written
from scratch on numpy. This page is the guide to opening one up and explaining it.

## Where everything is

```
algorithms/
  pipeline.py        ONE capture:  graph -> decode -> spread -> map layers
  batch.py           many captures through pipeline.py, in parallel
  simulate.py        ALL captures: history -> forecast -> city -> changes
  track_algos/
    graph/           road_graph.py   the road network: junctions and directed roads
                     build_graph.py  builds it from OpenStreetMap
                     areas.py        Farmgate and Mohakhali, and street names
    decode/          palette.py      Google's four colours
                     decoder.py      reads the tiles into a colour per road
    traffic/         impute.py       the spread: a colour for every road
                     bpr.py          traffic level -> travel time
                     weights.py      the 25 / 55 / 85 / 105 scale
    search/          astar.py        A*: one best route
                     dijkstra.py     Dijkstra: best routes from one place to everywhere
    ml/              kmeans.py       K-means clustering
                     knn.py          K nearest neighbours
                     logistic_regression.py
    forecast/        history.py      every capture as one table
                     scenarios.py    K-means over time: the city's traffic patterns
                     predict.py      the forecasters: KNN, logistic regression, baselines
                     evaluate.py     the test: forecast captures never seen
    simulation/      demand.py       the gravity model: who travels where
                     assignment.py   routing many trips with A*, all at once or in batches
                     city.py         the city-wide experiment
    planning/        csp.py          the CSP solver: backtracking with forward checking
                     road_changes.py the street changes for one area, as a CSP
    progress.py      tells the map which algorithm is running
    layers.py        one map layer per algorithm, for a single capture
```

## What runs when

| When | Command | Algorithms |
|---|---|---|
| Every capture (GitHub Actions, or `batch.py`) | `pipeline.py` | decoder, spread, then the map layers |
| Once all captures are processed | `simulate.py history` | reads every capture into one table |
| | `simulate.py forecast` | K-means, KNN, logistic regression, two baselines |
| | `simulate.py city` | KNN forecast, BPR, gravity model, A* (thousands), assignment |
| After a city run | `simulate.py changes` | CSP, then A* to score each layout |

The map's **Simulate** tab runs the last three and shows which algorithm is running
and how far it has got.

---

## 1. The road graph — `graph/road_graph.py`

- **Nodes** are junctions (longitude, latitude). **Edges** are one direction of a
  road between two junctions, so a two-way street is two edges and each direction
  carries its own traffic.
- Each edge knows its length, road class, capacity (vehicles per hour) and
  free-flow time. No edge is longer than 150 m.
- Dhaka: 48,413 junctions, 110,382 edges.

## 2. The decoder — `decode/decoder.py`, `decode/palette.py`

Turns Google's traffic picture into a colour per road.

1. Walk along every edge, taking a sample every 5 m (skipping 10 m at each end,
   so a side street doesn't read the main road it joins).
2. Step 4 pixels to the left of the direction of travel, since Bangladesh drives on
   the left and Google draws each direction on its own side.
3. Read a 3 x 3 patch of pixels and match each pixel to the nearest of Google's
   four colours.
4. The road takes the colour painted on **most** of it (a tie goes to the worse
   colour). Under 25 % painted, the road counts as unpainted.

**Show:** the Google capture and the TRACK model side by side (A/B compare).

## 3. The spread — `traffic/impute.py`

Google paints about a tenth of the roads. The spread gives every other road a colour.

1. For each colour, run one breadth-first search outward from every road
   painted that colour. That gives every road its distance, in junctions, to
   the nearest road of each colour.
2. The **nearest** painted road decides. If two of different colours are equally
   near, the **worse** colour wins.
3. A road touching it gets the same colour; every junction further out is one
   level milder: dark red, red, yellow, green.
4. A road no painted road connects to is green: with no evidence against it, it
   is a road worth sending traffic down.

Every weight is exactly 25, 55, 85 or 105, never in between.
**Test:** `tests/test_spread.py` checks it against a brute-force version on random networks.

## 4. BPR — `traffic/bpr.py`

Turns how full a road is into how long it takes:

    time = free-flow time x (1 + alpha x (volume / capacity) ^ 4)

The textbook alpha (0.15) makes the four colours differ by under 20 %, and a router
then ignores traffic. TRACK sets alpha so a dark-red road is 4 times slower than
free flow: green 1.01x, yellow 1.23x, red 2.29x, dark red 4x.

volume / capacity is capped at 2 (about 40x slower than free flow). Past twice
capacity a road cannot carry more and the queue spills onto other roads, which
the model does not track; without the cap the fourth power turned a 15 m link
with thousands of vehicles on it into hours of delay.

## 5. A* — `search/astar.py`

The fastest route from one junction to another.

1. Keep a queue of junctions, ordered by f = g + h: g is the time to get there,
   h an estimate of the time left.
2. h is the straight-line distance at the city's top speed. Nothing is faster,
   so h never overestimates, and the first time the destination leaves the queue
   its route is the best one.
3. Take the junction with the smallest f, and for each road out of it, see if
   that road reaches the next junction sooner than any route found before.

The cost of a road is passed in, so the same A* finds the free-flow route or the
congested one. **Show:** A*: routes layer (empty roads vs today's traffic).

## 6. Dijkstra — `search/dijkstra.py`

A* without the estimate: best routes from one place to **every** junction in one
run. **Show:** Dijkstra layer, minutes from Shahbagh to everywhere.

## 7. K-means — `ml/kmeans.py`, used in `forecast/scenarios.py`

Groups the captures into traffic **scenarios** without being told the time.

1. Summarise each capture as 25 numbers: the average traffic level in each cell of
   a 5 x 5 grid over the city.
2. K-means: start with 5 centres (spread out by k-means++), give each capture to
   its nearest centre, move each centre to the average of its captures, repeat
   until nothing moves.
3. Describe each group by the times it contains.

**Show:** the scenarios list. In week 1 the lightest group was mostly 2-5 am and the
heaviest mostly 6-8 pm, found from the traffic alone.

## 8. KNN — `ml/knn.py`, used in `forecast/predict.py`

Forecasts every road for a day and time.

1. Describe each past capture by its time: time of day on a circle (so 11:50 pm is
   next to 12:10 am) and whether it was a weekend (Friday or Saturday).
2. Find the k = 6 past captures nearest the target time.
3. Each road gets the level most of those 6 agree on (a tie goes to the worse level).

## 9. Logistic regression — `ml/logistic_regression.py`, used in `forecast/predict.py`

The comparison forecaster. For a road at a time it uses: how busy that road usually
is, how busy it usually is at that hour, and the time of day. One logistic regression
per level (one-vs-rest), trained by gradient descent on 200,000 (road, capture) samples.

## 10. The forecast test — `forecast/evaluate.py`

Train on the earlier captures, forecast the later ones, compare with what was captured.
Scored only on roads Google painted, because those are real readings.

| Forecaster | Right level | Too green |
|---|---|---|
| always clear (floor) | 65.8 % | 34.2 % |
| same time last week (baseline) | 74.0 % | 15.1 % |
| **KNN** | **83.8 %** | **7.3 %** |
| logistic regression | 78.8 % | 13.7 % |

*Week 1 only (874 captures to learn, 106 to test, Sep 22-24). With week 2 processed,
the test becomes a true week-ahead forecast: learn week 1, forecast week 2.*

## 11. The gravity model — `simulation/demand.py`

Who travels where. A 6 x 6 grid of zones; busier zones send and attract more trips:

    trips between zone i and zone j  =  k x weight(i) x weight(j) / distance ^ 1.5

**Honest limit:** Google shows how busy roads are, not where people go, so these trips
are synthetic.

## 12. Assignment — `simulation/assignment.py`, `simulation/city.py`

The city-wide experiment: thousands of A* searches.

1. Every road starts with its forecast traffic as background load.
2. **All at once:** every trip takes its fastest route on today's roads, then all the
   load lands together. Everyone picks the same road, so it jams.
3. **In batches:** route a batch with A*, add its load, recompute travel times with
   BPR, route the next batch. Later trips go around the jams earlier trips made.
4. Compare total vehicle-hours and roads over capacity.

Trips with the same start and end in the same batch share one A* search, since nothing
changes until the load is added.
**Show:** Simulate → City simulation, then replay the batches on the map.

## 13. CSP — `planning/csp.py`, `planning/road_changes.py`

Which street changes would help Farmgate or Mohakhali.

- **Variables:** the area's busiest two-way side streets, and its U-turn gaps (the
  short links between the two halves of a divided road).
- **Domains:** a street is two-way, one-way either way, or closed. A U-turn gap is
  open or closed.
- **Constraints:** at most N changes; nobody is trapped (every junction that reaches
  every other today still does); two U-turn gaps under 400 m apart are not both closed;
  main roads never change.
- **Search:** backtracking with forward checking. Choose a value for the next street,
  cross out every value of the remaining streets that would now break a rule, and back
  up at once if a street is left with no values.
- **Traffic:** the area's gates are the main roads crossing its edge. Each road coming in
  brings its forecast volume (green 25 % of capacity .. dark red 105 %), shared among the
  roads going out in proportion to theirs: `T_ab = A_a x D_b / sum(D)`. An exit within
  200 m of the entry is the same road turning back and is left out. The 150 busiest
  journeys are kept, plus the city trips that crossed the area.
- **Score:** every valid layout is simulated: all that traffic is routed with A* in
  batches on the streets as the layout leaves them. The best layouts are the ones with
  the fewest vehicle-hours.

**Show:** Simulate → Road changes. The tab counts the search (choices tried, values
crossed out, dead ends, valid layouts) and draws the best layout on the map.

## The demo, in order

1. The timeline plays a week of Dhaka's real traffic.
2. Forecast: K-means finds the city's traffic patterns; KNN beats both baselines.
3. One A* route bends around a jam.
4. City simulation: the same trips all at once against in batches, replayed on the map.
5. CSP: the best street changes for Farmgate and Mohakhali, drawn on the map.
