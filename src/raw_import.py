"""
Turn a company's messiest sales export into clean data and answers.

Real exports (Tally, SAP, Shopify, a shop's Excel) never match a template. This
module does what an analyst does by hand, and writes down every step:

  read_any()     any Excel / CSV / gzip / zip file; finds the header row even under
                 title lines; stacks sheets that share columns
  guess_roles()  which column is the date, product, quantity, price ... from names
                 AND contents (the user can override every guess)
  clean()        parses any number or date format, fixes product names, and removes
                 rows that are not demand (blank and total lines, repeated headers,
                 bad dates, duplicates, returns, tax/freight/discount lines, free
                 samples, bulk orders keyed in by mistake), logging each step
  analyse()      everything that needs only sales: trend, ABC-XYZ, forecast with a
                 backtest, slowing and dead products, returns, customer concentration
  stock_sheet()  a pre-filled sheet of the top products for the user to add stock,
                 cost and lead time, which unlocks the full reorder dashboard
  to_tables()    turns the clean sales + that sheet into the tables upload.validate() reads
"""
import csv
import io
import re
import warnings
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Column roles and the header words that point to them
# ---------------------------------------------------------------------------
ROLES = {
    "date": ["invoice date", "invoicedate", "order date", "bill date", "voucher date", "txn date",
             "transaction date", "posting date", "doc date", "sale date", "date", "created at", "day"],
    "product_id": ["stockcode", "stock code", "sku", "item code", "itemcode", "product code", "product id",
                   "productid", "material", "part no", "part number", "article", "item no", "item id", "barcode",
                   "lineitem sku"],
    "product_name": ["description", "item name", "product name", "item description", "product", "item",
                     "particulars", "stock item", "name of item", "material description", "article name",
                     "lineitem name", "title"],
    "quantity": ["quantity", "qty", "units", "unit sold", "units sold", "pcs", "nos", "billed qty",
                 "actual qty", "qty sold", "lineitem quantity", "count"],
    "price": ["unit price", "unitprice", "price", "rate", "selling price", "sp", "mrp", "unit rate",
              "price per unit", "lineitem price"],
    "amount": ["amount", "value", "total", "line total", "net amount", "gross amount", "sales value",
               "net value", "taxable value", "revenue", "sales", "subtotal", "line amount"],
    "invoice": ["invoice", "invoice no", "invoice number", "bill no", "vch no", "voucher no", "voucher number",
                "order id", "order no", "order number", "doc no", "document no", "receipt no", "name"],
    "customer": ["customer id", "customer", "customer name", "party", "party name", "particulars", "client",
                 "buyer", "account", "ledger", "email", "billing name"],
    "doc_type": ["voucher type", "vch type", "transaction type", "doc type", "document type", "type", "txn type",
                 "entry type", "financial status"],
    "country": ["country", "region", "state", "city", "billing country", "shipping country", "market"],
}
NEED = ["date", "quantity"]               # plus product_id or product_name, plus price or amount (or neither)
LABELS = {"date": "Date", "product_id": "Product code", "product_name": "Product name", "quantity": "Quantity",
          "price": "Unit price", "amount": "Line amount", "invoice": "Invoice / order no.",
          "customer": "Customer", "doc_type": "Transaction type", "country": "Country / region"}

# Lines that are not products: taxes, freight, discounts, fees, rounding ...
NOT_PRODUCT = re.compile(
    r"\b(postage|post(al)? charges?|shipping|freight|carriage|courier|delivery charges?|discount|round(ing)?[ -]?off|"
    r"rounding|c?gst|sgst|igst|utgst|vat|tax|tcs|tds|cess|bank charges?|commission|fees?|amazon ?fee|"
    r"adjust(ment)?|manual|gift ?(card|voucher)|voucher|packing|packaging charges?|cartage|labou?r charges?|"
    r"service charges?|handling|insurance|test(ing)? (item|product|code)|dummy|samples?)\b", re.I)
NOT_PRODUCT_CODES = {"POST", "DOT", "M", "C2", "D", "S", "B", "CRUK", "PADS", "BANK CHARGES", "AMAZONFEE",
                     "ADJUST", "ADJUST2", "TEST001", "TEST002", "SP1002", "GIFT", "FREIGHT", "SHIPPING"}
RETURN_WORDS = re.compile(r"return|credit ?note|cancel|refund|\bcn\b|reversal|rejection", re.I)
TOTAL_WORDS = re.compile(r"^\s*(grand )?(sub ?)?total\b|^\s*opening balance|^\s*closing balance|carried over|"
                         r"brought forward|continued", re.I)

MAX_ROWS = 3_000_000
warnings.filterwarnings("ignore", message="This pattern is interpreted as a regular expression")


# ---------------------------------------------------------------------------
# 1. Reading anything
# ---------------------------------------------------------------------------
def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _keyword_score(cell: str) -> int:
    """How strongly a header cell looks like one of our column names."""
    c = _norm(cell)
    if not c or len(c) > 40:
        return 0
    best = 0
    for words in ROLES.values():
        for w in words:
            if c == w or c.replace(" ", "") == w.replace(" ", ""):
                best = max(best, 3)
            elif re.search(rf"\b{re.escape(w)}\b", c):
                best = max(best, 1)
    return best


