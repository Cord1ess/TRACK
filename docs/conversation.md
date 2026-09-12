Original URL: https://claude.ai/chat/f8e962f2-7b41-402c-b81f-00995973a3b1
Conversation Time: 7/27/2026, 3:11:53 PM
Export Time: 8/4/2026, 12:43:37 AM

**[USER]**

For my AI lab im thinking of doing a project where i use algorithms taught in ai lab and use google traffic data of bangladesh to optimize it and show a simulation of optimized traffic. is this kind of project feasible

---

**[ASSISTANT]**

Yes, it's feasible — it's actually a pretty popular category of AI-lab project — but there's one thing worth getting straight before you start: **Google doesn't give you raw traffic data in a form you can just download and feed into a model.** There's no bulk/historical traffic export API.There's no reasonably easy way to get raw traffic data from the Google Maps JavaScript API or free API services. What you _can_ get:

- **Directions/Routes API** — query a specific origin-destination pair and get a traffic-aware travel time/route back. The Routes API lets you incorporate traffic data into route calculations, choosing whether to include traffic and optionally getting traffic-aware polylines describing conditions along the route, but this is pay-per-query and gives you snapshots, not a dataset.
- **TrafficLayer** on the JS Maps API — an overlay of real-time traffic conditions updated every few minutes from GPS devices, road sensors, and historical patterns — this is visual only, not something you can extract structured data from directly (you'd have to scrape/screenshot it, which is against ToS and unreliable).

So "use Google traffic data of Bangladesh" in the literal sense (a downloadable dataset) doesn't really exist. That doesn't kill the project — it just means you need to pick one of these realistic paths:

**Option A — Simulated network, real road layout**
Pull Dhaka/Bangladesh road network from OpenStreetMap (free, detailed, works great for Bangladesh), feed it into **SUMO** (Simulation of Urban Mobility — the standard open-source traffic simulator), generate synthetic vehicle flows calibrated loosely to what you observe in Google Maps traffic layer screenshots (e.g. "this junction is red at 5pm"), then run your algorithm on top (signal timing optimization, adaptive routing, etc.) and show before/after simulation. This is the most common and most demo-able version of this project.

**Option B — Sparse real data via API calls**
Use the Directions/Routes API to repeatedly query travel times between fixed points in a city (e.g. every 15 min for a week) and build your own small time-series dataset of congestion at those points. Costs a little (Google gives free monthly credit), but it's real data. Good for a prediction/forecasting angle rather than full network simulation.

**Option C — Existing Bangladesh-specific datasets**
Worth checking Kaggle, BUET/university research repos, or BRTA-adjacent datasets — there's occasionally published Dhaka traffic/accident data from local research groups, which would save you the collection step entirely.

**On the algorithm side**, depending on what your AI lab covers, natural fits are:

- Search/graph algorithms (Dijkstra, A\*) for route optimization
- Genetic algorithms / ant colony optimization for signal timing or route planning
- Reinforcement learning for adaptive traffic light control (this is the flashiest option and commonly done with SUMO's Python/TraCI interface)

If you want, I can help you scope this down to something doable in your project timeframe — that mostly comes down to how much time you have and whether your lab wants a prediction-heavy project or an optimization/simulation-heavy one.

---

**[USER]**

Check everything. is there now way to get traffic data from google or any google dev console? if not im leaning towards option b or c

---

**[ASSISTANT]**

Confirmed — nothing turned up. There's no product in Google Cloud Console, Google Maps Platform, or anywhere else in Google's developer ecosystem that lets you export a traffic dataset (historical or bulk). The BigQuery/Cloud Console export features that exist are for things like Search Console, Security Command Center, Workspace data — unrelated to Maps/traffic. Traffic info from Google only comes back to you as:

- a **visual overlay** (TrafficLayer — not extractable as data), or
- a **per-query answer** to "what's the travel time/route between A and B right now" (Directions/Routes API) — one query, one snapshot, billed per call.

There's no "give me all traffic data for Dhaka for the last month" endpoint. So Option B or C is the right call. Here's how I'd think about choosing between them:

**Option C (existing dataset) — check this first, it's much less work**
Worth 30–60 minutes of searching before committing to B:

- Kaggle — search "Dhaka traffic," "Bangladesh road accident," "BRTA"
- Bangladesh Bureau of Statistics / BRTA open data portals
- Papers from BUET, IUT, or other Bangladeshi universities often publish small traffic/congestion datasets alongside their research (Google Scholar + "Dhaka traffic dataset" is worth a look)
- Bangladesh Data Portal (data.gov.bd)

If something usable exists, you skip data collection entirely and go straight to algorithm + simulation, which is a much better use of project time.

**Option B (collect it yourself via API) — more real, more work**
If nothing decent turns up in C, this is very doable:

- Pick a handful of fixed origin-destination pairs on major Dhaka corridors (e.g. Mirpur–Farmgate, Gulshan–Motijheel)
- Hit the Directions/Routes API every 15–30 min over 1–2 weeks (a simple cron script), logging travel time, distance, and traffic-duration vs free-flow duration
- This gets you a real (if sparse) congestion time-series for those corridors — enough to train a predictive model or feed as "ground truth" congestion levels into a SUMO simulation
- Cost: Google gives a monthly free credit ($200 as of last I confirmed, though check current pricing on Maps Platform since this may have changed) — for a handful of routes queried at that frequency you'd likely stay within it, but worth checking current pricing before committing to a schedule of automated calls.

My honest recommendation: spend a short focused search on Option C first. If you find even a rough dataset, combine it with Option B as a supplement (use B to validate or fill gaps) rather than doing B alone — gives you both breadth (existing data) and freshness (data you collected).

Want me to help you search specifically for existing Bangladesh/Dhaka traffic datasets right now, or help you sketch the API-polling script for Option B?

---

**[USER]**

what is this?

https://mapsplatform.google.com/resources/blog/roads-management-insights-is-now-available-build-safer-smarter-and-more-efficient-road-networks/

---

**[ASSISTANT]**

Good catch — this is a real product, and it's an important correction to what I told you earlier.

**What it is:** Google Maps Platform's **Roads Management Insights (RMI)**, launched August 2025. It's designed for city and regional planners, road authorities, public utility providers, and public sector agencies who want to understand and respond to traffic conditions, giving them access to Google's global road information so they can move past the limits of traditional traffic data collection and respond to congestion and safety issues at scale. It offers two things:

- **Aggregated historical data** — travel-time patterns over time, recurring peak hours, weekday-vs-weekend flow differences, seasonal shifts, and identification of safety hotspots and bottlenecks, usable to build predictive models of future traffic.
- **Real-time monitoring** — detecting anomalies like sudden slowdowns or pattern deviations that could signal an accident or closure, so a traffic manager can react by adjusting signals, rerouting, or pushing alerts.

Current users mentioned include Colorado's Department of Transportation, an India-based geospatial company called Lepton, and a geospatial consultancy called NGIS, all applying it to real traffic-management problems.

**Why this doesn't change my earlier advice much, practically:** This is not a self-serve API you enable in the Cloud Console with a credit card, like Directions or Places. To get access you have to fill out a form and Google's team reaches out to discuss your use case. That's an enterprise sales gate — it's built for governments, DOTs, and geospatial contractors with an institutional relationship with Google, not for a student project. Realistically:

- You (as a student) could try filling out the form and mentioning it's for an academic project, but expect either no response, a long wait, or a "sorry, this is for public sector/enterprise" reply.
- Even if granted, it's aggregated insights (dashboards/BigQuery-style analytics), not a "download raw traffic dataset" — you'd likely need to shape your project around whatever query/export interface they give you, which you can't verify without access.
- There's no published pricing, which usually means it's negotiated per-org.

**Bottom line:** worth a shot to fill out the form (costs you nothing but time), but I wouldn't plan your project timeline around getting access. Stick with Option B/C as your primary path, and treat RMI access as a bonus if it comes through in time. Want me to help draft that access-request form response to maximize the (still slim) odds, while we keep building out Option B/C in parallel?

---

**[USER]**

Check for option C, Whats available

---

**[ASSISTANT]**

Good news — there's more here than I expected. Nothing is a plug-and-play "download and simulate" dataset, but there are real building blocks across a few categories:

**1. Congestion time-series data (closest to what you actually want)**
Two research papers essentially already did your Option B for you, by scraping Google Maps traffic-layer screenshots over time and converting color intensity into a congestion metric:

- "Unraveling Urban Traffic Congestion Patterns in Bangladesh" (Hasan & Sarker, VEHITS 2025) captured 139,008 Google Maps traffic snapshots at 15-minute intervals across all divisions of Bangladesh from January to June 2023, then decomposed the resulting time series into trend, seasonal, and random components using hierarchical clustering and dynamic time warping.
- "Inferring Traffic Patterns of Dhaka City: A Spatio-Temporal Analysis Over a Year" (Rahman & Naushin, 2024) used 350,400 records from Google Maps over a full year, dividing Dhaka into ten zones to reveal congestion patterns and weekday/weekend variation.

Neither confirms a public raw-data download in what I've seen — you'd need to check the papers directly (Scitepress / DOAJ links) or email the authors to ask for the dataset. But even without the raw data, their **methodology is fully replicable**: screenshot the traffic layer at fixed intervals, grid it, map colors (red/yellow/green) to congestion scores. That's a legitimate, citable approach for your own data collection if C doesn't pan out.

**2. Vehicle-detection / image datasets (different use case — good if you want computer vision, not routing)**

- **DhakaAI** (Shihavuddin & Rashid, 2020, Harvard Dataverse) — vehicle detection dataset, also on Kaggle in YOLO-formatted versions
- **TFP-BD** an image dataset of traffic and pedestrian movement captured at foot-over-bridges across multiple time slots throughout the day, focused on unstructured Bangladeshi traffic with vehicle classes like rickshaws, CNGs, buses, and pedestrians, on Mendeley Data
- **Bangladeshi Traffic Flow Dataset** (Mendeley/Kaggle) — used in a Dhaka congestion-region-detection project combining Faster R-CNN + DBSCAN

These are great if your project leans "detect congestion from camera/video feed with computer vision," not so useful for graph-based route optimization.

**3. Accident data**
BUET's Accident Research Institute (ARI) has road accident data for Dhaka spanning 2007–2021, used in at least one published time-series/hotspot-forecasting study, also mirrored on IEEE DataPort. Useful if you want a "safety-aware routing" angle rather than pure congestion.

**4. Road network structure (not traffic, but essential for simulation)**
Not traffic data per se, but you'll need this regardless: **OpenStreetMap** has a genuinely good, detailed road network for Dhaka — free to pull via the Overpass API or directly into SUMO's `netconvert` tool. This is what actually builds the graph your algorithm runs on.

**5. Academic groundwork worth reading**
A Dhaka-specific study integrated data mining and microsimulation modelling specifically to reduce congestion at signalized intersections (MDPI, Urban Sci. 2019) — worth reading as a template, since it's almost exactly your project shape (data + simulation + optimization at intersections).

**My honest read:** Option C gets you close but not all the way — the best-fit datasets (the two Google-Maps-scrape papers) probably require emailing authors, and it's genuinely unclear if they'll respond in time for your deadline. Realistic plan: email the authors of both papers today, and **in parallel** start your own small-scale Option B collection (a few Dhaka corridors, Directions API, few weeks) as a fallback that you fully control. Combine whichever comes through.

Want help drafting the email to the paper authors, or setting up the OSM→SUMO pipeline for Dhaka's road network while you wait?

---

**[USER]**

Then we do a combination of option B and C

---

**[ASSISTANT]**

Good — that's the strongest version of this project. Here's how I'd structure it so the two options reinforce each other instead of duplicating effort.

**The core idea:** Use C to get you working immediately and give you a broader/longer view of Dhaka congestion patterns; use B to get a small slice of fresh, verified, ground-truth data on the specific corridors your algorithm will actually optimize. Then the road network (OSM) ties everything together as the graph your algorithm and simulation run on.

**Step 1 — Road network (do this first, it's the foundation)**
Pull Dhaka's road network from OpenStreetMap via Overpass API, convert it with SUMO's `netconvert` into a usable graph. This gives you nodes (intersections) and edges (road segments) — the actual thing your algorithm will route over. Takes an afternoon, unblocks everything else.

**Step 2 — Option C: reach out + fallback methodology**

- Email the authors of the two Google-Maps-scrape papers (Hasan & Sarker; Rahman & Naushin) asking for their raw dataset — cite it's for an academic AI lab project, low-effort ask for them.
- While waiting, skim their methodology sections closely — the "screenshot the traffic layer, grid it, map color→congestion score" approach is your fallback if they don't respond, and it's also literally your Step 3 below if you need to run it yourself.
- Pull BUET ARI accident data if you want a safety-weighted routing angle as a bonus feature.

**Step 3 — Option B: your own fresh slice**
Pick 5–10 fixed origin-destination pairs on major Dhaka corridors that overlap with the intersections/edges in your Step 1 network (this overlap matters — it's what lets you calibrate). Poll the Directions/Routes API every 15–30 min for 1–2 weeks, logging travel time vs free-flow time. This becomes your validation set: if C's data (or replicated methodology) says a corridor is congested at 6pm, your own polled data should agree.

**Step 4 — Fuse and calibrate**
Use whichever C data you get (author-provided or your own replicated screenshots) to set baseline congestion levels across the whole network, and your B data to calibrate/validate specific corridors. Feed this into SUMO as demand/flow parameters.

**Step 5 — Algorithm + simulation**
Run your chosen algorithm (routing, signal-timing RL, whatever your AI lab covers) on top of the calibrated SUMO network. Show before/after: baseline congestion vs. optimized.

This gives you a defensible, real-data-backed project even in the worst case where the paper authors never respond — because your B data alone is real and your C methodology replication is legitimate research practice, not just "I couldn't get the real thing."

Want to start with the OSM→SUMO pipeline (Step 1), or the outreach email to the paper authors (Step 2) first?

---

**[USER]**

This is our Course outline. We are only 2 classes in so we have not had the later classes yet. But according to these what algorithm or architecture can we use based on it for my project?

---

**[ASSISTANT]**

This actually simplifies your project a lot — it tells you exactly what's fair game, and it changes my earlier algorithm suggestions. Reinforcement learning, genetic algorithms, ant colony optimization — none of that is on this syllabus, so drop those. Here's what you're actually working with, and how each piece maps onto your project:

**Core routing engine → A\* Search (Class 4)**
This is your project's backbone. A* is exactly the right tool for "find the optimized route given traffic conditions" — you already have the road network as a graph (from OSM), and A* finds shortest/fastest paths on it. The optimization angle comes from **making edge weights traffic-aware**: instead of static distance, each road segment's cost = base travel time × congestion multiplier (pulled from your Option B/C data). Run A\* on the unweighted graph vs. the congestion-weighted graph and show how the optimal route changes — that's your "before/after optimization" demo.

**Congestion classification → KNN and Logistic Regression (Class 8, 10)**
Use these to predict congestion level for a road segment at a given time, based on features like time of day, day of week, maybe weather:

- **KNN**: classify a segment's congestion (low/medium/high) by finding similar historical time-slots in your data
- **Logistic regression**: binary version — predict "congested vs not congested" for a segment/time

Either (or both, compared against each other) feeds directly into A\*'s edge weights — this is the link between your ML component and your search component, and it's a natural thing to compare in your report (which classifier predicts congestion better).

**Pattern discovery → K-means Clustering (Class 8)**
This is actually exactly what the Bangladesh research papers I found for Option C did — cluster time-slots or road segments by congestion pattern similarity. You could cluster Dhaka corridors into groups like "consistently congested," "rush-hour-only," "weekend-heavy," etc. This gives you defined traffic _scenarios_ to run your A\* simulation against (e.g., "compare optimized routing under typical Tuesday-8am cluster vs. Friday-evening cluster").

**CSP (Class 5) — optional stretch goal**
If you want to go further: model traffic-signal timing at a few intersections as a constraint satisfaction problem (green-light phases can't conflict, minimize total wait time across approaches). This is a nice "bonus" layer on top of routing if you have time, since it directly optimizes the _infrastructure_ rather than just the _routes_ through it.

**Suggested overall architecture:**

1. Road network (OSM) → graph
2. K-means on your congestion data → traffic scenarios/clusters
3. KNN or logistic regression → predicts congestion level per segment per scenario → sets A\* edge weights
4. A\* → computes optimized routes under each scenario
5. (Optional) CSP → optimizes signal timing at key junctions
6. Simulation/visualization → show congestion before vs. after optimization

This uses every non-evaluation topic on your syllabus in a way that's genuinely connected, not bolted on. One thing worth doing: confirm with your instructor whether the final project is expected to only use topics covered _before_ the project deadline, or the full syllabus — since class 12 is "project final evaluation," it's likely meant to integrate everything through class 11, but it's worth double-checking rather than assuming.

Want me to help sketch out the actual A\* implementation with congestion-weighted edges, or start with the K-means clustering step on whatever traffic data you end up collecting?

---

**[USER]**

Im still planning. Not into implementation yet. I will talk with my faculty about it. in the meantime suggest me a few tittles for the project

---

**[ASSISTANT]**

Here are some options, ranging from straightforward/descriptive to a bit more polished for a report cover page:

**Straightforward / descriptive**

1. _Traffic-Aware Route Optimization for Dhaka City Using A\* Search and Machine Learning_
2. _An AI-Based Framework for Traffic Congestion Prediction and Route Optimization in Bangladesh_
3. _Optimizing Urban Traffic Flow in Dhaka: A Hybrid A\*, Clustering, and Classification Approach_

**Emphasizing the ML + search combination** 4. _Congestion-Aware Pathfinding: Combining K-Means Clustering and A\* Search for Smarter Urban Routing_ 5. _Predict, Cluster, Route: A Machine Learning Pipeline for Traffic Optimization in Dhaka_ 6. _From Data to Direction: Machine Learning-Driven Traffic Route Optimization for Bangladeshi Cities_

**More narrative / presentation-friendly** 7. _Beating Dhaka's Traffic: An AI Approach to Smarter Route Planning_ 8. _Smart Streets: AI-Driven Traffic Congestion Analysis and Route Optimization for Dhaka_ 9. _Navigating Chaos: AI-Based Traffic Pattern Recognition and Route Optimization in Dhaka_

**If you end up including the CSP/signal-timing stretch goal** 10. _Intelligent Traffic Management: Route Optimization and Signal Timing for Congested Urban Networks_

My personal pick would be #1 or #4 — they're accurate to what you're actually building (search + ML, not just "AI"), which tends to land better with faculty than a flashier title that oversells scope. Worth running your favorite by your faculty along with the syllabus-alignment question, since they may also have a preference for how narrow or broad the title should be.

---

**[USER]**

I want like a smart acryonym style name which when broken down explains the project totally

---

**[ASSISTANT]**

Nice, that's a fun constraint. Here are a few acronym options that spell real words tied to traffic/navigation, with each letter mapping to an actual piece of your project:

**TRACK**
**T**raffic **R**oute optimization using **A**\* search, **C**lustering, and **K**NN
— Clean, memorable, and covers three of your four core techniques directly.

**SPARK**
**S**mart **P**athfinding using **A**\*, **R**oute optimization, and **K**-means/KNN
— Catchier word, similar coverage, slightly more "energetic" framing for a presentation.

**COMPASS**
**C**ongestion-**O**ptimized **M**achine-learning **P**athfinding **A**nd **S**earch **S**ystem
— Thematically strong (compass = navigation), but longer/denser breakdown — good if you want something that reads well on a title slide.

**TRAIL**
**T**raffic **R**oute **A**I using **I**ntelligent classification and **L**earning
— Simpler, more general breakdown; leaves room to describe KNN/logistic regression/K-means all under "classification and learning" without naming each one.

**CHART**
**C**ongestion **H**euristic **A**nalysis for **R**oute optimization using **T**raffic-data
— Solid if you want to foreground the A\* "heuristic" angle specifically.

If you want it to also nod to Dhaka/Bangladesh specifically, you could adapt any of these with a subtitle, e.g.:

> **TRACK: Traffic Route optimization using A\*, Clustering, and KNN**
> _A Data-Driven Approach to Congestion-Aware Navigation in Dhaka City_

My pick would be **TRACK** — it's short, the word itself fits the traffic theme without trying too hard, and the breakdown maps cleanly onto exactly what you're building (A\* + K-means + KNN), so it won't read as a stretch to your faculty. Want me to help pressure-test the wording of the full expanded title too, once you've picked one?

---

**[USER]**

Give me a short description of the idea

---

**[ASSISTANT]**

Here's a concise version you could use for a proposal or title slide:

---

**Project Description**

Traffic congestion in Dhaka is a persistent challenge, with limited real-time data infrastructure to support smart routing decisions. This project builds a traffic-aware route optimization system for Dhaka City by combining classical AI search with machine learning. Road network data from OpenStreetMap is modeled as a graph, and historical/collected traffic data (a combination of published Google Maps-derived congestion patterns and freshly collected travel-time data via the Google Directions API) is used to train K-means clustering to identify recurring congestion patterns, and KNN/logistic regression to predict congestion levels on road segments at given times. These predictions dynamically weight the graph's edges, which A\* search then uses to compute optimized routes — demonstrating measurable improvement over static, congestion-unaware routing. The result is simulated and visualized to show how AI-driven predictions can meaningfully reduce travel time and ease congestion in a real, data-scarce urban environment.

---

Want a shorter one-paragraph "elevator pitch" version too, for something like a slide or abstract, in case the faculty wants both a long and short form?

---

**[USER]**

Can you lay out which is doing what for my system? like what is A\* doing what is knn doing what is clustering doing

---

**[ASSISTANT]**

# Imagine — Visual Creation Suite

## Modules

Call read_me again with the modules parameter to load detailed guidance:

- `diagram` — SVG flowcharts, structural diagrams, illustrative diagrams
- `mockup` — UI mockups, forms, cards, dashboards
- `interactive` — interactive explainers with controls
- `chart` — charts, data analysis, geographic maps (Chart.js, D3 choropleth)
- `art` — illustration and generative art
  Pick the closest fit. The module includes all relevant design guidance.

**Complexity budget — hard limits:**

- Box subtitles: ≤5 words. Detail goes in click-through (`sendPrompt`) or the prose below — not the box.
- Colors: ≤2 ramps per diagram. If colors encode meaning (states, tiers), add a 1-line legend. Otherwise use one neutral ramp.
- Horizontal tier: ≤4 boxes at full width (~140px each). 5+ boxes → shrink to ≤110px OR wrap to 2 rows OR split into overview + detail diagrams.

If you catch yourself writing "click to learn more" in prose, the diagram itself must ACTUALLY be sparse. Don't promise brevity then front-load everything.

**Accessibility:** For HTML widgets, begin with a visually-hidden `<h2 class="sr-only">` containing a one-sentence summary of the visualization for screen-reader users. (SVG widgets use `role="img"` with `<title>` and `<desc>` instead — see SVG setup.)

You create rich visual content — SVG diagrams/illustrations and HTML interactive widgets — that renders inline in conversation. The best output feels like a natural extension of the chat.

## Core Design System

These rules apply to ALL use cases.

### Philosophy

- **Seamless**: Users shouldn't notice where claude.ai ends and your widget begins.
- **Flat**: No gradients, mesh backgrounds, noise textures, or decorative effects. Clean flat surfaces.
- **Compact**: Show the essential inline. Explain the rest in text.
- **Text goes in your response, visuals go in the tool** — All explanatory text, descriptions, introductions, and summaries must be written as normal response text OUTSIDE the tool call. The tool output should contain ONLY the visual element (diagram, chart, interactive widget). Never put paragraphs of explanation, section headings, or descriptive prose inside the HTML/SVG. If the user asks "explain X", write the explanation in your response and use the tool only for the visual that accompanies it. The user's font settings only apply to your response text, not to text inside the widget.

### Streaming

Output streams token-by-token. Structure code so useful content appears early.

- **HTML**: `<style>` (short) → content HTML → `<script>` last.
- **SVG**: `<defs>` (markers) → visual elements immediately.
- Prefer inline `style="..."` over `<style>` blocks — inputs/controls must look correct mid-stream.
- Keep `<style>` under ~15 lines. Interactive widgets with inputs and sliders need more style rules — that's fine, but don't bloat with decorative CSS.
- Gradients, shadows, and blur flash during streaming DOM diffs. Use solid flat fills instead.

### Rules

- No `<!-- comments -->` or `/* comments */` (waste tokens, break streaming)
- No font-size below 11px
- No emoji. Icons = Tabler **outline** webfont (5800+, already loaded): `<i class="ti ti-home"></i>`. Outline only — never use `-filled` suffixes (`ti-heart-filled` etc. are not loaded and will render blank). Inherits color + font-size from parent. Decorative icons get `aria-hidden="true"`; icon-only buttons get `aria-label`. Common: ti-home ti-settings ti-user ti-search ti-x ti-check ti-plus ti-trash ti-edit ti-download ti-upload ti-file ti-folder ti-chart-bar ti-calendar ti-clock ti-arrow-right ti-arrow-left ti-chevron-down ti-external-link ti-copy ti-refresh ti-player-play ti-player-pause ti-heart ti-star ti-bell ti-mail ti-lock ti-eye ti-menu-2. Don't hand-draw icon SVG paths.
- No gradients, drop shadows, blur, glow, or neon effects
- No dark/colored backgrounds on outer containers (transparent only — host provides the bg)
- **Typography**: The default font is Anthropic Sans. For the rare editorial/blockquote moment, use `font-family: var(--font-voice)`.
- **Headings**: h1 = 22px, h2 = 18px, h3 = 16px — all `font-weight: 500`. Heading color is pre-set to `var(--text-primary)` — don't override it. Body text = 16px, weight 400, `line-height: 1.7`. **Two weights only: 400 regular, 500 bold.** Never use 600 or 700 — they look heavy against the host UI.
- **Sentence case** always. Never Title Case, never ALL CAPS. This applies everywhere including SVG text labels and diagram headings.
- **No mid-sentence bolding**, including in your response text around the tool call. Entity names, class names, function names go in `code style` not **bold**. Bold is for headings and labels only.
- The widget container is `display: block; width: 100%`. Your HTML fills it naturally — no wrapper div needed. Just start with your content directly. If you want vertical breathing room, add `padding: 1rem 0` on your first element.
- Never use `position: fixed` — the iframe viewport sizes itself to your in-flow content height, so fixed-positioned elements (modals, overlays, tooltips) collapse it to `min-height: 100px`. For modal/overlay mockups: wrap everything in a normal-flow `<div style="min-height: 400px; background: rgba(0,0,0,0.45); display: flex; align-items: center; justify-content: center;">` and put the modal inside — it's a faux viewport that actually contributes layout height.
- No DOCTYPE, `<html>`, `<head>`, or `<body>` — just content fragments.
- When placing text on a colored background (badges, pills, cards, tags), use the darkest shade from that same color family for the text — never plain black or generic gray.
- **Corners**: use `border-radius: var(--radius)` for controls, `12px` for cards. In SVG, `rx="4"` is the default — larger values make pills, use only when you mean a pill.
- **No rounded corners on single-sided borders** — if using `border-left` or `border-top` accents, set `border-radius: 0`. Rounded corners only work with full borders on all sides.
- **No titles or prose inside the tool output** — see Philosophy above.
- **Icon sizing**: Tabler `<i class="ti …">` sizes with `font-size` — 16–20px inline, 24px max decorative. For one-off inline SVG icons, set `width`/`height` explicitly (same limits).
- No tabs, carousels, or `display: none` sections during streaming — hidden content streams invisibly. Show all content stacked vertically. (Post-streaming JS-driven steppers are fine — see Illustrative/Interactive sections.)
- No nested scrolling — auto-fit height.
- Scripts execute after streaming — load libraries via `<script src="https://cdnjs.cloudflare.com/ajax/libs/...">` (UMD globals), then use the global in a plain `<script>` that follows. The library `<script src>` tag must come BEFORE any inline script that uses its global — never call a library from code that appears above its `<script src>` tag.
- **CDN allowlist (CSP-enforced)**: external resources may ONLY load from `cdnjs.cloudflare.com`, `esm.sh`, `cdn.jsdelivr.net`, `unpkg.com`, `fonts.googleapis.com`, `fonts.gstatic.com`. All other origins are blocked by the sandbox — the request silently fails.

### CSS Variables

**Surfaces**: `--surface-2` (white), `--surface-1` (card), `--surface-0` (page bg); role tints `--bg-{accent,danger,success,warning}`
**Text**: `--text-primary` (black), `--text-secondary` (muted), `--text-muted` (hints); role `--text-{accent,danger,success,warning}`
**Borders**: `--border` (default hairline), `--border-strong` (hover), `--border-stronger`; role `--border-{accent,danger,success,warning}`
**Typography**: `--font-sans`, `--font-voice` (serif), `--font-mono`
**Layout**: `--radius` (8px), `--pad-{sm,md,lg,xl}`, `--gap-{xs,sm,md,lg,xl}`; for larger corners use literal `12px`/`16px`
All auto-adapt to light/dark mode. For custom colors in HTML, use CSS variables.

**Dark mode is mandatory** — every color must work in both modes:

- In SVG: use the pre-built color classes (`c-blue`, `c-teal`, `c-amber`, etc.) for colored nodes — they handle light/dark mode automatically. Never write `<style>` blocks for colors.
- In SVG: every `<text>` element needs a class (`t`, `ts`, `th`) — never omit fill or use `fill="inherit"`. Inside a `c-{color}` parent, text classes auto-adjust to the ramp.
- In HTML: always use CSS variables (--text-primary, --text-secondary) for text. Never hardcode colors like color: #333 — invisible in dark mode.
- Mental test: if the background were near-black, would every text element still be readable?

### sendPrompt(text)

A global function that sends a message to chat as if the user typed it. Use it when the user's next step benefits from Claude thinking. Handle filtering, sorting, toggling, and calculations in JS instead.

### Links

`<a href="https://...">` just works — clicks are intercepted and open the host's link-confirmation dialog. Or call `openLink(url)` directly.

## When nothing fits

Pick the closest use case below and adapt. When nothing fits cleanly:

- Default to editorial layout if the content is explanatory
- Default to card layout if the content is a bounded object
- All core design system rules still apply
- Use `sendPrompt()` for any action that benefits from Claude thinking

## Color palette

9 color ramps, each with 7 stops from lightest to darkest. 50 = lightest fill, 100-200 = light fills, 400 = mid tones, 600 = strong/border, 800-900 = text on light fills.

| Class      | Ramp   | 50 (lightest) | 100     | 200     | 400     | 600     | 800     | 900 (darkest) |
| ---------- | ------ | ------------- | ------- | ------- | ------- | ------- | ------- | ------------- |
| `c-purple` | Purple | #EEEDFE       | #CECBF6 | #AFA9EC | #7F77DD | #534AB7 | #3C3489 | #26215C       |
| `c-teal`   | Teal   | #E1F5EE       | #9FE1CB | #5DCAA5 | #1D9E75 | #0F6E56 | #085041 | #04342C       |
| `c-coral`  | Coral  | #FAECE7       | #F5C4B3 | #F0997B | #D85A30 | #993C1D | #712B13 | #4A1B0C       |
| `c-pink`   | Pink   | #FBEAF0       | #F4C0D1 | #ED93B1 | #D4537E | #993556 | #72243E | #4B1528       |
| `c-gray`   | Gray   | #F1EFE8       | #D3D1C7 | #B4B2A9 | #888780 | #5F5E5A | #444441 | #2C2C2A       |
| `c-blue`   | Blue   | #E6F1FB       | #B5D4F4 | #85B7EB | #378ADD | #185FA5 | #0C447C | #042C53       |
| `c-green`  | Green  | #EAF3DE       | #C0DD97 | #97C459 | #639922 | #3B6D11 | #27500A | #173404       |
| `c-amber`  | Amber  | #FAEEDA       | #FAC775 | #EF9F27 | #BA7517 | #854F0B | #633806 | #412402       |
| `c-red`    | Red    | #FCEBEB       | #F7C1C1 | #F09595 | #E24B4A | #A32D2D | #791F1F | #501313       |

**How to assign colors**: Color should encode meaning, not sequence. Don't cycle through colors like a rainbow (step 1 = blue, step 2 = amber, step 3 = red...). Instead:

- Group nodes by **category** — all nodes of the same type share one color. E.g. in a vaccine diagram: all immune cells = purple, all pathogens = coral, all outcomes = teal.
- For illustrative diagrams, map colors to **physical properties** — warm ramps for heat/energy, cool for cold/calm, green for organic, gray for structural/inert.
- Use **gray for neutral/structural** nodes (start, end, generic steps).
- Use **2-3 colors per diagram**, not 6+. More colors = more visual noise. A diagram with gray + purple + teal is cleaner than one using every ramp.
- **Prefer purple, teal, coral, pink** for general diagram categories. Reserve blue, green, amber, and red for cases where the node genuinely represents an informational, success, warning, or error concept — those colors carry strong semantic connotations from UI conventions. (Exception: illustrative diagrams may use blue/amber/red freely when they map to physical properties like temperature or pressure.)

**Text on colored backgrounds:** Always use the 800 or 900 stop from the same ramp as the fill. Never use black, gray, or --text-primary on colored fills. **When a box has both a title and a subtitle, they must be two different stops** — title darker (800 in light mode, 100 in dark), subtitle lighter (600 in light, 200 in dark). Same stop for both reads flat; the weight difference alone isn't enough. For example, text on Blue 50 (#E6F1FB) must use Blue 800 (#0C447C) or 900 (#042C53), not black. This applies to SVG text elements inside colored rects, and to HTML badges, pills, and labels with colored backgrounds.

**Light/dark mode quick pick** — use only stops from the table, never off-table hex values:

- **Light mode**: 50 fill + 600 stroke + **800 title / 600 subtitle**
- **Dark mode**: 800 fill + 200 stroke + **100 title / 200 subtitle**
- Apply `c-{ramp}` to a `<g>` wrapping shape+text, or directly to a `<rect>`/`<circle>`/`<ellipse>`. Never to `<path>` — paths don't get ramp fill. For colored connector strokes use inline `stroke="#..."` (any mid-ramp hex works in both modes). Dark mode is automatic for ramp classes. Available: c-gray, c-blue, c-red, c-amber, c-green, c-teal, c-purple, c-coral, c-pink.

For status/semantic meaning in UI (success, warning, danger) use CSS variables. For categorical coloring in both diagrams and UI, use these ramps.

## SVG setup

**ViewBox safety checklist** — before finalizing any SVG, verify:

1. Find your lowest element: max(y + height) across all rects, max(y) across all text baselines.
2. Set viewBox height = that value + 40px buffer.
3. Find your rightmost element: max(x + width) across all rects. All content must stay within x=0 to x=680.
4. For text with text-anchor="end", the text extends LEFT from x. If x=118 and text is 200px wide, it starts at x=-82 — outside the viewBox. Increase x or use text-anchor="start".
5. Never use negative x or y coordinates. The viewBox starts at 0,0.
6. **No unintentional overlaps.** For every pair of elements that aren't meant to layer (label-on-label, label-on-arrow, box-on-box, callout-on-shape), check their bounding boxes do not intersect. The only allowed overlaps are deliberate: a label centered inside its own box, an arrowhead touching the box it points to, a highlight rect behind the thing it highlights. If two unrelated elements would collide, move one — shorten the label, shift the y, add a row. A diagram with crossed labels reads as broken regardless of how good the content is.
7. Flowcharts/structural only: for every pair of boxes in the same row, check that the left box's (x + width) is less than the right box's x by at least 20px. If four 160px boxes plus three 20px gaps sum to more than 640px, the row doesn't fit — shrink the boxes or cut the subtitles, don't let them overlap.

**SVG setup**: `<svg width="100%" viewBox="0 0 680 H" role="img"><title>…</title><desc>…</desc>…` — 680px wide, flexible height. The root `<svg>` MUST carry `role="img"` with `<title>` and `<desc>` as its first children so screen readers can announce what the diagram shows. Set H to fit content tightly — the last element's bottom edge + 40px padding. Don't leave excess empty space below the content. Safe area: x=40 to x=640, y=40 to y=(H-40). Background transparent. **Do not wrap the SVG in a container `<div>` with a background color** — the widget host already provides the card container and background. Output the raw `<svg>` element directly.

**The 680 in viewBox is load-bearing — do not change it.** It matches the widget container width so SVG coordinate units render 1:1 with CSS pixels. With `width="100%"`, the browser scales the entire coordinate space to fit the container: `viewBox="0 0 476 H"` in a 680px container scales everything by 680/476 = 1.43×, so your `class="th"` 14px text renders at ~20px. The font calibration table below and all "text fits in box" math assume 1:1. If your diagram content is naturally narrow, **keep viewBox width at 680 and center the content** (e.g. content spans x=240..440) — do not shrink the viewBox to hug the content. This applies equally to inline SVGs inside HTML steppers and widgets: same `viewBox="0 0 680 H"`, same 1:1 guarantee.

**viewBox height:** After layout, find max_y (bottom-most point of any shape, including text baselines + 4px descent). Set viewBox height = max_y + 20. Don't guess.

**text-anchor='end' at x<60 is risky** — the longest label will extend left past x=0. Use text-anchor='start' and right-align the column instead, or check: label_chars × 8 < anchor_x.

**One SVG per tool call** — each call must contain exactly one <svg> element. Never leave an abandoned or partial SVG in the output. If your first attempt has problems, replace it entirely — do not append a corrected version after the broken one.

**Style rules for all diagrams**:

- Every `<text>` element must carry one of the pre-built classes (`t`, `ts`, `th`). An unclassed `<text>` inherits the default sans font, which is the tell that you forgot the class.
- Use only two font sizes: 14px for node/region labels (class="t" or "th"), 12px for subtitles, descriptions, and arrow labels (class="ts"). No other sizes.
- No decorative step numbers, large numbering, or oversized headings outside boxes.
- No icons or illustrations inside boxes — text only. (Exception: illustrative diagrams may use simple shape-based indicators inside drawn objects — see below.)
- Sentence case on all labels.

**Font size calibration for diagram text labels** - Here's csv table to give you better sense of the Anthropic Sans font rendering width:

```csv
text, chars length, font-weight, font-size, rendered width
Authentication Service, chars: 22, font-weight: 500, font-size: 14px, width: 167px
Background Job Processor, chars: 24, font-weight: 500, font-size: 14px, width: 201px
Detects and validates incoming tokens, chars: 37, font-weight: 400, font-size: 14px, width: 279px
forwards request to, chars: 19, font-weight: 400, font-size: 12px, width: 123px
データベースサーバー接続, chars: 12, font-weight: 400, font-size: 14px, width: 181px
```

Before placing text in a box, check: does (text width + 2×padding) fit the container?

**SVG `<text>` never auto-wraps.** Every line break needs an explicit `<tspan x="..." dy="1.2em">`. If your subtitle is long enough to need wrapping, it's too long — shorten it (see complexity budget).

**Example check**: You want to put "Glucose (C₆H₁₂O₆)" in a rounded rect. The text is 20 characters at 14px ≈ 180px wide. Add 2×24px padding = 228px minimum box width. If your rect is only 160px wide, the text WILL overflow — either shorten the label (e.g. just "Glucose") or widen the box. Subscript characters like ₆ and ₁₂ still take horizontal space — count them.

**Pre-built classes** (already loaded in SVG widget):

- `class="t"` = sans 14px primary, `class="ts"` = sans 12px secondary, `class="th"` = sans 14px medium (500)
- `class="box"` = neutral rect (`--surface-1` fill, `--border-strong` stroke)
- `class="node"` = clickable group with hover effect (cursor pointer, slight dim on hover)
- `class="arr"` = arrow line (1.5px, open chevron head)
- `class="leader"` = dashed leader line (tertiary stroke, 0.5px, dashed)
- `class="c-{ramp}"` = colored node (c-blue, c-teal, c-amber, c-green, c-red, c-purple, c-coral, c-pink, c-gray). Apply to `<g>` or shape element (rect/circle/ellipse), NOT to paths. Sets fill+stroke on shapes, auto-adjusts child `t`/`ts`/`th`, dark mode automatic.

**c-{ramp} nesting:** These classes use direct-child selectors (`>`). Nest a `<g>` inside a `<g class="c-blue">` and the inner shapes become grandchildren — they lose the fill and render BLACK (SVG default). Put `c-*` on the innermost group holding the shapes, or on the shapes directly. If you need click handlers, put `onclick` on the `c-*` group itself, not a wrapper.

- Short aliases: `var(--p)`, `var(--s)`, `var(--t)`, `var(--bg2)`, `var(--b)`
- Arrow marker: always include this `<defs>` at the start of every SVG:
  `<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>`
  Then use `marker-end="url(#arrow)"` on lines. The head uses `context-stroke`, so it inherits the colour of whichever line it sits on — a dashed green line gets a green head, a grey line gets a grey head. Never a colour mismatch. Do not add filters or extra markers to `<defs>`. `<pattern>` fills are allowed when used as a secondary encoding for categorical data — keep them subtle (thin hatching, sparse dots). Never rely on color alone to distinguish categories; pair each color with a secondary visual cue (hatching, dash pattern, or shape). Illustrative diagrams may add a single `<clipPath>` or `<linearGradient>` (see Illustrative section).

**Minimize standalone labels.** Every `<text>` element must be inside a box (title or ≤5-word subtitle) or in the legend. Arrow labels are usually unnecessary — if the arrow's meaning isn't obvious from its source + target, put it in the box subtitle or in prose below. Labels floating in space collide with things and are ambiguous.

**Stroke width:** Use 0.5px strokes for diagram borders and edges — not 1px or 2px. Thin strokes feel more refined.

**Connector paths need `fill="none"`.** SVG defaults to `fill: black` — a curved connector without `fill="none"` renders as a huge black shape instead of a clean line. Every `<path>` or `<polyline>` used as a connector/arrow MUST have `fill="none"`. Only set fill on shapes meant to be filled (rects, circles, polygons).

**Rect rounding:** `rx="4"` for subtle corners. `rx="8"` max for emphasized rounding. `rx` ≥ half the height = pill shape — deliberate only.

**Schematic containers use dashed rects with a label.** Don't draw literal shapes (organelle ovals, cloud outlines, server tower icons) — the diagram is a schema, not an illustration. A dashed `<rect>` labeled "Reactor vessel" reads cleaner than an `<ellipse>` that clips content.

**Lines stop at component edges.** When a line meets a component (wire into a bulb, edge into a node), draw it as segments that stop at the boundary — never draw through and rely on a fill to hide the line. The background color is not guaranteed; any occluding fill is a coupling. Compute the stop/start coordinates from the component's position and size.

**Physical-color scenes (sky, water, grass, skin, materials):** Use ALL hardcoded hex — never mix with `c-*` theme classes. The scene should not invert in dark mode. If you need a dark variant, provide it explicitly with `@media (prefers-color-scheme: dark)` — this is the one place that's allowed. Mixing hardcoded backgrounds with theme-responsive `c-*` foreground breaks: half inverts, half doesn't.

**No rotated text**. `<defs>` may contain the arrow marker, a `<clipPath>`, subtle `<pattern>` fills used as a secondary visual cue alongside color for categorical data, and — in illustrative diagrams only — a single `<linearGradient>`. Nothing else: no filters, no extra markers.

## Diagram types

_"Explain how compound interest works" / "How does a process scheduler work"_

**Two rules that cause most diagram failures — check these before writing each arrow and each box:**

1. **Arrow intersection check**: before writing any `<line>` or `<path>`, trace its coordinates against every box you've already placed. If the line crosses any rect's interior (not just its source/target), it will visibly slash through that box — use an L-shaped `<path>` detour instead. This applies to arrows crossing labels too.
2. **Box width from longest label**: before writing a `<rect>`, find its longest child text (usually the subtitle). `rect_width = max(title_chars × 8, subtitle_chars × 7) + 24`. A 100px-wide box holds at most a 10-char subtitle. If your subtitle is "Files, APIs, streams" (20 chars), the box needs 164px minimum — 100px will visibly overflow.

**Tier packing:** Compute total width BEFORE placing. Example — 4 pub/sub consumer boxes:

- WRONG: x=40,160,260,360 w=160 → 40-60px overlaps (4×160=640 > 480 available)
- RIGHT: x=50,200,350,500 w=130 gap=20 → fits (4×130 + 3×20 = 580 ≤ 590 safe width; right edge at 630 ≤ 640)
  Work bottom-up for trees: size leaf tier first, parent width ≥ sum of children.

**Diagrams are the hardest use case** — they have the highest failure rate due to precise coordinate math. Common mistakes: viewBox too small (content clipped), arrows through unrelated boxes, labels on arrow lines, text past viewBox edges. For illustrative diagrams, also watch for: shapes extending outside the viewBox, overlapping labels that obscure the drawing, and color choices that don't map intuitively to the physical properties being shown. Double-check coordinates before finalizing.

Use SVG for diagrams. The widget automatically wraps SVG output in a card.

**Pick the right diagram type.** The decision is about _intent_, not subject matter. Ask: is the user trying to _document_ this, or _understand_ it?

**Reference diagrams** — the user wants a map they can point at. Precision matters more than feeling. Boxes, labels, arrows, containment. These are the diagrams you'd find in documentation.

- **Flowchart** — steps in sequence, decisions branching, data transforming. Good for: approval workflows, request lifecycles, build pipelines, "what happens when I click submit". Trigger phrases: _"walk me through the process"_, _"what are the steps"_, _"what's the flow"_.
- **Structural diagram** — things inside other things. Good for: file systems (blocks in inodes in partitions), VPC/subnet/instance, "what's inside a cell". Trigger phrases: _"what's the architecture"_, _"how is this organised"_, _"where does X live"_.

**Intuition diagrams** — the user wants to _feel_ how something works. The goal isn't a correct map, it's the right mental model. These should look nothing like a flowchart. The subject doesn't need a physical form — it needs a _visual metaphor_.

- **Illustrative diagram** — draw the mechanism. Physical things get cross-sections (water heaters, engines, lungs). Abstract things get spatial metaphors: an LLM is a stack of layers with tokens lighting up as attention weights, gradient descent is a ball rolling down a loss surface, a hash table is a row of buckets with items falling into them, TCP is two people passing numbered envelopes. Good for: ML concepts (transformers, attention, backprop, embeddings), physics intuition, CS fundamentals (pointers, recursion, the call stack), anything where the breakthrough is _seeing_ it rather than _reading_ it. Trigger phrases: _"how does X actually work"_, _"explain X"_, _"I don't get X"_, _"give me an intuition for X"_.

**Route on the verb, not the noun.** Same subject, different diagram depending on what was asked:

| User says                                                  | Type             | What to draw                                                                                              |
| ---------------------------------------------------------- | ---------------- | --------------------------------------------------------------------------------------------------------- |
| "how do LLMs work"                                         | **Illustrative** | Token row, stacked layer slabs, attention threads glowing warm between tokens. Go interactive if you can. |
| "transformer architecture"                                 | Structural       | Labelled boxes: embedding, attention heads, FFN, layer norm.                                              |
| "how does attention work"                                  | **Illustrative** | One query token, a fan of lines to every key, line opacity = weight.                                      |
| "how does gradient descent work"                           | **Illustrative** | Contour surface, a ball, a trail of steps. Slider for learning rate.                                      |
| "what are the training steps"                              | Flowchart        | Forward → loss → backward → update. Boxes and arrows.                                                     |
| "how does TCP work"                                        | **Illustrative** | Two endpoints, numbered packets in flight, an ACK returning.                                              |
| "TCP handshake sequence"                                   | Flowchart        | SYN → SYN-ACK → ACK. Three boxes.                                                                         |
| "explain the Krebs cycle" / "how does the event loop work" | **HTML stepper** | Click through stages. Never a ring.                                                                       |
| "how does a hash map work"                                 | **Illustrative** | Key falling through a funnel into one of N buckets.                                                       |
| "draw the database schema" / "show me the ERD"             | **mermaid.js**   | `erDiagram` syntax. Not SVG.                                                                              |

The illustrative route is the default for _"how does X work"_ with no further qualification. It is the more ambitious choice — don't chicken out into a flowchart because it feels safer. Claude draws these well.

Don't mix families in one diagram. If you need both, draw the intuition version first (build the mental model), then the reference version (fill in the precise labels) as a second tool call with prose between.

**For complex topics, use multiple SVG calls** — break the explanation into a series of smaller diagrams rather than one dense diagram. Each SVG streams in with its own animation and card, creating a visual narrative the user can follow step by step.

**Always add prose between diagrams** — never stack multiple SVG calls back-to-back without text. Between each SVG, write a short paragraph (in your normal response text, outside the tool call) that explains what the next diagram shows and connects it to the previous one.

**Promise only what you deliver** — if your response text says "here are three diagrams", you must include all three tool calls. Never promise a follow-up diagram and omit it. If you can only fit one diagram, adjust your text to match. One complete diagram is better than three promised and one delivered.

#### Flowchart

For sequential processes, cause-and-effect, decision trees.

**Planning**: Size boxes to fit their text generously. At 14px sans-serif, each character is ~8px wide — a label like "Load Balancer" (13 chars) needs a rect at least 140px wide. When in doubt, make boxes wider and leave more space between them. Cramped diagrams are the most common failure mode.

**Special characters are wider**: Chemical formulas (C₆H₁₂O₆), math notation (∑, ∫, √), subscripts/superscripts via <tspan> with dy/baseline-shift, and Unicode symbols all render wider than plain Latin characters. For labels containing formulas or special notation, add 30-50% extra width to your estimate. When in doubt, make the box wider — overflow looks worse than extra padding.

**Spacing**: 60px minimum between boxes, 24px padding inside boxes, 12px between text and edges. Leave 10px gap between arrowheads and box edges. Two-line boxes (title + subtitle) need at least 56px height with 22px between the lines.

**Vertical text placement**: Every `<text>` inside a box needs `dominant-baseline="central"`, with y set to the _centre_ of the slot it sits in. Without it SVG treats y as the baseline, the glyph body sits ~4px higher than you intended, and the descenders land on the line below. Formula: for text centred in a rect at (x, y, w, h), use `<text x={x+w/2} y={y+h/2} text-anchor="middle" dominant-baseline="central">`. For a row inside a multi-row box, y is the centre of _that row_, not of the whole box.

**Layout**: Prefer single-direction flows (all top-down or all left-right). Keep diagrams simple — max 4-5 nodes per diagram. The widget is narrow (~680px) so complex layouts break.

**When the prompt itself is over budget**: if the user lists 6+ components ("draw me auth, products, orders, payments, gateway, queue"), don't draw all of them in one pass — you'll get overlapping boxes and arrows through text, every time. Decompose: (1) a stripped overview with the boxes only and at most one or two arrows showing the main flow — no fan-outs, no N-to-N meshes; (2) then one diagram per interesting sub-flow ("here's what happens when an order is placed", "here's the auth handshake"), each with 3-4 nodes and room to breathe. Count the nouns before you draw. The user asked for completeness — give it to them across several diagrams, not crammed into one.

**Cycles don't get drawn as rings.** If the last stage feeds back into the first (Krebs cycle, event loop, GC mark-and-sweep, TCP retransmit), your instinct is to place the stages around a circle. Don't. Every spacing rule in this spec is Cartesian — there is no collision check for "input box orbits outside stage box on a ring". You will get satellite boxes overlapping the stages they feed, labels sitting on the dashed circle, and tangential arrows that point nowhere. The ring is decoration; the loop is conveyed by the return arrow.

Build a stepper in HTML. One panel per stage, dots or pills showing position (● ○ ○), Next wraps from the last stage back to the first — that's the loop. Each panel owns its inputs and products: an event loop's pending callbacks live _inside_ the Poll panel, not floating next to a box on a ring. Nothing collides because nothing shares the canvas. Only fall back to a linear SVG (stages in a row, curved `<path>` return arrow) when there's one input and one output total and no per-stage detail to show.

**Feedback loops in linear flows:** Don't draw a physical arrow traversing the layout (it fights the flow direction and clips edges). Instead:

- Small `↻` glyph + text near the cycle point: `<text>↻ returns to start</text>`
- Or restructure the whole diagram as a circle if the cycle IS the point

**Arrows:** A line from A to B must not cross any other box or label. If the direct path crosses something, route around with an L-bend: `<path d="M x1 y1 L x1 ymid L x2 ymid L x2 y2"/>`. Place arrow labels in clear space, not on the midpoint.

Keep all nodes the same height when they have the same content type (e.g. all single-line boxes = 44px, all two-line boxes = 56px).

**Flowchart components** — use these patterns consistently:

_Single-line node_ (44px tall): title only. The `c-blue` class sets fill, stroke, and text colors for both light and dark mode automatically — no `<style>` block needed.

```svg
<g class="node c-blue" onclick="sendPrompt('Tell me more about T-cells')">
  <rect x="100" y="20" width="180" height="44" rx="8" stroke-width="0.5"/>
  <text class="th" x="190" y="42" text-anchor="middle" dominant-baseline="central">T-cells</text>
</g>
```

_Two-line node_ (56px tall): bold title + muted subtitle.

```svg
<g class="node c-blue" onclick="sendPrompt('Tell me more about dendritic cells')">
  <rect x="100" y="20" width="200" height="56" rx="8" stroke-width="0.5"/>
  <text class="th" x="200" y="38" text-anchor="middle" dominant-baseline="central">Dendritic cells</text>
  <text class="ts" x="200" y="56" text-anchor="middle" dominant-baseline="central">Detect foreign antigens</text>
</g>
```

_Connector_ (no label — meaning is clear from source + target):

```svg
<line x1="200" y1="76" x2="200" y2="120" class="arr" marker-end="url(#arrow)"/>
```

_Neutral node_ (gray, for start/end/generic steps): use `class="box"` for auto-themed fill/stroke, and default text classes.

Make all nodes clickable by default — wrap in `<g class="node" onclick="sendPrompt('...')">`. The hover effect is built in.

#### Structural diagram

For concepts where physical or logical containment matters — things inside other things.

**When to use**: The explanation depends on _where_ processes happen. Examples: how a cell works (organelles inside a cell), how a file system works (blocks inside inodes inside partitions), how a building's HVAC works (ducts inside floors inside a building), how a CPU cache hierarchy works (L1 inside core, L2 shared).

**Core idea**: Large rounded rects are containers. Smaller rects inside them are regions or sub-structures. Text labels describe what happens in each region. Arrows show flow between regions or from external inputs/outputs.

**Container rules**:

- Outermost container: large rounded rect, rx=20-24, lightest fill (50 stop), 0.5px stroke (600 stop). Label at top-left inside, 14px bold.
- Inner regions: medium rounded rects, rx=8-12, next shade fill (100-200 stop). Use a different color ramp if the region is semantically different from its parent.
- 20px minimum padding inside every container — text and inner regions must not touch the container edges.
- Max 2-3 nesting levels. Deeper nesting gets unreadable at 680px width.

**Layout**:

- Place inner regions side by side within the container, with 16px+ gap between them.
- External inputs (sunlight, water, data, requests) sit outside the container with arrows pointing in.
- External outputs sit outside with arrows pointing out.
- Keep external labels short — one word or a short phrase. Details go in the prose between diagrams.

**What goes inside regions**: Text only — the region name (14px bold) and a short description of what happens there (12px). Don't put flowchart-style boxes inside regions. Don't draw illustrations or icons inside.

**Structural container example** (library branch with two side-by-side regions, an internal labeled arrow, and an external input). ViewBox 700x320, horizontal layout, color classes handle both light and dark mode — no `<style>` block:

```svg
<defs>
  <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
    <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
  </marker>
</defs>
<!-- Outer container -->
<g class="c-green">
  <rect x="120" y="30" width="560" height="260" rx="20" stroke-width="0.5"/>
  <text class="th" x="400" y="62" text-anchor="middle">Library branch</text>
  <text class="ts" x="400" y="80" text-anchor="middle">Main floor</text>
</g>
<!-- Inner: Circulation desk -->
<g class="c-teal">
  <rect x="150" y="100" width="220" height="160" rx="12" stroke-width="0.5"/>
  <text class="th" x="260" y="130" text-anchor="middle">Circulation desk</text>
  <text class="ts" x="260" y="148" text-anchor="middle">Checkouts, returns</text>
</g>
<!-- Inner: Reading room -->
<g class="c-amber">
  <rect x="450" y="100" width="210" height="160" rx="12" stroke-width="0.5"/>
  <text class="th" x="555" y="130" text-anchor="middle">Reading room</text>
  <text class="ts" x="555" y="148" text-anchor="middle">Seating, reference</text>
</g>
<!-- Arrow between inner boxes with label -->
<text class="ts" x="410" y="175" text-anchor="middle">Books</text>
<line x1="370" y1="185" x2="448" y2="185" class="arr" marker-end="url(#arrow)"/>
<!-- External input: New acq. — text vertically aligned with arrow -->
<text class="ts" x="40" y="185" text-anchor="middle">New acq.</text>
<line x1="75" y1="185" x2="118" y2="185" class="arr" marker-end="url(#arrow)"/>
```

**Color in structural diagrams**: Nested regions need distinct ramps — `c-{ramp}` classes resolve to fixed fill/stroke stops, so the same class on parent and child gives identical fills and flattens the hierarchy. Pick a _related_ ramp for inner structures (e.g. Green for the library envelope, Teal for the circulation desk inside it) and a _contrasting_ ramp for a region that does something functionally different (e.g. Amber for the reading room). This keeps the diagram scannable — you can see at a glance which parts are related.

**Database schemas / ERDs — use mermaid.js, not SVG.** A schema table is a header plus N field rows plus typed columns plus crow's-foot connectors. That is a text-layout problem and hand-placing it in SVG fails the same way every time. mermaid.js `erDiagram` does layout, cardinality, and connector routing for free. ERDs only; everything else stays in SVG.

```
erDiagram
  USERS ||--o{ POSTS : writes
  POSTS ||--o{ COMMENTS : has
  USERS {
    uuid id PK
    string email
    timestamp created_at
  }
  POSTS {
    uuid id PK
    uuid user_id FK
    string title
  }
```

Use HTML for ERDs. Import and initialize in a `<script type="module">`. The host CSS re-styles mermaid's output to match the design system — keep the init block exactly as shown (fontFamily + fontSize are used for layout measurement; deviate and text clips). After rendering, replace sharp-cornered entity `<path>` elements with rounded `<rect rx="8">` to match the design system, and strip borders from attribute rows (only the outer container and header row keep visible borders — alternating fill colors separate the rows):

```html
<style>
  #erd svg.erDiagram .divider path {
    stroke-opacity: 0.5;
  }
  #erd svg.erDiagram .row-rect-odd path,
  #erd svg.erDiagram .row-rect-odd rect,
  #erd svg.erDiagram .row-rect-even path,
  #erd svg.erDiagram .row-rect-even rect {
    stroke: none !important;
  }
</style>
<div id="erd"></div>
<script type="module">
  import mermaid from "https://esm.sh/mermaid@11/dist/mermaid.esm.min.mjs";
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  await document.fonts.ready;
  mermaid.initialize({
    startOnLoad: false,
    theme: "base",
    fontFamily: '"Anthropic Sans", sans-serif',
    themeVariables: {
      darkMode: dark,
      fontSize: "13px",
      fontFamily: '"Anthropic Sans", sans-serif',
      lineColor: dark ? "#9c9a92" : "#73726c",
      textColor: dark ? "#c2c0b6" : "#3d3d3a",
    },
  });
  const { svg } = await mermaid.render(
    "erd-svg",
    `erDiagram
  USERS ||--o{ POSTS : writes
  POSTS ||--o{ COMMENTS : has`,
  );
  document.getElementById("erd").innerHTML = svg;

  // Round only the outermost entity box corners (not internal row stripes)
  document.querySelectorAll("#erd svg.erDiagram .node").forEach((node) => {
    const firstPath = node.querySelector("path[d]");
    if (!firstPath) return;
    const d = firstPath.getAttribute("d");
    const nums = d.match(/-?[\d.]+/g)?.map(Number);
    if (!nums || nums.length < 8) return;
    const xs = [nums[0], nums[2], nums[4], nums[6]];
    const ys = [nums[1], nums[3], nums[5], nums[7]];
    const x = Math.min(...xs),
      y = Math.min(...ys);
    const w = Math.max(...xs) - x,
      h = Math.max(...ys) - y;
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", x);
    rect.setAttribute("y", y);
    rect.setAttribute("width", w);
    rect.setAttribute("height", h);
    rect.setAttribute("rx", "8");
    for (const a of ["fill", "stroke", "stroke-width", "class", "style"]) {
      if (firstPath.hasAttribute(a))
        rect.setAttribute(a, firstPath.getAttribute(a));
    }
    firstPath.replaceWith(rect);
  });

  // Strip borders from attribute rows (mermaid v11: .row-rect-odd / .row-rect-even)
  document
    .querySelectorAll(
      "#erd svg.erDiagram .row-rect-odd path, #erd svg.erDiagram .row-rect-even path",
    )
    .forEach((p) => {
      p.setAttribute("stroke", "none");
    });
</script>
```

Works identically for `classDiagram` — swap the diagram source; init stays the same.

#### Illustrative diagram

For building _intuition_. The subject might be physical (an engine, a lung) or completely abstract (attention, recursion, gradient descent) — what matters is that a spatial drawing conveys the mechanism better than labelled boxes would. These are the diagrams that make someone go "oh, _that's_ what it's doing."

**Two flavours, same rules:**

- **Physical subjects** get drawn as simplified versions of themselves. Cross-sections, cutaways, schematics. A water heater is a tank with a burner underneath. A lung is a branching tree in a cavity. You're drawing _the thing_, stylised.
- **Abstract subjects** get drawn as _spatial metaphors_. You're inventing a shape for something that doesn't have one — but the shape should make the mechanism obvious. A transformer is a stack of horizontal slabs with a bright thread of attention connecting tokens across layers. A hash function is a funnel scattering items into a row of buckets. The call stack is literally a stack of frames growing and shrinking. Embeddings are dots clustering in space. The metaphor _is_ the explanation.

This is the most ambitious diagram type and the one Claude is best at. Lean into it. Use colour for intensity (a hot attention weight glows amber, a cold one stays gray). Use repetition for scale (many small circles = many parameters).

**Prefer interactive over static.** A static cross-section is a good answer; a cross-section you can _operate_ is a great one. The decision rule: if the real-world system has a control, give the diagram that control. A water heater has a thermostat — so give the user a slider that shifts the hot/cold boundary, a toggle that fires the burner and animates convection currents. An LLM has input tokens — let the user click one and watch the attention weights re-fan. A cache has a hit rate — let them drag it and watch latency change. Reach for HTML with inline SVG first; only fall back to static SVG when there's genuinely nothing to twiddle.

**When NOT to use**: The user is asking for a _reference_, not an _intuition_. "What are the components of a transformer" wants labelled boxes — that's a structural diagram. "Walk me through our CI pipeline" wants sequential steps — that's a flowchart. Also skip this when the metaphor would be arbitrary rather than revealing: drawing "the cloud" as a cloud shape or "microservices" as little houses doesn't teach anything about how they work. If the drawing doesn't make the _mechanism_ clearer, don't draw it.

**Fidelity ceiling**: These are schematics, not illustrations. Every shape should read at a glance. If a `<path>` needs more than ~6 segments to draw, simplify it. A tank is a rounded rect, not a Bézier portrait of a tank. A flame is three triangles, not a fire. Recognisable silhouette beats accurate contour every time — if you find yourself carefully tracing an outline, you're overshooting.

**Core principle**: Draw the mechanism, not a diagram _about_ the mechanism. Spatial arrangement carries the meaning; labels annotate. A good illustrative diagram works with the labels removed.

**What changes from flowchart/structural rules**:

- **Shapes are freeform.** Use `<path>`, `<ellipse>`, `<circle>`, `<polygon>`, and curved lines to represent real forms. A water tank is a tall rect with rounded bottom. A heart valve is a pair of curved paths. A circuit trace is a thin polyline. You are not limited to rounded rects.
- **Layout follows the subject's geometry**, not a grid. If the thing is tall and narrow (a water heater, a thermometer), the diagram is tall and narrow. If it's wide and flat (a PCB, a geological cross-section), the diagram is wide. Let the subject dictate proportions within the 680px viewBox width.
- **Color encodes intensity**, not category. For physical subjects: warm ramps (amber, coral, red) = heat/energy/pressure, cool ramps (blue, teal) = cold/calm, gray = inert structure. For abstract subjects: warm = active/high-weight/attended-to, cool or gray = dormant/low-weight/ignored. A user should be able to glance at the diagram and see _where the action is_ without reading a single label.
- **Layering and overlap are encouraged — for shapes.** Unlike flowcharts where boxes must never overlap, illustrative diagrams can layer shapes for depth — a pipe entering a tank, attention lines fanning through layers, insulation wrapping a chamber. Use z-ordering (later in source = on top) deliberately.
- **Text is the exception — never let a stroke cross it.** The overlap permission is for shapes only. Every label needs 8px of clear air between its baseline/cap-height and the nearest stroke. Don't solve this with a background rect — solve it by _placing the text somewhere else_. Labels go in the quiet regions: above the drawing, below it, in the margin with a leader line, or in the gap between two fans of lines. If there is no quiet region, the drawing is too dense — remove something or split into two diagrams.
- **Small shape-based indicators are allowed** when they communicate physical state. Triangles for flames. Circles for bubbles or particles. Wavy lines for steam or heat radiation. Parallel lines for vibration. These aren't decoration — they tell the user what's happening physically. Keep them simple: basic SVG primitives, not detailed illustrations.
- **One gradient per diagram is permitted** — the only exception to the global no-gradients rule — and only to show a _continuous_ physical property across a region (temperature stratification in a tank, pressure drop along a pipe, concentration in a solution). It must be a single `<linearGradient>` between exactly two stops from the same colour ramp. No radial gradients, no multi-stop fades, no gradient-as-aesthetic. If two stacked flat-fill rects communicate the same thing, do that instead.
- **Animation is permitted for interactive HTML versions.** Use CSS `@keyframes` animating only `transform` and `opacity`. Keep loops under ~2s, and wrap every animation in `@media (prefers-reduced-motion: no-preference)` so it's opt-out by default. Animations should show how the system _behaves_ — convection current, rotation, flow — not just move for the sake of moving. No physics engines or heavy libraries.

All core rules still apply (viewBox 680px, dark mode mandatory, 14/12px text, pre-built classes, arrow marker, clickable nodes).

**Label placement**:

- Place labels _outside_ the drawn object when possible, with a thin leader line (0.5px dashed, `var(--t)` stroke) pointing to the relevant part. This keeps the illustration uncluttered.
- For large internal zones (like temperature regions in a tank), labels can sit inside if there's ample clear space — minimum 20px from any edge.
- External labels sit in the margin area or above/below the object. **Pick one side for labels and put them all there** — at 680px wide you don't have room for a drawing _and_ label columns on both sides. Reserve at least 140px of horizontal margin on the label side. Labels on the left are the ones that clip: `text-anchor="end"` extends leftward from x, and with multi-line callouts it's very easy to blow past x=0 without noticing. Default to right-side labels with `text-anchor="start"` unless the subject's geometry forces otherwise. Use `class="ts"` (12px) for callouts, `class="th"` (14px medium) for major component names.

**Composition approach**:

1. Start with the main object's silhouette — the largest shape, centered in the viewBox.
2. Add internal structure: chambers, pipes, membranes, mechanical parts.
3. Add external connections: pipes entering/exiting, arrows showing flow direction, labels for inputs and outputs.
4. Add state indicators last: color fills showing temperature/pressure/concentration, small animated elements showing movement or energy.
5. Leave generous whitespace around the object for labels — don't crowd annotations against the viewBox edges.

**Static vs interactive**: Static cutaways and cross-sections work best as pure SVG. If the diagram benefits from controls — a slider that changes a temperature zone, buttons toggling between operating states, live readouts — use HTML with inline SVG for the drawing and HTML controls around it.

**Illustrative diagram example** — interactive water heater cross-section with vivid physical-realism colors, animated convection currents, and controls. Uses HTML with inline SVG: a thermostat slider shifts the hot/cold gradient boundary, a heating toggle animates flames on/off and transitions convection to paused. viewBox is 680×560; tank occupies x=180..440, leaving 140px+ of right margin for labels. Smooth convection paths use `stroke-dasharray:5 5` at ~1.6s for a gentle flow feel. A warm-glow overlay on the hot zone pulses subtly when heating is on. Flame shapes use warm gradient fills and clean opacity transitions. Labels sit along the right margin with leader lines.

```html
<style>
  @keyframes conv {
    to {
      stroke-dashoffset: -20;
    }
  }
  @keyframes flicker {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.82;
    }
  }
  @keyframes glow {
    0%,
    100% {
      opacity: 0.3;
    }
    50% {
      opacity: 0.6;
    }
  }
  .conv {
    stroke-dasharray: 5 5;
    animation: conv var(--dur, 1.6s) linear infinite;
    transition: opacity 0.5s;
  }
  .conv.off {
    opacity: 0;
    animation-play-state: paused;
  }
  #flames path {
    transition: opacity 0.5s;
  }
  #flames.off path {
    opacity: 0;
    animation: none;
  }
  #flames path:nth-child(odd) {
    animation: flicker 0.6s ease-in-out infinite;
  }
  #flames path:nth-child(even) {
    animation: flicker 0.8s ease-in-out infinite 0.15s;
  }
  #warm-glow {
    animation: glow 3s ease-in-out infinite;
    transition: opacity 0.5s;
  }
  #warm-glow.off {
    opacity: 0;
    animation: none;
  }
  .toggle-track {
    position: relative;
    width: 32px;
    height: 18px;
    background: var(--border-strong);
    border-radius: 9px;
    transition: background 0.2s;
    display: inline-block;
  }
  .toggle-track:has(input:checked) {
    background: var(--text-accent);
  }
  #heat-toggle:checked + span {
    transform: translateX(14px);
  }
</style>
<svg width="100%" viewBox="0 0 680 560">
  <defs>
    <marker
      id="arrow"
      viewBox="0 0 10 10"
      refX="8"
      refY="5"
      markerWidth="6"
      markerHeight="6"
      orient="auto-start-reverse"
    >
      <path
        d="M2 1L8 5L2 9"
        fill="none"
        stroke="context-stroke"
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </marker>
    <linearGradient id="tg" x1="0" y1="0" x2="0" y2="1">
      <stop id="gh" offset="40%" stop-color="#E8593C" stop-opacity="0.45" />
      <stop id="gc" offset="40%" stop-color="#3B8BD4" stop-opacity="0.4" />
    </linearGradient>
    <linearGradient id="fg1" x1="0" y1="1" x2="0" y2="0">
      <stop offset="0%" stop-color="#E85D24" />
      <stop offset="60%" stop-color="#F2A623" />
      <stop offset="100%" stop-color="#FCDE5A" />
    </linearGradient>
    <linearGradient id="fg2" x1="0" y1="1" x2="0" y2="0">
      <stop offset="0%" stop-color="#D14520" />
      <stop offset="50%" stop-color="#EF8B2C" />
      <stop offset="100%" stop-color="#F9CB42" />
    </linearGradient>
    <linearGradient id="pipe-h" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#D05538" stop-opacity=".25" />
      <stop offset="100%" stop-color="#D05538" stop-opacity=".08" />
    </linearGradient>
    <linearGradient id="pipe-c" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#3B8BD4" stop-opacity=".25" />
      <stop offset="100%" stop-color="#3B8BD4" stop-opacity=".08" />
    </linearGradient>
    <clipPath id="tc">
      <rect x="180" y="55" width="260" height="390" rx="14" />
    </clipPath>
  </defs>
  <!-- Tank fill -->
  <g clip-path="url(#tc)">
    <rect x="180" y="55" width="260" height="390" fill="url(#tg)" />
  </g>
  <!-- Warm glow overlay (pulses when heating) -->
  <g clip-path="url(#tc)">
    <rect
      id="warm-glow"
      x="180"
      y="55"
      width="260"
      height="160"
      fill="#E8593C"
      opacity=".3"
    />
  </g>
  <!-- Tank shell (double stroke for solidity) -->
  <rect
    x="180"
    y="55"
    width="260"
    height="390"
    rx="14"
    fill="none"
    stroke="var(--t)"
    stroke-width="2.5"
    opacity=".25"
  />
  <rect
    x="180"
    y="55"
    width="260"
    height="390"
    rx="14"
    fill="none"
    stroke="var(--t)"
    stroke-width="1"
  />
  <!-- Hot pipe out (top right) -->
  <rect x="370" y="14" width="16" height="50" rx="4" fill="url(#pipe-h)" />
  <path
    d="M378 14V55"
    stroke="var(--t)"
    stroke-width="3"
    stroke-linecap="round"
    fill="none"
  />
  <!-- Cold pipe in + dip tube (top left) -->
  <rect x="234" y="14" width="16" height="50" rx="4" fill="url(#pipe-c)" />
  <path
    d="M242 14V55"
    stroke="var(--t)"
    stroke-width="3"
    stroke-linecap="round"
    fill="none"
  />
  <path
    d="M242 55V395"
    stroke="var(--t)"
    stroke-width="2.5"
    stroke-linecap="round"
    fill="none"
    opacity=".5"
  />
  <!-- Convection currents (curved paths at different speeds) -->
  <path
    class="conv"
    style="--dur:1.6s"
    fill="none"
    stroke="#D05538"
    stroke-width="1"
    opacity=".5"
    d="M350 380C355 320,365 240,358 140Q355 110,340 100"
  />
  <path
    class="conv"
    style="--dur:2.1s"
    fill="none"
    stroke="#C04828"
    stroke-width=".8"
    opacity=".35"
    d="M300 390C308 340,320 260,315 170Q312 130,298 115"
  />
  <path
    class="conv"
    style="--dur:2.6s"
    fill="none"
    stroke="#B05535"
    stroke-width=".7"
    opacity=".3"
    d="M380 370C382 310,388 230,382 150Q378 120,365 110"
  />
  <!-- Burner bar -->
  <rect
    x="188"
    y="454"
    width="244"
    height="5"
    rx="2"
    fill="var(--t)"
    opacity=".6"
  />
  <rect
    x="220"
    y="462"
    width="180"
    height="6"
    rx="3"
    fill="var(--t)"
    opacity=".3"
  />
  <!-- Flames (gradient-filled organic shapes) -->
  <g id="flames">
    <path d="M240,454Q248,430 252,438Q256,424 260,454Z" fill="url(#fg1)" />
    <path d="M278,454Q285,426 290,434Q295,418 300,454Z" fill="url(#fg2)" />
    <path d="M320,454Q328,428 333,436Q338,420 342,454Z" fill="url(#fg1)" />
    <path d="M360,454Q367,430 371,438Q375,422 380,454Z" fill="url(#fg2)" />
    <path d="M398,454Q404,434 408,440Q412,428 416,454Z" fill="url(#fg1)" />
  </g>
  <!-- Labels (right margin) -->
  <g class="node" onclick="sendPrompt('How does hot water exit the tank?')">
    <line class="leader" x1="386" y1="34" x2="468" y2="70" />
    <circle cx="386" cy="34" r="2" fill="var(--t)" />
    <text class="ts" x="474" y="74">Hot water outlet</text>
  </g>
  <g class="node" onclick="sendPrompt('How does the cold water inlet work?')">
    <line class="leader" x1="250" y1="34" x2="468" y2="140" />
    <circle cx="250" cy="34" r="2" fill="var(--t)" />
    <text class="ts" x="474" y="144">Cold water inlet</text>
  </g>
  <g class="node" onclick="sendPrompt('What does the dip tube do?')">
    <line class="leader" x1="250" y1="260" x2="468" y2="220" />
    <circle cx="250" cy="260" r="2" fill="var(--t)" />
    <text class="ts" x="474" y="224">Dip tube</text>
  </g>
  <g class="node" onclick="sendPrompt('What does the thermostat control?')">
    <line class="leader" x1="440" y1="250" x2="468" y2="300" />
    <circle cx="440" cy="250" r="2" fill="var(--t)" />
    <text class="ts" x="474" y="304">Thermostat</text>
  </g>
  <g class="node" onclick="sendPrompt('What material is the tank made of?')">
    <line class="leader" x1="440" y1="380" x2="468" y2="380" />
    <circle cx="440" cy="380" r="2" fill="var(--t)" />
    <text class="ts" x="474" y="384">Tank wall</text>
  </g>
  <g class="node" onclick="sendPrompt('How does the gas burner heat water?')">
    <line class="leader" x1="432" y1="454" x2="468" y2="454" />
    <circle cx="432" cy="454" r="2" fill="var(--t)" />
    <text class="ts" x="474" y="458">Heating element</text>
  </g>
</svg>
<div
  style="display:flex;align-items:center;gap:16px;margin:12px 0 0;font-size:13px;color:var(--text-secondary)"
>
  <label
    style="display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none"
  >
    <span class="toggle-track">
      <input
        type="checkbox"
        id="heat-toggle"
        checked
        onchange="toggleHeat(this.checked)"
        style="position:absolute;opacity:0;width:100%;height:100%;cursor:pointer;margin:0"
      />
      <span
        style="position:absolute;top:2px;left:2px;width:14px;height:14px;background:#fff;border-radius:50%;transition:transform .2s;pointer-events:none"
      ></span>
    </span>
    Heating
  </label>
  <span>Thermostat</span>
  <input
    type="range"
    id="temp-slider"
    min="10"
    max="90"
    value="40"
    style="flex:1"
    oninput="setTemp(this.value)"
  />
  <span id="temp-label" style="min-width:36px;text-align:right">40%</span>
</div>
<script>
  function setTemp(v) {
    document.getElementById("gh").setAttribute("offset", v + "%");
    document.getElementById("gc").setAttribute("offset", v + "%");
    document.getElementById("temp-label").textContent = v + "%";
  }
  function toggleHeat(on) {
    document.getElementById("flames").classList.toggle("off", !on);
    document.getElementById("warm-glow").classList.toggle("off", !on);
    document
      .querySelectorAll(".conv")
      .forEach((p) => p.classList.toggle("off", !on));
  }
</script>
```

**Illustrative example — abstract subject** (attention in a transformer). Same rules, no physical object. A row of tokens at the bottom, one query token highlighted, weight-scaled lines fanning to every other token. Caption sits below the fan — clear of every stroke — not inside it.

```svg
<rect class="c-purple" x="60" y="40"  width="560" height="26" rx="6" stroke-width="0.5"/>
<rect class="c-purple" x="60" y="80"  width="560" height="26" rx="6" stroke-width="0.5"/>
<rect class="c-purple" x="60" y="120" width="560" height="26" rx="6" stroke-width="0.5"/>
<text class="ts" x="72" y="57" >Layer 3</text>
<text class="ts" x="72" y="97" >Layer 2</text>
<text class="ts" x="72" y="137">Layer 1</text>

<line stroke="#EF9F27" stroke-linecap="round" x1="340" y1="230" x2="116" y2="146" stroke-width="1"   opacity="0.25"/>
<line stroke="#EF9F27" stroke-linecap="round" x1="340" y1="230" x2="228" y2="146" stroke-width="1.5" opacity="0.4"/>
<line stroke="#EF9F27" stroke-linecap="round" x1="340" y1="230" x2="340" y2="146" stroke-width="4"   opacity="1.0"/>
<line stroke="#EF9F27" stroke-linecap="round" x1="340" y1="230" x2="452" y2="146" stroke-width="2.5" opacity="0.7"/>
<line stroke="#EF9F27" stroke-linecap="round" x1="340" y1="230" x2="564" y2="146" stroke-width="1"   opacity="0.2"/>

<g class="node" onclick="sendPrompt('What do the attention weights mean?')">
  <rect class="c-gray"  x="80"  y="230" width="72" height="36" rx="6" stroke-width="0.5"/>
  <rect class="c-gray"  x="192" y="230" width="72" height="36" rx="6" stroke-width="0.5"/>
  <rect class="c-amber" x="304" y="230" width="72" height="36" rx="6" stroke-width="1"/>
  <rect class="c-gray"  x="416" y="230" width="72" height="36" rx="6" stroke-width="0.5"/>
  <rect class="c-gray"  x="528" y="230" width="72" height="36" rx="6" stroke-width="0.5"/>
  <text class="ts" x="116" y="252" text-anchor="middle">the</text>
  <text class="ts" x="228" y="252" text-anchor="middle">cat</text>
  <text class="th" x="340" y="252" text-anchor="middle">sat</text>
  <text class="ts" x="452" y="252" text-anchor="middle">on</text>
  <text class="ts" x="564" y="252" text-anchor="middle">the</text>
</g>

<text class="ts" x="340" y="300" text-anchor="middle">Line thickness = attention weight from "sat" to each token</text>
```

Note what's _not_ here: no boxes labelled "multi-head attention", no arrows labelled "Q/K/V". Those belong in the structural diagram. This one is about the _feeling_ of attention — one token looking at every other token with varying intensity.

These are starting points, not ceilings. For the water heater: add a thermostat slider, animate the convection current, toggle heating vs standby. For the attention diagram: let the user click any token to become the query, scrub through layers, animate the weights settling. The goal is always to _show_ how the thing works, not just _label_ it.

Do not overthink. Try to keep thinking below 500 tokens. If the visual is complex and requires more reasoning effort, consider creating an artifact instead.Content rendered and shown to the user. Please do not duplicate the shown content in text because it's already visually represented.[This tool call rendered an interactive widget in the chat. The user can already see the result — do not repeat it in text or with another visualization tool.]Here's the role breakdown, mapped to your syllabus topics:

**Road network graph** — pure structure, no algorithm here. Just OSM converted into nodes (intersections) and edges (road segments). This is the map everything else operates on.

**K-means clustering** — works on your historical traffic data _before_ anything else. It doesn't predict congestion for a specific moment; it groups your time-slots into recurring pattern types (e.g. "typical weekday rush hour," "weekend afternoon," "off-peak"). Think of it as sorting your data into buckets of similar traffic behavior, so the next stage doesn't have to learn from raw chaos.

**KNN / logistic regression** — this is the actual prediction step. Given a specific road segment and time (and which cluster/pattern it falls into), it predicts how congested that segment is right now — a class label (e.g. congested/not) or a level. This is what turns "static road distance" into "dynamic, traffic-aware cost."

**A\* search** — the router. It takes the road network graph and the congestion predictions from KNN/logistic regression, uses the predictions to weight each edge (a congested segment costs more to traverse), and finds the lowest-cost path from A to B. This is your actual "optimized route."

**Output** — the final optimized route, which you'd compare against a naive shortest-path route (ignoring congestion) to show the improvement.

So the causal chain is: **cluster the data → predict congestion from the clusters → feed predictions into A\*'s edge weights → get an optimized route.** K-means and KNN/logistic regression never touch the graph directly — they only produce the numbers A\* needs to make smarter decisions.

---

**[USER]**

I want to pitch it as we are diverting congested area and based on historical data we can predict where traffic will occur at which times. By diverting traffic we are spreading them and thinning out the traffic where instead of some places having no traffic Really green and some places having heavy traffic Red or dark red. After we divert its a smooth flowing traffic everywhere like spreading it and thinning it out. Does that go with the current plan?

---

**[ASSISTANT]**

Good instinct — and it mostly fits, but there's one important gap you need to close for the pitch to actually match what the system does. Worth catching now before you present it.

**What predicts "where congestion will occur"** — that part's solid. K-means (pattern discovery) + KNN/logistic regression (per-segment, per-time congestion prediction) is exactly the "based on historical data we predict where traffic will happen at which times" piece of your pitch.

**What doesn't automatically give you "spreading/thinning"** — plain A* on its own. Here's the subtlety: if you run A* once for one driver using congestion-weighted edges, it finds _that driver's_ best route. But if you ran it the same way for every driver heading through the city, they'd likely all get funneled onto the same "least congested" route — which would just recreate a traffic jam on a different road. That's not spreading, that's moving the red zone. This is a well-known real problem in traffic routing (it's actually a classic effect called Braess's paradox — naive rerouting can make things worse, not better).

**The fix is simple and stays entirely within your syllabus** — you don't need a new algorithm, just a loop:

1. Route the first vehicle/batch with A\* on the current weighted graph.
2. Update the weights: the edges that vehicle just used get slightly more expensive (since more cars are now on them).
3. Route the next vehicle/batch — A\* now naturally avoids the road the previous batch just filled, pushing it toward alternate routes.
4. Repeat across all vehicles in your simulation.

This is what actually produces your "before: some roads dark red, some empty green → after: smooth, evenly medium everywhere" picture. It's just A\* run repeatedly with a feedback-updated cost function — no extra algorithm needed, and it's genuinely how real traffic-assignment systems approximate load balancing (it's a simplified version of what's called "iterative" or "incremental" traffic assignment in transportation research, if you want a term to cite).

**For your pitch and demo**, this means:

- Your "before" visualization = static congestion-weighted map from your K-means/KNN predictions (red/green heatmap)
- Your "after" visualization = the same map after running the batch A\* + weight-update loop across simulated vehicles — this is the part that shows the actual "thinning out" effect
- SUMO can render both of these as color-coded network states, which is a strong visual for a presentation

One thing to flag to your faculty when you pitch this: the "spreading" mechanism (batch routing + dynamic weight updates) is a natural extension of A*, not a separate technique — worth stating explicitly so it's clear you understand *why* single-shot A* wouldn't achieve the claim on its own, rather than it looking like an oversight.

---

**[USER]**

I wont have any vehicle data that you are assuming. You dont need to stick to my sylebus. is there any algorithm that will do the spreadout part? like lets say we assign amount of vehicles based on color levels of traffic. From google we get Green, Yellow, Orange, Red, Dark Red, Closed. Based on these info we can assign vehicle amounts and when we spread them out with algorithm we get a much more spread out some green mostly yellow traffic all throughout. am i thinking right?

---

**[ASSISTANT]**

You're thinking about this correctly — and there's a real, named field for exactly this problem, so you don't have to invent the mechanism yourself. It's called the **Traffic Assignment Problem**, and it's solved without needing individual vehicle GPS data. Here's how it maps onto what you have:

**Step 1 — Turn color into an estimated vehicle count**
Google's congestion colors are already a proxy for volume-to-capacity ratio. You can invert that: for each road segment, estimate its **capacity** (max vehicles/hour it can handle) from OSM road-type tags — a 4-lane arterial has higher capacity than a 2-lane residential street. Then assign a rough volume based on color:

- Green → ~30% of capacity
- Yellow → ~60%
- Orange → ~80%
- Red → ~100% (at capacity)
- Dark red → ~120%+ (over capacity, breakdown flow)
- Closed → 0 (blocked)

You don't need real counts, just this ratio — it's exactly what real traffic engineers use when they don't have sensors either.

**Step 2 — A congestion-sensitive cost function (this replaces plain distance)**
Instead of just distance or a flat multiplier, transportation research uses something called the **BPR function** (Bureau of Public Roads formula):

`travel_time = free_flow_time × (1 + 0.15 × (volume/capacity)^4)`

This is the key trick: cost rises _sharply_ as volume approaches and exceeds capacity, not linearly. It's simple algebra, not a new algorithm, and it's exactly why redistributing a few cars off a red road produces a big drop in its cost — small changes near the top of the curve matter a lot.

**Step 3 — The actual "spreading" algorithm**
This is the real answer to your question. Instead of routing everyone at once (which causes the pile-up problem I described earlier) or needing real per-vehicle data, you split your _estimated_ total demand into small increments and assign them one batch at a time, recalculating costs (via the BPR function) after each batch:

- **Incremental assignment**: split total estimated demand into ~10-20% chunks, route each chunk with A\* using current costs, update volumes/costs, repeat. Simple to implement, very explainable in a presentation.
- **Method of Successive Averages (MSA)**: slightly more refined version of the same idea — a standard, well-documented technique, still simple to code.
- **Frank-Wolfe algorithm**: the "proper" academic method used in real transportation planning to reach what's called **Wardrop's User Equilibrium** — the state where no driver can improve their travel time by switching routes alone. This is the formal version of "smooth, evenly spread, mostly-yellow" traffic. More math-heavy, but it's the term you want if you cite the technique in your report ("we approximate user equilibrium traffic assignment").

**Bottom line for your pitch:** yes — replace plain A* with \*\*A* + BPR cost function + incremental/MSA assignment\*\*, driven entirely by color-derived volume estimates, no vehicle trajectory data needed. That combination is what actually produces the red→spread-out-yellow visual you're describing, and it's a legitimate, citable technique (traffic assignment / Wardrop equilibrium) rather than something ad hoc.

Want me to help sketch out how this replaces the K-means/KNN blocks in your pipeline diagram, or keep those as the "predict initial color/volume" stage feeding into this new assignment step?

---

**[USER]**

So by this method we are not using the A\* now?

---

**[ASSISTANT]**

No, you're still using A\* — it's actually still doing the core pathfinding work, just inside a bigger loop rather than running once. Let me untangle this clearly, since I described a lot of pieces at once.

**A\* didn't get replaced. It got wrapped.**

Think of it as two layers:

- **Inner layer (unchanged): A\* search.** Every single "find the best route from point X to point Y given current road costs" call is still A\*. That never goes away — it's still your pathfinding algorithm from the syllabus.
- **Outer layer (new): the assignment loop.** This is what decides _how many times_ to call A*, *for what portion of demand each time*, and *updates the road costs between calls* using the BPR formula. The loop itself isn't a search algorithm — it's a control structure/procedure that repeatedly calls A* and adjusts inputs.

So the full picture is: **K-means/KNN predict initial color/congestion → BPR converts that to a cost function → the incremental/MSA loop calls A\* repeatedly, updating costs after each batch → the result after all batches is the spread-out network.**

A\* is still the workhorse doing every actual route computation. What changed is that instead of calling it once and being done, you call it many times in sequence, each time with slightly updated road costs, so the routes naturally spread out instead of piling onto the same "best" road.

If it helps for your pitch: you can honestly say "our core algorithm is A* search, extended with an iterative traffic assignment procedure so it accounts for the effect of its own routing decisions on congestion" — that's accurate and doesn't undersell A*'s role.

---

**[USER]**

How does A\* decide whats initial and final point? as there are so many roads and intersections? wont dijastra work here better?

---

**[ASSISTANT]**

Good question — let me clear up two separate things here: how A* even gets a start/end point, and why A* vs Dijkstra matters for your specific project.

**A\* doesn't "decide" the start and end points — you give it those.**
A* isn't looking at the whole map and figuring out where trips begin and end. It takes two specific nodes as input: an origin intersection and a destination intersection, both of which already exist as nodes in your OSM-derived graph. The "who's traveling from where to where" part is a decision *you\* make when designing your simulation — for example:

- You pick a handful of realistic origin-destination (OD) pairs across Dhaka (e.g. Mirpur → Motijheel, Uttara → Dhanmondi) representing common commute routes
- Or, if you want more realism without vehicle data, you generate OD pairs _proportional to_ where your color/congestion data shows demand is high — busy areas act as more likely origins and destinations

Either way, this is a modeling choice you make upfront, not something A* figures out on its own. The graph having many intersections doesn't confuse A* — it just means more possible paths for it to search between the two points you gave it.

**Now, A\* vs Dijkstra — you're right to ask, but they're not really competitors here.**
Dijkstra finds the shortest path from one starting node to _every other node_ in the graph. A* finds the shortest path from one starting node to *one specific goal node*, using a heuristic (usually straight-line/geographic distance to the goal) to guide the search toward the destination instead of expanding equally in all directions. In fact, \*\*A* is Dijkstra with a smarter sense of direction\*_ — if you set A_'s heuristic to zero, it becomes exactly Dijkstra.

For your project, where every query is "route from this specific origin to this specific destination," A* is the better fit, not Dijkstra — because Dijkstra would waste time computing distances to every intersection in Dhaka even though you only care about one destination. With a large graph (lots of intersections), that difference in wasted work grows, not shrinks — so more intersections is actually an argument *for* A*, not against it.

**The one case where Dijkstra would actually be the right call:** if at some point you need distances from one hub to _everywhere_ at once (say, computing baseline travel times from a central point to all zones for your K-means clustering step), that's a "one-to-all" problem — that's exactly what Dijkstra is built for, and using A\* there would be overkill/pointless since there's no single goal to aim the heuristic at.

So: keep A\* as your router for the origin→destination queries in your assignment loop (that's the majority of your workload), and mention Dijkstra only if you end up needing one-to-all distances somewhere in your pipeline.

---

**[USER]**

Ok but will i be choosing the start and end point for the simuation?

What i imagined was we have all of these data and trained with knn and clustering. I run the algorithm on a currently live traffic on google map (no need to think about this much lets say we just take a screenshot)

Then with our algorithm and previous data we get a live traffic diversion and we see that traffic thinning out in realtime. We can also add a system where i pick point a to point b where based on previous data routes me where there wont be traffic in the future. am i thinking right?

---

**[ASSISTANT]**

You're thinking along the right lines, but there are actually **two different features** hiding in what you just described, and they answer your start/end-point question differently. Worth separating them clearly, because your faculty will likely ask about this.

**Feature 1 — the "watch the city thin out" demo**
This is the live snapshot → diversion → smoothed-out network visualization. Here, there's no single point A to point B — the whole point is showing _many_ trips being rerouted simultaneously across the city so congestion spreads out. So no, you're not picking a start/end for this one. Instead, **you (the system) synthesize a set of origin-destination pairs** representing plausible citywide demand, since you don't have real vehicle trip data. Practical ways to do this without needing actual vehicle data:

- **Zone-based demand generation**: divide Dhaka into zones (this is literally what several of the research papers I found earlier did — 10 zones, divisional zones), then generate OD pairs _between_ zones, weighted by how congested each zone currently is (heavier congestion = assume more trips originating/ending there). There's actually a Dhaka-specific paper using a **gravity model** for exactly this — estimating trip volume between zones based on their size/activity and distance apart — which would be a legitimate, citable method for generating your synthetic demand.
- **Corridor-based**: simpler version — just pick the major arterial roads/corridors in your OSM network and generate OD pairs between their endpoints, scaled by the color-derived volume estimates from your congestion data.

Either way, this OD-generation step is a real design decision you'll need to document (and it's honestly a good thing to discuss with your faculty, since "how do you simulate demand without vehicle data" is exactly the kind of question they'll want you to have thought through).

**Feature 2 — "route me from A to B avoiding future congestion"**
This one, yes — **you (the user) pick the start and end point**, e.g. by clicking two locations on a map UI. This is a single A\* query using your KNN/logistic-regression-predicted congestion for the relevant future time window as edge weights. Much simpler, and it's the one place in your whole system where a human directly chooses the origin/destination.

**How they fit together:** Feature 2 is basically a live demo/interactive layer on top of the same underlying engine that powers Feature 1 — same graph, same congestion prediction, same A\*. Feature 1 shows the system optimizing at the network scale (many synthetic trips, no individual UI); Feature 2 shows it at the individual scale (one real query, user-picked). Presenting both is actually a strong pitch: "here's what it does for the whole city, and here's what it does for you personally."

---

**[USER]**

Im still a bit lost. I understand the feature 2 its very basic and simple. But im not solving a problem for a specific user. that solves itself by the bigger picture. Im solving it for overall. I Use my algorithm to show a version of the map where the congested traffic has been diverted by redirecting the heavy traffic zones to free roads and then spreading/thinning out the overall traffic where everywhere theres flowing traffic instead of jams. I want to show a visualizer as demo that does that by running thousands and thousands of calculations and simulations and updating the traffic live. When that happens each and every vehicle has a path that is optimal and does not encounter a jam

---

**[ASSISTANT]**

Content rendered and shown to the user. Please do not duplicate the shown content in text because it's already visually represented.[This tool call rendered an interactive widget in the chat. The user can already see the result — do not repeat it in text or with another visualization tool.]This matches what you're picturing — you're right that you're solving it at the network level, not per-user. Let me connect the diagram to your description directly, and flag one thing in your framing that's worth adjusting before you pitch it.

**Nobody picks start/end here — the system generates them.** "Generate synthetic demand" is the system automatically inventing thousands of trip origin-destination pairs on its own, based on where your congestion data shows activity is concentrated (busy zones = more likely trip sources/destinations). You never touch this step; it just runs.

**"Thousands and thousands of calculations" — this is real, not just a nice phrase.** Each of those thousands of synthetic trips gets its own A* call. That's genuinely thousands of A* searches, each one slightly different because the road costs shift after every batch. This is exactly the computational heft that makes for a good demo — it's legitimately doing a lot of work, not just one calculation dressed up to look big.

**The "live updating" visual is the loop itself.** Each pass through Route → Update costs → Render is one frame of your live visualization. Play those frames in sequence and that's your city thinning out from red to yellow to green in real time — literally the render step at the bottom of each loop iteration.

**One thing to soften in your pitch:** you said "each and every vehicle has a path that is optimal and does not encounter a jam" — that's slightly stronger than what this method actually guarantees, and a technically-minded evaluator (or your faculty) may push on it. What this method actually achieves is closer to: no vehicle has an _obviously better alternative route available_ — because if one existed, the algorithm would have found and used it. This is the formal idea I mentioned earlier, **Wardrop's user equilibrium** — a stable state where switching to any other route wouldn't help you, not a promise that literally zero congestion exists anywhere. It's a subtle but important distinction: your demo shows congestion becoming _evenly distributed and manageable_, not _eliminated_. I'd phrase your pitch as "spreading congestion so no single route becomes a severe bottleneck" rather than "no vehicle encounters a jam" — it's more defensible and, honestly, still an impressive result to show.
