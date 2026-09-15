# TRACK: talking points

## 1. Title

TRACK. Traffic Route optimization using A* search, Clustering and KNN.
A data-driven approach to congestion-aware traffic optimization in Dhaka.

## 2. The problem

- Dhaka traffic is unbalanced: some roads jammed, roads next to them empty.
- There is no public traffic dataset for Bangladesh. Google shows traffic but gives no data.
- Sending everyone to the best road moves the jam instead of removing it.

## 3. The idea

- Read the traffic Google shows, for the whole city, every few minutes.
- Give every road a number, including the roads Google never paints.
- Route trips in batches so each batch avoids what the last one filled. The city thins out instead of moving the jam.

## 4. The data, live

- Google's tile server returns the traffic layer as a transparent tile with no API key. 4,928 tiles cover the metro area in about 1 minute.
- A GitHub Actions workflow captures every 10 minutes, runs the pipeline and publishes the map. The page updates itself without a reload.
- Every capture is archived. Show the site.

## 5. From pixels to a city model

1. Road graph from OpenStreetMap: 48,413 junctions, 110,382 directed edges, 8,302 km, no edge longer than 150 m.
2. Decoder: each edge is sampled along its length and read from the tiles on the left of travel, the side Bangladesh drives on. Weight: green 25, yellow 55, red 85, dark red 105.
3. Imputation: Google paints about a tenth of the network. Every other road is predicted from its nearest observed roads with KNN, one level quieter when it is a smaller road, faded to a K-means zone average far from any data.
4. Every road now has a cost, so a router can leave a jammed arterial for the side streets.

## 6. The algorithms, each on the map

- Decoder: which roads were read, and how much of each.
- Imputation: how each road got its weight.
- K-means: the zones.
- KNN: a fifth of the observed roads hidden and predicted; error 6 points on a scale of 80, right level 85 % of the time.
- Logistic regression: the traffic level of every road from position, class and neighbours; 91 % hold-out accuracy, the same as KNN on the same features.
- Dijkstra: minutes from Shahbagh to every road, one run.
- A*: six routes across the city on an empty network and on today's traffic.
- Gravity model: trips between 36 zones, busier zones make and attract more.
- Assignment: 300 new trips. All at once, 617 roads over capacity. In ten batches with feedback, 496. Before the trips, 263.

## 7. Why the costs matter

- The BPR function turns volume over capacity into travel time. The textbook setting makes the four traffic levels differ by under 20 %, and a router then ignores traffic.
- TRACK sets it so a dark red road runs at a quarter of its free-flow speed. Green 1.01 times free flow, yellow 1.23, red 2.29, dark red 4.
- Each batch of trips is routed with A* on the current costs, then the costs are recomputed. Later batches go around what earlier ones filled.

## 8. Checked, not assumed

- Which side of the road a direction's line is drawn on: measured. Left observes 72 % of major-road edges, right 47 %.
- The assumption that a smaller road is quieter than the busy road beside it: measured on observed pairs. 15 points lower on average, lower in 62 % of pairs.
- The imputation: hold-out on observed edges, four models compared.
- The map itself: a browser suite of 22 scenarios checks every 60 ms that no road layer disappears.

## 9. What is built

- Collection, graph, decoder, imputation, all algorithms with tests, one map layer each, the map with live weight tuning, the workflow and the site.

## 10. What is next

- Scenarios over time: the workflow now produces a capture every 15 minutes; K-means over days of captures gives rush hour, off-peak and weekend patterns, and KNN against logistic regression per time of day.
- The animated assignment run, and personal A-to-B routing on predicted costs.
- Signal timing as a constraint problem, optional.

## 11. Who would use it

- Traffic police: advisories with times. Corridor X saturates at 5 pm, start diverting at 4:30.
- Fleets and ride sharing: many vehicles under central control can follow assigned routes.
- Planners: test a one-way scheme or a link road in the model before building it.

## 12. Questions to expect

- Where is the data from? Google's traffic layer, read every 10 minutes; OpenStreetMap for the roads.
- Is that allowed? Google's terms forbid storing their tiles. The owner accepted the risk for academic use.
- Won't rerouting create new jams? Yes if everyone is routed at once. Batches with cost feedback are the fix, and the numbers show it: 617 against 496.
- How do you get trips without vehicle data? A gravity model between zones, weighted by observed traffic.
- Why A* and not Dijkstra? Trips are point to point; A* searches toward the destination. Dijkstra is for one to everything, and the map has that too.
- Does every car avoid jams? No. Traffic becomes balanced, not gone.