def _find_header(grid: pd.DataFrame) -> int:
    """Index of the row that holds the column names (exports often have title lines on top)."""
    best_row, best = 0, -1
    for i in range(min(40, len(grid))):
        row = grid.iloc[i]
        texts = [v for v in row if isinstance(v, str) and v.strip()]
        score = sum(_keyword_score(v) for v in texts) + 0.1 * len(texts)
        if score > best:
            best_row, best = i, score
    return best_row


def _frame_from_grid(grid: pd.DataFrame) -> pd.DataFrame:
    """Raw grid (no header) -> table with the detected header row as column names."""
    grid = grid.dropna(how="all").dropna(axis=1, how="all").reset_index(drop=True)
    if grid.empty:
        return grid
    h = _find_header(grid)
    names, seen = [], {}
    for j, v in enumerate(grid.iloc[h]):
        n = str(v).strip() if pd.notna(v) and str(v).strip() else f"Column {j + 1}"
        seen[n] = seen.get(n, 0) + 1
        names.append(n if seen[n] == 1 else f"{n} ({seen[n]})")
    df = grid.iloc[h + 1:].copy()
    df.columns = names
    return df.reset_index(drop=True)


def _read_csv_bytes(data: bytes) -> pd.DataFrame:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text_head = data[:50_000].decode(enc)
            break
        except UnicodeDecodeError:
            continue
    try:
        sep = csv.Sniffer().sniff(text_head, delimiters=",;\t|").delimiter
    except csv.Error:
        sep = ","
    return pd.read_csv(io.BytesIO(data), sep=sep, header=None, dtype=str, encoding=enc,
                       on_bad_lines="skip", engine="python" if sep != "," else "c",
                       keep_default_na=True, skip_blank_lines=False)


def _read_one(name: str, data: bytes) -> list[tuple[str, pd.DataFrame]]:
    """Returns [(source label, table)] for one file. Handles .gz and .zip by looking inside."""
    low = name.lower()
    if low.endswith(".gz"):
        import gzip
        return _read_one(name[:-3], gzip.decompress(data))
    if low.endswith(".zip"):
        out = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if not n.endswith("/") and Path(n).suffix.lower() in (".csv", ".txt", ".tsv", ".xlsx", ".xls", ".xlsm"):
                    out += _read_one(n, z.read(n))
        return out
    if Path(low).suffix in (".xlsx", ".xlsm", ".xls"):
        try:
            book = pd.ExcelFile(io.BytesIO(data), engine="calamine")      # fast reader
        except Exception:
            book = pd.ExcelFile(io.BytesIO(data))
        out = []
        for sheet in book.sheet_names:          # one sheet at a time keeps memory down on big files
            out.append((f"{name} / {sheet}", _slim(_frame_from_grid(book.parse(sheet, header=None)))))
        return out
    return [(name, _slim(_frame_from_grid(_read_csv_bytes(data))))]


def read_any(files) -> tuple[pd.DataFrame, list]:
    """
    files = [(name, bytes)]. Reads every sheet of every file, keeps the ones that look
    like sales (most columns recognised), and stacks those that share columns.
    Returns (table, notes).
    """
    parts, notes = [], []
    for name, data in files:
        for label, df in _read_one(name, data):
            if df.empty or df.shape[1] < 2:
                continue
            score = sum(_keyword_score(c) > 0 for c in df.columns)
            parts.append((label, df, score))
    if not parts:
        return pd.DataFrame(), ["No tables found in the file."]
    best = max(p[2] for p in parts)
    keep = [p for p in parts if p[2] >= max(2, best - 1)] or [max(parts, key=lambda p: p[2])]
    cols = keep[0][1].columns
    same = [p for p in keep if list(p[1].columns) == list(cols)]
    for label, df, _ in keep:
        if not any(df is q[1] for q in same):
            notes.append(f"Skipped {label}: its columns differ from the main table.")
    for label, df, _ in parts:
        if all(df is not q[1] for q in keep):
            notes.append(f"Skipped {label}: it doesn't look like a sales table.")
    table = pd.concat([p[1] for p in same], ignore_index=True)
    if len(same) > 1:
        notes.append(f"Stacked {len(same)} sheets/files with the same columns: " + ", ".join(p[0] for p in same) + ".")
    if len(table) > MAX_ROWS:
        notes.append(f"Only the first {MAX_ROWS:,} of {len(table):,} rows were used.")
        table = table.iloc[:MAX_ROWS]
    return _slim(table), notes


def _slim(t: pd.DataFrame) -> pd.DataFrame:
    """Store repeated text as categories: a million-row export then fits in far less memory."""
    if len(t) < 50_000:
        return t
    for c in t.columns:
        if t[c].dtype == object or pd.api.types.is_string_dtype(t[c]):
            if t[c].nunique(dropna=True) < 0.5 * len(t):
                t[c] = t[c].astype("category")
    return t


