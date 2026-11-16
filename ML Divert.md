# ML Divert: plan for the machine learning project

Course: CSE 4889 Machine Learning (term paper 20%, presentation in week 24).
Status: plan only. The work will live in a new directory, separate from TRACK.

---

## 1. What changes from TRACK, and why

TRACK was an AI-course project. It had to use A\*, CSP, KNN and the rest, and the
data was built to serve those algorithms. Three lessons carry over:

1. **The data is good.** 2,542 complete captures of the whole Dhaka metro area in two
   weeks, 100% tile coverage and no gap over 19 minutes. That is rare for Dhaka.
2. **The labels were mixed with guesses.** About 9% of road values came from Google.
   The other 91% were filled in by our own rule. An ML project cannot train or test on
   values we invented.
3. **Rerouting alone cannot clear the evening peak.** The diversion experiment showed
   the network has no spare room near its jams. So "system-wide optimization" in the
   new project must mean using *predictions* to make better decisions (which route,
   which departure time), and it must be measured against what really happened.

The new project starts again from the raw captures, builds a clean labelled dataset
with documented rules, and only then trains models. The planned split of effort:

| Share | Work |
|---|---|
| 70% | the dataset: extraction, cleaning, labels, features, quality checks, splits, documentation |
| 20% | the algorithms: baselines, course models, tuning |
| 10% | results, benchmarks and the paper |

---

## 2. Suggested name

The name should say what the project does with ML. Some options:

| Name | Stands for |
|---|---|
| **DIVERT** | Data-driven Inference and Validation of Evolving Road Traffic |
| **DHAKAFLOW** | plain and descriptive |
| **PULSE** | Predicting Urban Load from Satellite-free Evidence |

My suggestion is **DIVERT**. It keeps the link to your goal of diverting traffic, and
the expanded name describes the ML work honestly.

---

## 3. The goal, written as ML research questions

The overall goal stays: system-wide traffic optimization for Dhaka. In ML terms that
becomes four questions, each with its own data, model and test.

| | Question | ML type | Course topics |
|---|---|---|---|
| RQ1 | Can a clean, valid labelled traffic dataset be built from public map imagery? | data | the 70% |
| RQ2 | How well can each road's traffic level be forecast 15, 30 and 60 minutes ahead? | supervised classification | Naive Bayes, SVM, RF, boosting, MLP, RNN |
| RQ3 | Can roads Google does not colour be estimated better than with a fixed rule? | semi-supervised learning | self-training, co-training |
| RQ4 | Do decisions made on the forecasts save real time? | evaluation of a decision | predict-then-optimize |

Unsupervised work (clustering, PCA, SOM, HMM) supports the four questions by
describing the data and creating features.

**RQ4 is the optimization part, and it is where the project stays honest.** For each
test journey we choose a route or a departure time using the *forecast*, then measure
its travel time on the capture that *actually* came later. We compare that with
choosing on the current traffic. This uses held-out future data, so it cannot be
fooled by a model's own assumptions. Departure-time advice ("leave now or in 30
minutes") is the lever the TRACK results point to: spreading the peak in time, since
there is no road space to spread it in.

---

## 4. The dataset (70%)

### 4.1 Source and what stays fixed

- **Raw source:** the captured traffic tiles in `week1/captures` and `week2/captures`
  (about 80 GB, 2,542 captures). These are treated as read-only. Nothing in the new
  project edits them.
- **Road network:** OpenStreetMap, rebuilt and versioned once, with a fingerprint so
  every label file names the network it belongs to.
- **Extraction is redone from scratch** with a small, documented reader. Each rule
  (sample spacing, which side of the road, how many coloured pixels count) is written
  down with the reason and a check that proves it. TRACK's numbers are used only to
  compare against, never as input.

### 4.2 The unit of data

One row = **one road segment at one capture time.**

| Field | Meaning |
|---|---|
| `road_id` | directed road segment |
| `time` | capture time (UTC and Dhaka time) |
| `label` | traffic level 0 to 3 (green, yellow, red, dark red) **only if Google coloured the road** |
| `coverage` | share of the road's samples that had a colour |
| `labelled` | true only for roads Google coloured at that time |

Rows without a Google colour stay **unlabelled**. They are used only in the
semi-supervised part (RQ3), never as training labels or test labels for RQ2.

Expected size: about 15,600 roads that Google ever colours × 2,542 captures, about
40 million rows, of which about 25.8 million are labelled.

### 4.3 Cleaning rules (each one written down and counted)

1. **Duplicate captures:** the 139 captures of 23 September appear in both weeks. Keep one.
2. **Gaps:** mark any gap between captures over 15 minutes. Features that look back in
   time must not reach across a gap as if it were 8 minutes.
