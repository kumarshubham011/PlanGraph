# Models

Three pieces of "intelligence" sit between the graph and the alerts. Two are machine learning models, one is arithmetic. All three read history from Postgres and write their results back to the graph.

| Model | Question it answers | Method | File |
|---|---|---|---|
| Demand forecast | How many units of each product will sell next month? | LightGBM, one model for all products | `src/plangraph/models/forecast.py` |
| Late-PO risk | How likely is this open purchase order to arrive late? | Logistic regression | `src/plangraph/models/late_po.py` |
| Inventory projection | When will this part run out? | Week-by-week sum | `src/plangraph/models/inventory.py` |

## 1. Demand forecast

### Why it is needed
Planning looks forward. To know whether a part will run out, you need to know how many products will be built, which depends on how many will sell. The forecast is also the yardstick for "demand is running above plan".

### The data
One row per product per **planning month**, with units sold. It is built from sales order lines, and a month with no sales is stored as zero. That matters: leaving such months out would teach the model that every product sells every month.

Planning months follow the sample's own calendar months, because reseller orders arrive in one batch at the end of each sample month. After the date shift, each planning month runs from about the 15th to the 14th. History covers 35 months and 266 products.

### Features
Each row describes the product's recent past. Every feature looks only at earlier months.

| Feature | Meaning |
|---|---|
| `lag_1`, `lag_2`, `lag_3`, `lag_6`, `lag_12` | Units sold 1, 2, 3, 6 and 12 months earlier |
| `mean_3`, `mean_6` | Average of the last 3 and 6 months |
| `month` | Month of the year, for seasonality |
| `age` | Months since the product first sold |
| `list_price` | Price, which separates bikes from accessories |

```python
for n in LAGS:
    data[f"lag_{n}"] = by_product.shift(n)   # shift(n) = the value n months earlier
```

### Why one model for all products
This is called a global model. A single product has at most 35 data points, too few to learn from. Trained together, 266 products give about 9,000 rows, and the model learns patterns that products share, such as "after three rising months, the next one tends to be high".

### A range, not just a number
Three models are trained on the same features: one for the expected value and two **quantile** models for the 10th and 90th percentile (P10 and P90). Demand above P90 is treated as a signal that something has changed, and triggers the demand surge alert.

### How it is tested: backtest
A random train/test split would let the model see the future, so the test walks forward in time:

1. Train on everything before month M.
2. Forecast month M.
3. Compare with what really sold.
4. Repeat for each of the last 6 months.

### The metric: WAPE
Weighted absolute percentage error = total absolute error ÷ total actual demand.

*Example:* two products sold 100 units each. The forecasts were 90 and 120. Errors: 10 and 20. WAPE = 30 ÷ 200 = 15%.

### Results (6-month backtest, 1,596 product-months)

| Method | WAPE |
|---|---|
| **LightGBM model** | **18.3%** |
| Baseline: same as last month | 25.8% |
| Baseline: same month last year | 57.3% |

- The model cuts the error of the best simple baseline by about 29%.
- 76% of actual values fall between P10 and P90. A perfectly calibrated range would hold 80%, so the range is slightly too narrow.
- The last backtest month contains the injected demand surge, which no model could foresee. It is left in on purpose.

### Forecasting ahead
The model predicts one month ahead. For three months, it runs three times, each time feeding its own prediction back in as "last month". This is a recursive forecast: simple, and errors grow with each step. The result is 549 `Forecast` nodes (183 products still selling × 3 months).

## 2. Late-PO risk

### Why it is needed
A PO that is already overdue is easy to spot. The valuable warning comes *before* the due date: "this order is not late yet, and it probably will be".

### Read this first: the labels are synthetic
AdventureWorks has no receipt dates, so "late" is simulated (see [DATA.md](DATA.md), section 4.3). In the simulation, lateness depends on the vendor only. The model's score therefore shows that the method and the pipeline work. It says nothing about real vendors.

### Features
One row per purchase order.

| Feature | Meaning |
|---|---|
| `vendor_late_rate` | Share of this vendor's past POs that were late, smoothed |
| `vendor_recent_late_rate` | The same over the last 60 days only |
| `credit_rating` | Vendor credit rating, 1 best to 5 worst |
| `lead_days` | The vendor's average quoted lead time |

