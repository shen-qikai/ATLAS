"""Direct cleaned-data plotting adapters; existing rendering algorithms are reused."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import threading
import uuid

import pandas as pd

from .pipeline import _inside
from .plot_data import numeric_frame
from .curve_cache import PROBABILITY_CURVES


# Matplotlib's global pyplot state must not be used by simultaneous tab workers.
PLOT_LOCK = threading.Lock()


def safe_name(value):
    value = str(value)
    name = re.sub(r"[^\w.-]", "_", value, flags=re.UNICODE).strip(". ") or "item"
    if name != value or len(name) > 75:
        name = name[:65] + "_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return name


def parse_bin_tasks(multicolor, good_bad, highlighted, text):
    tasks = []
    if multicolor:
        tasks.append((0, None))
    if good_bad:
        tasks.append((1, None))
    if highlighted:
        for part in text.replace("，", ",").split(","):
            part = part.strip()
            if not part:
                continue
            pieces = [piece.strip() for piece in part.split("+")]
            if not all(re.fullmatch(r"\d+", piece) for piece in pieces):
                raise ValueError("BIN 格式应为 6,9 或 6+9，编号必须是非负整数")
            bins = list(dict.fromkeys(int(piece) for piece in pieces))
            task = (2, bins if len(bins) > 1 else bins[0])
            if task not in tasks:
                tasks.append(task)
        if not any(mode == 2 for mode, _ in tasks):
            raise ValueError("指定 BIN 高亮已启用，请填写 BIN 编号")
    if not tasks:
        raise ValueError("请至少选择一个 BIN 模式")
    return tasks


@dataclass
class PlotOptions:
    kind: str
    tests: list[str] = field(default_factory=list)
    modes: list[str] = field(default_factory=lambda: ["auto"])
    x_mode: str = "auto"
    x_range: list[float] | None = None
    marker_size: int = 5
    grouping: str = "wafer"
    reference_tokens: list[str] = field(default_factory=list)
    highlight_tokens: list[str] | None = None
    bin_tasks: list = field(default_factory=lambda: [(1, None)])
    combine_bin_indices: list[int] = field(default_factory=list)
    combine_modes: list[str] = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    notch: int = 6
    sort_method: str = "alphabetical"
    manual_order: str = ""
    verify_hashes: bool = False
    output_dpi: int = 300
    x_modes: list[str] | None = None


def probability_modes(options):
    return options.x_modes if options.x_modes is not None else [options.x_mode]


@dataclass
class PlotResult:
    output_folder: Path
    image_count: int
    notes: list[str]


def validate_options(options, refs):
    if options.kind not in ("probability", "bin", "test"):
        raise ValueError("不支持的绘图类型")
    if not refs or len({ref.token for ref in refs}) != len(refs):
        raise ValueError("请选择 Wafer，且同片不能重复选择")
    if options.output_dpi not in (90, 300):
        raise ValueError("绘图分辨率只能为预览90或正式300 DPI")
    if options.kind != "bin":
        available = {test for ref in refs for test in ref.tests}
        if not options.tests or any(test not in available for test in options.tests):
            raise ValueError("请至少选择一个存在于所选片的测试项")
        if len(set(options.tests)) != len(options.tests):
            raise ValueError("测试项不能重复")
    def valid_range(values):
        return (values is not None and len(values) == 2
                and all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)
                and values[0] < values[1])
    if options.kind == "probability":
        selected_modes = probability_modes(options)
        if (not selected_modes or len(set(selected_modes)) != len(selected_modes)
                or any(mode not in ("auto", "spec", "manual") for mode in selected_modes)
                or options.grouping not in ("wafer", "lot")):
            raise ValueError("概率图范围或分组模式无效")
        if type(options.marker_size) is not int or not 1 <= options.marker_size <= 15:
            raise ValueError("数据点大小必须为 1–15")
        if "manual" in selected_modes and not valid_range(options.x_range):
            raise ValueError("手动范围必须是有限数值，且 min < max")
    else:
        if options.notch not in (6, 9, 12, 3) or options.sort_method not in ("alphabetical", "manual"):
            raise ValueError("Notch 或排序模式无效")
        if options.sort_method == "manual":
            order = [item.strip() for item in options.manual_order.split(",") if item.strip()]
            if not order or any(item not in {ref.label for ref in refs} for item in order):
                raise ValueError("手动排序须填写所选片的完整身份片名（见左侧数据或日志）")
            if len(set(order)) != len(order):
                raise ValueError("手动排序片名不能重复")
    if options.kind == "test":
        if not options.modes or any(mode not in ("auto", "manual", "usl_lsl", "pass_fail") for mode in options.modes):
            raise ValueError("测试项颜色模式无效")
        if any(mode not in options.modes for mode in options.combine_modes):
            raise ValueError("拼接模式必须已启用")
        if len(set(options.modes)) != len(options.modes) or len(set(options.combine_modes)) != len(options.combine_modes):
            raise ValueError("颜色/拼接模式不能重复")
        if "manual" in options.modes and not valid_range([options.minimum, options.maximum]):
            raise ValueError("手动颜色范围必须是有限数值，且 Min < Max")
        if any(test in ("X_COORD", "Y_COORD") for test in options.tests):
            raise ValueError("测试项名称与绘图内部坐标名称冲突，请调整列名配置/数据")
    if options.kind == "bin":
        if not options.bin_tasks:
            raise ValueError("请至少选择一个 BIN 模式")
        for mode, bins in options.bin_tasks:
            if mode not in (0, 1, 2):
                raise ValueError("BIN 模式无效")
            if mode == 2:
                values = bins if isinstance(bins, list) else [bins]
                if not values or any(type(value) is not int or value < 0 for value in values):
                    raise ValueError("BIN 编号必须是非负整数")
        if any(type(index) is not int or not 0 <= index < len(options.bin_tasks)
               for index in options.combine_bin_indices):
            raise ValueError("BIN 拼接模式索引无效")
        if len(set(options.combine_bin_indices)) != len(options.combine_bin_indices):
            raise ValueError("BIN 拼接模式不能重复")


def _manual_order(options):
    return ",".join(safe_name(item.strip()) for item in options.manual_order.split(",") if item.strip())


def _metadata(values):
    def number(value):
        converted = pd.to_numeric(value, errors="coerce")
        return None if pd.isna(converted) else float(converted)
    return {"unit": values[0], "lsl": number(values[1]), "usl": number(values[2])}


def _spec_groups(loaded, test, log):
    groups = {}
    missing = []
    for ref, frame, meta in loaded:
        if test not in frame:
            missing.append(ref.label)
            continue
        spec = _metadata(meta[test])
        groups.setdefault((spec["unit"], spec["lsl"], spec["usl"]), []).append((ref, frame, spec))
    if missing:
        log(f"{test}: {len(missing)} 片没有该测试项，未参与此图：{', '.join(missing)}")
    if len(groups) > 1:
        log(f"{test}: 单位或上下限不同，自动拆为 {len(groups)} 个规格组，避免错误共用 Spec")
    return list(groups.values())


def _render_probability(loaded, output, options, log):
    from probability import ProbabilityLogic
    logic = ProbabilityLogic(log)
    generated = {mode: [] for mode in probability_modes(options)}
    if options.grouping == "lot":
        log("按 Lot 合并 die 分布；这会隐藏片间差异，且按每片实际有效测试值数贡献数据")
    before = PROBABILITY_CURVES.computations, PROBABILITY_CURVES.hits
    for test in options.tests:
        for index, group in enumerate(_spec_groups(loaded, test, log), 1):
            series, highlight = {}, []
            for ref, frame, _ in group:
                role = "参考" if ref.token in options.reference_tokens else "分析"
                label = ref.label if options.grouping == "wafer" else f"{ref.folder.name}_{ref.record['lot']}_{role}"
                series.setdefault(label, []).append((ref, frame[test]))
                if options.highlight_tokens is not None and ref.token in options.highlight_tokens:
                    highlight.append(label)
            curves = {}
            spec = group[0][2]
            for label, blocks in series.items():
                # Exact selected-member set is part of the key, including partial Lots.
                key = (test, options.grouping, spec["unit"], spec["lsl"], spec["usl"],
                       tuple(sorted((ref.token, ref.record["cleaned_sha256"]) for ref, _ in blocks)))
                curve = PROBABILITY_CURVES.curve(key, [column for _, column in blocks])
                if len(curve[0]):
                    curves[label] = curve
                else:
                    log(f"{label}/{test}: 没有有效数值，未参与此图")
            if not curves:
                continue
            frame = pd.DataFrame({label: pd.Series(curve[0]) for label, curve in curves.items()})
            for mode in generated:
                limits = options.x_range if mode == "manual" else None
                if mode == "spec":
                    if spec["lsl"] is not None and spec["usl"] is not None and spec["usl"] > spec["lsl"]:
                        pad = (spec["usl"] - spec["lsl"]) / 0.9 * 0.05
                        limits = [spec["lsl"] - pad, spec["usl"] + pad]
                    else:
                        log(f"{test}/规格组{index}: 无有效双边规格，规格锁定沿用原逻辑回退为自动范围")
                name = f"{mode}_{safe_name(test)}_spec{index}_prob.png"
                path = output / name
                if not logic.create_overlay_probability_plot(
                        frame, test, path, limits, spec["lsl"], spec["usl"], spec["unit"],
                        list(dict.fromkeys(highlight)) if options.highlight_tokens is not None else None,
                        options.marker_size, dpi=options.output_dpi, prepared_curves=curves):
                    raise RuntimeError(f"概率图生成失败: {test}/{mode}")
                generated[mode].append(path)
                log(f"已生成 {name}（{len(curves)} 条曲线）")
    for mode, images in generated.items():
        if images:
            logic.stitch_images_2x2(images, output, "selected", mode, strict=True)
    log(f"概率计算缓存：新增 {PROBABILITY_CURVES.computations - before[0]}，复用 {PROBABILITY_CURVES.hits - before[1]}")


def _render_bin(loaded, output, options, log):
    import BIN_map as renderer
    data, labels = {}, {}
    for ref, frame, _ in loaded:
        renamed = frame.rename(columns={ref.profile.x_column: "X_COORD", ref.profile.y_column: "Y_COORD",
                                         ref.profile.bin_column: "SOFT_BIN"})
        cleaned = numeric_frame(renamed[["X_COORD", "Y_COORD", "SOFT_BIN"]]).dropna()
        if cleaned.empty:
            raise ValueError(f"{ref.label}: 没有有效坐标/BIN 数据")
        if len(cleaned) != len(frame):
            log(f"{ref.label}: 排除 {len(frame) - len(cleaned)} 条无效坐标/BIN 记录")
        label = safe_name(ref.label)
        data[label] = cleaned
        labels[label] = ref.label
    dirs = {}
    for mode, bins in options.bin_tasks:
        directory = Path(renderer.get_output_folder_path(output, mode, bins))
        dirs[(mode, tuple(bins) if isinstance(bins, list) else bins)] = str(directory)
        settings = renderer.get_color_settings(mode, bins, sorted({b for frame in data.values() for b in frame['SOFT_BIN'].unique()}))
        with tempfile.TemporaryDirectory(prefix="_stitch_", dir=directory) as temporary:
            for label, frame in data.items():
                fig, ax = renderer.plt.subplots(figsize=(8, 8))
                try:
                    renderer.create_wafer_map_pcolormesh(frame, ax, labels[label], options.notch, mode, bins,
                                                        global_settings=settings, add_legend=False, strict=True)
                    fig.savefig(Path(temporary) / f"selected_W{label}_temp.png", dpi=options.output_dpi, bbox_inches='tight')
                    if mode == 0 and settings['legend_info']:
                        renderer._add_custom_legend(ax, frame, settings['legend_info'])
                    fig.savefig(directory / f"selected_W{label}_notch{options.notch}{renderer.get_mode_suffix(mode, bins)}.png",
                                dpi=options.output_dpi, bbox_inches='tight')
                finally:
                    renderer.plt.close(fig)
            renderer.create_composite_map_pcolormesh(data, str(directory), "selected", options.notch,
                                                     mode, bins, temporary, options.sort_method, _manual_order(options),
                                                     output_dpi=options.output_dpi)
        log(f"BIN 模式 {mode} / {bins}: {len(data)} 片及整合图已生成")
    tasks = [options.bin_tasks[index] for index in options.combine_bin_indices]
    if len(tasks) > 1:
        # The legacy image-stitching method only uses its explicit arguments;
        # constructing a Tk application in this worker is intentionally avoided.
        renderer.BinMapApp.create_combined_maps(None, data, str(output), "selected", options.notch,
                                               tasks, dirs, options.sort_method, _manual_order(options), strict=True,
                                               output_dpi=options.output_dpi)


def _render_test(loaded, output, options, log):
    import test_map as renderer
    for test in options.tests:
        for index, group in enumerate(_spec_groups(loaded, test, log), 1):
            data, metas, labels = {}, {}, {}
            for ref, frame, spec in group:
                renamed = frame.rename(columns={ref.profile.x_column: "X_COORD", ref.profile.y_column: "Y_COORD"})
                cleaned = numeric_frame(renamed[["X_COORD", "Y_COORD", test]], strip_markers=True).dropna()
                if cleaned.empty:
                    log(f"{ref.label}/{test}: 无有效坐标和测试值，未参与此图")
                    continue
                label = safe_name(ref.label)
                data[label] = cleaned
                labels[label] = ref.label
                metas[label] = {key: "NA" if value is None else value for key, value in spec.items()}
            if not data:
                continue
            base = output / f"{safe_name(test)}_spec{index}_Maps"
            base.mkdir()
            for mode in options.modes:
                directory = base / mode
                directory.mkdir()
                for label, frame in data.items():
                    renderer.save_single_map(frame, test, labels[label], str(directory), "selected", metas[label],
                                              mode, options.minimum, options.maximum, options.notch,
                                              output_dpi=options.output_dpi)
                renderer.save_composite(data, test, str(directory), "selected", metas, mode,
                                         options.minimum, options.maximum, options.notch,
                                         options.sort_method, _manual_order(options), display_labels=labels,
                                         output_dpi=options.output_dpi)
            combined = [mode for mode in options.combine_modes if mode in options.modes]
            if len(combined) > 1:
                renderer.create_combined_maps(data, test, str(base), "selected", metas, combined,
                                              options.sort_method, _manual_order(options), options.notch, log,
                                              display_labels=labels, strict=True, output_dpi=options.output_dpi)
            log(f"{test} / 规格组{index}: {len(data)} 片已生成")


def run_plot_job(service, refs, options, log=None, *, output_folder=None, preview=False):
    validate_options(options, refs)
    notes = []
    def emit(message):
        notes.append(message)
        if log:
            log(message)
    service.validate_refs(refs, options.verify_hashes)
    loaded = []
    for ref in refs:
        columns = []
        if options.kind in ("bin", "test"):
            columns += [ref.profile.x_column, ref.profile.y_column]
        if options.kind == "bin":
            columns.append(ref.profile.bin_column)
        else:
            columns += [test for test in options.tests if test in ref.tests]
        if not columns:
            continue
        frame, metadata = service.load_columns(ref, columns)
        loaded.append((ref, frame, metadata))
        emit(f"加载 {ref.label}: {len(frame)} 条记录 / {len(columns)} 列")
        for warning in ref.record.get("metrics", {}).get("warnings", []):
            emit(f"{ref.label}: {warning}")
    folder = refs[0].folder
    output = (Path(output_folder) if output_folder is not None else folder / "plots" / options.kind /
              (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]))
    _inside(output, folder)
    output.mkdir(parents=True)
    manifest_path = output / "manifest.json"
    manifest = {"status": "running", "options": asdict(options), "purpose": "preview" if preview else "archive",
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "inputs": [{"label": ref.label, "key": ref.key, "cleaned_path": ref.record["cleaned_path"],
                            "sha256": ref.record["cleaned_sha256"]} for ref in refs], "notes": notes}
    def save_manifest():
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    save_manifest()
    try:
        emit("等待绘图引擎（三个页面串行渲染，避免 Matplotlib 冲突）")
        with PLOT_LOCK:
            service.validate_refs(refs, options.verify_hashes)
            try:
                {"probability": _render_probability, "bin": _render_bin, "test": _render_test}[
                    options.kind](loaded, output, options, emit)
            finally:
                import matplotlib.pyplot as plt
                plt.close("all")
        service.validate_refs(refs, options.verify_hashes)
        images = list(output.rglob("*.png"))
        if not images:
            raise ValueError("未生成图片，请检查所选测试项是否存在有效数据")
        manifest.update(status="complete", image_count=len(images))
        save_manifest()
        emit(f"完成：{len(images)} 张图片，目录 {output}")
        return PlotResult(output, len(images), notes)
    except Exception as exc:
        manifest.update(status="failed", error=str(exc))
        save_manifest()
        raise RuntimeError(f"{exc}\n本次输出标记为 failed，不作为有效结果：{output}") from exc