# ---------------------------------------------------------------------------
# 2. Parsing numbers and dates written any way
# ---------------------------------------------------------------------------
_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


def to_number(s: pd.Series) -> pd.Series:
    """
    '₹ 1,25,000.00', 'Rs.450', '$1,234', '1.234,56' (European), '(120)' and '120-' (negative),
    '12 Nos', '5 pcs', '45 Cr' -> numbers. Anything unreadable becomes NaN.
    """
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_numeric(s, errors="coerce").astype(float)
    t = s.astype(str).str.strip()
    t = t.where(~t.str.lower().isin(["", "nan", "none", "-", "--", "n/a", "na", "null"]), np.nan)
    neg = t.str.match(r"^\(.*\)$", na=False) | t.str.match(r"^[^-]*\d[^-]*-\s*$", na=False) | \
        t.str.contains(r"\bdr\b", case=False, na=False)
    sample = t.dropna().head(2000)
    euro = sample.str.match(r"^[^\d-]*-?\d{1,3}(\.\d{3})+,\d+").mean() > 0.3 if len(sample) else False
    if euro:
        t = t.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    else:
        t = t.str.replace(",", "", regex=False)
    t = t.str.replace(r"(?<=\d)\s+(?=\d)", "", regex=True)       # '1 250' thousands with spaces
    t = t.str.replace(r"(?i)\b(rs|inr|usd|eur|gbp|aed)\b\.?|[₹$£€]", " ", regex=True)   # 'Rs.450' is 450, not .450
    x = pd.to_numeric(t.str.extract(r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", expand=False), errors="coerce")
    return x.where(~neg, -x.abs())


def _in_range(d: pd.Series) -> pd.Series:
    d = pd.to_datetime(d, errors="coerce")
    if getattr(d.dt, "tz", None) is not None:
        d = d.dt.tz_localize(None)
    return d.where(d.dt.year.between(1900, 2100))


def to_date(s: pd.Series) -> pd.Series:
    """Excel dates, Excel serial numbers, '01-04-2025' (day first), '04/01/2025', '2025-04-01', '1-Apr-25' ..."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return _in_range(s).astype("datetime64[ns]")
    res = pd.Series(pd.NaT, index=s.index, dtype="object")
    is_dt = s.map(lambda v: isinstance(v, (pd.Timestamp, np.datetime64)) or (hasattr(v, "year") and hasattr(v, "month")))
    if is_dt.any():
        res[is_dt] = list(_in_range(pd.Series(list(s[is_dt]), index=s.index[is_dt])))
    rest = s[~is_dt].astype(str).str.strip()
    num = pd.to_numeric(rest, errors="coerce")
    serial = num.between(20000, 60000)
    if serial.any():
        res[rest.index[serial]] = list(pd.to_datetime("1899-12-30") + pd.to_timedelta(num[serial], unit="D"))
    txt = rest[~serial & num.isna() & rest.ne("") & rest.str.lower().ne("nan") & rest.str.contains(r"\d", na=False)]
    if len(txt):
        lead = txt.str.extract(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-]\d{2,4}")
        a, b = pd.to_numeric(lead[0], errors="coerce"), pd.to_numeric(lead[1], errors="coerce")
        dayfirst = bool((a > 12).sum() > (b > 12).sum())
        if (a > 12).sum() == 0 and (b > 12).sum() == 0:
            dayfirst = False
        iso = txt.str.match(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")      # 2025-04-01 is always year-month-day
        for part, dfirst in ((txt[iso], False), (txt[~iso], dayfirst)):
            if len(part):
                res[part.index] = list(_in_range(pd.to_datetime(part, errors="coerce", dayfirst=dfirst, format="mixed")))
    return pd.to_datetime(res, errors="coerce").astype("datetime64[ns]")


# ---------------------------------------------------------------------------
# 3. Which column is which
# ---------------------------------------------------------------------------
def _profile(col: pd.Series) -> dict:
    v = col.dropna()
    v = v[v.astype(str).str.strip().ne("") & (v.astype(str).map(_norm) != _norm(col.name))]  # skip repeated headers
    fill = len(v) / max(len(col), 1)
    v = v.iloc[np.linspace(0, len(v) - 1, min(len(v), 600)).astype(int)] if len(v) else v   # spread-out sample
    if v.empty:
        return {"num": 0, "date": 0, "int": 0, "uniq": 0, "len": 0, "text": 0, "fill": 0}
    n = to_number(v)
    num_rate = n.notna().mean()
    as_text = v.astype(str)
    date_like = as_text.str.match(r"^\s*\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}")
    number_like = as_text.str.fullmatch(r"[\s₹$£€Rs.,()\-+\d]*\d[\s\d.,()\-+]*(?:\s*(nos|pcs|kg|units?|cr|dr))?",
                                        case=False).fillna(False).astype(bool)
    looks_number_only = (number_like & ~date_like.fillna(False).astype(bool)).mean()
    d = to_date(v)
    date_rate = d.notna().mean() * (0.3 if looks_number_only > 0.9 and not pd.api.types.is_datetime64_any_dtype(v)
                                    and not v.map(lambda x: hasattr(x, "year")).any() else 1)
    if date_rate and d.notna().any():
        yrs = d.dropna().dt.year
        date_rate *= yrs.between(1990, 2100).mean()
    return {"num": num_rate * looks_number_only, "date": date_rate,
            "int": (n.dropna() % 1 == 0).mean() if n.notna().any() else 0,
            "uniq": v.astype(str).nunique() / len(v), "len": as_text.str.len().mean(),
            "text": as_text.str.contains(r"[A-Za-z]{3}").mean(), "fill": fill}


def guess_roles(df: pd.DataFrame) -> dict:
    """Best guess of {role: column name or None}, from header words and what the values look like."""
    profiles = {c: _profile(df[c]) for c in df.columns}
    scores = {}
    for c in df.columns:
        name = _norm(c)
        p = profiles[c]
        for role, words in ROLES.items():
            s = 0.0
            for rank, w in enumerate(words):
                if name == w or name.replace(" ", "") == w.replace(" ", ""):
                    s = max(s, 6 - rank * 0.05)
                elif re.search(rf"\b{re.escape(w)}\b", name):
                    s = max(s, 3 - rank * 0.02)
            if role == "date":
                s = s + 4 * p["date"] if p["date"] > 0.6 else s * 0.2
            elif role in ("quantity", "price", "amount"):
                s = s + 1.5 * p["num"] if p["num"] > 0.8 else s * 0.1
                if role == "quantity" and p["num"] > 0.8:
                    s += 0.5 * p["int"]
            elif role == "product_name":
                s = s + (1.5 if p["text"] > 0.7 and p["len"] > 8 else 0) if p["num"] < 0.5 else s * 0.2
            elif role == "product_id":
                s = s * (1.0 if p["len"] < 25 else 0.3)
            elif role in ("customer", "invoice", "country", "doc_type"):
                s = s if p["date"] < 0.6 else s * 0.1
            scores[(role, c)] = s * min(1.0, p["fill"] * 4)   # a mostly empty column is a poor match
    roles, used = {r: None for r in ROLES}, set()
    for (role, c), s in sorted(scores.items(), key=lambda kv: -kv[1]):
        if s >= 1.5 and roles[role] is None and c not in used:
            roles[role] = c
            used.add(c)
    # No date column by name? take the most date-like column
    if roles["date"] is None:
        cands = [(profiles[c]["date"], c) for c in df.columns if c not in used and profiles[c]["date"] > 0.8]
        if cands:
            roles["date"] = max(cands)[1]
    return roles


def missing_roles(roles: dict) -> list[str]:
    """What is still needed before cleaning can run, in plain words."""
    out = []
    if not roles.get("date"):
        out.append("a **date** column")
    if not (roles.get("product_id") or roles.get("product_name")):
        out.append("a **product** column (code or name)")
    return out


# ---------------------------------------------------------------------------
# 4. Cleaning, with a reason for every row removed
# ---------------------------------------------------------------------------
def _canon_name(s: pd.Series) -> pd.Series:
    """'  Brake pads (PAIR) ' and 'BRAKE PADS (pair)' are the same product."""
    return s.astype(str).str.strip().str.replace(r"\s+", " ", regex=True).str.upper()


def clean(df: pd.DataFrame, roles: dict):
    """
    Returns (sales, returns, log, fixes). sales has: date, product, name, quantity, price,
    revenue, invoice, customer, country. log = one row per removal step.
    """
    def g(r):
        if not roles.get(r):
            return pd.Series(np.nan, index=df.index)
        col = df[roles[r]]
        return col.astype(object) if isinstance(col.dtype, pd.CategoricalDtype) else col
    w = pd.DataFrame({
        "date": to_date(g("date")),
        "pid_raw": g("product_id"), "name_raw": g("product_name"),
        "quantity": to_number(g("quantity")) if roles.get("quantity") else 1.0,
        "price": to_number(g("price")), "amount": to_number(g("amount")),
        "invoice": g("invoice").astype(str).str.strip().replace({"nan": np.nan}),
        "customer": g("customer").astype(str).str.strip().replace({"nan": np.nan, "": np.nan}),
        "doc_type": g("doc_type").astype(str), "country": g("country").astype(str).str.strip().replace({"nan": np.nan}),
    })
    log, fixes = [], []
    raw_rows = len(w)

    def amt(x):
        v = (x["quantity"].abs() * x["price"].abs()).fillna(x["amount"].abs())
        return float(v.sum())

    def step(name, bad, why, example_col="name_raw", count_value=True):
        nonlocal w
        bad = bad.fillna(False)
        removed = w[bad]
        ex = removed[example_col].dropna().astype(str).str.strip()
        ex = ex[ex.ne("") & ex.ne("nan")].value_counts().head(3).index.tolist()
        log.append({"Step": name, "Rows removed": int(bad.sum()), "Value removed": amt(removed) if count_value else 0.0,
                    "Why": why, "Examples": ", ".join(ex)[:120]})
        w = w[~bad]
        return removed

    # product key and display name
    pid = w["pid_raw"].astype(str).str.strip().str.upper().replace({"NAN": np.nan, "": np.nan})
    pname = w["name_raw"].astype(str).str.strip().replace({"nan": np.nan, "": np.nan})
    if roles.get("product_id"):
        w["product"] = pid
        if pid.notna().any():
            stray = (w["pid_raw"].astype(str) != w["pid_raw"].astype(str).str.strip().str.upper()) & pid.notna()
            if stray.sum():
                fixes.append(f"{stray.sum():,} product codes had stray spaces or lower case; matched them up.")
    else:
        w["product"] = _canon_name(pname).replace({"NAN": np.nan})
        variants = pname.dropna().nunique() - w["product"].dropna().nunique()
        if variants > 0:
            fixes.append(f"{variants:,} product names were the same item spelt differently "
                         "(spaces, capitals); merged them.")
    w["name"] = pname

    # 1. lines that are not transactions
    header_like = w["name_raw"].astype(str).map(_norm).isin({_norm(c) for c in df.columns}) | \
        w["pid_raw"].astype(str).map(_norm).isin({_norm(c) for c in df.columns})
    totals = pd.concat([w[c].astype(str) for c in ("name_raw", "pid_raw", "invoice", "customer")], axis=1) \
        .apply(lambda col: col.str.contains(TOTAL_WORDS, na=False)).any(axis=1)
    empty = w["product"].isna() & w["date"].isna()
    step("Blank lines, totals and repeated headers", header_like | totals | empty | w["product"].isna(),
         "Page-break headers, subtotal and grand total rows, and lines with no product", "name_raw",
         count_value=False)

    # 2. dates
    today = pd.Timestamp.today().normalize()
    step("Unreadable or impossible dates",
         w["date"].isna() | (w["date"] > today + pd.Timedelta(days=1)) | (w["date"].dt.year < 1990),
         "No readable date, a date in the future, or before 1990", "name_raw")

    # 3. duplicates
    key_cols = ["date", "product", "quantity", "price", "amount", "invoice", "customer"]
    step("Exact duplicates", w.duplicated(subset=key_cols), "The same line entered twice")

    # 4. not products (before returns: a discount line is negative but is not a return)
    text = w["name_raw"].fillna("").astype(str) + " " + w["pid_raw"].fillna("").astype(str)
    nonprod = text.str.contains(NOT_PRODUCT, na=False) | w["product"].isin(NOT_PRODUCT_CODES)
    step("Not products (tax, freight, fees, discounts)", nonprod,
         "Lines like GST, postage, freight, round-off, discounts, bank charges and samples")

    # 5. returns and cancellations (kept aside)
    doc_return = w["doc_type"].str.contains(RETURN_WORDS, na=False)
    inv_cancel = w["invoice"].astype(str).str.match(r"^[Cc]\d", na=False)
    neg = (w["quantity"] < 0) | ((w["quantity"].isna() | (w["quantity"] == 0)) & (w["amount"] < 0))
    returns = step("Returns and cancellations", doc_return | inv_cancel | neg,
                   "Negative quantities, credit notes and cancelled invoices. Kept aside for the returns view")
    returns = returns.assign(quantity=returns["quantity"].abs())

    # 6. accounting adjustments: invoice codes like A123 with no real quantity
    adj = w["invoice"].astype(str).str.match(r"^[Aa]\d", na=False)
    step("Accounting adjustments", adj, "Bad-debt or ledger adjustments (invoice starting with A), not sales")

    # 7. quantity / price
    if roles.get("quantity"):
        step("Zero or missing quantity", w["quantity"].isna() | (w["quantity"] <= 0),
             "No units sold on the line (notes, write-offs, blank quantities)")
    # unit price from line amount when missing
    need_price = w["price"].isna() & w["amount"].notna() & (w["quantity"] > 0)
    if need_price.any():
        w.loc[need_price, "price"] = w.loc[need_price, "amount"] / w.loc[need_price, "quantity"]
        fixes.append(f"{need_price.sum():,} lines had no unit price; worked it out as amount ÷ quantity.")
    has_money = w["price"].notna().any()
    if has_money:
        step("Free or zero-price lines", w["price"].isna() | (w["price"] <= 0), "Free samples, gifts and pricing errors")

    # 8. bulk orders keyed in by mistake and then cancelled
    if len(returns):
        r = returns.assign(qk=returns["quantity"].round(3))[["product", "qk"]].drop_duplicates()
        big_cut = max(1000.0, float(w["quantity"].quantile(0.999))) if len(w) else 1000.0
        cand = w.assign(qk=w["quantity"].round(3)).merge(r, on=["product", "qk"], how="left", indicator=True)
        mistake = (cand["_merge"].eq("both").to_numpy()) & (w["quantity"].to_numpy() >= big_cut)
        gone = step("Huge orders later cancelled", pd.Series(mistake, index=w.index),
                    f"Orders of {big_cut:,.0f}+ units with an identical cancellation: keyed in by mistake")
        if len(gone):   # their cancellations were not real returns either
            pairs = set(zip(gone["product"], gone["quantity"].round(3)))
            returns = returns[[(p, q) not in pairs for p, q in zip(returns["product"], returns["quantity"].round(3))]]

    w = w.copy()
    w["quantity"] = w["quantity"].astype(float)
    w["revenue"] = w["quantity"] * w["price"] if has_money else 0.0
    # one display name per product: its most common spelling
    if w["name"].notna().any():
        common = w.dropna(subset=["name"]).groupby("product")["name"].agg(lambda s: s.value_counts().index[0])
        w["name"] = w["product"].map(common).fillna(w["product"])
    else:
        w["name"] = w["product"]
    w["name"] = w["name"].astype(str).str.strip()
    # flag (don't remove) prices far from the product's usual price
    if has_money:
        med = w.groupby("product")["price"].transform("median")
        odd = ((w["price"] > 20 * med) | (w["price"] < med / 20)).sum()
        if odd:
            fixes.append(f"{odd:,} lines have a price 20 times above or below that product's usual price. "
                         "Kept, but worth checking.")
    if not roles.get("customer"):
        w["customer"] = np.nan
    sales = w[["date", "product", "name", "quantity", "price", "revenue", "invoice", "customer", "country"]] \
        .sort_values("date").reset_index(drop=True)
    head = {"Step": "Rows read", "Rows removed": 0, "Value removed": 0.0,
            "Why": f"{raw_rows:,} rows in the file", "Examples": ""}
    tail = {"Step": "Clean sales lines", "Rows removed": 0, "Value removed": 0.0,
            "Why": f"{len(sales):,} lines kept ({len(sales) / max(raw_rows, 1):.0%})", "Examples": ""}
    return sales, returns, pd.DataFrame([head] + log + [tail]), fixes


# ---------------------------------------------------------------------------
# 5. Answers from sales alone
# ---------------------------------------------------------------------------
def _monthly_units(sales: pd.DataFrame, end: pd.Timestamp) -> pd.DataFrame:
    """Product x month units, full months only."""
    m = sales.assign(month=sales["date"].dt.to_period("M").dt.to_timestamp()) \
        .pivot_table(index="month", columns="product", values="quantity", aggfunc="sum").fillna(0)
    full = m.index < end.to_period("M").to_timestamp() if end.day < 25 else m.index <= end.to_period("M").to_timestamp()
    m = m[full]
    if len(m):
        m = m.reindex(pd.date_range(m.index.min(), m.index.max(), freq="MS"), fill_value=0)
    return m


def _seasonal_index(total: pd.Series) -> pd.Series:
    if len(total) < 12:
        return pd.Series(1.0, index=range(1, 13))
    rel = total / total.rolling(12, center=True, min_periods=6).mean()
    idx = rel.groupby(rel.index.month).mean().reindex(range(1, 13)).fillna(1.0)
    return (0.5 * idx / idx.mean() + 0.5)


def _ses(h: pd.DataFrame, alpha=0.3) -> pd.Series:
    level = h.iloc[0]
    for _, row in h.iloc[1:].iterrows():
        level = alpha * row + (1 - alpha) * level
    return level


def forecast_test(m: pd.DataFrame, top: list) -> dict:
    """Backtest simple methods month by month (no peeking ahead), per product and on the total."""
    m = m[top] if top else m
    n_test = max(0, min(6, len(m) - 4))
    if n_test == 0:
        return {"table": pd.DataFrame(), "best": None, "months": 0}
    methods = {
        "Same as last month": lambda h, s, t: h.iloc[-1],
        "3-month average": lambda h, s, t: h.tail(3).mean(),
        "Exponential smoothing": lambda h, s, t: _ses(h.tail(12)),
    }
    if len(m) >= 16:
        methods["3-month average × seasonality"] = lambda h, s, t: (
            h.tail(3).div(s.loc[h.tail(3).index.month].values, axis=0).mean() * s.loc[t.month])
    rows = []
    for name, f in methods.items():
        e_prod = e_tot = act = act_tot = 0.0
        for i in range(len(m) - n_test, len(m)):
            hist, actual = m.iloc[:i], m.iloc[i]
            s = _seasonal_index(hist.sum(axis=1))
            pred = f(hist, s, m.index[i]).clip(lower=0)
            e_prod += (pred - actual).abs().sum()
            act += actual.sum()
            e_tot += abs(pred.sum() - actual.sum())
            act_tot += actual.sum()
        rows.append({"Method": name, "Error per product": e_prod / max(act, 1e-9),
                     "Error on total": e_tot / max(act_tot, 1e-9)})
    t = pd.DataFrame(rows).sort_values("Error per product").reset_index(drop=True)
    return {"table": t, "best": t.iloc[0]["Method"], "months": n_test, "methods": methods}


def analyse(sales: pd.DataFrame, returns: pd.DataFrame) -> dict:
    end = sales["date"].max()
    last12 = sales[sales["date"] > end - pd.DateOffset(months=12)]
    money = sales["revenue"].abs().sum() > 0
    val = "revenue" if money else "quantity"

    # trend
    monthly = sales.assign(month=sales["date"].dt.to_period("M").dt.to_timestamp()) \
        .groupby("month").agg(revenue=("revenue", "sum"), units=("quantity", "sum"), lines=("product", "size"))
    partial_last = end.day < 25

    # ABC on the last 12 months
    by = last12.groupby("product").agg(name=("name", "first"), units=("quantity", "sum"),
                                       revenue=("revenue", "sum"), lines=("quantity", "size")) \
        .sort_values(val, ascending=False)
    share = by[val] / max(by[val].sum(), 1e-9)
    before = share.cumsum() - share
    by["abc"] = np.where(before < 0.8, "A", np.where(before < 0.95, "B", "C"))
    by["share"] = share

    # XYZ: how steady monthly demand is (last 12 full months)
    m = _monthly_units(sales, end)
    m12 = m.tail(12)
    if len(m12) >= 3:
        cv = m12.std(ddof=1) / m12.mean().replace(0, np.nan)
        by["cv"] = cv.reindex(by.index)
        by["xyz"] = pd.cut(by["cv"].fillna(9), [-np.inf, 0.5, 1.0, np.inf], labels=["X", "Y", "Z"]).astype(str)
        grid = by.groupby(["abc", "xyz"]).agg(products=("name", "size"), value=(val, "sum")).reset_index()
        grid["share"] = grid["value"] / max(by[val].sum(), 1e-9)
    else:
        by["xyz"], grid = "?", pd.DataFrame()

    # forecast: test methods on the top products, then forecast next month with the winner
    top = by.head(200).index.intersection(m.columns).tolist()
    test = forecast_test(m, top)
    nxt = None
    if len(m) >= 3:
        target = m.index[-1] + pd.offsets.MonthBegin(1)
        s = _seasonal_index(m.sum(axis=1))
        f = test["methods"][test["best"]] if test["best"] else (lambda h, s_, t: h.tail(3).mean())
        fc = f(m, s, target).clip(lower=0)
        nxt = pd.DataFrame({"product": fc.index, "forecast": fc.round(0).values}) \
            .merge(by[["name", "abc", "xyz"]], left_on="product", right_index=True, how="left") \
            .assign(last_3_avg=m.tail(3).mean().reindex(fc.index).round(1).values) \
            .sort_values("forecast", ascending=False)
        nxt.attrs["month"] = target.strftime("%b %Y")

    # slowing and stopped products
    lastsale = sales.groupby("product").agg(name=("name", "first"), last_sale=("date", "max"),
                                            lines=("quantity", "size"), units=("quantity", "sum"),
                                            revenue=("revenue", "sum"))
    lastsale["days_since"] = (end - lastsale["last_sale"]).dt.days
    stopped = lastsale[(lastsale["days_since"] >= 90) & (lastsale["lines"] >= 5)].sort_values("revenue", ascending=False)
    slowing = pd.DataFrame()
    if len(m) >= 6:
        recent, prior = m.tail(3).mean(), m.iloc[-6:-3].mean()
        drop = (recent - prior) / prior.replace(0, np.nan)
        slowing = pd.DataFrame({"prior_3m": prior.round(1), "last_3m": recent.round(1), "change": drop}) \
            .query("prior_3m >= 5 and change <= -0.5").join(by[["name", "abc"]], how="left") \
            .sort_values("prior_3m", ascending=False)

    # returns
    ret = None
    if len(returns):
        rv = returns.assign(value=(returns["quantity"] * returns["price"].abs()).fillna(returns["amount"].abs()),
                            product=returns["product"])
        rp = rv.groupby("product").agg(returned_units=("quantity", "sum"), returned_value=("value", "sum"))
        sold = sales.groupby("product").agg(sold_units=("quantity", "sum"), name=("name", "first"))
        ret = rp.join(sold, how="left").assign(rate=lambda x: x.returned_units / x.sold_units.replace(0, np.nan)) \
            .sort_values("returned_value", ascending=False)

    # customers
    cust = None
    if sales["customer"].notna().mean() > 0.3:
        c = last12.dropna(subset=["customer"]).groupby("customer")[val].sum().sort_values(ascending=False)
        cust = {"customers": int(c.size), "top10_share": float(c.head(10).sum() / max(c.sum(), 1e-9)),
                "top": c.head(10)}

    return {"end": end, "start": sales["date"].min(), "money": money, "monthly": monthly,
            "partial_last": partial_last, "abc": by, "grid": grid, "test": test, "next": nxt,
            "stopped": stopped, "slowing": slowing, "returns": ret, "customers": cust,
            "revenue_12m": float(last12["revenue"].sum()), "units_12m": float(last12["quantity"].sum()),
            "products": int(sales["product"].nunique()), "lines": len(sales)}


# ---------------------------------------------------------------------------
# 6. Unlocking the reorder dashboard
# ---------------------------------------------------------------------------
STOCK_COLS = ["product_id", "product_name", "avg_price", "units_per_month", "on_hand_units", "unit_cost",
              "lead_time_days", "supplier"]


def stock_sheet(sales: pd.DataFrame, a: dict, n: int = 300) -> bytes:
    """Top-n products pre-filled; the user adds what is in stock, what it costs and how long it takes to get."""
    by = a["abc"].head(n)
    m = sales[sales["date"] > a["end"] - pd.Timedelta(days=90)].groupby("product")["quantity"].sum() / 3
    price = sales.groupby("product")["price"].median()
    sheet = pd.DataFrame({
        "product_id": by.index, "product_name": by["name"].values,
        "avg_price": price.reindex(by.index).round(2).values,
        "units_per_month": m.reindex(by.index).fillna(0).round(1).values,
        "on_hand_units": "", "unit_cost": "", "lead_time_days": "", "supplier": ""})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame({"Fill in your stock to unlock reorder alerts": [
            "1. In the 'stock' sheet, fill on_hand_units (what is on the shelf today) for each product you stock.",
            "2. Add unit_cost (what one unit costs you) and lead_time_days (days from ordering to receiving).",
            "   Missing cost? The tool assumes 60% of the selling price. Missing lead time? It assumes 14 days.",
            "3. Rows with no on_hand_units are left out of reorder planning. Don't change product_id.",
            "4. Upload this file in the sidebar under 'Add stock (optional)'."]}).to_excel(
            xw, sheet_name="Read me", index=False)
        sheet.to_excel(xw, sheet_name="stock", index=False)
        ws = xw.book["stock"]
        for col, width in zip("ABCDEFGH", [16, 42, 11, 15, 14, 11, 15, 18]):
            ws.column_dimensions[col].width = width
        xw.book["Read me"].column_dimensions["A"].width = 110
    return buf.getvalue()


def read_stock_sheet(files) -> pd.DataFrame:
    """The filled stock sheet back, tolerant of renamed headers and extra sheets."""
    frames = []
    for name, data in files:
        for label, df in _read_one(name, data):
            cols = {_norm(c): c for c in df.columns}
            if "product id" in cols or "sku" in cols or "product" in cols:
                frames.append(df.rename(columns={c: _norm(c).replace(" ", "_") for c in df.columns}))
    if not frames:
        return pd.DataFrame()
    st = max(frames, key=len)
    st = st.rename(columns={"sku": "product_id", "product": "product_id", "on_hand": "on_hand_units",
                            "stock": "on_hand_units", "closing_stock": "on_hand_units", "cost": "unit_cost",
                            "lead_time": "lead_time_days"})
    return st


def to_tables(sales: pd.DataFrame, stock: pd.DataFrame, end: pd.Timestamp) -> tuple[dict, list]:
    """
    Clean sales + the filled stock sheet -> the six tables upload.validate() expects.
    Only products with a stock figure are planned. Returns (raw tables, notes).
    """
    notes = []
    st = stock.copy()
    st["product_id"] = st["product_id"].astype(str).str.strip().str.upper()
    st["on_hand_units"] = to_number(st.get("on_hand_units", pd.Series(np.nan, index=st.index)))
    have = st["on_hand_units"].notna()
    if (~have).any():
        notes.append(f"{(~have).sum():,} products had no stock figure and are left out of reorder planning.")
    st = st[have]
    price = sales.groupby("product")["price"].median()
    cost = to_number(st["unit_cost"]) if "unit_cost" in st else pd.Series(np.nan, index=st.index)
    no_cost = cost.isna()
    if no_cost.any():
        notes.append(f"{no_cost.sum():,} products had no cost; assumed 60% of their usual selling price.")
    cost = cost.fillna(st["product_id"].map(price) * 0.6)
    lt = to_number(st["lead_time_days"]) if "lead_time_days" in st else pd.Series(np.nan, index=st.index)
    if lt.isna().any():
        notes.append(f"{lt.isna().sum():,} products had no lead time; assumed 14 days.")
    sup = st["supplier"].astype(str).str.strip().replace({"": np.nan, "nan": np.nan}) if "supplier" in st \
        else pd.Series(np.nan, index=st.index)
    names = sales.groupby("product")["name"].first()
    products = pd.DataFrame({"product_id": st["product_id"].values,
                             "product_name": st["product_id"].map(names).fillna(st.get("product_name", st["product_id"])).values,
                             "unit_cost": cost.values, "lead_time_days": lt.fillna(14).values,
                             "unit_price": st["product_id"].map(price).values, "supplier_id": sup.values})
    keep = set(products["product_id"])
    s = sales[sales["product"].isin(keep)]
    tables = {
        "products": products,
        "sales": pd.DataFrame({"order_date": s["date"].dt.date, "product_id": s["product"],
                               "quantity": s["quantity"], "unit_price": s["price"],
                               "invoice_no": s["invoice"].astype(str), "customer_id": s["customer"]}),
        "stock": pd.DataFrame({"product_id": products["product_id"], "on_hand_units": st["on_hand_units"].values,
                               "as_of_date": (end + pd.Timedelta(days=1)).date()}),
    }
    if sup.notna().any():
        ids = sorted(sup.dropna().unique())
        tables["suppliers"] = pd.DataFrame({"supplier_id": ids, "supplier_name": ids})
    return tables, notes
