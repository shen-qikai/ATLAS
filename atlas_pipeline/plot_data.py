"""Read-only Wafer catalog and bounded, shared cleaned-column cache for plotting."""

from collections import OrderedDict
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import threading

import numpy as np
import pandas as pd

from .config import PipelineProfile, load_pipeline_profiles
from .pipeline import GENERATED_FOLDERS, _inside, file_hash, parse_filename, scan_root, resolve_wafer_identity
from .state import read_snapshot


@dataclass(frozen=True)
class WaferRef:
    folder: Path
    category: str
    key: str
    profile: PipelineProfile
    record: dict
    status: str
    error: str = ""

    @property
    def token(self):
        return str(self.folder) + "|" + self.key

    @property
    def label(self):
        return f"{self.folder.name}_{self.record['lot']}_W{int(self.record['wafer']):02d}"

    @property
    def available(self):
        return self.status == "current"

    @property
    def tests(self):
        excluded = {self.profile.header_column, self.profile.pass_fail_column,
                    self.profile.x_column, self.profile.y_column, self.profile.bin_column,
                    "source_file", "ending_time"}
        return {name: meta for name, meta in self.record.get("metrics", {}).get("tests", {}).items()
                if name not in excluded and (meta.get("valid_count", 0) > 0
                                             or meta.get("lower") is not None or meta.get("upper") is not None)}


@dataclass
class PlotCatalog:
    root: Path
    wafers: list[WaferRef] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _inventory(folder, profile, snapshot):
    """Names/stat only: catalog refresh never parses/hashes historical CSV contents."""
    groups, blocked = {}, {}
    for lot in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
        if not lot.is_dir() or lot.name.startswith(".") or lot.name.casefold() in GENERATED_FOLDERS:
            continue
        _inside(lot, folder)
        for source in sorted(lot.iterdir(), key=lambda p: p.name.casefold()):
            if not source.is_file() or source.suffix.lower() != ".csv":
                continue
            if re.search(r"_(summary(?:_cleaning)?|onlydata)\.csv$", source.name, re.I):
                continue
            try:
                _inside(source, folder)
                parsed_lot, wafer = parse_filename(source.name, profile)
                if parsed_lot.casefold() != lot.name.casefold():
                    raise ValueError(f"文件Lot与目录不一致: {source.name}")
                stat = source.stat()
                key, _ = resolve_wafer_identity(snapshot, lot.name, wafer)
                groups.setdefault(key, {})[
                    source.relative_to(folder).as_posix()] = (stat.st_size, stat.st_mtime_ns)
            except (OSError, ValueError) as exc:
                blocked.setdefault(lot.name, []).append(str(exc))
    return groups, blocked


def _record_status(folder, profile, record, key, snapshot, groups, blocked):
    if record["lot"] in blocked:
        return "invalid", "; ".join(blocked[record["lot"]])
    if record.get("status") != "current":
        return record.get("status", "unprocessed"), record.get("error", "请先运行增量处理")
    if record.get("profile_signature") != profile.signature:
        return "stale", "产品配置已变化，请先运行增量处理"
    sources = groups.get(key, {})
    if not sources:
        return "missing", "原始输入缺失或数据标识已变化"
    registered = {stamp["relative_path"] for stamp in record.get("source_files", [])}
    if set(sources) != registered:
        return "stale", "输入集合已变化（新增/缺失 CP、RT 或分段文件），请先增量处理"
    for name, stamp in sources.items():
        cached = snapshot["files"].get(name, {})
        if stamp != (cached.get("size"), cached.get("mtime_ns")):
            return "stale", f"原始输入已变化: {name}，请先增量处理"
    try:
        cleaned = _inside(folder / record["cleaned_path"], folder)
        stat = cleaned.stat()
        if (stat.st_size, stat.st_mtime_ns) != (record.get("cleaned_size"), record.get("cleaned_mtime_ns")):
            return "stale", "清洗文件已变化，请先增量处理"
    except (OSError, ValueError, KeyError) as exc:
        return "missing", f"清洗文件不可用: {exc}"
    return "current", ""


@dataclass
class _CacheEntry:
    columns: dict
    metadata: dict
    bytes: int