**Smoothing.** A vendor with one past order that happened to be late would show a 100% late rate. Smoothing pulls vendors with little history towards a typical 20%:

```python
smoothed = (late_count + 0.2 * 5) / (order_count + 5)
```

One late order out of one becomes (1 + 1) ÷ (1 + 5) = 33%, not 100%. With 50 past orders the adjustment barely matters.

**No leakage.** A PO's outcome only becomes known later: on the day it arrives, or the day after its due date passes. The features for a PO use only outcomes known before the day it is scored. A unit test checks exactly this (`test_vendor_history_ignores_outcomes_not_known_yet`).

### How it is tested
POs are sorted by order date. The oldest 75% (1,944) train the model and the newest 25% (648) test it, so the model is always tested on a later period.

### The metric: AUC
Pick one late PO and one on-time PO at random. AUC is the chance that the model gives the late one the higher risk score. 0.5 is guessing, 1.0 is perfect.

### Results

| Method | AUC |
|---|---|
| **Logistic regression, 4 features** | **0.640** |
| Baseline: smoothed vendor late rate alone | 0.635 |

The model barely beats its baseline, and that is the expected result: the simulation makes lateness depend on the vendor alone, so the vendor's late rate already holds almost all the signal. A gradient-boosted tree model was tried first and scored *lower* (about 0.60), because it fitted noise in a small dataset. The simpler model was kept.

On real data, order size, season, part type and shipping method would likely add signal, and this comparison should be repeated.

### What the score is used for
Open POs that are not yet due get a `late_risk` between 0 and 1, stored on the `PurchaseOrder` node. A risk of 0.40 or more, twice the typical late rate, raises a "PO at risk" alert. In the current run that flags 6 POs. Two belong to Custom Frames, the vendor in the injected scenario, because its recent late rate jumped to 88%.

## 3. Inventory projection

### Why it is needed
It turns "stock is low" into a date: "Lock Ring runs out in week 2".

### The method
No machine learning. For each purchased item, week by week for 6 weeks:

```
stock at end of week = stock at start + deliveries that week - demand that week
```

```python
def project_stock(on_hand, weekly_demand, inbound, weeks=6):
    projection, stock = [], on_hand
    for week in range(1, weeks + 1):
        stock = stock + inbound.get(week, 0.0) - weekly_demand
        projection.append(stock)
    return projection
```

*Example:* 100 units on hand, demand 40 per week, 50 units arriving in week 3.
Week 1: 60. Week 2: 20. Week 3: 20 + 50 − 40 = 30. Week 4: −10. The item runs out in week 4.

### Where the inputs come from
- **Demand for a part** is not forecast directly. The finished goods forecast is pushed down the bill of materials: a forecast of 100 bikes becomes 200 wheels and 7,200 spokes. This is the "explosion" step of classic material requirements planning (MRP). `src/plangraph/bom.py` does it in about 20 lines.
- **Deliveries** are open PO lines, placed in the week of their due date. An overdue line is assumed to arrive one week from today.
- **Stock** is today's on-hand quantity.

### Results
Of 123 purchased items with demand, 7 are projected to run out within 6 weeks: the 6 metal sheets from the slipping vendor, and Lock Ring from the stock shortfall scenario. No other item is flagged.

### Simplifications to know about
- Stock of half-built subassemblies is ignored, so demand for deep components is somewhat overstated.
- Demand is spread evenly over the weeks of a month, while reseller orders really arrive in one batch.
- An overdue PO is assumed to arrive in one week. The late-PO model could supply a better estimate.

## 4. Writing results back to the graph

`src/plangraph/models/writeback.py` stores every output where the rest of the system can reach it:

| Output | Stored as |
|---|---|
| Forecast | `Forecast` nodes linked by `(Product)-[:HAS_FORECAST]->` |
| Late risk | `late_risk` property on `PurchaseOrder` |
| Stock projection | `weekly_demand`, `weeks_of_cover`, `stockout_week` on `Product` |

Metrics from each run are saved in the Postgres table `ml.metrics` and served by the API at `/metrics`.
