"""CSV parsing and the existing coordinate/RT cleaning rules, without Tkinter."""

from dataclasses import dataclass
from datetime import datetime
import csv
import math
from pathlib import Path
import re

import pandas as pd
import numpy as np

from .config import PipelineProfile, digest


PASS_TOKENS = {"TRUE", "PASS", "T", "1"}
FAIL_TOKENS = {"FALSE", "FAIL", "F", "0"}
PROVENANCE_COLUMNS = {"source_file", "ending_time"}


@dataclass
class ParsedCsv:
    path: Path
    columns: list[str]
    metadata: list[list[str]]
    data: pd.DataFrame
    ending_time: datetime | None
    retest_run: int


@dataclass
class CleanedWafer:
    columns: list[str]
    metadata: list[list[str]]
    data: pd.DataFrame
    warnings: list[str]
    input_rows: int

    def to_csv(self, path: Path):
        columns = self.columns + ["source_file", "ending_time"]
        metadata = pd.DataFrame(
            [row + ["", ""] for row in self.metadata], columns=columns
        )
        pd.concat([metadata, self.data[columns]], ignore_index=True).to_csv(
            path, index=False, encoding="utf-8"
        )


def retest_run(filename: str) -> int:
    # Explicit suffix tokens only: do not interpret RT embedded in a product name.
    suffix = filename.split("#", 1)[-1]
    match = re.search(r"(?:^|[_#-])RT(\d*)(?=[_.-]|$)", suffix, re.IGNORECASE)
    return int(match.group(1) or "1") if match else 0


def _read_rows(path: Path):
    for encoding in ("utf-8-sig", "gbk"):
        try:
            with path.open("r", encoding=encoding, newline="") as source:
                return list(csv.reader(source))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path.name}: 不支持的 CSV 编码（需要 UTF-8 或 GBK）")


def parse_csv(path: Path, profile: PipelineProfile) -> ParsedCsv:
    rows = _read_rows(path)
    header_index = next(
        (i for i, row in enumerate(rows) if row and row[0].strip() == profile.header_column),
        None,
    )
    if header_index is None:
        raise ValueError(f"{path.name}: 未找到表头 {profile.header_column}")
    columns = [cell.strip() for cell in rows[header_index]]
    # A trailing delimiter creates an unnamed field, not a measurement. Only
    # discard trailing unnamed columns after checking metadata AND every record.
    unnamed = [i for i, name in enumerate(columns) if not name]
    for index in unnamed:
        for row_index in range(header_index + 1, len(rows)):
            row = rows[row_index]
            if index < len(row) and row[index].strip():
                raise ValueError(
                    f"{path.name}: 第 {index + 1} 列无列名，但第 {row_index + 1} 行含实际内容，"
                    "请人工确认测试项名称；未删除该列"
                )
    while columns and not columns[-1]:
        columns.pop()
    if not all(columns):
        positions = ", ".join(str(i + 1) for i, name in enumerate(columns) if not name)
        raise ValueError(f"{path.name}: 非末尾空列名（第 {positions} 列），请人工确认；未删除该列")
    if len(set(columns)) != len(columns):
        raise ValueError(f"{path.name}: 表头包含重复列名，请人工确认测试项")
    if PROVENANCE_COLUMNS.intersection(columns):
        raise ValueError(f"{path.name}: 原始文件包含保留的溯源列名")
    for required in (profile.pass_fail_column, profile.bin_column):
        if required not in columns:
            raise ValueError(f"{path.name}: 缺少必需列 {required}")
    if profile.cleaning_mode == "coord":
        for required in (profile.x_column, profile.y_column):
            if required not in columns:
                raise ValueError(f"{path.name}: 坐标清理缺少 {required}")

    body = []
    for row in rows[header_index + 1:]:
        if len(row) > len(columns) and any(cell.strip() for cell in row[len(columns):]):
            raise ValueError(f"{path.name}: 存在超出表头列数的数据")
        body.append(row[:len(columns)] + [""] * max(0, len(columns) - len(row)))
    pf_index = columns.index(profile.pass_fail_column)
    # Numeric limits/test IDs in metadata must not be mistaken for numeric Pass/Fail.
    strong_flags = (PASS_TOKENS | FAIL_TOKENS) - {"0", "1"}
    first_data = next(
        (i for i, row in enumerate(body) if row[pf_index].strip().upper() in strong_flags),
        4 if len(body) > 4 and body[4][pf_index].strip().upper() in PASS_TOKENS | FAIL_TOKENS else None,
    )
    if first_data != 4:
        raise ValueError(
            f"{path.name}: 需要表头后4行单位/规格等信息，再接测试数据；检测到 {first_data} 行"
        )
    metadata = body[:4]
    data = pd.DataFrame(
        [row for row in body[4:] if any(cell.strip() for cell in row)], columns=columns
    )
    flags = data[profile.pass_fail_column].str.strip().str.upper()
    if not flags.isin(PASS_TOKENS | FAIL_TOKENS).all():
        raise ValueError(f"{path.name}: 数据区存在无效 Pass/Fail 值，不能安全判定记录")
    end_time = None
    time_pattern = r"\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{1,2}:\d{1,2}"
    for row in rows[:header_index]:
        row_text = " ".join(row)
        if "ending time" in row_text.lower() or "end time" in row_text.lower():
            match = re.search(time_pattern, row_text)
            if match:
                end_time = pd.to_datetime(match.group()).to_pydatetime()
    return ParsedCsv(path, columns, metadata, data, end_time, retest_run(path.name))


