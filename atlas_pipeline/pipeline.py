"""Root discovery, Wafer-level incremental execution and recovery."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from .cleaning import calculate_metrics, merge_and_clean
from .config import PipelineProfile, digest, load_pipeline_profiles
from .state import ProductState, product_lock, read_snapshot


CATEGORIES = {"ldo", "dcdc", "load_switch"}
GENERATED_FOLDERS = {".atlas", "plots", "cleaned", "summary_data", "summary_cleaning_data", "summary_clean_onlydata"}
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def file_hash(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def wafer_key(lot: str, wafer: str, stage: str) -> str:
    return json.dumps([lot, wafer, stage], ensure_ascii=False)


def resolve_wafer_identity(snapshot, lot, wafer):
    """Reuse a unique legacy key without inferring physical CP/FT stage."""
    matches = [(key, record) for key, record in snapshot["wafers"].items()
               if record["lot"].casefold() == lot.casefold() and int(record["wafer"]) == int(wafer)]
    if len(matches) > 1:
        raise ValueError(f"{lot}/W{wafer}: 存在多套历史数据标识，请人工分开目录，不能自动合并")
    if matches:
        key, record = matches[0]
        return key, record["stage"]
    return wafer_key(lot, wafer, "DATA"), "DATA"


@dataclass(frozen=True)
class FileStamp:
    relative_path: str
    size: int
    mtime_ns: int
    sha256: str


@dataclass
class WaferTask:
    key: str
    lot: str
    wafer: str
    stage: str
    files: list[FileStamp]
    source_signature: str
    action: str
    error: str = ""


@dataclass
class ProductPlan:
    folder: Path
    category: str
    profile: PipelineProfile | None
    tasks: list[WaferTask] = field(default_factory=list)
    unavailable: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)
    reports_needed: bool = False


@dataclass
class RootPlan:
    root: Path
    products: list[ProductPlan]


@dataclass
class RunResult:
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    unavailable: int = 0
    reports: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    log_path: str = ""


def validate_root(root: str | Path) -> Path:
    if not str(root).strip():
        raise ValueError("请选择数据根目录")
    path = Path(root).resolve()
    if not path.is_dir() or path == Path(path.anchor):
        raise ValueError("请选择存在的、独立的数据根目录，不要选择磁盘根目录")
    if path == REPOSITORY_ROOT or REPOSITORY_ROOT in path.parents:
        raise ValueError("数据及报表必须存放在 ATLAS Git 仓库之外")
    return path


def _inside(path: Path, parent: Path):
    resolved = path.resolve()
    if parent not in resolved.parents:
        raise ValueError(f"目录或文件指向数据范围之外: {path}")
    return resolved


def _fingerprint(path: Path, product: Path, cached: dict, verify_hashes: bool):
    _inside(path, product)
    stat = path.stat()
    relative = path.relative_to(product).as_posix()
    previous = cached.get(relative, {})
    if not verify_hashes and previous.get("size") == stat.st_size and previous.get("mtime_ns") == stat.st_mtime_ns:
        sha = previous["sha256"]
    else:
        sha = file_hash(path)
        after = path.stat()
        if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            raise ValueError(f"文件仍在写入，请复制完成后再运行: {path.name}")
    return FileStamp(relative, stat.st_size, stat.st_mtime_ns, sha)


def parse_filename(filename: str, profile: PipelineProfile):
    # Canonical filenames may carry dotted Lot IDs. Other formats use the product Regex.
    canonical = rf"^{re.escape(profile.naming.name)}_(?P<lot>[^_#]+)_(?P<wafer>\d+)#(?:_(?P<suffix>.*))?\.csv$"
    match = re.match(canonical, filename, re.IGNORECASE)
    match = match or profile.naming.compile_regex().search(filename)
    if not match:
        raise ValueError(f"UNMATCHED: {filename}")
    lot, wafer = match.group("lot"), match.group("wafer")
    if not lot or not wafer or not wafer.isdigit():
        raise ValueError(f"INVALID: {filename}（Lot必须非空，Wafer必须为数字）")
    number = int(wafer)
    if profile.naming.wafer_min is not None and number < profile.naming.wafer_min:
        raise ValueError(f"INVALID: {filename}（Wafer超出配置范围）")
    if profile.naming.wafer_max is not None and number > profile.naming.wafer_max:
        raise ValueError(f"INVALID: {filename}（Wafer超出配置范围）")
    if any(character in lot for character in '/\\:#') or lot in (".", ".."):
        raise ValueError(f"INVALID: {filename}（Lot包含非法路径字符）")
    # CP/FT tokens are naming conventions, not authoritative test-stage metadata.
    return lot, str(number)


def _report_paths(folder: Path):
    return [folder / f"{folder.name}_{suffix}.xlsx" for suffix in ("BIN良率", "测试项失效率", "测试项平均值")]


def scan_product(folder: Path, category: str, profile: PipelineProfile,
                 force=False, verify_hashes=False, cache_only=False) -> ProductPlan:
    snapshot = read_snapshot(folder)
    plan = ProductPlan(folder, category, profile, snapshot=snapshot)
    if cache_only:
        plan.reports_needed = True
        return plan
    groups = {}
    blocked_lots = {}
    for lot_folder in sorted(folder.iterdir(), key=lambda path: path.name.casefold()):
        if not lot_folder.is_dir() or lot_folder.name.casefold() in GENERATED_FOLDERS or lot_folder.name.startswith("."):
            continue
        _inside(lot_folder, folder)
        lot_errors = []
        for source in sorted(lot_folder.iterdir(), key=lambda path: path.name.casefold()):
            if not source.is_file() or source.suffix.lower() != ".csv":
                continue
            if re.search(r"_(summary(?:_cleaning)?|onlydata)\.csv$", source.name, re.IGNORECASE):
                continue
            try:
                lot, wafer = parse_filename(source.name, profile)
                if lot.casefold() != lot_folder.name.casefold():
                    raise ValueError(f"{source.name}: 文件Lot {lot} 与Lot文件夹 {lot_folder.name} 不一致")
                lot = lot_folder.name
                stamp = _fingerprint(source, folder, snapshot["files"], verify_hashes)
                key, identity = resolve_wafer_identity(snapshot, lot, wafer)
                groups.setdefault(key, {"lot": lot, "wafer": wafer, "identity": identity, "files": []})["files"].append(stamp)
            except (OSError, ValueError) as exc:
                lot_errors.append(str(exc))
        if lot_errors:
            blocked_lots[lot_folder.name] = "; ".join(lot_errors)
            plan.errors.extend(f"{lot_folder.name}: {error}" for error in lot_errors)

    for key, group in groups.items():
        stamps = group["files"]
        source_signature = digest([(stamp.relative_path, stamp.sha256) for stamp in stamps])
        previous = snapshot["wafers"].get(key)
        action, error = "NEW", ""
        previous_sources = {source["relative_path"] for source in previous.get("source_files", [])} if previous else set()
        removed_sources = previous_sources - {stamp.relative_path for stamp in stamps}
        if group["lot"] in blocked_lots:
            action, error = "INVALID", blocked_lots[group["lot"]]
        elif removed_sources:
            action = "INVALID"
            error = "已登记的部分输入消失，拒绝用残缺数据重算: " + ", ".join(sorted(removed_sources))
        elif force:
            action = "FORCE"
        elif previous:
            if previous.get("status") != "current":
                action = "RETRY"
            elif previous.get("source_signature") != source_signature:
                action = "INPUT_CHANGED"
            elif previous.get("profile_signature") != profile.signature:
                action = "CONFIG_CHANGED"
            elif (profile.cleaning_mode == "rt"
                  and previous["metrics"]["record_count"] != previous["metrics"]["effective_die_count"]
                  and "wafer_total_die_count" not in previous["metrics"]):
                action = "METRICS_UPGRADE"
            else:
                cleaned = folder / previous["cleaned_path"]
                if not cleaned.exists():
                    action = "OUTPUT_MISSING"
                else:
                    _inside(cleaned, folder)
                    stat = cleaned.stat()
                    if (stat.st_size, stat.st_mtime_ns) != (previous.get("cleaned_size"), previous.get("cleaned_mtime_ns")):
                        action = "OUTPUT_CHANGED"
                    elif verify_hashes and file_hash(cleaned) != previous.get("cleaned_sha256"):
                        action = "OUTPUT_CHANGED"
                    else:
                        action = "SKIP"
        plan.tasks.append(WaferTask(key, group["lot"], group["wafer"], group["identity"],
                                   stamps, source_signature, action, error))

    for key, previous in snapshot["wafers"].items():
        if key not in groups:
            updated = dict(previous)
            updated["key"] = key
            updated["status"] = "failed" if previous["lot"] in blocked_lots else "missing"
            updated["error"] = blocked_lots.get(previous["lot"], "输入文件已消失或数据标识已改变，旧指标不再纳入汇总")
            plan.unavailable.append(updated)
    changed_unavailable = any(
        record["status"] != snapshot["wafers"][record["key"]].get("status")
        or record["error"] != snapshot["wafers"][record["key"]].get("error")
        for record in plan.unavailable
    )
    plan.reports_needed = (
        any(task.action != "SKIP" for task in plan.tasks) or changed_unavailable
        or snapshot["generation"] != snapshot["report_generation"]
        or not all(path.is_file() for path in _report_paths(folder))
    )
    plan.tasks.sort(key=lambda task: (task.lot.casefold(), int(task.wafer), task.stage))
    return plan


def scan_root(root: str | Path, config_path: str | Path, force=False,
              verify_hashes=False, cache_only=False) -> RootPlan:
    root_path = validate_root(root)
    profiles = load_pipeline_profiles(config_path)
    categories = [folder for folder in root_path.iterdir() if folder.is_dir() and folder.name.casefold() in CATEGORIES]
    if not categories:
        raise ValueError("根目录下未找到 LDO、DCDC 或 Load_switch 文件夹")
    plans = []
    for category in sorted(categories, key=lambda path: path.name.casefold()):
        _inside(category, root_path)
        for folder in sorted(category.iterdir(), key=lambda path: path.name.casefold()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            resolved_folder = _inside(folder, root_path)
            if resolved_folder == REPOSITORY_ROOT or REPOSITORY_ROOT in resolved_folder.parents:
                raise ValueError("产品数据目录不得位于 ATLAS Git 仓库内")
            profile = profiles.get(folder.name.casefold())
            if not profile or not profile.enabled:
                plans.append(ProductPlan(folder, category.name, profile,
                                         errors=[f"产品 {folder.name} 未配置或 automation.enabled 为 false"]))
                continue
            try:
                plans.append(scan_product(folder, category.name, profile, force, verify_hashes, cache_only))
            except (OSError, RuntimeError, ValueError) as exc:
                plans.append(ProductPlan(folder, category.name, profile, errors=[str(exc)]))
    if not plans:
        raise ValueError("分类目录下没有产品文件夹")
    return RootPlan(root_path, plans)


def _assert_stamps(folder: Path, stamps: list[FileStamp]):
    for stamp in stamps:
        source = folder / stamp.relative_path
        _inside(source, folder)
        stat = source.stat()
        if (stat.st_size, stat.st_mtime_ns) != (stamp.size, stamp.mtime_ns):
            raise ValueError(f"文件在扫描后发生变化: {stamp.relative_path}，请复制完成后重试")


def _base_record(task: WaferTask, folder: Path, category: str):
    return {"product": folder.name, "category": category, "lot": task.lot,
            "wafer": task.wafer, "stage": task.stage}


def _process_task(folder: Path, task: WaferTask, profile: PipelineProfile, category: str):
    _assert_stamps(folder, task.files)
    cleaned = merge_and_clean([folder / stamp.relative_path for stamp in task.files], profile)
    metrics = calculate_metrics(cleaned, profile)
    _assert_stamps(folder, task.files)
    output_folder = folder / task.lot / "summary_cleaning_data"
    output_folder.mkdir(exist_ok=True)
    _inside(output_folder, folder)
    output = output_folder / f"{folder.name}_{task.lot}_{task.wafer}#_summary_cleaning.csv"
    with tempfile.NamedTemporaryFile(dir=output_folder, suffix=".tmp", delete=False) as staging:
        staging_path = Path(staging.name)
    try:
        cleaned.to_csv(staging_path)
        _assert_stamps(folder, task.files)
        os.replace(staging_path, output)
    finally:
        staging_path.unlink(missing_ok=True)
    stat = output.stat()
    return {
        **_base_record(task, folder, category), "status": "current", "error": "",
        "source_files": [asdict(stamp) for stamp in task.files],
        "source_signature": task.source_signature, "profile_signature": profile.signature,
        "metrics": metrics, "updated_at": timestamp(),
        "cleaned_path": output.relative_to(folder).as_posix(),
        "cleaned_size": stat.st_size, "cleaned_mtime_ns": stat.st_mtime_ns,
        "cleaned_sha256": file_hash(output),
    }


def run_pipeline(root: str | Path, config_path: str | Path, force=False,
                 verify_hashes=False, rebuild_reports=False, progress=None) -> RunResult:
    # Initial discovery is read-only; every product is scanned again under its OS lock.
    initial = scan_root(root, config_path, cache_only=True)
    result = RunResult()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    log_folder = initial.root / "_atlas_runs"
    log_folder.mkdir(exist_ok=True)
    _inside(log_folder, initial.root)
    result.log_path = str(log_folder / f"{run_id}.log")
    with Path(result.log_path).open("w", encoding="utf-8") as logfile:
        def emit(message):
            line = f"{timestamp()} {message}"
            logfile.write(line + "\n")
            logfile.flush()
            if progress:
                progress(line)

        emit(f"START root={initial.root} force={force} verify_hashes={verify_hashes} cache_only={rebuild_reports}")
        for initial_product in initial.products:
            folder = initial_product.folder
            product_lines = []
            def product_emit(message):
                product_lines.append(message)
                emit(f"[{initial_product.category}/{folder.name}] {message}")

            try:
                if initial_product.profile is None or not initial_product.profile.enabled:
                    raise ValueError("; ".join(initial_product.errors))
                with product_lock(folder):
                    plan = scan_product(folder, initial_product.category, initial_product.profile,
                                        force, verify_hashes, rebuild_reports)
                    state = ProductState(folder)
                    try:
                        if not rebuild_reports:
                            state.save_scan_errors(plan.errors)
                        for error in plan.errors:
                            product_emit(f"VALIDATION_FAILED {error}")
                            result.errors.append(f"{folder.name}: {error}")
                        if plan.errors:
                            result.failed += len(plan.errors)
                        for task in plan.tasks:
                            product_emit(f"{task.action} Lot={task.lot} Wafer={task.wafer}")
                            for stamp in task.files:
                                product_emit(f"  input={stamp.relative_path} sha256={stamp.sha256}")
                            if task.action == "SKIP":
                                state.save_files(task.files)
                                result.skipped += 1
                                continue
                            try:
                                if task.action == "INVALID":
                                    raise ValueError(task.error)
                                record = _process_task(folder, task, plan.profile, plan.category)
                                state.save_wafer(task.key, record)
                                state.save_files(task.files)
                                result.processed += 1
                                product_emit(f"SUCCESS cleaned={record['cleaned_path']} records={record['metrics']['record_count']} valid_die={record['metrics']['effective_die_count']}")
                                for warning in record["metrics"]["warnings"]:
                                    product_emit(f"WARNING {warning}")
                            except Exception as exc:
                                record = dict(plan.snapshot["wafers"].get(task.key, _base_record(task, folder, plan.category)))
                                record.update(status="failed", error=str(exc), attempted_at=timestamp())
                                state.save_wafer(task.key, record)
                                result.failed += 1
                                result.errors.append(f"{folder.name}/{task.lot}/W{task.wafer}: {exc}")
                                product_emit(f"FAILED {exc}; old results excluded from reports")
                        for record in plan.unavailable:
                            key = record["key"]
                            previous = plan.snapshot["wafers"][key]
                            if record["status"] != previous.get("status") or record["error"] != previous.get("error"):
                                record["attempted_at"] = timestamp()
                                state.save_wafer(key, record)
                            result.unavailable += 1
                            product_emit(f"{record['status'].upper()} Lot={record['lot']} Wafer={record['wafer']}: {record['error']}")
                        snapshot = read_snapshot(folder)
                        if rebuild_reports or plan.reports_needed or snapshot["generation"] != snapshot["report_generation"]:
                            from .reports import export_reports
                            paths = export_reports(folder, snapshot["wafers"], snapshot["scan_errors"])
                            state.reports_exported(snapshot["generation"])
                            result.reports.extend(str(path) for path in paths)
                            product_emit("REPORTS_UPDATED " + ", ".join(path.name for path in paths))
                        else:
                            product_emit("REPORTS_UNCHANGED")
                    finally:
                        state.close()
                    product_logs = folder / ".atlas" / "logs"
                    product_logs.mkdir(exist_ok=True)
                    _inside(product_logs, folder)
                    (product_logs / f"{run_id}.log").write_text(
                        f"timestamp: {timestamp()}\nroot: {initial.root}\n" + "\n".join(product_lines) + "\n",
                        encoding="utf-8",
                    )
            except Exception as exc:
                result.failed += 1
                result.errors.append(f"{folder.name}: {exc}")
                product_emit(f"PRODUCT_FAILED {exc}")
        emit(f"END processed={result.processed} skipped={result.skipped} failed={result.failed} unavailable={result.unavailable} reports={len(result.reports)}")
    return result
