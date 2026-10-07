from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backup import BackupError, restore_backup


def _absolute_path(value: str, name: str, *, must_exist: bool) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError(f"{name} must be an absolute path")
    if must_exist and not path.is_file():
        raise argparse.ArgumentTypeError(f"{name} must point to an existing file")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Restore a validated Su Wan local backup while the app is stopped."
    )
    parser.add_argument("--archive", required=True, help="absolute path to the backup ZIP")
    parser.add_argument("--data-dir", required=True, help="absolute path to the app data directory")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the RESTORE prompt; use only for an explicitly authorized test or restore",
    )
    args = parser.parse_args(argv)
    try:
        archive_path = _absolute_path(args.archive, "--archive", must_exist=True)
        data_dir = _absolute_path(args.data_dir, "--data-dir", must_exist=False)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))

    if not args.yes:
        try:
            confirmation = input(
                "恢复会用此备份替换当前数据库。请先关闭应用。输入 RESTORE 继续："
            )
        except (EOFError, KeyboardInterrupt):
            print("已取消恢复。", file=sys.stderr)
            return 2
        if confirmation != "RESTORE":
            print("已取消恢复。", file=sys.stderr)
            return 2

    try:
        result = restore_backup(archive_path, data_dir, confirmed=True)
    except BackupError as exc:
        print(f"恢复失败：{exc}", file=sys.stderr)
        return 1

    print("恢复完成。应用重启后会生成新的本机会话令牌。")
    print(f"数据库：{result['database']}")
    print(f"恢复前快照：{result['rollback_database']}")
    print(f"快照校验说明：{result['rollback_manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