def merge_and_clean(paths: list[Path], profile: PipelineProfile, source_root: Path | None = None) -> CleanedWafer:
    parsed = [parse_csv(path, profile) for path in paths]
    if not parsed:
        raise ValueError("Wafer 没有输入文件")
    first = parsed[0]
    first_specs = {col: [row[i] for row in first.metadata] for i, col in enumerate(first.columns)}
    for source in parsed[1:]:
        source_specs = {col: [row[i] for row in source.metadata] for i, col in enumerate(source.columns)}
        if source_specs != first_specs:
            raise ValueError(f"{source.path.name}: 同一 Wafer 的测试项/单位/规格信息不一致")

    warnings = []
    if all(source.ending_time is not None for source in parsed):
        parsed.sort(key=lambda source: (source.ending_time, source.retest_run, source.path.name.casefold()))
    else:
        parsed.sort(key=lambda source: (
            source.retest_run, source.ending_time or datetime.min, source.path.name.casefold()
        ))
        if len(parsed) > 1:
            warnings.append("部分 Ending Time 缺失，按明确的初测/RT序号排序")

    if profile.cleaning_mode == "coord":
        # Require usable coordinates rather than reporting success without deduplication.
        for source in parsed:
            for col in (profile.x_column, profile.y_column):
                numeric = pd.to_numeric(source.data[col], errors="coerce")
                if not np.isfinite(numeric).all():
                    raise ValueError(f"{source.path.name}: {col} 含空值或非数字，无法核验 die")
                source.data[col] = numeric
        for i, left in enumerate(parsed):
            for right in parsed[i + 1:]:
                ambiguous = left.retest_run == right.retest_run and (
                    left.ending_time is None or right.ending_time is None
                    or left.ending_time == right.ending_time
                )
                if ambiguous:
                    left_coords = set(zip(left.data[profile.x_column], left.data[profile.y_column]))
                    right_coords = set(zip(right.data[profile.x_column], right.data[profile.y_column]))
                    if left_coords & right_coords:
                        raise ValueError("同一测试轮次的文件坐标重叠且时间顺序不明确，不能猜测最终记录")

    frames = []
    source_names = {source.path: source.path.relative_to(source_root).as_posix() if source_root else source.path.name
                    for source in parsed}
    for source in parsed:
        frame = source.data.copy()
        frame["source_file"] = source_names[source.path]
        frame["ending_time"] = source.ending_time.isoformat(sep=" ") if source.ending_time else ""
        frames.append(frame)
    merged = pd.concat(frames, ignore_index=True)
    input_rows = len(merged)
    if profile.cleaning_mode == "coord":
        merged = merged.drop_duplicates([profile.x_column, profile.y_column], keep="last")
    elif any(source.retest_run for source in parsed):
        # Same rule as merge_cleaning: retain every RT record and initial-test passes.
        rt_names = {source_names[source.path] for source in parsed if source.retest_run}
        initial_pass = ~merged[profile.pass_fail_column].str.strip().str.upper().isin(FAIL_TOKENS)
        merged = merged[merged["source_file"].isin(rt_names) | initial_pass]
    if merged.empty:
        raise ValueError("清理后没有有效测试记录")
    return CleanedWafer(first.columns, first.metadata, merged.reset_index(drop=True), warnings, input_rows)


def _number(value):
    number = pd.to_numeric(value, errors="coerce")
    return float(number) if pd.notna(number) and math.isfinite(float(number)) else None


