"""读取产品信息表（CSV / XLSX / XLS），按编号查花宽、幅宽、花高。

真实表格往往带标题行、合并单元格、列名里带空格和单位，所以这里：
1. 先读成二维网格；
2. 在前 15 行里找「表头行」（至少两个单元格命中已知列名）；
3. 按列名打分挑出编号 / 花高列；花宽和幅宽**同时存在时取较大值**。

认得的列名：
    编号：对外编号 / 编号 / 型号 / 款号 / 货号 / model / no
    花宽、幅宽：花宽 / 幅宽 / 门幅 / P.W. / F.W.
    花高：花高 / P.H. / 循环
"""

from __future__ import annotations

import csv
import os
import re
import zipfile
import xml.etree.ElementTree as ET

MODEL_ALIASES = ["对外花型号", "对外编号", "对外型号", "编号", "型号", "款号", "货号",
                 "model", "no", "style"]
WIDTH_ALIASES = ["花宽", "幅宽", "门幅", "p.w.", "pw", "f.w.", "fw"]
HEIGHT_ALIASES = ["花高", "p.h.", "ph", "循环"]
BAD_WORDS = ("供应商", "工厂", "早期", "内部", "日期", "备注", "单价", "数量")


def _norm(text: str) -> str:
    text = str(text or "").strip().lower()
    return re.sub(r"[\s（）()／/、:：*]+", "", text)


def _to_float(value):
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace("，", "").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


# ------------------------------------------------------------------ 读成网格


def _grid_csv(path: str) -> list[list]:
    for encoding in ("utf-8-sig", "gb18030", "utf-8"):
        try:
            with open(path, encoding=encoding, newline="") as fh:
                return [row for row in csv.reader(fh)]
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法识别 CSV 编码：{path}")


def _grid_xlsx(path: str) -> list[list]:
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{ns}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
        sheets = sorted(n for n in z.namelist()
                        if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        if not sheets:
            raise ValueError(f"XLSX 里没有工作表：{path}")
        root = ET.fromstring(z.read(sheets[0]))
        rows: list[list] = []
        for row in root.iter(f"{ns}row"):
            cells: dict[int, str] = {}
            for c in row.findall(f"{ns}c"):
                idx = _col_idx(c.get("r"))
                v = c.find(f"{ns}v")
                value = v.text if v is not None else ""
                if c.get("t") == "s" and value not in ("", None):
                    value = shared[int(value)]
                elif c.get("t") == "inlineStr":
                    is_ = c.find(f"{ns}is")
                    value = "".join(t.text or "" for t in is_.iter()) if is_ is not None else ""
                cells[idx] = value
            if cells:
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return rows


def _grid_xls(path: str) -> list[list]:
    try:
        import xlrd
    except ImportError as exc:  # pragma: no cover
        raise ValueError(
            "读取 .xls 需要 xlrd，请先运行：pip install xlrd（或把表格另存为 .xlsx）"
        ) from exc
    book = xlrd.open_workbook(path)
    sheet = book.sheet_by_index(0)
    return [[sheet.cell_value(r, c) for c in range(sheet.ncols)]
            for r in range(sheet.nrows)]


def _col_idx(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref or "A").group(1)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


# ------------------------------------------------------------------ 定位表头


def _score(header: str, aliases: list[str]) -> int:
    h = _norm(header)
    if not h:
        return 0
    best = 0
    for alias in aliases:
        a = _norm(alias)
        if not a:
            continue
        if h == a:
            best = max(best, 100 + len(a))
        elif a in h:
            best = max(best, 50 + len(a))
    if best and any(w in header for w in BAD_WORDS):
        best -= 60
    return best


def _find_header(rows: list[list], limit: int = 15) -> int | None:
    best_row, best_hits = None, 0
    for r, row in enumerate(rows[:limit]):
        hits = sum(1 for cell in row
                   if _score(cell, MODEL_ALIASES) > 0
                   or _score(cell, WIDTH_ALIASES) > 0
                   or _score(cell, HEIGHT_ALIASES) > 0)
        if hits > best_hits:
            best_row, best_hits = r, hits
    return best_row if best_hits >= 2 else None


def find_table_near(folder: str) -> str | None:
    """在输入目录（或其上级）里自动找一份规格表。

    优先认「规格信息 / 产品信息 / 信息 / 报价」这类名字，其次任意 .xls/.xlsx/.csv。
    """
    if not folder:
        return None
    dirs = [folder]
    parent = os.path.dirname(os.path.abspath(folder.rstrip("\\/")))
    if parent and parent != folder:
        dirs.append(parent)
    exts = (".xls", ".xlsx", ".csv")
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        hits = [n for n in names if n.lower().endswith(exts)]
        preferred = [n for n in hits
                     if any(k in n for k in ("规格", "产品信息", "信息", "报价", "花型"))]
        for name in preferred + hits:
            if name.startswith("~$"):        # Excel 临时文件
                continue
            return os.path.join(d, name)
    return None


def _fmt_model(value) -> str:
    """型号统一成字符串：1359334.0 → 1359334。"""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value or "").strip()
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".")[0]
    return text