3. **Weak labels:** a road counts as labelled only if enough of its samples are coloured
   (TRACK used 25%). Test 25%, 50% and 75% and report how many labels each keeps.
4. **Junction bleed:** samples near a junction can read the crossing road's colour.
   Trim the road ends and measure how many labels change.
5. **Rare roads:** roads coloured in fewer than a set number of captures are kept for
   description but left out of the main experiments, so a road is not judged on three
   readings.
6. **Class balance:** report the share of each level (about 66% green, 17% yellow, 4%
   red, 13% dark red). This drives the choice of metrics in Section 6.

Every rule produces a count ("removed N rows, because ...") that goes into the paper's
data section.

### 4.4 Quality checks

- **Repeatability:** extract the same capture twice and get identical output.
- **Hand check:** for a sample of about 200 roads, compare the extracted label with
  the tile picture by eye. Report agreement.
- **Consistency:** the two directions of a road, and neighbouring segments of one road,
  should mostly agree. Report the agreement rate.
- **Stability over time:** the daily curve of each week should match (TRACK found a
  correlation of 0.998).

### 4.5 Features

All features use only information available **before** the moment being predicted.

| Group | Features |
|---|---|
| Recent history of the road | level now, 1 to 6 captures ago, change over the last 30 minutes |
| Neighbours | levels on connected roads now and recently (from the network) |
| Time | hour and minute as a circle (sine and cosine), day of week, weekend flag |
| Road | class, length, number of lanes, distance to the city centre |
| Road habits | the road's usual level at this hour, learned from the training period only |
| Outside data | rainfall per hour (Open-Meteo, free) and public holidays |

PCA can shrink the road-habit profiles (24 hourly values per road) to a few numbers,
and clustering can give each road a "type" as one more feature.

### 4.6 Splits: train, validation and test

Traffic minutes apart are almost the same, so a **random split would leak** the answer
into training and make every model look far better than it is. All splits are by time.

| Split | Period | Use |
|---|---|---|
| Train | 16 to 25 September | fit the models |
| Validation | 26 to 28 September | choose settings, early stopping |
| Test | 29 September to 1 October | report once, at the end |
| Fresh test | a new collection period (Section 4.7) | proves the model works on unseen weeks |

- **Cross-validation** (course week 8) is done as *rolling-origin* validation: train on
  days 1 to n, validate on day n+1, move forward. Ordinary k-fold is shown only as the
  example of what *not* to do, with its inflated score reported next to the honest one.
- **For RQ3** the split is also by *place*: whole areas of the city are held out, so a
  road is never tested right next to a road used in training.
- **Rule:** anything learned from data (averages, road habits, scaling) is computed on
  the training period only.

### 4.7 More data for testing

1. **A new Dhaka collection period.** Re-enable the existing collector for two weeks
   (for example in December) and keep it untouched until the end. This is the strongest
   evidence: a model trained on September must still work months later.
2. **A public benchmark: METR-LA or PEMS-BAY.** These are the standard road traffic
   datasets in ML papers (speeds from road sensors in Los Angeles and the Bay Area).
   Running the same forecasting setup on one of them shows the method is not tied to
   Dhaka and gives reviewers familiar numbers.
3. **Rainfall and holidays**, as above, to test whether outside conditions explain errors.

### 4.8 Documentation

- A **datasheet** for the dataset: source, period, extraction rules, cleaning counts,
  label quality, known biases, licence and terms.
- **Versioned files**: each dataset file carries a version and a checksum; every
  experiment records which version it used.
- **Known bias, stated up front:** Google colours mainly main roads and roads with
  enough phone data. Results apply to those roads, not to every street.

---

## 5. The algorithms (20%)

### 5.1 Baselines (every model must beat these)

1. **No change:** the level now is the level later.
2. **Habit:** the road's usual level at that hour and day type.
3. **Same time last week.**
4. **Majority class:** always green (shows why accuracy alone misleads).

### 5.2 Models mapped to the course

