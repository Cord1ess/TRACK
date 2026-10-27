# TRACK: talking points

Two weeks of data, 16 September to 1 October 2026. Every number here is measured.

## 1. Title

TRACK. Traffic Route optimization using A* search, Clustering and KNN.
A data-driven approach to congestion-aware traffic optimization in Dhaka.

## 2. The problem

- No open traffic feed for Bangladesh; TomTom and HERE do not cover it; Google shows traffic but gives no data.
- Congestion is uneven: jammed arterials next to quieter streets.
- Sending everyone down the fastest road moves the jam instead of removing it.

## 3. The data we built

- 2,542 captures of the whole metro area, every 8 minutes (median), for 14.5 days.
- 12.5 million map tiles, 10.1 billion traffic pixels classified, 100 % tile coverage on every capture, longest gap 19 minutes.
- Fully automated on GitHub Actions; every capture archived to Hugging Face.

## 4. From pixels to a city model

- Road graph from OpenStreetMap: 48,413 junctions, 110,382 directed roads, 8,302 km.
- Decoder: every 5 m, 4 px to the left of travel (Bangladesh drives on the left), majority colour. Left side observes 72 % of major roads, right side 47 %.
- Spread: Google paints 9 % of roads; the nearest painted road along the network decides, one level milder per junction.
- 280.6 million road-level values over the two weeks.

## 5. What the data shows

- 7 pm is the worst hour in both weeks: 29 % of painted roads red or dark red. 6 am: 10 %.
- Weekends are lighter by day (15 % at noon against 21 % on weekdays).
- Slow means stopped: dark red 13 % of readings, red 4 %.

## 6. K-means

Five scenarios from the traffic alone, across all 2,542 captures: the small hours, light, the morning, the working afternoon, and the 6-8 pm peak.

## 7. KNN, a week ahead

Learn week 1, forecast week 2, scored on 4.6 million real readings:
KNN 84.0 % right (7.8 % too green), same time last week 81.7 %, logistic regression 79.0 %, always clear 65.8 %.

## 8. City simulation

2,000 trips × 10 vehicles, Tuesday 7 pm, 2,529 A* searches. Routed in batches with load feedback, the trips add 33 % less delay and the average trip is 38 % shorter than everyone taking the same fastest road. The result holds under three colour scales (30-36 %).

## 9. CSP

698 valid street layouts around Farmgate and Mohakhali, each simulated with A*. In Mohakhali, closing the U-turn gap on Bir Uttam Ziaur Rahman Road saves 1.1 %. In Farmgate no layout is measurably better on the two-week forecast (−7.1 % on week 1's): a plan is a lead, not a recommendation.

## 10. Diversion over time

Tuesday 7 pm to 8:30 pm, Actual against With TRACK on one clock: the same traffic at each moment, steered around jams by a coordinator. About 6,000 vehicles an hour move and total time falls 0.03-0.04 %. Only 17 of 400 detours were faster than the jam: the way around one jammed main road runs along another. Dhaka's evening congestion is a capacity problem, not a routing one.

## 11. Live demo

1. The timeline plays two weeks of real traffic.
2. City simulation: the newest A* routes in blue, roads filling batch by batch.
3. CSP: each layout as it is scored, with its routes and the best so far.
4. Diversion: pick 7 pm, run, switch Actual / With TRACK at 8:30.

Start the map with both weeks: `python server.py --captures ../week1/captures ../week2/captures`.
If the internet basemap does not load within 8 s the map continues on a plain background.

## 12. Questions to expect

- Where is the data from? Google's traffic layer every 8 minutes; OpenStreetMap for the roads.
- Is that allowed? Google's terms forbid storing their tiles; the owner accepted the risk for academic use.
- Won't rerouting create new jams? Yes if everyone is sent at once; batches with cost feedback fix that (33 %).
- Then why doesn't the diversion clear 7 pm? Because the alternatives are jammed too: there is no spare road nearby.
- How do you get trips without vehicle data? A gravity model between zones, weighted by observed traffic.
- Why 25 / 55 / 85 / 105? Volume over capacity per colour. Tested against two other scales: the batching result holds, the street-change size does not.
