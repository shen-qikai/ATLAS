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
from .archives import discover_csvs, is_ignored, lot_folders
from .input_changes import (archive_previous, archive_signature, describe_change, entries, file_signature,
                            InputReplacementApproval, rename_pairs, restore_previous,
                            revised_archives, validate_approval)


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
    input_change: dict = field(default_factory=dict)


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
    pending_files: list[dict] = field(default_factory=list)


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


def cleaned_relative_path(product: str, lot: str, wafer: str) -> Path:
    """One human-readable cleaned filename, independent of raw naming conventions."""
    for value in (product, lot):
        if (not value or value in (".", "..") or value.endswith((".", " "))
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)):
            raise ValueError("产品或Lot包含不适合输出文件名的字符，请检查目录与命名规则")
    return Path(lot) / "summary_cleaning_data" / f"{product}_{lot}_W{int(wafer):02d}_summary_cleaning.csv"


def scan_product(folder: Path, category: str, profile: PipelineProfile,
                 force=False, verify_hashes=False, cache_only=False, *, strict_keys=None) -> ProductPlan:
    snapshot = read_snapshot(folder)
    plan = ProductPlan(folder, category, profile, snapshot=snapshot)
    if cache_only:
        plan.reports_needed = True
        return plan
    groups = {}
    blocked_lots = {}
    member_issues = {}
    strict_keys = set(strict_keys or [])
    source_owners = {}
    for key, previous in snapshot["wafers"].items():
        for source in previous.get("source_files", []):
            source_owners.setdefault(source["relative_path"], set()).add(key)
    for lot_folder in lot_folders(folder):
        issues = []
        sources, lot_errors = discover_csvs(folder, lot_folder, snapshot, verify_hashes, member_issues=issues)
        for issue in issues:
            owners = source_owners.get(issue["relative_path"], set())
            if len(owners) == 1:
                member_issues.setdefault(next(iter(owners)), []).append(issue)
            else:
                lot_errors.append(issue["reason"])
        for source in sources:
            try:
                relative = source.relative_to(folder).as_posix()
                if is_ignored(source, folder, snapshot):
                    plan.pending_files.append({"relative_path": relative, "lot": lot_folder.name,
                                               "ignored": True, "reason": "用户已确认忽略（文件仍保留）"})
                    continue
                lot, wafer = parse_filename(source.name, profile)
                if lot.casefold() != lot_folder.name.casefold():
                    raise ValueError(f"{source.name}: 文件Lot {lot} 与Lot文件夹 {lot_folder.name} 不一致")
                lot = lot_folder.name
                key, identity = resolve_wafer_identity(snapshot, lot, wafer)
                stamp = _fingerprint(source, folder, snapshot["files"], verify_hashes or key in strict_keys)
                groups.setdefault(key, {"lot": lot, "wafer": wafer, "identity": identity, "files": []})["files"].append(stamp)
            except (OSError, ValueError) as exc:
                reason = f"{source.relative_to(folder).as_posix()}: {exc}"
                lot_errors.append(reason)
                if str(exc).startswith("UNMATCHED:"):
                    plan.pending_files.append({"relative_path": source.relative_to(folder).as_posix(),
                                               "lot": lot_folder.name, "ignored": False, "reason": reason})
        if lot_errors:
            blocked_lots[lot_folder.name] = "; ".join(lot_errors)
            plan.errors.extend(f"{lot_folder.name}: {error}" for error in lot_errors)

    superseded = {path for record in snapshot.get("archives", {}).values() if record.get("status") == "current"
                  for path in record.get("superseded_members", [])}
    for key, issues in member_issues.items():
        if key not in groups:
            lot = snapshot["wafers"][key]["lot"]
            reason = "; ".join(issue["reason"] for issue in issues)
            blocked_lots[lot] = "; ".join(filter(None, (blocked_lots.get(lot), reason)))
            plan.errors.extend(f"{lot}: {issue['reason']}" for issue in issues)
    for key, group in groups.items():
        stamps = group["files"]
        source_signature = digest([(stamp.relative_path, stamp.sha256) for stamp in stamps])
        previous = snapshot["wafers"].get(key)
        action, error = "NEW", ""
        previous_sources = {source["relative_path"] for source in previous.get("source_files", [])} if previous else set()
        removed_sources = previous_sources - {stamp.relative_path for stamp in stamps}
        removed_sources -= superseded
        if previous and (removed_sources or key in member_issues):
            # Proving a rename or rebinding archive inputs always reads real bytes.
            stamps = [_fingerprint(folder / stamp.relative_path, folder, {}, True) for stamp in stamps]
            source_signature = digest([(stamp.relative_path, stamp.sha256) for stamp in stamps])
        change = describe_change(previous, stamps)
        aliases = rename_pairs(previous, stamps) if previous and removed_sources else {}
        change.update(renames=aliases, hard_error=group["lot"] in blocked_lots,
                      archive_issues=member_issues.get(key, []), replacement_required=bool(removed_sources and not aliases))
        if group["lot"] in blocked_lots:
            action, error = "INVALID", blocked_lots[group["lot"]]
        elif aliases:
            action = "INPUT_RENAMED"
        elif removed_sources:
            action = "INVALID"
            error = "已登记的部分输入消失，拒绝用残缺数据重算: " + ", ".join(sorted(removed_sources))
        elif key in member_issues:
            action = "INPUT_CHANGED" if change["changed"] else "INPUT_PROVENANCE_CHANGED"
        elif force:
            action = "FORCE"
        elif previous:
            if previous.get("status") != "current":
                action = "RETRY"
            elif previous.get("source_signature") != source_signature:
                action = "INPUT_CHANGED"
            elif previous.get("profile_signature") != profile.signature:
                action = "CONFIG_CHANGED"
            elif previous.get("cleaned_path") != cleaned_relative_path(folder.name, group["lot"], group["wafer"]).as_posix():
                action = "OUTPUT_NAME_CHANGED"
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
                                   stamps, source_signature, action, error, change))

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
              verify_hashes=False, cache_only=False, *, strict_targets=None) -> RootPlan:
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
                keys = (strict_targets or {}).get(folder, set())
                plans.append(scan_product(folder, category.name, profile, force, verify_hashes, cache_only, strict_keys=keys))
            except (OSError, RuntimeError, ValueError) as exc:
                plans.append(ProductPlan(folder, category.name, profile, errors=[str(exc)]))
    if not plans:
        raise ValueError("分类目录下没有产品文件夹")
    return RootPlan(root_path, plans)