def calculate_metrics(cleaned: CleanedWafer, profile: PipelineProfile) -> dict:
    data = cleaned.data
    bins = data[profile.bin_column].astype(str).str.strip().str.replace(r"\.0+$", "", regex=True)
    valid_bins = bins[~bins.str.casefold().isin(["", "nan", "none"])]
    bin_counts = {str(key): int(value) for key, value in valid_bins.value_counts().items()}
    count_method = "unique_coordinates"
    if profile.x_column in data and profile.y_column in data:
        coords = data[[profile.x_column, profile.y_column]].apply(pd.to_numeric, errors="coerce")
        coords = coords.where(np.isfinite(coords))
        effective_die_count = len(coords.dropna().drop_duplicates())
        if coords.isna().any().any():
            cleaned.warnings.append("部分坐标无效，有效die数量仅统计可识别坐标")
    else:
        effective_die_count = len(valid_bins)
        count_method = "valid_bin_records"
        cleaned.warnings.append("无坐标：有效die数量采用有效BIN记录数，无法校验重复die")
    expected = profile.expected_die_count
    delta = effective_die_count - expected if expected is not None else None
    count_status = "未配置期望值" if delta is None else (
        "容差内" if abs(delta) <= profile.die_count_tolerance else "数量偏差警告"
    )
    if count_status == "数量偏差警告":
        cleaned.warnings.append(f"有效die数量 {effective_die_count}，期望 {expected}，差值 {delta}")
    if len(valid_bins) != len(data):
        cleaned.warnings.append(f"{len(data) - len(valid_bins)} 条记录缺少BIN")
    if profile.cleaning_mode == "rt" and effective_die_count < len(data):
        cleaned.warnings.append("RT条件清理保留了重复坐标记录：BIN良率分母为记录数，测试项失效率按最后同坐标die统计")

    # Test failure rates count final identifiable dies, not repeated RT records.
    die_data = data
    if profile.x_column in data and profile.y_column in data:
        valid_coordinates = coords.notna().all(axis=1)
        die_data = data.loc[valid_coordinates].copy()
        die_data[[profile.x_column, profile.y_column]] = coords.loc[valid_coordinates]
        die_data = die_data.drop_duplicates([profile.x_column, profile.y_column], keep="last")
    else:
        cleaned.warnings.append("测试项失效率无坐标：以清洗后记录作为die，无法识别重复die")
    wafer_total = len(die_data)
    tests = {}
    for i, column in enumerate(cleaned.columns):
        values = pd.to_numeric(data[column], errors="coerce")
        # Nonfinite numeric values are missing, never valid measurements.
        values = values.where(np.isfinite(values))
        valid = values.dropna()
        lower, upper = (_number(cleaned.metadata[index][i]) for index in (1, 2))
        has_spec = lower is not None or upper is not None
        if lower is not None and upper is not None and lower > upper:
            raise ValueError(f"{column}: 下限大于上限")
        passed_count = None
        if has_spec:
            inside = pd.Series(True, index=valid.index)
            if lower is not None:
                inside &= valid >= lower
            if upper is not None:
                inside &= valid <= upper
            passed_count = int(inside.sum())
        die_values = pd.to_numeric(die_data[column], errors="coerce")
        die_values = die_values.where(np.isfinite(die_values)).dropna()
        failed_die_count = None
        if has_spec:
            failed = pd.Series(False, index=die_values.index)
            if lower is not None:
                failed |= die_values < lower
            if upper is not None:
                failed |= die_values > upper
            failed_die_count = int(failed.sum())
        value_sum = float(valid.sum()) if len(valid) else None
        value_mean = float(valid.mean()) if len(valid) else None
        if any(value is not None and not math.isfinite(value) for value in (value_sum, value_mean)):
            raise ValueError(f"{column}: 数值聚合溢出，不能保存可靠指标")
        tests[column] = {
            "unit": cleaned.metadata[0][i], "lower": lower, "upper": upper,
            "valid_count": len(valid), "total_count": len(data),
            "passed_count": passed_count,
            "failed_die_count": failed_die_count,
            "valid_die_count": len(die_values),
            "missing_die_count": wafer_total - len(die_values),
            "sum": value_sum,
            "mean": value_mean,
        }
    specs = {column: [value["unit"], value["lower"], value["upper"]] for column, value in tests.items()}
    return {
        "record_count": len(data), "input_record_count": cleaned.input_rows,
        "wafer_total_die_count": wafer_total,
        "effective_die_count": effective_die_count, "count_method": count_method,
        "valid_bin_count": len(valid_bins), "expected_die_count": expected,
        "die_delta": delta, "die_count_status": count_status,
        "bin_counts": bin_counts, "good_count": sum(bin_counts.get(b, 0) for b in profile.good_bins),
        "denominator": profile.denominator, "spec_version": digest(specs)[:12],
        "tests": tests, "warnings": cleaned.warnings,
    }
