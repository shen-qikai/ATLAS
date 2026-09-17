"""CLI for ATLAS's local incremental Wafer pipeline."""

import argparse
from pathlib import Path
import sys

from atlas_pipeline.pipeline import run_pipeline, scan_root
from atlas_pipeline.archives import prepare_archives, preview_archives


def main():
    parser = argparse.ArgumentParser(description="ATLAS 数据清洗与良率汇总")
    parser.add_argument("root", help="LDO/DCDC/Load_switch 的父目录（仓库之外）")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parent / "config/products.yaml"))
    parser.add_argument("--preview", action="store_true", help="只扫描，不修改数据目录")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preview-archives", action="store_true", help="只读预览Lot嵌套目录中的压缩包和待整理文件")
    modes.add_argument("--prepare-csv", action="store_true", help="只准备压缩包CSV，不按命名规则过滤，不计算良率")
    modes.add_argument("--force", action="store_true", help="重算所有当前Wafer")
    modes.add_argument("--rebuild-reports", action="store_true", help="只从缓存重建三个Excel，不读取原始CSV")
    parser.add_argument("--verify-hashes", action="store_true", help="完整校验内容（默认仅重读大小/时间变化的文件）")
    parser.add_argument("--confirm-archive-cleanup", action="store_true",
                        help="明确确认将非CSV和嵌套原文件移出Lot备份（与--prepare-csv一起使用）")
    args = parser.parse_args()
    try:
        if args.prepare_csv or args.preview_archives:
            plan = preview_archives(args.root)
            for item in plan.lots:
                print(f"{item.category}/{item.product.name}/{item.lot.name}:")
                for saved in item.files:
                    print(f"  {saved['kind']} {saved['relative_path']} CSV={len(saved['csv_names'])} -> {saved['action']}")
                for error in item.errors:
                    print(f"  ERROR {error}")
            if args.preview or args.preview_archives:
                return int(any(item.errors for item in plan.lots))
            if not args.confirm_archive_cleanup:
                if not sys.stdin.isatty() or input("CSV平铺到Lot；非CSV及嵌套原文件移入产品/.atlas/ingest_backups。确认请输入 YES：") != "YES":
                    print("未确认，不执行解压整理。可先使用 --preview-archives 查看清单。")
                    return 1
            result = prepare_archives(args.root, args.verify_hashes, progress=print, approved_plan=plan)
            print(f"prepared={result.prepared} csv={result.csv_count} backed_up={result.backup_count} failed={result.failed}")
            print(f"log={result.log_path}")
            return int(bool(result.failed))
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
                for item in product.pending_files:
                    print(f"  {'IGNORED' if item['ignored'] else 'PENDING_CSV'} {item['relative_path']}")
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
