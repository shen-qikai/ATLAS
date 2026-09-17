"""Content-bound input replacement reviews, audit records and old-output backups."""

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
import getpass
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from .archives import inside, plain_path, sha256
from .cleaning import retest_run
from .config import digest


def entries(files):
    return [asdict(source) for source in files]


def file_signature(files):
    return digest(sorted(entries(files), key=lambda item: item["relative_path"]))


def describe_change(previous, files):
    old = {item["relative_path"]: item for item in previous.get("source_files", [])} if previous else {}
    new = {item["relative_path"]: item for item in entries(files)}
    return {"removed": sorted(old.keys() - new.keys()), "added": sorted(new.keys() - old.keys()),
            "changed": sorted(path for path in old.keys() & new.keys()
                              if old[path]["sha256"] != new[path]["sha256"])}


def rename_pairs(previous, files):
    """Only a unique SHA + test-run bijection of the entire Wafer is a pure rename."""
    old = {item["relative_path"]: item for item in previous.get("source_files", [])}
    new = {item["relative_path"]: item for item in entries(files)}
    removed, added = old.keys() - new.keys(), new.keys() - old.keys()
    if not removed or len(old) != len(new):
        return {}
    if any(old[path]["sha256"] != new[path]["sha256"] for path in old.keys() & new.keys()):
        return {}
    before, after = {}, {}
    for paths, sources, groups in ((removed, old, before), (added, new, after)):
        for path in paths:
            key = (sources[path]["sha256"], retest_run(Path(path).name))
            groups.setdefault(key, []).append(path)
    if before.keys() != after.keys() or any(len(before[key]) != 1 or len(after[key]) != 1 for key in before):
        return {}
    return {before[key][0]: after[key][0] for key in before}


def archive_signature(snapshot, previous, files):
    names = {item["relative_path"] for item in previous.get("source_files", [])} | {
        item["relative_path"] for item in entries(files)}
    bindings = {}
    for path, record in snapshot.get("archives", {}).items():
        members = [item for item in record.get("members", []) if item["relative_path"] in names]
        if members:
            bindings[path] = {field: record.get(field) for field in
                              ("sha256", "size", "mtime_ns", "source_removed", "layout", "status")}
            bindings[path]["members"] = members
    return digest(bindings)


@dataclass(frozen=True)
class InputReplacementReview:
    product: Path
    category: str
    key: str
    lot: str
    wafer: str
    previous: dict
    files: tuple
    profile_signature: str
    archive_signature: str


@dataclass(frozen=True)
class InputReplacementApproval:
    product: str
    key: str
    profile_signature: str
    baseline_signature: str
    archive_signature: str
    files_signature: str
    approval_id: str
    approved_at: str
    operator: str
    reason: str


def approve_replacement(review, reason):
    reason = reason.strip()
    if not reason or len(reason) > 300:
        raise ValueError("请填写1–300个字符的替换原因")
    return InputReplacementApproval(str(review.product), review.key, review.profile_signature,
        digest(review.previous), review.archive_signature, file_signature(review.files),
        uuid.uuid4().hex, datetime.now().astimezone().isoformat(timespec="seconds"), getpass.getuser(), reason)


def validate_approval(approval, plan, task):
    previous = plan.snapshot["wafers"].get(task.key)
    if not isinstance(approval.reason, str) or not approval.reason.strip() or len(approval.reason) > 300:
        raise ValueError("替换确认必须包含明确原因")
    if (Path(approval.product) != plan.folder or approval.key != task.key or not previous
            or approval.profile_signature != plan.profile.signature
            or approval.baseline_signature != digest(previous)
            or approval.files_signature != file_signature(task.files)
            or approval.archive_signature != archive_signature(plan.snapshot, previous, task.files)):
        raise ValueError("替换预览已过期（输入、历史登记、压缩包来源或配置变化），请重新预览并确认")
    if not task.files or task.input_change.get("hard_error") or not previous.get("source_files"):
        raise ValueError("身份/压缩包校验错误、没有当前CSV或没有历史输入，不能通过替换确认绕过")


