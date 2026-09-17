"""CSV-only archive preparation, deliberately independent of product naming rules."""

from dataclasses import dataclass, field
from datetime import datetime
import gzip
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import uuid
import zipfile

from .state import ProductState, product_lock, read_snapshot


IMPORT_FOLDER = "imported_csv"
EXCLUDED_FOLDERS = {".atlas", "plots", "cleaned", "summary_data", "summary_cleaning_data",
                    "summary_clean_onlydata", IMPORT_FOLDER}
MAX_UNPACKED_BYTES = 1024 ** 3
MAX_MEMBERS = 10000


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def inside(path, parent):
    if parent.resolve() not in path.resolve().parents:
        raise ValueError(f"文件或目录超出产品数据范围: {path}")
    return path


def sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def stamp(path, product):
    inside(path, product)
    before = path.stat()
    digest = sha256(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"文件仍在写入，请复制完成后重试: {path.name}")
    return {"relative_path": path.relative_to(product).as_posix(), "size": after.st_size,
            "mtime_ns": after.st_mtime_ns, "sha256": digest}


def unchanged(path, saved, verify=False):
    if not path.is_file() or path.is_symlink():
        return False
    info = path.stat()
    return ((info.st_size, info.st_mtime_ns) == (saved.get("size"), saved.get("mtime_ns"))
            and (not verify or sha256(path) == saved.get("sha256")))


def lot_folders(product):
    for path in sorted(product.iterdir(), key=lambda p: p.name.casefold()):
        if path.is_dir() and not path.name.startswith(".") and path.name.casefold() not in EXCLUDED_FOLDERS:
            yield inside(path, product)


def _walk_error(error):
    raise error


def input_files(lot, include_legacy=False):
    """Walk actual directories only, never generated outputs or links/junctions."""
    protected = EXCLUDED_FOLDERS - ({IMPORT_FOLDER} if include_legacy else set())
    files = []
    for directory, names, filenames in os.walk(lot, followlinks=False, onerror=_walk_error):
        current = Path(directory)
        for name in list(names):
            path = current / name
            if name.casefold() in protected or name == ".git":
                names.remove(name)
            else:
                plain_path(path, lot)
        for name in filenames:
            path = current / name
            plain_path(path, lot)
            files.append(path)
    return sorted(files, key=lambda p: p.relative_to(lot).as_posix().casefold())


def plain_path(path, scope):
    inside(path, scope)
    if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
        raise ValueError(f"不处理链接或junction，请使用实际目录/文件: {path}")
    return path


def archive_paths(lot):
    return [p for p in input_files(lot) if p.suffix.lower() in (".zip", ".gz")]


def safe_member(name):
    path = PurePosixPath(name.replace("\\", "/"))
    if (path.is_absolute() or not path.parts or ".." in path.parts
            or any(re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " ")) for part in path.parts)):
        raise ValueError(f"压缩包包含不安全路径: {name}")
    return Path(*path.parts)


def copy_limited(source, target, budget):
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as destination:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            budget[0] += len(block)
            if budget[0] > MAX_UNPACKED_BYTES:
                raise ValueError("压缩包CSV解压总量超过安全上限（1 GiB），请拆分压缩包")
            destination.write(block)


def extract_csvs(archive, staging):
    members, ignored, budget = [], [], [0]
    if archive.name.lower().endswith(".tar.gz"):
        raise ValueError("暂不支持 tar.gz，请先展开容器后放入ZIP或单文件GZ")
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if len(entries) > MAX_MEMBERS:
                raise ValueError("ZIP条目数超过安全上限（10000）")
            for entry in entries:
                path = safe_member(entry.filename)
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError(f"ZIP不能包含符号链接: {entry.filename}")
                if entry.is_dir():
                    continue
                lower = path.name.lower()
                if lower.endswith((".zip", ".tar.gz")):
                    raise ValueError(f"暂不支持嵌套压缩容器，请先展开: {entry.filename}")
                if lower.endswith(".csv") or lower.endswith(".csv.gz"):
                    if entry.file_size > MAX_UNPACKED_BYTES:
                        raise ValueError("单个CSV条目超过安全上限")
                    destination = path.with_suffix("") if lower.endswith(".gz") else path
                    with bundle.open(entry) as stream:
                        if lower.endswith(".gz"):
                            with gzip.GzipFile(fileobj=stream) as decoded:
                                copy_limited(decoded, staging / destination, budget)
                        else:
                            copy_limited(stream, staging / destination, budget)
                    members.append({"archive_member": entry.filename, "path": destination})
                else:
                    ignored.append(entry.filename)
    else:
        lower = archive.name.lower()
        if lower.endswith(".csv.gz"):
            destination = safe_member(archive.name[:-3])
            with gzip.open(archive, "rb") as stream:
                copy_limited(stream, staging / destination, budget)
            members.append({"archive_member": archive.name[:-3], "path": destination})
        elif lower.endswith(".std.gz"):
            ignored.append(archive.name[:-3])
        else:
            raise ValueError("单文件GZ应使用 .csv.gz 或 .std.gz 后缀，无法确认类型，请人工确认")
    return members, ignored


