"""Reusable, UI-independent ingestion logic for ATLAS FT data files."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import gzip
from pathlib import Path
import re
import shutil
from typing import Any
import zipfile

import yaml


class ProfileConfigError(ValueError):
    """Raised when a product profile is missing or malformed."""


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys rather than silently losing product profiles."""


def _unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ProfileConfigError(f"products.yaml 存在重复字段: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


def load_product_document(config_path: str | Path) -> dict:
    path = Path(config_path)
    if not path.is_file():
        raise ProfileConfigError(f"Product Profile 配置不存在: {path}")
    try:
        with path.open("r", encoding="utf-8-sig") as config_file:
            document = yaml.load(config_file, Loader=UniqueKeyLoader) or {}
    except yaml.YAMLError as exc:
        raise ProfileConfigError(f"products.yaml 格式错误: {exc}") from exc
    if not isinstance(document, dict):
        raise ProfileConfigError("products.yaml 顶层必须是映射")
    return document


class PreviewValidationError(ValueError):
    """Raised when a preview cannot safely be executed."""


class ExecutionError(RuntimeError):
    """Raised when an approved rename plan cannot be completed safely."""


@dataclass(frozen=True)
class ProductProfile:
    name: str
    enabled: bool
    naming_version: int
    naming_regex: str
    output_template: str
    lot_required: bool = True
    wafer_required: bool = True
    wafer_min: int | None = None
    wafer_max: int | None = None
    examples: tuple["NamingExample", ...] = ()

    def compile_regex(self) -> re.Pattern[str]:
        return re.compile(self.naming_regex, re.IGNORECASE)


@dataclass(frozen=True)
class NamingExample:
    input_filename: str
    expected_lot: str
    expected_wafer: str


@dataclass(frozen=True)
class ScanResult:
    raw_archives: tuple[str, ...]
    raw_csv: tuple[str, ...]
    normalized_csv: tuple[str, ...]
    unknown_files: tuple[str, ...]


@dataclass(frozen=True)
class FileMetadata:
    original_filename: str
    product: str
    lot: str = ""
    wafer: str = ""
    standard_filename: str = ""
    rule_version: int = 0
    status: str = "UNMATCHED"
    error: str = ""
    source_size: int = 0
    source_mtime_ns: int = 0


@dataclass(frozen=True)
class PreviewResult:
    folder: Path
    product: str
    items: tuple[FileMetadata, ...]
    scan: ScanResult

    @property
    def total_files(self) -> int:
        return len(self.items)

    @property
    def matched_files(self) -> int:
        return sum(item.status == "MATCHED" for item in self.items)

    @property
    def failed_files(self) -> int:
        return self.total_files - self.matched_files

    @property
    def validation_passed(self) -> bool:
        return self.total_files > 0 and self.matched_files == self.total_files


@dataclass(frozen=True)
class ExecutionResult:
    renamed_files: int
    log_path: Path | None
    log_warning: str = ""


class ProductProfileStore:
    """Load and validate product-specific naming knowledge from YAML."""

    def __init__(self, profiles: dict[str, ProductProfile]):
        self._profiles = profiles
        self._names_by_casefold = {name.casefold(): name for name in profiles}
        if len(self._names_by_casefold) != len(profiles):
            raise ProfileConfigError("Product 名称存在大小写重复，无法唯一选择配置")

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> "ProductProfileStore":
        document = load_product_document(config_path)
        raw_products = document.get("products")
        if not isinstance(raw_products, dict) or not raw_products:
            raise ProfileConfigError("products.yaml 必须包含非空的 products 映射")

        profiles: dict[str, ProductProfile] = {}
        for raw_name, raw_profile in raw_products.items():
            name = str(raw_name).strip()
            if not name:
                raise ProfileConfigError("Product 名称不能为空")
            if not isinstance(raw_profile, dict):
                raise ProfileConfigError(f"Product {name} 的配置必须是映射")

            enabled = bool(raw_profile.get("enabled", True))
            naming = raw_profile.get("naming")
            if not isinstance(naming, dict):
                raise ProfileConfigError(f"Product {name} 缺少 naming 配置")

            pattern = naming.get("regex")
            if not isinstance(pattern, str) or not pattern.strip():
                raise ProfileConfigError(f"Product {name} 缺少 naming.regex")
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ProfileConfigError(f"Product {name} 的 Regex 无效: {exc}") from exc
            missing_groups = {"lot", "wafer"} - set(compiled.groupindex)
            if missing_groups:
                missing = ", ".join(sorted(missing_groups))
                raise ProfileConfigError(
                    f"Product {name} 的 Regex 缺少命名捕获组: {missing}"
                )

            try:
                naming_version = int(naming.get("version", 1))
            except (TypeError, ValueError) as exc:
                raise ProfileConfigError(
                    f"Product {name} 的 naming.version 必须是整数"
                ) from exc
            if naming_version < 1:
                raise ProfileConfigError(f"Product {name} 的 naming.version 必须大于 0")

            output_name = raw_profile.get("output_name") or {}
            if not isinstance(output_name, dict):
                raise ProfileConfigError(f"Product {name} 的 output_name 必须是映射")
            output_template = output_name.get(
                "template", "{product}_{lot}_{wafer}#_{original}"
            )
            if not isinstance(output_template, str) or not output_template:
                raise ProfileConfigError(f"Product {name} 的输出模板不能为空")
            cls._validate_output_template(name, output_template)

            validation = raw_profile.get("validation") or {}
            if not isinstance(validation, dict):
                raise ProfileConfigError(f"Product {name} 的 validation 必须是映射")

            examples = cls._load_examples(name, naming, compiled)

            profiles[name] = ProductProfile(
                name=name,
                enabled=enabled,
                naming_version=naming_version,
                naming_regex=pattern,
                output_template=output_template,
                lot_required=bool(validation.get("lot_required", True)),
                wafer_required=bool(validation.get("wafer_required", True)),
                wafer_min=cls._optional_int(name, validation, "wafer_min"),
                wafer_max=cls._optional_int(name, validation, "wafer_max"),
                examples=examples,
            )

        return cls(profiles)

    @staticmethod
    def _validate_output_template(product: str, template: str) -> None:
        allowed_fields = {"product", "lot", "wafer", "original"}
        referenced_fields = set(re.findall(r"{([^{}!:]+)", template))
        unknown_fields = referenced_fields - allowed_fields
        if unknown_fields:
            unknown = ", ".join(sorted(unknown_fields))
            raise ProfileConfigError(
                f"Product {product} 的输出模板包含未知字段: {unknown}"
            )
        try:
            rendered = template.format(
                product="PRODUCT", lot="LOT", wafer="01", original="source.csv"
            )
        except (KeyError, ValueError) as exc:
            raise ProfileConfigError(f"Product {product} 的输出模板无效: {exc}") from exc
        if not rendered.lower().endswith(".csv"):
            raise ProfileConfigError(f"Product {product} 的输出模板必须生成 CSV 文件名")
        if Path(rendered).name != rendered:
            raise ProfileConfigError(f"Product {product} 的输出模板不得包含路径")

    @staticmethod
    def _optional_int(product: str, values: dict[str, Any], key: str) -> int | None:
        raw_value = values.get(key)
        if raw_value is None:
            return None
        try:
            return int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ProfileConfigError(
                f"Product {product} 的 validation.{key} 必须是整数"
            ) from exc

    @staticmethod
    def _load_examples(
        product: str, naming: dict[str, Any], compiled: re.Pattern[str]
    ) -> tuple[NamingExample, ...]:
        raw_examples = naming.get("examples") or []
        if not isinstance(raw_examples, list):
            raise ProfileConfigError(f"Product {product} 的 naming.examples 必须是列表")

        examples: list[NamingExample] = []
        for index, raw_example in enumerate(raw_examples, start=1):
            if not isinstance(raw_example, dict):
                raise ProfileConfigError(
                    f"Product {product} 的 example #{index} 必须是映射"
                )
            input_filename = str(raw_example.get("input", "")).strip()
            expected_lot = str(raw_example.get("lot", "")).strip()
            expected_wafer = str(raw_example.get("wafer", "")).strip()
            if not input_filename or not expected_lot or not expected_wafer:
                raise ProfileConfigError(
                    f"Product {product} 的 example #{index} 缺少 input/lot/wafer"
                )
            match = compiled.search(input_filename)
            if not match:
                raise ProfileConfigError(
                    f"Product {product} 的 example #{index} 无法被当前 Regex 匹配"
                )
            actual_lot = match.group("lot")
            actual_wafer = match.group("wafer")
            if actual_lot != expected_lot or actual_wafer != expected_wafer:
                raise ProfileConfigError(
                    f"Product {product} 的 example #{index} 结果不符: "
                    f"得到 lot={actual_lot}, wafer={actual_wafer}"
                )
            examples.append(
                NamingExample(input_filename, expected_lot, expected_wafer)
            )
        return tuple(examples)

    @property
    def product_names(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                (name for name, profile in self._profiles.items() if profile.enabled),
                key=str.casefold,
            )
        )

    def resolve_name(self, product: str) -> str | None:
        name = self._names_by_casefold.get(product.strip().casefold())
        if name and self._profiles[name].enabled:
            return name
        return None

    def get(self, product: str) -> ProductProfile:
        resolved = self.resolve_name(product)
        if not resolved:
            raise ProfileConfigError(f"未找到或未启用产品 {product.strip() or '(空)'}")
        return self._profiles[resolved]

    def is_normalized_filename(self, filename: str) -> bool:
        """Recognize the legacy ATLAS name retained for downstream compatibility."""
        for product in self.product_names:
            pattern = rf"^{re.escape(product)}_[^_#]+_\d+#_.*\.csv$"
            if re.match(pattern, filename, re.IGNORECASE):
                return True
        return False