def _assert_stamps(folder: Path, stamps: list[FileStamp], verify_hashes=False):
    for stamp in stamps:
        source = folder / stamp.relative_path
        _inside(source, folder)
        stat = source.stat()
        if (stat.st_size, stat.st_mtime_ns) != (stamp.size, stamp.mtime_ns):
            raise ValueError(f"文件在扫描后发生变化: {stamp.relative_path}，请复制完成后重试")
        if verify_hashes and file_hash(source) != stamp.sha256:
            raise ValueError(f"文件内容在确认/扫描后发生变化: {stamp.relative_path}，请重新预览")


def _base_record(task: WaferTask, folder: Path, category: str):
    return {"product": folder.name, "category": category, "lot": task.lot,
            "wafer": task.wafer, "stage": task.stage}


def _process_task(folder: Path, task: WaferTask, profile: PipelineProfile, category: str, previous=None,
                  *, verify_inputs=False, before_publish=None):
    output = folder / cleaned_relative_path(folder.name, task.lot, task.wafer)
    _inside(output, folder)
    if output.is_symlink():
        raise ValueError("清理输出不能是符号链接")
    if output.exists() and (not previous or previous.get("cleaned_path") != output.relative_to(folder).as_posix()):
        raise ValueError(f"清理目标已存在但未登记为当前片输出，拒绝覆盖，请人工确认: {output.name}")
    _assert_stamps(folder, task.files, verify_inputs)
    cleaned = merge_and_clean([folder / stamp.relative_path for stamp in task.files], profile, source_root=folder)
    metrics = calculate_metrics(cleaned, profile)
    _assert_stamps(folder, task.files, verify_inputs)
    output_folder = output.parent
    output_folder.mkdir(exist_ok=True)
    _inside(output_folder, folder)
    with tempfile.NamedTemporaryFile(dir=output_folder, suffix=".tmp", delete=False) as staging:
        staging_path = Path(staging.name)
    try:
        cleaned.to_csv(staging_path)
        _assert_stamps(folder, task.files, verify_inputs)
        saved_stat, saved_sha = staging_path.stat(), file_hash(staging_path)
        if before_publish:
            before_publish()
        os.replace(staging_path, output)
    finally:
        staging_path.unlink(missing_ok=True)
    return {
        **_base_record(task, folder, category), "status": "current", "error": "",
        "source_files": [asdict(stamp) for stamp in task.files],
        "source_signature": task.source_signature, "profile_signature": profile.signature,
        "metrics": metrics, "updated_at": timestamp(),
        "cleaned_path": output.relative_to(folder).as_posix(),
        "cleaned_size": saved_stat.st_size, "cleaned_mtime_ns": saved_stat.st_mtime_ns,
        "cleaned_sha256": saved_sha,
    }


