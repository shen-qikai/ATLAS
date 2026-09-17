"""Explicit analysis tasks, one replaceable draft, immutable saved plot versions."""

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from .archives import inside, plain_path, sha256
from .pipeline import REPOSITORY_ROOT, validate_root
from .plot_jobs import PlotOptions, PlotResult, run_plot_job, safe_name
from .state import product_lock

PLOT_ENGINE_VERSION = "workspace-2"


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def checked(path, product):
    path = Path(path)
    inside(path, product)
    for parent in [path] + list(path.parents):
        if parent == product:
            break
        if parent.exists() or parent.is_symlink():
            plain_path(parent, product)
    return path


def read_json(path):
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def write_json(path, value, product):
    checked(path, product)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", mode="w",
                                     encoding="utf-8", delete=False) as target:
        temporary = checked(Path(target.name), product)
        json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@dataclass(frozen=True)
class AnalysisTask:
    product: Path
    kind: str
    folder: Path
    name: str
    task_id: str


@dataclass
class PlotPreview:
    task: AnalysisTask
    refs: list
    options: PlotOptions
    output_folder: Path
    image_count: int
    notes: list[str]
    signature: str


def load_task(folder, product, kind):
    folder = checked(Path(folder), product)
    base = product / "plots" / kind / "tasks"
    if kind not in ("bin", "test", "probability") or folder.parent != base:
        raise ValueError("分析任务路径不合法")
    data = read_json(checked(folder / "task.json", product))
    if data["kind"] != kind or not re.fullmatch(r"[0-9a-f]{12}", data["task_id"]):
        raise ValueError("分析任务记录不合法")
    return AnalysisTask(product, kind, folder, data["name"], data["task_id"])


def task_list(product, kind):
    product = validate_root(product)
    base = checked(product / "plots" / kind / "tasks", product)
    if not base.exists():
        return []
    tasks = [load_task(path, product, kind) for path in base.iterdir()
             if path.is_dir() and not path.name.startswith(".")]
    return sorted(tasks, key=lambda task: task.folder.name, reverse=True)


def create_task(product, kind, name):
    product = validate_root(product)
    if kind not in ("bin", "test", "probability") or not name.strip() or len(name.strip()) > 80:
        raise ValueError("请输入1–80个字符的分析任务名称")
    task_id = uuid.uuid4().hex[:12]
    folder = checked(product / "plots" / kind / "tasks" /
                     (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + safe_name(name.strip())[:35] + "_" + task_id), product)
    with product_lock(product):
        folder.mkdir(parents=True)
        task = AnalysisTask(product, kind, folder, name.strip(), task_id)
        write_json(folder / "task.json", {"task_id": task_id, "name": task.name, "kind": kind,
                                         "created_at": now(), "updated_at": now()}, product)
    return task


def snapshot(refs, options):
    inputs = [{"label": ref.label, "key": ref.key, "token": ref.token,
               "lot": ref.record["lot"], "wafer": ref.record["wafer"],
               "cleaned_path": ref.record["cleaned_path"], "sha256": ref.record["cleaned_sha256"],
               "profile_signature": ref.profile.signature} for ref in refs]
    return {"options": asdict(replace(options, output_dpi=300)), "inputs": inputs,
            "engine_version": PLOT_ENGINE_VERSION}


def signature(payload):
    options = dict(payload["options"])
    options.pop("verify_hashes", None)
    options.pop("output_dpi", None)
    for field in ("reference_tokens", "highlight_tokens"):
        if options.get(field) is not None:
            options[field] = sorted(options[field])
    # Curve/legend order affects colors; do not reuse a different rendered ordering.
    value = {"options": options, "inputs": payload["inputs"],
             "engine_version": payload["engine_version"]}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def inventory(folder, product):
    result = []
    for path in sorted(folder.rglob("*")):
        checked(path, product)
        if path.is_file() and path != folder / "manifest.json":
            result.append({"path": path.relative_to(folder).as_posix(), "sha256": sha256(path)})
    return result