class PreprocessingService:
    """Scan, preview, validate and execute FT file normalization."""

    def __init__(self, profiles: ProductProfileStore):
        self.profiles = profiles

    def scan_folder(self, folder: str | Path) -> ScanResult:
        folder_path = Path(folder)
        if not folder_path.is_dir():
            raise FileNotFoundError(f"数据文件夹不存在: {folder_path}")

        archives: list[str] = []
        raw_csv: list[str] = []
        normalized_csv: list[str] = []
        unknown: list[str] = []

        for path in sorted(folder_path.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_file():
                continue
            lower_name = path.name.lower()
            if lower_name.endswith((".zip", ".gz")):
                archives.append(path.name)
            elif lower_name.endswith(".csv"):
                if self.profiles.is_normalized_filename(path.name):
                    normalized_csv.append(path.name)
                else:
                    raw_csv.append(path.name)
            else:
                unknown.append(path.name)

        return ScanResult(
            raw_archives=tuple(archives),
            raw_csv=tuple(raw_csv),
            normalized_csv=tuple(normalized_csv),
            unknown_files=tuple(unknown),
        )

    def build_preview(self, folder: str | Path, product: str) -> PreviewResult:
        folder_path = Path(folder).resolve()
        profile = self.profiles.get(product)
        scan = self.scan_folder(folder_path)
        items = [self._parse_file(folder_path, profile, name) for name in scan.raw_csv]
        items = self._mark_collisions(folder_path, items)
        return PreviewResult(
            folder=folder_path,
            product=profile.name,
            items=tuple(items),
            scan=scan,
        )

    @staticmethod
    def _parse_file(
        folder: Path, profile: ProductProfile, filename: str
    ) -> FileMetadata:
        source = folder / filename
        stat = source.stat()
        base = FileMetadata(
            original_filename=filename,
            product=profile.name,
            rule_version=profile.naming_version,
            source_size=stat.st_size,
            source_mtime_ns=stat.st_mtime_ns,
        )
        match = profile.compile_regex().search(filename)
        if not match:
            return replace(base, error="Regex did not match")

        lot = (match.groupdict().get("lot") or "").strip()
        wafer = (match.groupdict().get("wafer") or "").strip()
        validation_error = PreprocessingService._validate_metadata(profile, lot, wafer)
        if validation_error:
            return replace(
                base,
                lot=lot,
                wafer=wafer,
                status="INVALID",
                error=validation_error,
            )

        try:
            standard_filename = profile.output_template.format(
                product=profile.name,
                lot=lot,
                wafer=wafer,
                original=filename,
            )
        except (KeyError, ValueError) as exc:
            return replace(base, lot=lot, wafer=wafer, status="INVALID", error=str(exc))

        if Path(standard_filename).name != standard_filename:
            return replace(
                base,
                lot=lot,
                wafer=wafer,
                status="INVALID",
                error="Output filename contains a path",
            )

        return replace(
            base,
            lot=lot,
            wafer=wafer,
            standard_filename=standard_filename,
            status="MATCHED",
        )

    @staticmethod
    def _validate_metadata(profile: ProductProfile, lot: str, wafer: str) -> str:
        if profile.lot_required and not lot:
            return "Lot is required"
        if profile.wafer_required and not wafer:
            return "Wafer is required"
        if wafer and (profile.wafer_min is not None or profile.wafer_max is not None):
            try:
                wafer_number = int(wafer)
            except ValueError:
                return "Wafer must be an integer"
            if profile.wafer_min is not None and wafer_number < profile.wafer_min:
                return f"Wafer is below {profile.wafer_min}"
            if profile.wafer_max is not None and wafer_number > profile.wafer_max:
                return f"Wafer is above {profile.wafer_max}"
        return ""

    @staticmethod
    def _mark_collisions(folder: Path, items: list[FileMetadata]) -> list[FileMetadata]:
        target_counts: dict[str, int] = {}
        for item in items:
            if item.status == "MATCHED":
                key = item.standard_filename.casefold()
                target_counts[key] = target_counts.get(key, 0) + 1

        checked: list[FileMetadata] = []
        for item in items:
            if item.status != "MATCHED":
                checked.append(item)
                continue
            if target_counts[item.standard_filename.casefold()] > 1:
                checked.append(
                    replace(item, status="COLLISION", error="Duplicate output filename")
                )
                continue
            if (folder / item.standard_filename).exists():
                checked.append(
                    replace(item, status="COLLISION", error="Output file already exists")
                )
                continue
            checked.append(item)
        return checked

    def execute(self, preview: PreviewResult) -> ExecutionResult:
        if not preview.validation_passed:
            raise PreviewValidationError("Preview validation did not pass")

        current_scan = self.scan_folder(preview.folder)
        preview_sources = {item.original_filename for item in preview.items}
        if set(current_scan.raw_csv) != preview_sources:
            raise ExecutionError("文件夹内容在 Preview 后已变化，请重新 Preview")

        for item in preview.items:
            source = preview.folder / item.original_filename
            target = preview.folder / item.standard_filename
            if not source.is_file():
                raise ExecutionError(f"原始文件不存在: {item.original_filename}")
            stat = source.stat()
            if stat.st_size != item.source_size or stat.st_mtime_ns != item.source_mtime_ns:
                raise ExecutionError(f"文件在 Preview 后已变化: {item.original_filename}")
            if target.exists():
                raise ExecutionError(f"输出文件已存在: {item.standard_filename}")

        try:
            log_path = self._write_log(preview, "STARTED")
        except OSError as exc:
            raise ExecutionError(f"无法写入 preprocessing 日志，未执行改名: {exc}") from exc

        renamed: list[tuple[Path, Path]] = []
        try:
            for item in preview.items:
                source = preview.folder / item.original_filename
                target = preview.folder / item.standard_filename
                source.rename(target)
                renamed.append((source, target))
        except OSError as exc:
            rollback_errors: list[str] = []
            for source, target in reversed(renamed):
                try:
                    if target.exists() and not source.exists():
                        target.rename(source)
                except OSError as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
            detail = f"重命名失败，已尝试回滚: {exc}"
            if rollback_errors:
                detail += f"；回滚异常: {'; '.join(rollback_errors)}"
            try:
                self._write_log(preview, "FAILED", detail)
            except OSError:
                pass
            raise ExecutionError(detail) from exc

        log_warning = ""
        try:
            self._write_log(preview, "SUCCESS")
        except OSError as exc:
            log_warning = f"重命名成功，但完成状态日志写入失败: {exc}"
        return ExecutionResult(len(renamed), log_path, log_warning)

    @staticmethod
    def _write_log(
        preview: PreviewResult, result: str, detail: str = ""
    ) -> Path:
        log_path = preview.folder / "atlas_preprocessing.log"
        lines = [
            "=" * 72,
            f"timestamp: {datetime.now().astimezone().isoformat(timespec='seconds')}",
            f"product: {preview.product}",
            f"input_folder: {preview.folder}",
            f"number_of_files: {preview.total_files}",
            f"matched_files: {preview.matched_files}",
            f"failed_files: {preview.failed_files}",
            f"regex_version: v{preview.items[0].rule_version if preview.items else '-'}",
            f"result: {result}",
        ]
        if detail:
            lines.append(f"detail: {detail}")
        lines.append("file_mapping:")
        lines.extend(
            f"  {item.original_filename} -> {item.standard_filename or '-'} [{item.status}]"
            for item in preview.items
        )
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write("\n".join(lines) + "\n")
        return log_path


def extract_archive(
    archive_path: str | Path, output_folder: str | Path, delete_original: bool = False
) -> int:
    """Extract ZIP/GZ without overwriting existing files or escaping the folder."""
    archive = Path(archive_path)
    output = Path(output_folder).resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"压缩文件不存在: {archive}")
    output.mkdir(parents=True, exist_ok=True)

    lower_name = archive.name.lower()
    extracted_count = 0
    if lower_name.endswith(".zip"):
        with zipfile.ZipFile(archive, "r") as zip_file:
            members = [member for member in zip_file.infolist() if not member.is_dir()]
            targets: list[Path] = []
            for member in members:
                target = (output / member.filename).resolve()
                if output not in target.parents:
                    raise ExecutionError(f"ZIP 包含不安全路径: {member.filename}")
                if target.exists():
                    raise ExecutionError(f"解压目标已存在，禁止覆盖: {target.name}")
                targets.append(target)
            for member, target in zip(members, targets):
                target.parent.mkdir(parents=True, exist_ok=True)
                with zip_file.open(member, "r") as source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
                extracted_count += 1
    elif lower_name.endswith(".gz"):
        target = output / archive.name[:-3]
        if target.exists():
            raise ExecutionError(f"解压目标已存在，禁止覆盖: {target.name}")
        with gzip.open(archive, "rb") as source, target.open("xb") as destination:
            shutil.copyfileobj(source, destination)
        extracted_count = 1
    else:
        raise ExecutionError(f"不支持的压缩格式: {archive.name}")

    if delete_original:
        archive.unlink()
    return extracted_count