def _retire_previous_output(folder: Path, previous: dict | None, record: dict, run_id: str):
    """Archive only the registered, unchanged old output after the new state commits."""
    if not previous or previous.get("cleaned_path") in (None, record["cleaned_path"]):
        return None
    old = folder / previous["cleaned_path"]
    expected_parent = folder / record["lot"] / "summary_cleaning_data"
    if (old.parent != expected_parent or not old.name.endswith("_summary_cleaning.csv")
            or old.is_symlink() or previous["cleaned_path"] in {
                source["relative_path"] for source in previous.get("source_files", [])}):
        raise ValueError("旧输出路径不符合安全归档条件，保留原文件，请人工确认")
    _inside(old, folder)
    if not old.exists():
        return None
    if file_hash(old) != previous.get("cleaned_sha256"):
        raise ValueError("旧清理文件已被修改，保留原文件，请人工确认")
    archive = folder / ".atlas" / "retired_cleaned" / run_id / previous["cleaned_path"]
    _inside(archive, folder)
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        raise ValueError("旧输出归档目标已存在，保留原文件")
    os.replace(old, archive)
    return archive.relative_to(folder).as_posix()


def run_pipeline(root: str | Path, config_path: str | Path, force=False,
                 verify_hashes=False, rebuild_reports=False, progress=None, *,
                 replacement_approvals=None, strict_targets=None) -> RunResult:
    # Initial discovery is read-only; every product is scanned again under its OS lock.
    initial = scan_root(root, config_path, cache_only=True)
    confirmed_only = replacement_approvals is not None
    approvals = {}
    if confirmed_only:
        if force or rebuild_reports or not replacement_approvals:
            raise ValueError("确认替换仅处理选中的片，不能与强制全部/缓存报表模式混用，也不能空选")
        folders = {product.folder for product in initial.products}
        for approval in replacement_approvals:
            if not isinstance(approval, InputReplacementApproval):
                raise ValueError("替换执行需要由预览生成的明确确认记录")
            token = (Path(approval.product), approval.key)
            if token[0] not in folders or token in approvals:
                raise ValueError("替换确认不属于当前根目录或包含重复片")
            approvals[token] = approval
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

        emit(f"START root={initial.root} force={force} verify_hashes={verify_hashes} cache_only={rebuild_reports} confirmed_only={confirmed_only}")
        for initial_product in initial.products:
            folder = initial_product.folder
            selected_keys = {key for product, key in approvals if product == folder}
            if confirmed_only and not selected_keys:
                continue
            product_lines = []
            def product_emit(message):
                product_lines.append(message)
                emit(f"[{initial_product.category}/{folder.name}] {message}")

            try:
                if initial_product.profile is None or not initial_product.profile.enabled:
                    raise ValueError("; ".join(initial_product.errors))
                with product_lock(folder):
                    plan = scan_product(folder, initial_product.category, initial_product.profile,
                                        force, verify_hashes, rebuild_reports,
                                        strict_keys=selected_keys | set((strict_targets or {}).get(folder, [])))
                    state = ProductState(folder)
                    changed_selected = set()
                    selected_lots = set()
                    try:
                        if not rebuild_reports and not confirmed_only:
                            state.save_scan_errors(plan.errors)
                        for error in plan.errors if not confirmed_only else []:
                            product_emit(f"VALIDATION_FAILED {error}")
                            result.errors.append(f"{folder.name}: {error}")
                        for item in plan.pending_files if not confirmed_only else []:
                            product_emit(f"{'IGNORED_CSV' if item['ignored'] else 'PENDING_CSV'} {item['relative_path']}: {item['reason']}")
                        if plan.errors and not confirmed_only:
                            result.failed += len(plan.errors)
                        present_keys = {task.key for task in plan.tasks}
                        for key in selected_keys - present_keys:
                            result.failed += 1
                            message = "所选片当前没有可识别CSV，不能确认空集合；恢复输入后重新预览"
                            result.errors.append(f"{folder.name}/{key}: {message}")
                            product_emit("REPLACEMENT_REJECTED " + message)
                        for task in plan.tasks:
                            if confirmed_only and task.key not in selected_keys:
                                continue
                            approval = approvals.get((folder, task.key))
                            if approval is not None:
                                try:
                                    validate_approval(approval, plan, task)
                                except ValueError as exc:
                                    result.failed += 1
                                    result.errors.append(f"{folder.name}/{task.lot}/W{task.wafer}: {exc}")
                                    product_emit(f"REPLACEMENT_REJECTED Lot={task.lot} Wafer={task.wafer}: {exc}")
                                    continue
                                task.action = "CONFIRMED_REPLACEMENT"
                                task.error = ""
                                product_emit(f"APPROVAL id={approval.approval_id} operator={approval.operator} reason={approval.reason}")
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
                                previous = plan.snapshot["wafers"].get(task.key)
                                revision = bool(previous and previous.get("source_files") and (
                                    approval is not None or task.source_signature != previous.get("source_signature")
                                    or task.input_change.get("archive_issues")))
                                event, history = None, {}
                                if revision:
                                    event = {"id": uuid.uuid4().hex, "kind": "confirmed_replacement" if approval else
                                             "auto_rename" if task.input_change["renames"] else "input_changed",
                                             "recorded_at": timestamp(), "lot": task.lot, "wafer": task.wafer,
                                             "before_sources": previous["source_files"], "after_sources": entries(task.files),
                                             "renames": task.input_change["renames"],
                                             "operator": approval.operator if approval else "ATLAS自动增量",
                                             "reason": approval.reason if approval else "已验证同内容改名" if task.input_change["renames"] else "检测到输入变化",
                                             "approval": asdict(approval) if approval else None}
                                # Validate provenance updates before publishing any new output.
                                updates = revised_archives(plan.snapshot, previous, task.files,
                                    task.input_change["renames"], event) if revision else None

                                def before_publish():
                                    profiles = load_pipeline_profiles(config_path)
                                    profile = profiles.get(folder.name.casefold())
                                    if not profile or not profile.enabled or profile.signature != plan.profile.signature:
                                        raise ValueError("处理期间产品配置变化，请重新预览")
                                    fresh = scan_product(folder, plan.category, profile, strict_keys={task.key})
                                    current = next((item for item in fresh.tasks if item.key == task.key), None)
                                    if (current is None or current.input_change.get("hard_error")
                                            or file_signature(current.files) != file_signature(task.files)
                                            or digest(fresh.snapshot["wafers"].get(task.key)) != digest(previous)
                                            or archive_signature(fresh.snapshot, previous, current.files) !=
                                               archive_signature(plan.snapshot, previous, task.files)):
                                        raise ValueError("处理期间输入集合或内容变化，请重新预览后确认")
                                    if approval is not None:
                                        validate_approval(approval, fresh, current)
                                    history["folder"] = archive_previous(folder, task.key, previous, run_id, event)

                                record = _process_task(folder, task, plan.profile, plan.category, previous,
                                    verify_inputs=verify_hashes or revision,
                                    before_publish=before_publish if revision else None)
                                if revision:
                                    record.update(input_revision=event["id"], input_history=event["history_path"])
                                    event.update(after_cleaned_path=record["cleaned_path"], after_cleaned_sha256=record["cleaned_sha256"])
                                    try:
                                        state.save_input_revision(task.key, record, task.files, event, updates)
                                    except Exception:
                                        restore_previous(folder, previous, record, history["folder"])
                                        raise
                                    # Later selected Wafers may share an archive, so do not overwrite their revised bindings.
                                    plan.snapshot["archives"].update(updates)
                                    product_emit(f"INPUT_REVISION id={event['id']} history={event['history_path']}")
                                else:
                                    state.save_wafer(task.key, record)
                                    state.save_files(task.files)
                                changed_selected.add(task.key)
                                selected_lots.add(task.lot)
                                result.processed += 1
                                product_emit(f"SUCCESS cleaned={record['cleaned_path']} records={record['metrics']['record_count']} valid_die={record['metrics']['effective_die_count']}")
                                try:
                                    retired = _retire_previous_output(folder, previous, record, run_id)
                                    if retired:
                                        product_emit(f"OLD_OUTPUT_ARCHIVED {retired}")
                                except (OSError, ValueError) as exc:
                                    product_emit(f"WARNING 旧输出归档未完成（新结果已登记）: {exc}")
                                for warning in record["metrics"]["warnings"]:
                                    product_emit(f"WARNING {warning}")
                            except Exception as exc:
                                record = dict(plan.snapshot["wafers"].get(task.key, _base_record(task, folder, plan.category)))
                                record.update(status="failed", error=str(exc), attempted_at=timestamp())
                                state.save_wafer(task.key, record)
                                changed_selected.add(task.key)
                                result.failed += 1
                                result.errors.append(f"{folder.name}/{task.lot}/W{task.wafer}: {exc}")
                                product_emit(f"FAILED {exc}; old results excluded from reports")
                        for record in plan.unavailable if not confirmed_only else []:
                            key = record["key"]
                            previous = plan.snapshot["wafers"][key]
                            if record["status"] != previous.get("status") or record["error"] != previous.get("error"):
                                record["attempted_at"] = timestamp()
                                state.save_wafer(key, record)
                            result.unavailable += 1
                            product_emit(f"{record['status'].upper()} Lot={record['lot']} Wafer={record['wafer']}: {record['error']}")
                        if confirmed_only and selected_lots:
                            state.save_scan_errors([error for error in plan.snapshot["scan_errors"]
                                                    if not any(error.startswith(lot + ":") for lot in selected_lots)])
                        snapshot = read_snapshot(folder)
                        reports_needed = bool(changed_selected) if confirmed_only else (
                            rebuild_reports or plan.reports_needed or snapshot["generation"] != snapshot["report_generation"])
                        if reports_needed:
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