@dataclass
class PreparationResult:
    prepared: int = 0
    skipped: int = 0
    csv_count: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    log_path: str = ""
    backup_count: int = 0


@dataclass
class LotArchivePlan:
    product: Path
    category: str
    lot: Path
    files: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ArchivePreview:
    root: Path
    lots: list[LotArchivePlan] = field(default_factory=list)


def preview_archives(root):
    """Read-only inventory. Capture exact files/content before cleanup approval."""
    from .pipeline import CATEGORIES, validate_root
    root = validate_root(root)
    categories = sorted(p for p in root.iterdir() if p.is_dir() and p.name.casefold() in CATEGORIES)
    if not categories:
        raise ValueError("根目录下未找到 LDO、DCDC 或 Load_switch")
    plan = ArchivePreview(root)
    for category in categories:
        plain_path(category, root)
        for product in sorted(category.iterdir()):
            if not product.is_dir() or product.name.startswith("."):
                continue
            plain_path(product, root)
            validate_root(product)
            for lot in lot_folders(product):
                item = LotArchivePlan(product, category.name, lot)
                plan.lots.append(item)
                try:
                    files = input_files(lot, include_legacy=True)
                except Exception as exc:
                    item.errors.append(str(exc))
                    continue
                for path in files:
                    try:
                        saved = stamp(path, product)
                        legacy = IMPORT_FOLDER in [part.casefold() for part in path.relative_to(lot).parts[:-1]]
                        kind = "旧导入文件" if legacy else (
                            "压缩包" if path.suffix.lower() in (".zip", ".gz") else
                            "CSV" if path.suffix.lower() == ".csv" else "非CSV")
                        action = "移出Lot并备份"
                        csv_names = []
                        if not legacy and kind == "压缩包":
                            action = "提取CSV后移出Lot"
                        elif not legacy and kind == "CSV":
                            action = "保留" if path.parent == lot else "平铺到Lot后备份原文件"
                        item.files.append({**saved, "kind": kind, "action": action, "csv_names": csv_names})
                        if not legacy and kind == "压缩包":
                            if path.suffix.lower() == ".zip":
                                with zipfile.ZipFile(path) as bundle:
                                    if len(bundle.infolist()) > MAX_MEMBERS:
                                        raise ValueError("ZIP条目数超过安全上限")
                                    for member in bundle.infolist():
                                        safe_member(member.filename)
                                        if stat.S_ISLNK(member.external_attr >> 16):
                                            raise ValueError("ZIP包含符号链接")
                                        if not member.is_dir() and member.filename.lower().endswith((".csv", ".csv.gz")):
                                            csv_names.append(member.filename)
                            elif path.name.lower().endswith(".csv.gz"):
                                csv_names.append(path.name[:-3])
                    except Exception as exc:
                        item.errors.append(f"{path.relative_to(product).as_posix()}: {exc}")
    return plan


def _assert_preview(item):
    plain_path(item.product, item.product.parent)
    plain_path(item.lot, item.product)
    current = {p.relative_to(item.product).as_posix(): p
               for p in input_files(item.lot, include_legacy=True)}
    expected = {saved["relative_path"] for saved in item.files}
    if set(current) != expected:
        raise ValueError("预览后文件集合变化，请重新预览压缩文件")
    for saved in item.files:
        if not unchanged(current[saved["relative_path"]], saved, verify=True):
            raise ValueError(f"预览后文件内容或时间变化，请重新预览: {saved['relative_path']}")


def _backup_file(path, item, backup_root, saved=None):
    plain_path(path, item.lot)
    if saved and not unchanged(path, saved, verify=True):
        raise ValueError(f"待移除文件变化，保留原文件，请重新预览: {path.name}")
    target = inside(backup_root / path.relative_to(item.product), item.product)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise ValueError(f"备份目标已存在，拒绝覆盖: {target}")
    os.replace(path, target)
    return target