class PlotDataService:
    def __init__(self, config_path=None, max_cache_bytes=256 * 1024 * 1024):
        self.config_path = Path(config_path or Path(__file__).resolve().parents[1] / "config/products.yaml")
        if max_cache_bytes < 0:
            raise ValueError("缓存容量不得为负数")
        self.max_cache_bytes = max_cache_bytes
        self._cache = OrderedDict()
        self._lock = threading.RLock()
        self.cache_bytes = 0
        self.csv_reads = 0
        self.cache_hits = 0

    def catalog(self, root):
        plan = scan_root(root, self.config_path, cache_only=True)
        catalog = PlotCatalog(plan.root)
        for product in plan.products:
            catalog.errors.extend(f"{product.category}/{product.folder.name}: {e}" for e in product.errors)
            if product.errors or not product.profile or not product.profile.enabled:
                continue
            try:
                groups, blocked = _inventory(product.folder, product.profile, product.snapshot)
                for key, record in product.snapshot["wafers"].items():
                    status, error = _record_status(product.folder, product.profile, record, key,
                                                   product.snapshot, groups, blocked)
                    catalog.wafers.append(WaferRef(product.folder, product.category, key,
                                                   product.profile, record, status, error))
                for key in groups.keys() - product.snapshot["wafers"].keys():
                    lot, wafer, stage = json.loads(key)
                    catalog.wafers.append(WaferRef(product.folder, product.category, key, product.profile,
                                                   {"lot": lot, "wafer": wafer, "stage": stage},
                                                   "unprocessed", "请先运行自动增量良率"))
                catalog.errors.extend(f"{product.folder.name}/{lot}: {'; '.join(errors)}"
                                      for lot, errors in blocked.items())
            except (OSError, ValueError, RuntimeError) as exc:
                catalog.errors.append(f"{product.folder.name}: {exc}")
        catalog.wafers.sort(key=lambda ref: (ref.category.casefold(), ref.folder.name.casefold(),
                                            ref.record["lot"].casefold(), int(ref.record["wafer"]),
                                            ref.record["stage"]))
        return catalog

    def validate_refs(self, refs, verify_hashes=False):
        """Recheck state/config/input membership before and after a plotting job."""
        if not refs:
            raise ValueError("请先选择 Wafer")
        if len({ref.folder for ref in refs}) != 1:
            raise ValueError("一次绘图只允许一个产品；同产品可以跨 Lot")
        profiles = load_pipeline_profiles(self.config_path)
        folder = refs[0].folder
        profile = profiles.get(folder.name.casefold())
        if not profile or not profile.enabled:
            raise ValueError("产品已禁用或配置已移除")
        snapshot = read_snapshot(folder)
        groups, blocked = _inventory(folder, profile, snapshot)
        for ref in refs:
            record = snapshot["wafers"].get(ref.key)
            if not record or not ref.available:
                raise ValueError(f"{ref.label}: 不可用，请刷新列表并先增量处理")
            status, error = _record_status(folder, profile, record, ref.key, snapshot, groups, blocked)
            if (status != "current" or profile.signature != ref.profile.signature
                    or record.get("cleaned_sha256") != ref.record.get("cleaned_sha256")):
                raise ValueError(f"{ref.label}: {error or '数据版本已变化，请刷新列表'}")
            if verify_hashes:
                for name in groups[ref.key]:
                    if file_hash(_inside(folder / name, folder)) != snapshot["files"][name]["sha256"]:
                        raise ValueError(f"{ref.label}: 输入内容已变化，请完整校验并增量处理")
                if file_hash(_inside(folder / record["cleaned_path"], folder)) != record["cleaned_sha256"]:
                    raise ValueError(f"{ref.label}: 清洗内容已变化，请增量处理")

    def clear_cache(self):
        with self._lock:
            self._cache.clear()
            self.cache_bytes = 0

    def load_columns(self, ref, columns):
        """Return independent frames so legacy plotters cannot mutate the shared cache.

        Caller validates the complete selection before/after its job. Each uncached
        file version is hash-verified; subsequent hits use its size/mtime stamp.
        """
        columns = list(dict.fromkeys(columns))
        if not ref.available or not columns:
            raise ValueError(f"{ref.label}: 请选择可用数据和列")
        path = _inside(ref.folder / ref.record["cleaned_path"], ref.folder)
        stat = path.stat()
        if (stat.st_size, stat.st_mtime_ns) != (ref.record["cleaned_size"], ref.record["cleaned_mtime_ns"]):
            raise ValueError(f"{ref.label}: 清洗文件已变化")
        key = (str(path), ref.record["cleaned_sha256"])
        with self._lock:
            entry = self._cache.get(key)
            missing = [col for col in columns if entry is None or col not in entry.columns]
            if missing:
                if file_hash(path) != key[1]:
                    raise ValueError(f"{ref.label}: 清洗文件内容与处理记录不一致")
                raw = pd.read_csv(path, usecols=missing, dtype=str, encoding="utf-8-sig",
                                  keep_default_na=False, skip_blank_lines=False)
                self.csv_reads += 1
                after = path.stat()
                if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                    raise ValueError(f"{ref.label}: 读取期间文件发生变化")
                if len(raw) < 5:
                    raise ValueError(f"{ref.label}: 清洗文件元信息或数据不完整")
                entry = _CacheEntry(dict(entry.columns) if entry else {},
                                    dict(entry.metadata) if entry else {}, 0)
                for col in missing:
                    values = raw[col].iloc[4:].reset_index(drop=True)
                    # Test map historically strips >< markers; probability retains
                    # its numeric-only behavior. Marker handling is a renderer option.
                    entry.columns[col] = values.copy()
                    entry.metadata[col] = raw[col].iloc[:4].tolist()
                entry.bytes = sum(int(s.memory_usage(deep=True)) for s in entry.columns.values())
                for old in list(self._cache):
                    if old[0] == str(path):
                        self.cache_bytes -= self._cache.pop(old).bytes
                if entry.bytes <= self.max_cache_bytes:
                    self._cache[key] = entry
                    self.cache_bytes += entry.bytes
                    while self.cache_bytes > self.max_cache_bytes:
                        _, removed = self._cache.popitem(last=False)
                        self.cache_bytes -= removed.bytes
            else:
                self.cache_hits += 1
                self._cache.move_to_end(key)
            return (pd.DataFrame({col: entry.columns[col].copy() for col in columns}),
                    {col: list(entry.metadata[col]) for col in columns})


def numeric_frame(frame, strip_markers=False):
    if strip_markers:
        frame = frame.apply(lambda s: s.astype(str).str.replace(r"[><]", "", regex=True))
    converted = frame.apply(pd.to_numeric, errors="coerce")
    return converted.where(np.isfinite(converted))


SHARED_PLOT_DATA = PlotDataService()