class ProductTable:
    """产品信息表；没有表格时也能用（型号取自文件名）。"""

    def __init__(self, rows: list[dict] | None = None):
        self.rows = rows or []

    @classmethod
    def load(cls, path: str) -> "ProductTable":
        ext = os.path.splitext(path)[1].lower()
        if ext == ".csv":
            grid = _grid_csv(path)
        elif ext == ".xlsx":
            grid = _grid_xlsx(path)
        elif ext == ".xls":
            grid = _grid_xls(path)
        else:
            raise ValueError(f"不支持的表格格式：{ext}")

        head_row = _find_header(grid)
        if head_row is None:
            return cls([])
        header = [str(c).strip() for c in grid[head_row]]
        rows: list[dict] = []
        for raw in grid[head_row + 1:]:
            row = {}
            for i, name in enumerate(header):
                if not name:
                    continue
                row[name] = raw[i] if i < len(raw) else ""
            if any(str(v).strip() for v in row.values()):
                rows.append(row)
        return cls(rows)

    # -------------------------------------------------------------- 查询

    def _model_col(self) -> str | None:
        headers = list(self.rows[0].keys()) if self.rows else []
        scored = [(_score(h, MODEL_ALIASES), h) for h in headers]
        scored = [(s, h) for s, h in scored if s > 0]
        return max(scored)[1] if scored else None

    def lookup(self, model: str) -> dict[str, float | str | None]:
        """按编号查花宽/幅宽/花高；花宽与幅宽同时有时取较大值。"""
        if not self.rows or not model:
            return {}
        headers = list(self.rows[0].keys())
        model_col = self._model_col()
        if model_col is None:
            return {}
        target = _norm(model)

        def extract(row: dict) -> dict:
            widths = []
            for h in headers:
                if _score(h, WIDTH_ALIASES) > 0:
                    v = _to_float(row.get(h))
                    if v is not None:
                        widths.append(v)
            height = None
            scored_h = sorted(((_score(h, HEIGHT_ALIASES), h) for h in headers), reverse=True)
            for s, h in scored_h:
                if s > 0:
                    height = _to_float(row.get(h))
                    if height is not None:
                        break
            return {
                "model": _fmt_model(row.get(model_col, "")),
                "width": max(widths) if widths else None,
                "height": height,
            }

        for row in self.rows:
            if _norm(row.get(model_col, "")) == target:
                return extract(row)

        best = None
        for row in self.rows:
            key = _norm(row.get(model_col, ""))
            if key and (key in target or target in key):
                if best is None or len(key) > len(_norm(best.get(model_col, ""))):
                    best = row
        return extract(best) if best is not None else {}