def _remove_empty_input_dirs(item):
    # Nonrecursive rmdir only; never remove Lot itself or generated result folders.
    protected = EXCLUDED_FOLDERS - {IMPORT_FOLDER}
    directories = []
    for directory, names, _ in os.walk(item.lot, followlinks=False, onerror=_walk_error):
        current = Path(directory)
        for name in list(names):
            child = current / name
            if name.casefold() in protected or name == ".git":
                names.remove(name)
            else:
                plain_path(child, item.lot)
                directories.append(child)
    for path in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        plain_path(path, item.lot)
        if not any(path.iterdir()):
            path.rmdir()


def _prepare_lot(item, snapshot, state, backup_root, emit, result):
    if item.errors:
        raise ValueError("; ".join(item.errors))
    _assert_preview(item)
    previous_records = {key: value for key, value in snapshot["archives"].items()
                        if value["lot"] == item.lot.name}
    sources = [saved for saved in item.files if saved["kind"] == "压缩包"]
    root_csvs = {Path(saved["relative_path"]).name.casefold(): saved for saved in item.files
                if saved["kind"] == "CSV" and (item.product / saved["relative_path"]).parent == item.lot}
    records, approvals, candidates = {}, {}, {}
    archive_members = {}
    with tempfile.TemporaryDirectory(dir=item.product / ".atlas", prefix="flat_prepare_") as temporary:
        staging = Path(temporary)

        def add_candidate(path, archive, member, old_path=None):
            name = path.name
            key = name.casefold()
            content_hash = sha256(path)
            candidate = {"path": path, "name": name, "sha256": content_hash,
                         "archive": archive, "member": member, "old_path": old_path}
            if key in candidates and candidates[key]["sha256"] != content_hash:
                old = candidates[key]
                raise ValueError(f"同名CSV内容不同，拒绝覆盖: {name}; "
                                 f"{old['archive'] or old['old_path']} / {archive or old_path}")
            candidates.setdefault(key, candidate)
            return key, content_hash

        for index, source in enumerate(sources):
            relative = source["relative_path"]
            archive = item.product / relative
            previous = previous_records.get(relative, {})
            extracted, ignored = extract_csvs(archive, staging / str(index))
            known = {m["archive_member"] for m in previous.get("members", [])}
            if known - {m["archive_member"] for m in extracted}:
                raise ValueError(f"新版压缩包缺少已登记CSV，请人工确认: {relative}")
            archive_members[relative] = []
            old_by_name = {m["archive_member"]: m for m in previous.get("members", [])}
            for member in extracted:
                old = old_by_name.get(member["archive_member"], {})
                key, digest = add_candidate(staging / str(index) / member["path"], relative,
                                            member["archive_member"], old.get("relative_path"))
                archive_members[relative].append((key, digest, member["archive_member"], old))
            records[relative] = {**source, "lot": item.lot.name, "status": "current", "error": "",
                                 "layout": "flat", "ignored": ignored, "updated_at": now(),
                                 "source_removed": True,
                                 "source_backup": (backup_root / relative).relative_to(item.product).as_posix()}
            result.prepared += 1

        # Flatten existing nested CSVs and only active registered legacy imports.
        for saved in item.files:
            path = item.product / saved["relative_path"]
            if saved["kind"] == "CSV" and path.parent != item.lot:
                add_candidate(path, None, None, saved["relative_path"])
        for relative, previous in previous_records.items():
            if relative in records:
                continue
            for member in previous.get("members", []):
                old_path = item.product / member["relative_path"]
                inside(old_path, item.lot)
                if previous.get("layout") == "flat" and not unchanged(old_path, member, verify=True):
                    raise ValueError(f"已登记CSV缺失或变化，请人工检查/恢复: {member['relative_path']}")
                if old_path.parent != item.lot:
                    if not unchanged(inside(old_path, item.product), member, verify=True):
                        raise ValueError(f"旧导入CSV缺失或变化，不能迁移: {member['relative_path']}")
                    add_candidate(old_path, relative, member["archive_member"], member["relative_path"])
            if previous.get("layout") != "flat":
                if previous.get("status") != "current":
                    raise ValueError(f"旧导入记录失败，请恢复原包后重新准备: {relative}")
                records[relative] = {**previous, "layout": "flat", "source_removed": True, "updated_at": now()}

        operations = []
        flat_saved = {}
        for key, candidate in candidates.items():
            existing = root_csvs.get(key)
            target = item.product / existing["relative_path"] if existing else item.lot / candidate["name"]
            if existing and existing["sha256"] != candidate["sha256"]:
                previous = previous_records.get(candidate["archive"], {})
                owned = any(m["relative_path"] == existing["relative_path"]
                            and m["archive_member"] == candidate["member"]
                            and m["sha256"] == existing["sha256"] for m in previous.get("members", []))
                if not owned:
                    raise ValueError(f"Lot根目录已有同名不同内容CSV，拒绝覆盖: {target.name}")
                if any(key != candidate["archive"] and any(
                        m["relative_path"] == existing["relative_path"] and m["sha256"] != candidate["sha256"]
                        for m in record.get("members", [])) for key, record in previous_records.items()):
                    raise ValueError(f"同名CSV被多个压缩包共用，新内容冲突，请人工确认: {target.name}")
            if not existing or existing["sha256"] != candidate["sha256"]:
                operations.append((candidate, target, existing))
            else:
                flat_saved[key] = existing

        # All collisions/CRC/content checks finish before any user-file movement.
        _assert_preview(item)
        created, replaced = [], []
        committed = False
        try:
            for index, (candidate, target, existing) in enumerate(operations):
                local = staging / f"publish_{index}.csv"
                shutil.copyfile(candidate["path"], local)
                if existing:
                    old_backup = _backup_file(target, item, backup_root, existing)
                    replaced.append((target, old_backup))
                elif target.exists() or target.is_symlink():
                    raise ValueError(f"新目标在预览后出现，拒绝覆盖: {target.name}")
                os.replace(local, target)
                created.append((target, candidate["sha256"]))
                flat_saved[target.name.casefold()] = stamp(target, item.product)

            for relative, record in records.items():
                previous = previous_records.get(relative, {})
                members = []
                if relative in archive_members:
                    planned = archive_members[relative]
                else:
                    planned = [(Path(m["relative_path"]).name.casefold(), m["sha256"],
                                m["archive_member"], m) for m in previous.get("members", [])]
                for key, digest, original_name, old in planned:
                    saved = flat_saved.get(key)
                    if saved is None:
                        saved = stamp(item.lot / Path(old["relative_path"]).name, item.product)
                    if saved["sha256"] != digest:
                        raise ValueError("平铺CSV指纹不一致，拒绝登记")
                    members.append({**saved, "archive_member": original_name})
                    decision = snapshot["ignored_files"].get(old.get("relative_path"), {})
                    if decision.get("ignored") and decision.get("sha256") == digest:
                        approvals[saved["relative_path"]] = {**decision, **saved}
                retired = set(previous.get("superseded_members", []))
                retired.update(m["relative_path"] for m in previous.get("members", []))
                retired.difference_update(m["relative_path"] for m in members)
                record.update(members=members, superseded_members=sorted(retired))

            for key, candidate in candidates.items():
                old = candidate["old_path"]
                decision = snapshot["ignored_files"].get(old, {})
                if decision.get("ignored") and decision.get("sha256") == candidate["sha256"]:
                    approvals[flat_saved[key]["relative_path"]] = {**decision, **flat_saved[key]}
            state.save_archives(records, approvals)
            committed = True
        finally:
            if not committed:
                # Roll back only exact files created by this operation. Preserve external edits.
                for target, digest in reversed(created):
                    if target.exists() and sha256(target) == digest:
                        target.unlink()
                for target, old_backup in reversed(replaced):
                    if not target.exists():
                        os.replace(old_backup, target)

        for target, old_backup in replaced:
            result.backup_count += 1
            emit(f"REPLACED_CSV_BACKUP {target.relative_to(item.product).as_posix()} -> {old_backup.relative_to(item.product).as_posix()}")
        for saved in item.files:
            path = item.product / saved["relative_path"]
            if saved["kind"] == "CSV" and path.parent == item.lot:
                continue
            moved = _backup_file(path, item, backup_root, saved)
            result.backup_count += 1
            emit(f"REMOVED_FROM_LOT {saved['relative_path']} -> {moved.relative_to(item.product).as_posix()}")
        _remove_empty_input_dirs(item)
        result.csv_count += len(operations)
        emit(f"LOT_READY {item.product.name}/{item.lot.name}: CSV直接位于Lot根目录，新写入={len(operations)}")


