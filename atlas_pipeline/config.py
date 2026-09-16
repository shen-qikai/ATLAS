"""Product-level settings for the incremental pipeline."""

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re

from preprocessing_core import ProductProfile, ProductProfileStore, ProfileConfigError
from preprocessing_core import load_product_document
from . import PIPELINE_VERSION


@dataclass(frozen=True)
class PipelineProfile:
    naming: ProductProfile
    cleaning_mode: str = "coord"
    header_column: str = "SITE_NUM"
    pass_fail_column: str = "PASSFG"
    x_column: str = "X_COORD"
    y_column: str = "Y_COORD"
    bin_column: str = "SOFT_BIN"
    denominator: str = "TotalCount"
    good_bins: tuple[str, ...] = ("1",)
    expected_die_count: int | None = None
    die_count_tolerance: int = 50
    test_stage: str = "FT"
    enabled: bool = True

    @property
    def signature(self):
        values = asdict(self)
        values["engine_version"] = PIPELINE_VERSION
        return digest(values)


def digest(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _section(document, name):
    value = document.get(name, {})
    if not isinstance(value, dict):
        raise ProfileConfigError(f"{name} 必须是映射")
    return value


def load_pipeline_profiles(config_path: str | Path) -> dict[str, PipelineProfile]:
    store = ProductProfileStore.from_yaml(config_path)
    document = load_product_document(config_path)
    defaults = _section(document, "pipeline_defaults")
    result = {}
    for name in store.product_names:
        raw = document["products"][name]
        sections = {
            key: {**_section(defaults, key), **_section(raw, key)}
            for key in ("cleaning", "columns", "yield", "wafer", "automation")
        }
        cleaning, columns, yielding, wafer, automation = (
            sections[key] for key in ("cleaning", "columns", "yield", "wafer", "automation")
        )
        mode = str(cleaning.get("mode", "coord"))
        if mode not in ("coord", "rt"):
            raise ProfileConfigError(f"{name}: cleaning.mode 只能为 coord 或 rt")
        denominator = str(yielding.get("denominator", "TotalCount"))
        if denominator not in ("TotalCount", "ValidOnly"):
            raise ProfileConfigError(f"{name}: yield.denominator 无效")
        bins = yielding.get("good_bins", [1])
        if not isinstance(bins, list) or not bins:
            raise ProfileConfigError(f"{name}: yield.good_bins 必须是非空列表")
        stage = str(automation.get("test_stage", "FT")).upper()
        if not re.fullmatch(r"[A-Z0-9-]+", stage):
            raise ProfileConfigError(f"{name}: automation.test_stage 只能包含字母、数字、横杠")
        expected = wafer.get("expected_die_count")
        tolerance = wafer.get("die_count_tolerance", 50)
        if expected is not None and (type(expected) is not int or expected <= 0):
            raise ProfileConfigError(f"{name}: expected_die_count 必须是正整数或 null")
        if type(tolerance) is not int or tolerance < 0:
            raise ProfileConfigError(f"{name}: die_count_tolerance 必须是非负整数")
        column_settings = {
            "header_column": columns.get("header", "SITE_NUM"),
            "pass_fail_column": columns.get("pass_fail", "PASSFG"),
            "x_column": columns.get("x", "X_COORD"),
            "y_column": columns.get("y", "Y_COORD"),
            "bin_column": columns.get("soft_bin", "SOFT_BIN"),
        }
        if any(not isinstance(value, str) or not value.strip() for value in column_settings.values()):
            raise ProfileConfigError(f"{name}: columns 中的列名不得为空")
        if len({column_settings[k] for k in ("pass_fail_column", "x_column", "y_column", "bin_column")}) != 4:
            raise ProfileConfigError(f"{name}: Pass/Fail、X、Y、BIN 列名必须不同")
        result[name.casefold()] = PipelineProfile(
            naming=store.get(name),
            cleaning_mode=mode,
            denominator=denominator,
            good_bins=tuple(str(value).removesuffix(".0") for value in bins),
            expected_die_count=expected,
            die_count_tolerance=tolerance,
            test_stage=stage,
            enabled=bool(automation.get("enabled", True)),
            **column_settings,
        )
    return result