def verified_output(folder, product, task_id, purpose):
    checked(folder, product)
    manifest = read_json(checked(folder / "manifest.json", product))
    if (manifest.get("status") != "complete" or manifest.get("task_id") != task_id
            or manifest.get("purpose") != purpose
            or inventory(folder, product) != manifest.get("image_inventory")):
        raise ValueError("图像目录未登记、内容变化或生成失败，保留文件，请重新预览/人工确认")
    return manifest


def _task_check(task, refs, options):
    current = load_task(task.folder, task.product, task.kind)
    if current.task_id != task.task_id or options.kind != task.kind or any(ref.folder != task.product for ref in refs):
        raise ValueError("分析任务和当前产品/绘图类型不一致")


def run_preview(service, refs, options, task, log=None):
    options = deepcopy(options)
    _task_check(task, refs, options)
    payload = snapshot(refs, options)
    digest = signature(payload)
    product = task.product
    parent = checked(product / ".atlas" / "plot_drafts" / task.kind / task.task_id, product)
    current = checked(parent / "current", product)
    with product_lock(product):
        parent.mkdir(parents=True, exist_ok=True)
        if current.exists():
            verified_output(current, product, task.task_id, "preview")
        with tempfile.TemporaryDirectory(dir=parent, prefix="building_") as temporary:
            staging = checked(Path(temporary), product)
            result = run_plot_job(service, refs, replace(options, output_dpi=90), log,
                                  output_folder=staging / "render", preview=True)
            manifest = read_json(result.output_folder / "manifest.json")
            manifest.update(task_id=task.task_id, task_name=task.name, selection_signature=digest,
                            snapshot=payload, image_inventory=inventory(result.output_folder, product))
            write_json(result.output_folder / "manifest.json", manifest, product)
            previous = checked(staging / "previous", product)
            if current.exists():
                os.replace(current, previous)
            try:
                os.replace(result.output_folder, current)
                metadata = read_json(task.folder / "task.json")
                metadata.update(snapshot=payload, updated_at=now())
                write_json(task.folder / "task.json", metadata, product)
            except Exception:
                if current.exists():
                    os.replace(current, staging / "unpublished")
                if previous.exists():
                    os.replace(previous, current)
                raise
    if log:
        log("任务草稿（不归档）：" + str(current))
    return PlotPreview(task, list(refs), replace(options, output_dpi=300), current,
                       result.image_count, result.notes, digest)


def versions(task):
    records = []
    seen = set()
    for folder in sorted(task.folder.iterdir(), reverse=True):
        if folder.is_dir() and re.fullmatch(r"v\d{3,}", folder.name):
            checked(folder, task.product)
            manifest = read_json(checked(folder / "manifest.json", task.product))
            records.append((folder, manifest))
            seen.add(folder.resolve())
    index = task.folder / "saved_versions.json"
    if index.exists():
        for entry in read_json(index)["versions"]:
            if not Path(entry["folder"]).is_dir():
                continue
            folder = export_destination(Path(entry["folder"]).parent, Path(entry["folder"]).name)
            if folder in seen or not folder.is_dir():
                continue
            manifest = read_json(checked(folder / "manifest.json", folder.parent))
            if (manifest.get("task_id") != task.task_id or manifest.get("purpose") != "archive"
                    or manifest.get("version") != entry["version"]
                    or not re.fullmatch(r"v\d{3,}", entry["version"])):
                raise ValueError("保存的图片与任务历史记录不一致")
            records.append((folder, manifest))
            seen.add(folder)
    return sorted(records, key=lambda item: int(item[1].get("version", item[0].name)[1:]), reverse=True)


def export_destination(parent, name):
    """A user-selected output folder; never overwrite arbitrary existing content."""
    name = name.strip()
    if (not name or len(name) > 120 or name in (".", "..") or name.endswith((".", " "))
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name, re.I)):
        raise ValueError("请输入有效的文件夹名称（1–120个字符，不能含路径分隔符或Windows禁用字符）")
    parent = Path(parent).resolve()
    if not parent.is_dir():
        raise ValueError("请选择已存在的保存位置")
    folder = parent / name
    repository = REPOSITORY_ROOT.resolve()
    if folder == repository or repository in folder.parents:
        raise ValueError("图片请保存在ATLAS Git仓库之外")
    checked(folder, parent)
    return folder


