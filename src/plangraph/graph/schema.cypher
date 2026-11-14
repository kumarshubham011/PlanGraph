// Graph schema: one uniqueness constraint per node label.
// Every node has a `uid` such as "Product:782". A constraint also creates an
// index, so MERGE and lookups by uid are fast.
CREATE CONSTRAINT product_uid IF NOT EXISTS FOR (n:Product) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT vendor_uid IF NOT EXISTS FOR (n:Vendor) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT location_uid IF NOT EXISTS FOR (n:Location) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT po_uid IF NOT EXISTS FOR (n:PurchaseOrder) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT so_uid IF NOT EXISTS FOR (n:SalesOrder) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT territory_uid IF NOT EXISTS FOR (n:Territory) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT salesperson_uid IF NOT EXISTS FOR (n:SalesPerson) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT target_uid IF NOT EXISTS FOR (n:Target) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT workorder_uid IF NOT EXISTS FOR (n:WorkOrder) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT scrapreason_uid IF NOT EXISTS FOR (n:ScrapReason) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT forecast_uid IF NOT EXISTS FOR (n:Forecast) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT alert_uid IF NOT EXISTS FOR (n:Alert) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT decision_uid IF NOT EXISTS FOR (n:Decision) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT docchunk_uid IF NOT EXISTS FOR (n:DocChunk) REQUIRE n.uid IS UNIQUE;

// Indexes for the filters the query templates use most.
CREATE INDEX so_status IF NOT EXISTS FOR (n:SalesOrder) ON (n.status);
CREATE INDEX po_status IF NOT EXISTS FOR (n:PurchaseOrder) ON (n.status);
CREATE INDEX product_name IF NOT EXISTS FOR (n:Product) ON (n.name);
CREATE INDEX territory_name IF NOT EXISTS FOR (n:Territory) ON (n.name);