| Course topic (week) | Use in the project |
|---|---|
| K-means, hierarchical, fuzzy C-means (2 to 4) | group roads by their daily pattern; group captures into traffic states |
| DBSCAN, HDBSCAN (5 to 6) | find spatial hotspots of roads that jam together |
| Cross-validation (8) | rolling-origin validation, shown against k-fold leakage |
| Self-training, co-training (9) | RQ3: learn from coloured roads, label the uncoloured ones. Co-training uses two views: the road's own time history, and its neighbours |
| Bagging, boosting, stacking (10) | ensemble of the best forecasters |
| Random forest (11) | main RQ2 model, plus feature importance |
| XGBoost, AdaBoost, CatBoost (12) | main RQ2 models (CatBoost handles road class directly) |
| Naive Bayes (contents) | simple probabilistic baseline |
| Perceptron, MLP, backpropagation (13 to 17) | neural forecaster on the same features |
| SVM, GRNN (18 to 19) | compared on a sample (they do not scale to millions of rows) |
| RNN (20) | LSTM or GRU on each road's recent sequence |
| SOM, HMM (21 to 22) | SOM maps daily patterns; HMM treats each road's level as a hidden state that moves between green and red, a natural fit for this data |
| PCA (contents) | compress road profiles into features |

### 5.3 Training practice

- One config file per experiment; fixed random seeds; every run logged with its
  dataset version, settings and scores.
- Settings are tuned on the validation period only. The test period is used once.
- Class imbalance is handled with class weights, and checked against the macro-F1 score.

---

## 6. Results and benchmarks (10%)

**Metrics:**

| Metric | Why |
|---|---|
| Macro-F1 | treats all four levels equally despite 66% green |
| Recall on dark red | how many real jams the model catches |
| Too-green rate | forecasts lighter than reality, the error that sends drivers into jams |
| Accuracy | reported, but never alone |
| For RQ4: minutes saved per trip | the real-world value of the forecasts |

**What can definitely be shown with the current data plus one fresh test period:**

1. A documented, checked dataset with its cleaning counts and label quality (RQ1).
2. Baselines against course models at 15, 30 and 60 minutes, on a clean time split,
   with rolling validation and confidence intervals (RQ2).
3. How much the honest split lowers the score compared with a random split. This is a
   useful lesson in itself.
4. Semi-supervised estimates for uncoloured roads against the fixed rule (RQ3).
5. Minutes saved by forecast-based route and departure choices, measured on real later
   captures (RQ4).
6. Whether the model still works on the fresh collection period, and on METR-LA or
   PEMS-BAY.

**Expected honest findings to plan for:**

- At 15 minutes, "no change" will be very hard to beat, because traffic changes little
  in one capture. ML should pull ahead at 30 to 60 minutes and around the start of the
  evening peak. Either outcome is reportable.
- Departure-time advice is likely to save more than route advice, matching TRACK's
  finding that the problem is capacity, not routing.

---

## 7. Risks

| Risk | What we do |
|---|---|
| Only two weeks of data (each weekday seen twice) | add the fresh collection period; report it as a limit |
| Google's colour thresholds are unknown | treat labels as Google's levels, not speeds; say so |
| Labels only on coloured roads | results claimed for those roads only; RQ3 handles the rest carefully |
| Leakage through time or space | time and area splits; statistics from training data only |
| Data size (about 40 million rows) | sample for slow models (SVM, GRNN); full data for boosting |
| Google's terms forbid storing tiles | keep tiles private; publish only derived, aggregated data; state it in the paper |

---

## 8. New directory layout

```
divert/
  data/
    raw/            links to the existing captures (read-only, never edited)
    interim/        extracted labels per capture
    processed/      the final dataset files, versioned
    external/       rainfall, holidays, benchmark datasets
  src/
    extract/        tiles to labels
    clean/          the cleaning rules
    features/       feature building
    split/          train, validation and test periods
    models/         baselines and course models
    evaluate/       metrics, RQ4 route and departure tests
  experiments/      one config per experiment, with logged results
  notebooks/        exploration and figures only, no pipeline code
  reports/
    datasheet.md
    paper/
  tests/            checks for extraction, leakage and splits
```

---

## 9. Order of work

| Phase | Work | Share |
|---|---|---|
| 1 | Set up the directory; rebuild the road network; write the extraction with tests | 15% |
| 2 | Extract all captures; apply cleaning rules; quality checks; datasheet | 25% |
| 3 | Features, splits and leak checks; start the fresh collection period | 20% |
| 4 | Exploration: class balance, daily patterns, clustering, PCA, hotspots | 10% |
| 5 | Baselines, then course models; tuning on validation | 20% |
| 6 | Final test, fresh period, benchmark, RQ4; write the paper and slides | 10% |

Phases 1 to 4 are the 70% on data. Nothing is trained until the dataset passes its
checks.

---

## 10. Decisions needed from you

1. **Project name:** DIVERT, or another.
2. **Fresh collection period:** when, and for how long (two weeks suggested).
3. **Benchmark:** METR-LA, PEMS-BAY, or none.
4. **Which research questions** go in the paper: all four, or RQ1 + RQ2 + RQ4 with RQ3
   as a smaller section.