def _register_save(task, output, version, custom):
    pointer = {"version": version}
    if custom:
        path = task.folder / "saved_versions.json"
        index = read_json(path) if path.exists() else {"versions": []}
        index["versions"] = [entry for entry in index["versions"] if entry["folder"] != str(output)]
        index["versions"].append({"version": version, "folder": str(output)})
        write_json(path, index, task.product)
        pointer["folder"] = str(output)
    write_json(task.folder / "latest.json", pointer, task.product)


def save_version(service, preview, log=None, *, destination=None):
    task, product = preview.task, preview.task.product
    _task_check(task, preview.refs, preview.options)
    with product_lock(product):
        manifest = verified_output(preview.output_folder, product, task.task_id, "preview")
        if manifest["selection_signature"] != preview.signature:
            raise ValueError("当前草稿已被新的预览替换，请重新预览后保存")
        if signature(snapshot(preview.refs, preview.options)) != preview.signature:
            raise ValueError("草稿输入或参数记录变化，请重新预览后保存")
        service.validate_refs(preview.refs, preview.options.verify_hashes)
        existing = versions(task)
        custom = destination is not None
        output = export_destination(Path(destination).parent, Path(destination).name) if custom else None
        if output is not None and output.exists():
            match = next(((folder, saved) for folder, saved in existing if folder == output), None)
            if match is None or match[1].get("selection_signature") != preview.signature:
                raise ValueError("该文件夹已存在且不是本次已保存结果，请换一个文件夹名称；原文件不会覆盖")
            saved = verified_output(output, output.parent, task.task_id, "archive")
            _register_save(task, output, saved["version"], True)
            if log:
                log("复用已保存高清图片：" + str(output))
            return PlotResult(output, saved["image_count"], ["复用已保存版本"])
        reusable = None
        for folder, saved in existing:
            if saved.get("status") == "complete" and saved.get("selection_signature") == preview.signature:
                verified_output(folder, folder.parent, task.task_id, "archive")
                if custom:
                    reusable = (folder, saved)
                    break
                if log:
                    log(f"相同数据与参数已保存为 {folder.name}，直接复用，不新增版本")
                _register_save(task, folder, saved.get("version", folder.name), folder.parent != task.folder)
                return PlotResult(folder, saved["image_count"], ["复用已保存版本"])
        numbers = [int(saved.get("version", folder.name)[1:]) for folder, saved in existing]
        index = task.folder / "saved_versions.json"
        if index.exists():
            numbers.extend(int(entry["version"][1:]) for entry in read_json(index)["versions"])
        count = max(numbers, default=0) + 1
        version = f"v{count:03d}"
        output = output if custom else checked(task.folder / version, product)
        with tempfile.TemporaryDirectory(dir=task.folder, prefix=".saving_") as temporary:
            staging = checked(Path(temporary), product)
            if reusable is None:
                result = run_plot_job(service, preview.refs, replace(preview.options, output_dpi=300), log,
                                      output_folder=staging / "render")
            else:
                shutil.copytree(reusable[0], staging / "render")
                result = PlotResult(staging / "render", reusable[1]["image_count"], ["复用高清图片，另存到所选位置"])
            saved = read_json(result.output_folder / "manifest.json")
            saved.update(task_id=task.task_id, task_name=task.name, version=version,
                         saved_folder_name=output.name,
                         selection_signature=preview.signature, snapshot=manifest["snapshot"],
                         image_inventory=inventory(result.output_folder, product))
            write_json(result.output_folder / "manifest.json", saved, product)
            if output.exists():
                raise ValueError("版本目录已存在，拒绝覆盖")
            if custom and output.parent.stat().st_dev != task.folder.stat().st_dev:
                # Stage on the chosen filesystem so exports also work across drives.
                with tempfile.TemporaryDirectory(dir=output.parent, prefix=".atlas_save_") as target_temp:
                    target = checked(Path(target_temp) / "render", output.parent)
                    shutil.copytree(result.output_folder, target)
                    if output.exists():
                        raise ValueError("保存期间同名文件夹出现，请换一个名称")
                    os.replace(target, output)
            else:
                os.replace(result.output_folder, output)
        _register_save(task, output, version, custom)
    if log:
        log("已保存高清版本：" + str(output))
    return PlotResult(output, result.image_count, result.notes)