def preview_replacements(root, config_path, targets):
    from .pipeline import scan_root, scan_product
    initial = scan_root(root, config_path, cache_only=True)
    products = {plan.folder: plan for plan in initial.products}
    selected = {}
    for folder, key in targets:
        folder = Path(folder).resolve()
        if folder not in products or key in selected.get(folder, set()):
            raise ValueError("所选产品不属于当前根目录或重复选片")
        selected.setdefault(folder, set()).add(key)
    if not selected:
        raise ValueError("请先选择已登记且仍有CSV输入的Wafer行")
    reviews = []
    for folder, keys in selected.items():
        initial_product = products[folder]
        if not initial_product.profile or not initial_product.profile.enabled:
            raise ValueError("所选产品未配置或已禁用")
        plan = scan_product(folder, initial_product.category, initial_product.profile, strict_keys=keys)
        tasks = {task.key: task for task in plan.tasks}
        for key in sorted(keys):
            task = tasks.get(key)
            previous = plan.snapshot["wafers"].get(key)
            if not task or not previous or not previous.get("source_files") or task.input_change.get("hard_error"):
                raise ValueError("所选片没有有效当前CSV/历史输入或存在身份/压缩包错误，请先解决后重新扫描")
            reviews.append(InputReplacementReview(folder, plan.category, key, task.lot, task.wafer,
                deepcopy(previous), tuple(task.files), plan.profile.signature,
                archive_signature(plan.snapshot, previous, task.files)))
    return reviews


def checked(path, product):
    inside(path, product)
    for candidate in [path] + list(path.parents):
        if candidate == product:
            break
        if candidate.exists() or candidate.is_symlink():
            plain_path(candidate, product)
    return path


def archive_previous(product, key, previous, run_id, event):
    folder = checked(product / ".atlas" / "input_history" / run_id / previous["lot"] /
                     ("W" + str(previous["wafer"]).zfill(2) + "_" + digest(key)[:12]), product)
    folder.mkdir(parents=True, exist_ok=False)
    saved = None
    if previous.get("cleaned_path"):
        source = checked(product / previous["cleaned_path"], product)
        if source.parent != product / previous["lot"] / "summary_cleaning_data":
            raise ValueError("历史清洗结果不在登记Lot的清洗目录，保留原文件，请人工确认")
        if source.exists():
            if sha256(source) != previous.get("cleaned_sha256"):
                raise ValueError("历史清洗结果已被手工修改，拒绝覆盖，请先另存并人工确认")
            saved = folder / "previous_cleaned.csv"
            shutil.copy2(source, saved)
            if sha256(saved) != previous["cleaned_sha256"] or sha256(source) != previous["cleaned_sha256"]:
                raise ValueError("备份时历史清洗结果变化，未发布新结果")
    event.update(history_path=folder.relative_to(product).as_posix(), previous_cleaned_available=saved is not None)
    (folder / "record.json").write_text(json.dumps({"previous_record": previous, "event": event,
        "notice": "此处保存旧版本；新版本是否成功登记以SQLite input_revisions及运行日志为准"},
        ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return folder


def restore_previous(product, previous, record, history):
    """Rollback only our just-published file; retain failed new bytes for recovery."""
    output = checked(product / record["cleaned_path"], product)
    if sha256(output) != record["cleaned_sha256"]:
        raise ValueError("新输出在登记失败后又发生变化，保留现有文件，请人工确认")
    backup = checked(history / "previous_cleaned.csv", product)
    old = checked(product / previous["cleaned_path"], product) if previous.get("cleaned_path") else None
    if backup.exists() and sha256(backup) != previous.get("cleaned_sha256"):
        raise ValueError("旧清洗备份在回滚前变化，保留新输出，请人工确认")
    os.replace(output, checked(history / "failed_new_cleaned.csv", product))
    if old is not None and old == output and backup.exists():
        with tempfile.NamedTemporaryFile(dir=old.parent, suffix=".rollback.tmp", delete=False) as target:
            temporary = Path(target.name)
        try:
            shutil.copy2(backup, temporary)
            os.replace(temporary, old)
        finally:
            temporary.unlink(missing_ok=True)


def revised_archives(snapshot, previous, files, aliases, event):
    """Rename verified aliases; retire corrected original members, never forge origin."""
    old_names = {item["relative_path"] for item in previous.get("source_files", [])}
    current = {item["relative_path"]: item for item in entries(files)}
    updates = {}
    for path, original in snapshot.get("archives", {}).items():
        members, retired, changed = [], [], False
        for member in original.get("members", []):
            name = member["relative_path"]
            if name not in old_names:
                members.append(member)
                continue
            replacement = current.get(aliases.get(name, name))
            if replacement is not None and replacement["sha256"] == member["sha256"]:
                saved = {**member, **replacement}
                if name in aliases:
                    saved.update(original_relative_path=member.get("original_relative_path", name))
                members.append(saved)
                changed |= saved != member
            else:
                retired.append({**member, "retired_by_input_revision": event["id"]})
                changed = True
        if changed:
            updates[path] = {**original, "members": members,
                            "retired_members": original.get("retired_members", []) + retired}
    return updates
