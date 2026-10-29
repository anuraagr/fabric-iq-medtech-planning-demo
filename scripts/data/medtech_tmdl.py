"""TMDL builder for the synthetic MedTech semantic model.

Dimensions and facts are embedded in the model as Power Query M, so the base model needs no external data source and no credentials:
small dimensions use readable `#table` literals, large fact tables use the compressed `Enter Data` encoding
(raw DEFLATE + Base64). The written-back plan tables (`WritebackSource`) are the exception: they read the Fabric SQL database, which needs the
OAuth2 credential bound by `deploy.py` / `refresh_model.py`. Output is a Power BI project style `definition/` folder suitable for the Fabric
semantic-model definition API (format TMDL).
"""

from __future__ import annotations

import base64
import json
import re
import zlib
from dataclasses import dataclass, field

from .medtech_generator import MedTechDataset

TAB = "\t"
_PLAIN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

PBISM = json.dumps(
    {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2",
        "settings": {},
    },
    indent=2,
)


def q(name: str) -> str:
    """Quote a TMDL object name when it is not a plain identifier."""
    return name if _PLAIN_NAME.match(name) else "'" + name.replace("'", "''") + "'"


def m_text(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def pack_rows(rows: list[list]) -> str:
    """Raw DEFLATE + Base64 of compact JSON, the encoding Power BI 'Enter Data' uses."""
    raw = json.dumps(rows, separators=(",", ":")).encode("utf-8")
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    return base64.b64encode(compressor.compress(raw) + compressor.flush()).decode("ascii")


def _lines(*items: str | None) -> str:
    return "\n".join(item for item in items if item is not None) + "\n"


@dataclass
class Column:
    name: str
    data_type: str
    hidden: bool = False
    key: bool = False
    summarize: str = "none"
    format_string: str | None = None
    sort_by: str | None = None
    description: str | None = None
    source: str | None = None

    def render(self) -> str:
        out = []
        if self.description:
            out.append(f"{TAB}/// {self.description}")
        out.append(f"{TAB}column {q(self.name)}")
        out.append(f"{TAB * 2}dataType: {self.data_type}")
        if self.hidden:
            out.append(f"{TAB * 2}isHidden")
        if self.key:
            out.append(f"{TAB * 2}isKey")
        if self.format_string:
            out.append(f"{TAB * 2}formatString: {self.format_string}")
        if self.sort_by:
            out.append(f"{TAB * 2}sortByColumn: {q(self.sort_by)}")
        out.append(f"{TAB * 2}summarizeBy: {self.summarize}")
        out.append(f"{TAB * 2}sourceColumn: {self.source or self.name}")
        return "\n".join(out)


@dataclass
class Measure:
    name: str
    expression: str
    format_string: str
    description: str | None = None

    def render(self) -> str:
        out = []
        if self.description:
            out.append(f"{TAB}/// {self.description}")
        if "\n" in self.expression:
            body = "\n".join(f"{TAB * 3}{line}" for line in self.expression.strip("\n").splitlines())
            out.append(f"{TAB}measure {q(self.name)} = ```\n{body}\n{TAB * 3}```")
        else:
            out.append(f"{TAB}measure {q(self.name)} = {self.expression}")
        out.append(f"{TAB * 2}formatString: {self.format_string}")
        return "\n".join(out)


@dataclass
class Hierarchy:
    name: str
    levels: list[str]

    def render(self) -> str:
        out = [f"{TAB}hierarchy {q(self.name)}", ""]
        for level in self.levels:
            out += [f"{TAB * 2}level {q(level)}", f"{TAB * 3}column: {q(level)}", ""]
        return "\n".join(out).rstrip("\n")


@dataclass
class Partition:
    name: str
    m_code: str

    def render(self) -> str:
        body = "\n".join(f"{TAB * 4}{line}" for line in self.m_code.strip("\n").splitlines())
        return f"{TAB}partition {q(self.name)} = m\n{TAB * 2}mode: import\n{TAB * 2}source =\n{body}"


@dataclass
class Table:
    name: str
    description: str | None = None
    measures: list[Measure] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    hierarchies: list[Hierarchy] = field(default_factory=list)
    partitions: list[Partition] = field(default_factory=list)
    data_category: str | None = None  # "Time" marks the table as the model's date table

    def render(self) -> str:
        blocks = []
        if self.description:
            blocks.append(f"/// {self.description}")
        blocks.append(f"table {q(self.name)}" + (f"\n{TAB}dataCategory: {self.data_category}" if self.data_category else ""))
        sections = [m.render() for m in self.measures] + [c.render() for c in self.columns] + [h.render() for h in self.hierarchies] + [p.render() for p in self.partitions]
        return "\n".join(blocks) + "\n\n" + "\n\n".join(sections) + "\n"


def _typed_table_m(column_types: list[tuple[str, str]], source_expression: str, extra: str = "") -> str:
    types = ", ".join(f'{{{m_text(name)}, {mtype}}}' for name, mtype in column_types)
    steps = [f"Source = {source_expression}", f"Typed = Table.TransformColumnTypes(Source, {{{types}}})"]
    last = "Typed"
    if extra:
        steps.append(extra)
        last = extra.split(" =", 1)[0].strip()
    return "let\n" + ",\n".join(f"    {s}" for s in steps) + f"\nin\n    {last}"


def _literal_table_m(headers: list[str], rows: list[tuple], types: list[str], extra: str = "") -> str:
    def cell(value: object) -> str:
        return m_text(value) if isinstance(value, str) else str(value)

    header = "{" + ", ".join(m_text(h) for h in headers) + "}"
    body = ",\n".join("            {" + ", ".join(cell(v) for v in row) + "}" for row in rows)
    source = f"#table({header}, {{\n{body}\n        }})"
    return _typed_table_m(list(zip(headers, types)), source, extra)


def _packed_table_m(headers: list[str], rows: list[list], types: list[str], extra: str = "") -> str:
    header = "{" + ", ".join(m_text(h) for h in headers) + "}"
    source = f'Table.FromRows(Json.Document(Binary.Decompress(Binary.FromText("{pack_rows(rows)}", BinaryEncoding.Base64), Compression.Deflate)), {header})'
    return _typed_table_m(list(zip(headers, types)), source, extra)


CURRENCY = "\\$#,##0"
CURRENCY_CENTS = "\\$#,##0.00"


def date_table(ds: MedTechDataset) -> Table:
    headers = ["Date Key", "Year", "Month", "Month Name", "Quarter", "Year Quarter", "Year Month", "Half Year", "Month Short"]
    types = ["Int64.Type", "Int64.Type", "Int64.Type", "type text", "type text", "type text", "type text", "type text", "type text"]
    # A real date column (first day of the month) and the "Time" data category make this the model's date table; reporting visuals
    # (Intelligence sheets) build their time-based queries from it.
    add_date = 'WithDate = Table.AddColumn(Typed, "Date", each #date([Year], [Month], 1), type date)'
    return Table(
        "Date",
        "Monthly calendar for the planning horizon (Jan 2023 - Dec 2027). Planning date hierarchy: Year > Quarter > Month Short.",
        columns=[
            Column("Date Key", "int64", hidden=True, format_string="0", description="Surrogate key in YYYYMM form (relationship column)."),
            Column("Date", "dateTime", key=True, format_string="mmm yyyy", description="First day of the month; the date-table key."),
            Column("Year", "int64", format_string="0"),
            Column("Month", "int64", format_string="0", description="Calendar month number (1-12); sort key for the month name columns."),
            Column("Month Name", "string", sort_by="Month"),
            Column("Quarter", "string", description="Calendar quarter, Q1 to Q4."),
            Column("Year Quarter", "string"),
            Column("Year Month", "string"),
            Column("Half Year", "string"),
            Column("Month Short", "string", sort_by="Month"),
        ],
        hierarchies=[Hierarchy("Date Hierarchy", ["Year", "Quarter", "Month Short"])],
        partitions=[Partition("Date", _literal_table_m(headers, ds.dates, types, add_date))],
        data_category="Time",
    )


def product_table(ds: MedTechDataset) -> Table:
    headers = ["Product Key", "Category", "Sub Category", "Product Name"]
    return Table(
        "Product",
        "Product dimension (36 fictional products): Category > Sub Category > Product Name. Synthetic demo data.",
        columns=[
            Column("Product Key", "int64", hidden=True, key=True, format_string="0"),
            Column("Category", "string", description="Business unit: Cardiovascular, Orthopedics, Diabetes & Metabolic, Diagnostics & Imaging, Surgical & Robotics, Life Sciences Tools."),
            Column("Sub Category", "string"),
            Column("Product Name", "string"),
        ],
        hierarchies=[Hierarchy("Product Hierarchy", ["Category", "Sub Category", "Product Name"])],
        partitions=[Partition("Product", _literal_table_m(headers, ds.products, ["Int64.Type", "type text", "type text", "type text"]))],
    )


def geography_table(ds: MedTechDataset) -> Table:
    headers = ["Geography Key", "Region", "Sub Region", "Country"]
    return Table(
        "Geography",
        "Geography dimension (17 countries in 5 regions): Region > Country. Synthetic demo data.",
        columns=[
            Column("Geography Key", "int64", hidden=True, key=True, format_string="0"),
            Column("Region", "string"),
            Column("Sub Region", "string"),
            Column("Country", "string"),
        ],
        hierarchies=[Hierarchy("Geography Hierarchy", ["Region", "Country"])],
        partitions=[Partition("Geography", _literal_table_m(headers, ds.geographies, ["Int64.Type", "type text", "type text", "type text"]))],
    )


SALES_HEADERS = ["Product Key", "Geography Key", "Date Key", "Units Sold", "Gross Revenue", "COGS"]
SALES_TYPES = ["Int64.Type", "Int64.Type", "Int64.Type", "Int64.Type", "Currency.Type", "Currency.Type"]
GROSS_PROFIT_STEP = 'WithGrossProfit = Table.AddColumn(Typed, "Gross Profit", each [Gross Revenue] - [COGS], Currency.Type)'


def sales_table(ds: MedTechDataset) -> Table:
    partitions = [
        Partition(f"Sales Transactions {year}", _packed_table_m(SALES_HEADERS, [list(r) for r in rows], SALES_TYPES, GROSS_PROFIT_STEP))
        for year, rows in ds.sales_by_year().items()
    ]
    return Table(
        "Sales Transactions",
        "Monthly sales by product and country (actuals Jan 2023 - Sep 2026). One partition per calendar year. Synthetic demo data.",
        columns=[
            Column("Product Key", "int64", hidden=True, format_string="0"),
            Column("Geography Key", "int64", hidden=True, format_string="0"),
            Column("Date Key", "int64", hidden=True, format_string="0"),
            Column("Units Sold", "int64", hidden=True, summarize="sum", format_string="#,##0"),
            Column("Gross Revenue", "decimal", hidden=True, summarize="sum", format_string=CURRENCY),
            Column("COGS", "decimal", hidden=True, summarize="sum", format_string=CURRENCY),
            Column("Gross Profit", "decimal", hidden=True, summarize="sum", format_string=CURRENCY),
        ],
        partitions=partitions,
    )


PNL_HEADERS = [
    "Geography Key", "Date Key", "Procedure Volume", "Revenue per Procedure", "Rebates and Returns", "Materials Cost", "Manufacturing Labor",
    "Sterilization and Packaging", "Sales and Marketing", "Clinical and Regulatory", "Administrative Expenses", "R&D",
]
PNL_TYPES = ["Int64.Type", "Int64.Type", "Int64.Type", "Currency.Type"] + ["Currency.Type"] * 8


def pnl_table(ds: MedTechDataset) -> Table:
    describe = {
        "Procedure Volume": "Procedures (or runs) supported in the month, in whole units. Revenue is Procedure Volume x Revenue per Procedure.",
        "Revenue per Procedure": "Blended revenue per procedure, constant within a calendar year (annual price reset) so that Volume x Price aggregates exactly.",
        "Rebates and Returns": "Gross-to-net deductions: group-purchasing rebates, contract discounts and returns.",
        "Materials Cost": "Medical-grade materials and components (share of COGS).",
        "Manufacturing Labor": "Direct manufacturing labor (share of COGS).",
        "Sterilization and Packaging": "Sterilization and sterile-barrier packaging (share of COGS).",
        "Sales and Marketing": "Field sales, clinical specialists and marketing.",
        "Clinical and Regulatory": "Clinical studies, regulatory affairs and quality compliance.",
        "Administrative Expenses": "General and administrative expenses.",
        "R&D": "Research and development expense.",
    }
    columns = [Column("Geography Key", "int64", hidden=True, format_string="0"), Column("Date Key", "int64", hidden=True, format_string="0")]
    columns.append(Column("Procedure Volume", "int64", summarize="sum", format_string="#,##0", description=describe["Procedure Volume"]))
    columns.append(Column("Revenue per Procedure", "decimal", summarize="average", format_string=CURRENCY_CENTS, description=describe["Revenue per Procedure"]))
    for name in PNL_HEADERS[4:]:
        columns.append(Column(name, "decimal", summarize="sum", format_string=CURRENCY, description=describe[name]))
    return Table(
        "P&L Drivers",
        "Driver-based P&L inputs by country and month (actuals Jan 2023 - Sep 2026): procedure volume, revenue per procedure, gross-to-net, COGS components and operating expenses. Gross revenue ties to Sales Transactions within rounding. Synthetic demo data.",
        columns=columns,
        partitions=[Partition("P&L Drivers", _packed_table_m(PNL_HEADERS, [list(r) for r in ds.pnl], PNL_TYPES))],
    )


@dataclass(frozen=True)
class WritebackSource:
    """A Planning writeback table (Fabric SQL) that is surfaced in the semantic model for reporting."""

    model_table: str  # e.g. "Plan Revenue Budget" (Planning prefixes such tables with "Plan")
    server: str
    database: str
    schema: str
    table: str
    series: str  # ValueColumnName written by Planning, e.g. "FY2026 Revenue Budget"
    measure: str  # measure added to the Measures Table
    role: str = "budget"  # "budget" feeds the actual-vs-budget variance; "forecast" feeds forecast-vs-budget
    dimensions: tuple[str, ...] = ("Region", "Category")  # business dimensions stored next to Year / Quarter / MonthShort


# Writeback column -> (model table, model column) used by the virtual relationships of the reporting measure
DIMENSION_COLUMNS = {"Region": ("Geography", "Region"), "Category": ("Product", "Category")}
ROLES = ("budget", "forecast")


def writeback_table(source: WritebackSource) -> Table:
    unknown = [d for d in source.dimensions if d not in DIMENSION_COLUMNS]
    if unknown:
        raise ValueError(f"Writeback source {source.model_table!r} has unsupported dimensions {unknown}; supported: {sorted(DIMENSION_COLUMNS)}")
    if source.role not in ROLES:
        raise ValueError(f"Writeback source {source.model_table!r} has unsupported role {source.role!r}; supported: {list(ROLES)}")
    columns = [Column(d, "string") for d in source.dimensions] + [
        Column("Year", "int64", format_string="0"), Column("Quarter", "string"),
        Column("Month Short", "string", source="MonthShort"),
        Column("Value", "decimal", summarize="sum", format_string=CURRENCY, description="Latest value written back by Planning at leaf level."),
        Column("Previous Value", "decimal", summarize="sum", format_string=CURRENCY, source="PreviousValue"),
        Column("Last Updated At", "dateTime", format_string="General Date", source="LastUpdatedAt"),
        Column("Last Updated By", "string", source="LastUpdatedBy"),
    ]
    leaf_filter = "".join(f' and [{d}] <> "All"' for d in (*source.dimensions, "MonthShort"))
    selected = ", ".join(m_text(c) for c in (*source.dimensions, "Year", "Quarter", "MonthShort", "Value", "PreviousValue", "LastUpdatedAt", "LastUpdatedBy"))
    code = (
        "let\n"
        f"    Source = Sql.Database({m_text(source.server)}, {m_text(source.database)}),\n"
        f"    Navigation = Source{{[Schema = {m_text(source.schema)}, Item = {m_text(source.table)}]}}[Data],\n"
        f"    Latest = Table.SelectRows(Navigation, each [IsLatest] = 1 and [ValueColumnName] = {m_text(source.series)}{leaf_filter}),\n"
        f"    Selected = Table.SelectColumns(Latest, {{{selected}}}),\n"
        "    Typed = Table.TransformColumnTypes(Selected, {{\"Year\", Int64.Type}})\n"
        "in\n    Typed"
    )
    return Table(
        source.model_table,
        f"Leaf-level latest values written back by Planning to {source.schema}.{source.table} ({source.series}). Reloaded from the Fabric SQL database on refresh. "
        f"Grain: {', '.join(source.dimensions)} x month. No physical relationships: reporting measures use virtual relationships (TREATAS) to Geography, Product and Date.",
        columns=columns,
        partitions=[Partition(source.model_table, code)],
    )


def writeback_measure_expression(source: WritebackSource) -> str:
    """Sum of the written-back leaves under the current Region / Category / Year / Quarter / Month filters.

    The writeback table has no physical relationships (Planning's columns are plain text), so without the virtual relationships below every
    region would show the grand total. A table without a Category column (the rolling forecast is planned by region) cannot answer a question
    asked per category; it returns BLANK there instead of repeating the regional value for every category."""
    table = q(source.model_table)
    filters = [f"TREATAS(VALUES({model_table}[{column}]), {table}[{d}])" for d in source.dimensions for model_table, column in [DIMENSION_COLUMNS[d]]]
    filters += [
        f"TREATAS(VALUES('Date'[Year]), {table}[Year])",
        f"TREATAS(VALUES('Date'[Quarter]), {table}[Quarter])",
        f"TREATAS(VALUES('Date'[Month Short]), {table}[Month Short])",
    ]
    body = f"CALCULATE(\n    SUM({table}[Value]),\n" + ",\n".join(f"    {f}" for f in filters) + "\n)"
    if "Category" in source.dimensions:
        return body
    return f"IF(\n    ISCROSSFILTERED(Product),\n    BLANK(),\n{indent(body, '    ')}\n)"


def indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def variance_measures(writeback: tuple[WritebackSource, ...]) -> list[Measure]:
    """Plan-versus-actual measures over the writeback sources (Variance = Actual - Reference; percentages divide by |Reference| and are BLANK for a zero reference)."""
    budget = next((s for s in writeback if s.role == "budget"), None)
    forecast = next((s for s in writeback if s.role == "forecast"), None)
    measures: list[Measure] = []
    if budget:
        ref = f"[{budget.measure}]"
        measures += [
            Measure(
                BUDGET_ACTUAL_PERIODS,
                f"SUMX(\n    VALUES('Date'[Date Key]),\n    IF(NOT ISBLANK([Gross Revenue]), {ref})\n)",
                CURRENCY,
                "Budget summed only for the months that already have actuals: the like-for-like reference for Actual vs Budget (a full-year budget would make a year-to-date actual look short).",
            ),
            Measure(
                ACTUAL_VS_BUDGET,
                f"VAR ActualRevenue = [Gross Revenue]\nRETURN IF(NOT ISBLANK(ActualRevenue), ActualRevenue - [{BUDGET_ACTUAL_PERIODS}])",
                CURRENCY,
                "Actual gross revenue minus the budget for the same months (positive = ahead of budget). BLANK when there are no actuals.",
            ),
            Measure(ACTUAL_VS_BUDGET_PCT, f"DIVIDE([{ACTUAL_VS_BUDGET}], ABS([{BUDGET_ACTUAL_PERIODS}]))", "0.0%", "Variance divided by the absolute budget; BLANK when the budget is zero or missing."),
        ]
    if budget and forecast:
        measures += [
            Measure(
                FORECAST_VS_BUDGET,
                f"IF(\n    NOT ISBLANK([{forecast.measure}]) && NOT ISBLANK([{budget.measure}]),\n    [{forecast.measure}] - [{budget.measure}]\n)",
                CURRENCY,
                "Rolling forecast (closed months = actuals, open months = forecast) minus the budget for the same periods. BLANK unless both exist: per product category (the forecast is planned by region) and in years without a budget.",
            ),
            Measure(FORECAST_VS_BUDGET_PCT, f"DIVIDE([{FORECAST_VS_BUDGET}], ABS([{budget.measure}]))", "0.0%", "Forecast variance divided by the absolute budget; BLANK when the budget is zero or missing."),
        ]
    return measures


BUDGET_ACTUAL_PERIODS = "Budget for Actual Periods"
ACTUAL_VS_BUDGET = "Actual vs Budget Variance"
ACTUAL_VS_BUDGET_PCT = "Actual vs Budget Variance %"
FORECAST_VS_BUDGET = "Forecast vs Budget Variance"
FORECAST_VS_BUDGET_PCT = "Forecast vs Budget Variance %"


def measures_table(writeback: tuple[WritebackSource, ...] = ()) -> Table:
    sales, pnl = "'Sales Transactions'", "'P&L Drivers'"
    prior_year = (
        "CALCULATE(\n    [Gross Revenue],\n    REMOVEFILTERS('Date'),\n"
        "    TREATAS(\n        SELECTCOLUMNS(VALUES('Date'[Date Key]), \"PriorKey\", 'Date'[Date Key] - 100),\n        'Date'[Date Key]\n    )\n)"
    )
    baseline = (
        "CALCULATE(\n    {m},\n    REMOVEFILTERS('Date'),\n"
        "    TREATAS(\n        SELECTCOLUMNS(\n            FILTER(VALUES('Date'[Date Key]), 'Date'[Date Key] >= 202601 && 'Date'[Date Key] <= 202612),\n"
        "            \"PriorKey\", 'Date'[Date Key] - 100\n        ),\n        'Date'[Date Key]\n    )\n)"
    )
    measures = [
        Measure("Units Sold", f"SUM({sales}[Units Sold])", "#,##0", "Units shipped."),
        Measure("Gross Revenue", f"SUM({sales}[Gross Revenue])", CURRENCY, "Gross revenue before gross-to-net deductions."),
        Measure("COGS", f"SUM({sales}[COGS])", CURRENCY, "Cost of goods sold."),
        Measure("Gross Profit", "[Gross Revenue] - [COGS]", CURRENCY),
        Measure("Gross Margin %", "DIVIDE([Gross Profit], [Gross Revenue])", "0.0%"),
        Measure("Avg Selling Price", "DIVIDE([Gross Revenue], [Units Sold])", CURRENCY_CENTS, "Revenue per unit; a mix-weighted average."),
        Measure("Baseline Revenue", baseline.format(m="[Gross Revenue]"), CURRENCY, "FY2025 gross revenue actuals shown in the matching FY2026 periods (blank in other years). Seed for the FY2026 revenue plan."),
        Measure("Baseline COGS", baseline.format(m="[COGS]"), CURRENCY, "FY2025 cost of goods sold shown in the matching FY2026 periods. Seed for the FY2026 cost plan."),
        Measure("Baseline Units", baseline.format(m="[Units Sold]"), "#,##0", "FY2025 units shown in the matching FY2026 periods. Seed for the FY2026 volume plan."),
        Measure("PY Gross Revenue", prior_year, CURRENCY, "Gross revenue for the same period one year earlier."),
        Measure("YoY Growth %", "DIVIDE([Gross Revenue] - [PY Gross Revenue], [PY Gross Revenue])", "0.0%", "Gross revenue growth versus the same period one year earlier."),
        Measure("Procedure Volume", f"SUM({pnl}[Procedure Volume])", "#,##0"),
        Measure("P&L Gross Revenue", f"SUMX({pnl}, {pnl}[Procedure Volume] * {pnl}[Revenue per Procedure])", CURRENCY, "Procedure Volume x Revenue per Procedure."),
        Measure("P&L Net Revenue", f"[P&L Gross Revenue] - SUM({pnl}[Rebates and Returns])", CURRENCY),
        Measure(
            "P&L COGS",
            f"SUM({pnl}[Materials Cost]) + SUM({pnl}[Manufacturing Labor]) + SUM({pnl}[Sterilization and Packaging])",
            CURRENCY,
        ),
        Measure("P&L Gross Profit", "[P&L Net Revenue] - [P&L COGS]", CURRENCY),
        Measure(
            "P&L Operating Expenses",
            f"SUM({pnl}[Sales and Marketing]) + SUM({pnl}[Clinical and Regulatory]) + SUM({pnl}[Administrative Expenses]) + SUM({pnl}[R&D])",
            CURRENCY,
        ),
        Measure("P&L Operating Profit", "[P&L Gross Profit] - [P&L Operating Expenses]", CURRENCY),
        Measure("Operating Margin %", "DIVIDE([P&L Operating Profit], [P&L Net Revenue])", "0.0%"),
    ]
    for source in writeback:
        measures.append(Measure(source.measure, writeback_measure_expression(source), CURRENCY, f"Sum of the latest leaf-level '{source.series}' values written back by Planning, filtered by {', '.join(source.dimensions)} and Date."))
    measures += variance_measures(writeback)
    return Table(
        "Measures Table",
        "Business measures for planning, reporting and Intelligence sheets.",
        measures=measures,
        columns=[Column("Value", "string", hidden=True)],
        partitions=[Partition("Measures Table", _typed_table_m([("Value", "type text")], '#table({"Value"}, {{"Measures"}})'))],
    )


RELATIONSHIPS = (
    ("SalesToProduct", "'Sales Transactions'.'Product Key'", "Product.'Product Key'"),
    ("SalesToGeography", "'Sales Transactions'.'Geography Key'", "Geography.'Geography Key'"),
    ("SalesToDate", "'Sales Transactions'.'Date Key'", "Date.'Date Key'"),
    ("PnlToGeography", "'P&L Drivers'.'Geography Key'", "Geography.'Geography Key'"),
    ("PnlToDate", "'P&L Drivers'.'Date Key'", "Date.'Date Key'"),
)

MODEL_DESCRIPTION = (
    "Northwind MedTech & Life Sciences planning model. Entirely synthetic demo data: no real company, product, customer or patient data. "
    "Actuals run Jan 2023 - Sep 2026; Oct 2026 onward is open for planning."
)


def build_parts(ds: MedTechDataset, writeback: tuple[WritebackSource, ...] = ()) -> dict[str, bytes]:
    tables = [date_table(ds), product_table(ds), geography_table(ds), sales_table(ds), pnl_table(ds)] + [writeback_table(s) for s in writeback] + [measures_table(writeback)]
    parts: dict[str, str] = {
        "definition.pbism": PBISM + "\n",
        "definition/database.tmdl": "database\n\tcompatibilityLevel: 1600\n",
        "definition/model.tmdl": f"/// {MODEL_DESCRIPTION}\nmodel Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\tsourceQueryCulture: en-US\n\tdataAccessOptions\n\t\tlegacyRedirects\n\t\treturnErrorValuesAsNull\n\n"
        + "\n".join(f"ref table {q(t.name)}" for t in tables)
        + "\n\nref culture en-US\n",
        # The model names a culture, so the culture object must exist too: report-style query paths (Intelligence sheets) need it.
        "definition/cultures/en-US.tmdl": "cultureInfo en-US\n",
        "definition/relationships.tmdl": "\n".join(f"relationship {name}\n\tfromColumn: {src}\n\ttoColumn: {dst}\n" for name, src, dst in RELATIONSHIPS),
    }
    for table in tables:
        parts[f"definition/tables/{table.name}.tmdl"] = table.render()
    return {path: text.encode("utf-8") for path, text in parts.items()}