def prepare_archives(root, verify_hashes=False, progress=None, approved_plan=None):
    """Execute the exact approved preview; non-CSV files move to recoverable backup."""
    from .pipeline import validate_root
    root = validate_root(root)
    if approved_plan is None or approved_plan.root != root:
        raise ValueError("请先预览压缩文件并确认，再执行解压整理")
    result = PreparationResult()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    log_folder = inside(root / "_atlas_runs", root)
    log_folder.mkdir(exist_ok=True)
    result.log_path = str(log_folder / f"prepare_{run_id}.log")
    with Path(result.log_path).open("w", encoding="utf-8") as log:
        def emit(message):
            line = f"{now()} {message}"
            log.write(line + "\n")
            log.flush()
            if progress:
                progress(line)
        emit(f"PREPARE_START root={root} approved_lots={len(approved_plan.lots)}")
        for item in approved_plan.lots:
            prepared_before = result.prepared
            try:
                plain_path(item.product, root)
                validate_root(item.product)
                with product_lock(item.product):
                    snapshot = read_snapshot(item.product)
                    state = ProductState(item.product)
                    try:
                        backup = inside(item.product / ".atlas" / "ingest_backups" / run_id, item.product)
                        _prepare_lot(item, snapshot, state, backup, emit, result)
                    finally:
                        state.close()
            except Exception as exc:
                result.prepared = prepared_before
                result.failed += 1
                result.errors.append(f"{item.product.name}/{item.lot.name}: {exc}")
                emit(f"LOT_PREPARE_FAILED {item.product.name}/{item.lot.name}: {exc}; 未移动的原文件保留，已移动文件见备份日志")
        emit(f"PREPARE_END prepared={result.prepared} csv={result.csv_count} backed_up={result.backup_count} failed_lots={result.failed}")
    return result


