-- Staging layer: one clean, typed table per business entity.
-- It joins across the five source schemas, so this is the first place where
-- "sales", "purchasing", "production", "inventory" and "finance" meet.
-- Statements are separated by a blank line + semicolon and run in order.

DROP SCHEMA IF EXISTS stg CASCADE;

CREATE SCHEMA stg;

-- Products, with category names, flags and total stock.
CREATE TABLE stg.product AS
SELECT p.product_id,
       p.product_number                       AS sku,
       p.name,
       COALESCE(c.name, 'Component')          AS category,
       COALESCE(s.name, 'Component')          AS subcategory,
       p.list_price::float                    AS list_price,
       p.standard_cost::float                 AS standard_cost,
       p.finished_goods_flag = 1              AS is_finished_good,
       p.make_flag = 1                        AS is_manufactured,
       EXISTS (SELECT 1 FROM purchasing.product_vendor pv
               WHERE pv.product_id = p.product_id) AS is_purchased,
       COALESCE(i.on_hand, 0)::int            AS on_hand
FROM production.product p
LEFT JOIN production.product_subcategory s USING (product_subcategory_id)
LEFT JOIN production.product_category c USING (product_category_id)
LEFT JOIN (SELECT product_id, SUM(quantity) AS on_hand
           FROM inventory.product_inventory GROUP BY 1) i USING (product_id);

-- Bill of materials: current links only (no end date, real parent).
CREATE TABLE stg.bom AS
SELECT product_assembly_id::int AS assembly_id,
       component_id::int        AS component_id,
       SUM(per_assembly_qty)::float AS qty
FROM production.bill_of_materials
WHERE end_date IS NULL AND product_assembly_id IS NOT NULL
GROUP BY 1, 2;

CREATE TABLE stg.vendor AS
SELECT vendor_id, name, credit_rating::int AS credit_rating,
       preferred = 1 AS is_preferred, active = 1 AS is_active
FROM purchasing.vendor;

-- Which vendor can supply which product, with lead time and price.
CREATE TABLE stg.vendor_product AS
SELECT vendor_id, product_id,
       average_lead_time::int AS lead_days,
       standard_price::float  AS price
FROM purchasing.product_vendor;

-- Purchase order lines. A line is open while nothing has been received.
CREATE TABLE stg.purchase_order_line AS
SELECT d.purchase_order_detail_id AS line_id,
       d.purchase_order_id,
       d.product_id,
       d.order_qty::float      AS qty,
       d.received_qty::float   AS received_qty,
       d.unit_price::float     AS unit_price,
       d.due_date::date        AS due_date,
       d.received_date::date   AS received_date,
       (h.status = 'Open' AND d.received_date IS NULL) AS is_open,
       CASE
         WHEN d.received_date IS NOT NULL THEN GREATEST(d.received_date::date - d.due_date::date, 0)
         WHEN h.status = 'Open' THEN GREATEST(r.as_of - d.due_date::date, 0)
         ELSE 0
       END AS days_late
FROM purchasing.purchase_order_detail d
JOIN purchasing.purchase_order_header h USING (purchase_order_id)
CROSS JOIN meta.run_info r;

-- Purchase order headers, with lateness rolled up from the lines.
CREATE TABLE stg.purchase_order AS
SELECT h.purchase_order_id,
       h.vendor_id,
       h.status,
       h.ship_method_id::int      AS ship_method_id,
       h.order_date::date         AS order_date,
       MIN(l.due_date)            AS due_date,
       MAX(l.received_date)       AS received_date,
       COUNT(*)::int              AS line_count,
       SUM(l.qty * l.unit_price)  AS total_value,
       MAX(l.days_late)::int      AS days_late,
       (h.status = 'Open' AND MAX(l.days_late) > 0) AS is_late
FROM purchasing.purchase_order_header h
JOIN stg.purchase_order_line l USING (purchase_order_id)
GROUP BY h.purchase_order_id, h.vendor_id, h.status, h.ship_method_id, h.order_date;

CREATE TABLE stg.sales_order AS
SELECT sales_order_id,
       status,
       order_date::date         AS order_date,
       due_date::date           AS due_date,
       ship_date::date          AS ship_date,
       online_order_flag = 1    AS is_online,
       territory_id::int        AS territory_id,
       sales_person_id::int     AS sales_person_id,
       sub_total::float         AS total_value
FROM sales.sales_order_header;

CREATE TABLE stg.sales_order_line AS
SELECT sales_order_detail_id AS line_id,
       sales_order_id,
       product_id,
       order_qty::float  AS qty,
       line_total::float AS value
FROM sales.sales_order_detail;

CREATE TABLE stg.territory AS
SELECT territory_id, name, region_group FROM sales.sales_territory;

-- Sales people. Three managers have no territory in the sample, so their home
-- territory is the one where they sold the most.
CREATE TABLE stg.sales_person AS
SELECT sp.sales_person_id,
       COALESCE(sp.territory_id::int, top.territory_id) AS home_territory_id
FROM finance.sales_person sp
LEFT JOIN (
    SELECT DISTINCT ON (sales_person_id) sales_person_id, territory_id
    FROM stg.sales_order
    WHERE sales_person_id IS NOT NULL
    GROUP BY sales_person_id, territory_id
    ORDER BY sales_person_id, SUM(total_value) DESC
) top USING (sales_person_id);

-- Financial targets: one quota per sales person per quota period (about a quarter).
-- A period runs from its quota date to the day before the next quota date.
CREATE TABLE stg.target AS
WITH periods AS (
    SELECT quota_date::date AS period_start,
           COALESCE(LEAD(quota_date::date) OVER (ORDER BY quota_date),
                    quota_date::date + 91) - 1 AS period_end
    FROM (SELECT DISTINCT quota_date FROM finance.sales_person_quota_history) q
)
SELECT q.sales_person_id || '-' || p.period_start AS target_id,
       q.sales_person_id,
       p.period_start,
       p.period_end,
       q.sales_quota::float AS amount,
       (r.as_of BETWEEN p.period_start AND p.period_end) AS is_current
FROM finance.sales_person_quota_history q
JOIN periods p ON p.period_start = q.quota_date::date
CROSS JOIN meta.run_info r;

CREATE TABLE stg.work_order AS
SELECT w.work_order_id,
       w.product_id,
       w.order_qty::int     AS order_qty,
       w.scrapped_qty::int  AS scrapped_qty,
       w.start_date::date   AS start_date,
       w.end_date::date     AS end_date,
       w.due_date::date     AS due_date,
       w.scrap_reason_id::int AS scrap_reason_id,
       CASE WHEN w.end_date IS NULL THEN 'In progress' ELSE 'Done' END AS status
FROM production.work_order w;

-- Which work centres (locations) each work order went through.
CREATE TABLE stg.work_order_location AS
SELECT DISTINCT work_order_id, location_id::int AS location_id
FROM production.work_order_routing;

CREATE TABLE stg.scrap_reason AS
SELECT scrap_reason_id, name FROM production.scrap_reason;

CREATE TABLE stg.location AS
SELECT location_id, name FROM inventory.location;

CREATE TABLE stg.inventory AS
SELECT product_id, location_id::int AS location_id, quantity::int AS on_hand
FROM inventory.product_inventory
WHERE quantity > 0;

CREATE INDEX ON stg.sales_order_line (sales_order_id);

CREATE INDEX ON stg.sales_order_line (product_id);

CREATE INDEX ON stg.purchase_order_line (purchase_order_id);
