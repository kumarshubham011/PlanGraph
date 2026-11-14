"""Which AdventureWorks files we load, and where each one lands in Postgres.

The raw CSV files have no header row, so the full column list (in file order)
is written here. `keep` maps the columns we actually use to snake_case names.
Each table goes into the schema of the "system" it simulates.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceTable:
    file: str  # CSV file name without .csv
    schema: str  # Postgres schema = the simulated source system
    table: str  # Postgres table name
    columns: list[str]  # every column in the CSV, in order
    keep: dict[str, str]  # CSV column -> our column name
    dates: tuple[str, ...] = ()  # our column names that hold dates


TABLES = [
    # ---- Sales / order management system ----
    SourceTable(
        "SalesOrderHeader", "sales", "sales_order_header",
        ["SalesOrderID", "RevisionNumber", "OrderDate", "DueDate", "ShipDate", "Status",
         "OnlineOrderFlag", "SalesOrderNumber", "PurchaseOrderNumber", "AccountNumber",
         "CustomerID", "SalesPersonID", "TerritoryID", "BillToAddressID", "ShipToAddressID",
         "ShipMethodID", "CreditCardID", "CreditCardApprovalCode", "CurrencyRateID", "SubTotal",
         "TaxAmt", "Freight", "TotalDue", "Comment", "rowguid", "ModifiedDate"],
        {"SalesOrderID": "sales_order_id", "OrderDate": "order_date", "DueDate": "due_date",
         "ShipDate": "ship_date", "Status": "status", "OnlineOrderFlag": "online_order_flag",
         "CustomerID": "customer_id", "SalesPersonID": "sales_person_id",
         "TerritoryID": "territory_id", "SubTotal": "sub_total"},
        ("order_date", "due_date", "ship_date"),
    ),
    SourceTable(
        "SalesOrderDetail", "sales", "sales_order_detail",
        ["SalesOrderID", "SalesOrderDetailID", "CarrierTrackingNumber", "OrderQty", "ProductID",
         "SpecialOfferID", "UnitPrice", "UnitPriceDiscount", "LineTotal", "rowguid",
         "ModifiedDate"],
        {"SalesOrderID": "sales_order_id", "SalesOrderDetailID": "sales_order_detail_id",
         "OrderQty": "order_qty", "ProductID": "product_id", "UnitPrice": "unit_price",
         "LineTotal": "line_total"},
    ),
    SourceTable(
        "SalesTerritory", "sales", "sales_territory",
        ["TerritoryID", "Name", "CountryRegionCode", "Group", "SalesYTD", "SalesLastYear",
         "CostYTD", "CostLastYear", "rowguid", "ModifiedDate"],
        {"TerritoryID": "territory_id", "Name": "name", "CountryRegionCode": "country_code",
         "Group": "region_group"},
    ),
    # ---- Purchasing system ----
    SourceTable(
        "PurchaseOrderHeader", "purchasing", "purchase_order_header",
        ["PurchaseOrderID", "RevisionNumber", "Status", "EmployeeID", "VendorID", "ShipMethodID",
         "OrderDate", "ShipDate", "SubTotal", "TaxAmt", "Freight", "TotalDue", "ModifiedDate"],
        {"PurchaseOrderID": "purchase_order_id", "Status": "status", "VendorID": "vendor_id",
         "ShipMethodID": "ship_method_id", "OrderDate": "order_date", "SubTotal": "sub_total"},
        ("order_date",),
    ),
    SourceTable(
        "PurchaseOrderDetail", "purchasing", "purchase_order_detail",
        ["PurchaseOrderID", "PurchaseOrderDetailID", "DueDate", "OrderQty", "ProductID",
         "UnitPrice", "LineTotal", "ReceivedQty", "RejectedQty", "StockedQty", "ModifiedDate"],
        {"PurchaseOrderID": "purchase_order_id",
         "PurchaseOrderDetailID": "purchase_order_detail_id", "DueDate": "due_date",
         "OrderQty": "order_qty", "ProductID": "product_id", "UnitPrice": "unit_price",
         "ReceivedQty": "received_qty", "RejectedQty": "rejected_qty"},
        ("due_date",),
    ),
    SourceTable(
        "Vendor", "purchasing", "vendor",
        ["BusinessEntityID", "AccountNumber", "Name", "CreditRating", "PreferredVendorStatus",
         "ActiveFlag", "PurchasingWebServiceURL", "ModifiedDate"],
        {"BusinessEntityID": "vendor_id", "Name": "name", "CreditRating": "credit_rating",
         "PreferredVendorStatus": "preferred", "ActiveFlag": "active"},
    ),
    SourceTable(
        "ProductVendor", "purchasing", "product_vendor",
        ["ProductID", "BusinessEntityID", "AverageLeadTime", "StandardPrice", "LastReceiptCost",
         "LastReceiptDate", "MinOrderQty", "MaxOrderQty", "OnOrderQty", "UnitMeasureCode",
         "ModifiedDate"],
        {"ProductID": "product_id", "BusinessEntityID": "vendor_id",
         "AverageLeadTime": "average_lead_time", "StandardPrice": "standard_price",
         "MinOrderQty": "min_order_qty", "MaxOrderQty": "max_order_qty"},
    ),
    # ---- Production / manufacturing execution system ----
    SourceTable(
        "Product", "production", "product",
        ["ProductID", "Name", "ProductNumber", "MakeFlag", "FinishedGoodsFlag", "Color",
         "SafetyStockLevel", "ReorderPoint", "StandardCost", "ListPrice", "Size",
         "SizeUnitMeasureCode", "WeightUnitMeasureCode", "Weight", "DaysToManufacture",
         "ProductLine", "Class", "Style", "ProductSubcategoryID", "ProductModelID",
         "SellStartDate", "SellEndDate", "DiscontinuedDate", "rowguid", "ModifiedDate"],
        {"ProductID": "product_id", "Name": "name", "ProductNumber": "product_number",
         "MakeFlag": "make_flag", "FinishedGoodsFlag": "finished_goods_flag",
         "SafetyStockLevel": "safety_stock_level", "ReorderPoint": "reorder_point",
         "StandardCost": "standard_cost", "ListPrice": "list_price",
         "DaysToManufacture": "days_to_manufacture",
         "ProductSubcategoryID": "product_subcategory_id"},
    ),
    SourceTable(
        "ProductSubcategory", "production", "product_subcategory",
        ["ProductSubcategoryID", "ProductCategoryID", "Name", "rowguid", "ModifiedDate"],
        {"ProductSubcategoryID": "product_subcategory_id",
         "ProductCategoryID": "product_category_id", "Name": "name"},
    ),
    SourceTable(
        "ProductCategory", "production", "product_category",
        ["ProductCategoryID", "Name", "rowguid", "ModifiedDate"],
        {"ProductCategoryID": "product_category_id", "Name": "name"},
    ),
    SourceTable(
        "BillOfMaterials", "production", "bill_of_materials",
        ["BillOfMaterialsID", "ProductAssemblyID", "ComponentID", "StartDate", "EndDate",
         "UnitMeasureCode", "BOMLevel", "PerAssemblyQty", "ModifiedDate"],
        {"BillOfMaterialsID": "bill_of_materials_id", "ProductAssemblyID": "product_assembly_id",
         "ComponentID": "component_id", "StartDate": "start_date", "EndDate": "end_date",
         "BOMLevel": "bom_level", "PerAssemblyQty": "per_assembly_qty"},
        ("start_date", "end_date"),
    ),
    SourceTable(
        "WorkOrder", "production", "work_order",
        ["WorkOrderID", "ProductID", "OrderQty", "StockedQty", "ScrappedQty", "StartDate",
         "EndDate", "DueDate", "ScrapReasonID", "ModifiedDate"],
        {"WorkOrderID": "work_order_id", "ProductID": "product_id", "OrderQty": "order_qty",
         "ScrappedQty": "scrapped_qty", "StartDate": "start_date", "EndDate": "end_date",
         "DueDate": "due_date", "ScrapReasonID": "scrap_reason_id"},
        ("start_date", "end_date", "due_date"),
    ),
    SourceTable(
        "WorkOrderRouting", "production", "work_order_routing",
        ["WorkOrderID", "ProductID", "OperationSequence", "LocationID", "ScheduledStartDate",
         "ScheduledEndDate", "ActualStartDate", "ActualEndDate", "ActualResourceHrs",
         "PlannedCost", "ActualCost", "ModifiedDate"],
        {"WorkOrderID": "work_order_id", "OperationSequence": "operation_sequence",
         "LocationID": "location_id"},
    ),
    SourceTable(
        "ScrapReason", "production", "scrap_reason",
        ["ScrapReasonID", "Name", "ModifiedDate"],
        {"ScrapReasonID": "scrap_reason_id", "Name": "name"},
    ),
    # ---- Inventory / warehouse management system ----
    SourceTable(
        "ProductInventory", "inventory", "product_inventory",
        ["ProductID", "LocationID", "Shelf", "Bin", "Quantity", "rowguid", "ModifiedDate"],
        {"ProductID": "product_id", "LocationID": "location_id", "Quantity": "quantity"},
    ),
    SourceTable(
        "Location", "inventory", "location",
        ["LocationID", "Name", "CostRate", "Availability", "ModifiedDate"],
        {"LocationID": "location_id", "Name": "name"},
    ),
    # ---- Finance / FP&A system ----
    SourceTable(
        "SalesPerson", "finance", "sales_person",
        ["BusinessEntityID", "TerritoryID", "SalesQuota", "Bonus", "CommissionPct", "SalesYTD",
         "SalesLastYear", "rowguid", "ModifiedDate"],
        {"BusinessEntityID": "sales_person_id", "TerritoryID": "territory_id"},
    ),
    SourceTable(
        "SalesPersonQuotaHistory", "finance", "sales_person_quota_history",
        ["BusinessEntityID", "QuotaDate", "SalesQuota", "rowguid", "ModifiedDate"],
        {"BusinessEntityID": "sales_person_id", "QuotaDate": "quota_date",
         "SalesQuota": "sales_quota"},
        ("quota_date",),
    ),
]

SCHEMAS = sorted({t.schema for t in TABLES})