def discover_csvs(product, lot, snapshot, verify=False, member_issues=None):
    """Direct Lot CSVs only; preparation history verifies flat published members."""
    errors, paths = [], []
    for path in input_files(lot):
        if path.suffix.lower() == ".csv":
            if path.parent != lot:
                errors.append(f"子目录CSV待整理，请先预览并准备CSV: {path.relative_to(product).as_posix()}")
            elif not re.search(r"_(summary(?:_cleaning)?|onlydata)\.csv$", path.name, re.I):
                paths.append(path)
    archives = {p.relative_to(product).as_posix(): p for p in archive_paths(lot)}
    records = {key: value for key, value in snapshot.get("archives", {}).items() if value["lot"] == lot.name}
    for relative in sorted(archives.keys() | records.keys()):
        saved = records.get(relative)
        if not saved or saved.get("status") != "current" or saved.get("layout") != "flat":
            errors.append(f"压缩包或旧导入数据待整理，请先预览并准备CSV: {relative}")
            continue
        source = inside(product / relative, product)
        if relative in archives:
            if not unchanged(source, saved, verify):
                errors.append(f"压缩包变化，请先重新预览并准备CSV: {relative}")
                continue
        elif not saved.get("source_removed"):
            errors.append(f"压缩包意外缺失，请恢复原包后准备CSV: {relative}")
            continue
        for member in saved.get("members", []):
            path = inside(product / member["relative_path"], product)
            if path.parent != lot or not unchanged(path, member, verify):
                reason = f"已准备CSV变化或缺失，请人工检查/恢复: {member['relative_path']}"
                if member_issues is None or path.parent != lot:
                    errors.append(reason)
                else:
                    member_issues.append({"relative_path": member["relative_path"], "reason": reason})
    return paths, errors


def is_ignored(path, product, snapshot):
    saved = snapshot.get("ignored_files", {}).get(path.relative_to(product).as_posix(), {})
    return (saved.get("ignored", False) and path.is_file() and not path.is_symlink()
            and path.stat().st_size == saved.get("size") and sha256(path) == saved.get("sha256"))


def set_csv_ignored(product, relative, ignored):
    product = Path(product).resolve()
    from .pipeline import validate_root
    validate_root(product)
    path = inside(product / relative, product)
    if path.suffix.lower() != ".csv":
        raise ValueError("只能对CSV进行人工忽略确认")
    with product_lock(product):
        record = {**stamp(path, product), "ignored": bool(ignored), "confirmed_at": now(),
                  "reason": "用户确认不参与分析" if ignored else "用户恢复参与身份校验"}
        state = ProductState(product)
        try:
            state.save_ignored_file(relative, record)
        finally:
            state.close()
