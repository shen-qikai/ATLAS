"""CLI for ATLAS's local incremental Wafer pipeline."""

import argparse
from pathlib import Path

from atlas_pipeline.pipeline import run_pipeline, scan_root


def main():
    parser = argparse.ArgumentParser(description="ATLAS 增量清理和产品良率汇总")
    parser.add_argument("root", help="LDO/DCDC/Load_switch 的父目录（仓库之外）")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parent / "config/products.yaml"))
    parser.add_argument("--preview", action="store_true", help="只扫描，不修改数据目录")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--force", action="store_true", help="重算所有当前Wafer")
    modes.add_argument("--rebuild-reports", action="store_true", help="只从缓存重建三个Excel，不读取原始CSV")
    parser.add_argument("--verify-hashes", action="store_true", help="完整校验内容（默认仅重读大小/时间变化的文件）")
    args = parser.parse_args()
    try:
        if args.preview:
            plan = scan_root(args.root, args.config, args.force, args.verify_hashes, args.rebuild_reports)
            for product in plan.products:
                print(f"{product.category}/{product.folder.name}: reports_needed={product.reports_needed}")
                for task in product.tasks:
                    print(f"  {task.action} Lot={task.lot} W{task.wafer} files={len(task.files)} {task.error}")
                for record in product.unavailable:
                    print(f"  {record['status'].upper()} Lot={record['lot']} W{record['wafer']} {record['error']}")
                for error in product.errors:
                    print(f"  ERROR {error}")
            return int(any(product.errors or product.unavailable for product in plan.products))
        result = run_pipeline(args.root, args.config, args.force, args.verify_hashes,
                              args.rebuild_reports, progress=print)
        print(f"processed={result.processed} skipped={result.skipped} failed={result.failed} unavailable={result.unavailable}")
        print(f"log={result.log_path}")
        return int(bool(result.failed or result.unavailable))
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
