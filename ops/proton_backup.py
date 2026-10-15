#!/usr/bin/env python3
"""Upload the latest database dump and any new photos to Proton Drive, then prune old dumps.

Runs on the host as the unprivileged backup user (see ops/systemd). Standard library only.
Everything that knows about the proton-drive CLI is in the Drive class, so the tool can be
swapped without touching the rest. The result is written to $STATE_DIR/last-run.json, which
ops/record-status.sh turns into a backup_status row for the app's header indicator.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

DUMP_NAME = re.compile(r"^velotheque-(\d{4}-\d{2}-\d{2})T(\d{4})Z\.dump$")
SKIP_DIRS = {"thumbs"}
PHOTO_BATCH = 200
MAX_DUMP_AGE = timedelta(hours=6)


class DriveError(RuntimeError):
    pass


@dataclass
class Config:
    state_dir: Path
    photos_dir: Path
    proton_bin: str
    proton_version: str
    remote_parent: str = "/my-files"
    remote_folder: str = "Velotheque"
    keep_daily: int = 14
    keep_weekly: int = 8

    @classmethod
    def from_env(cls, env=os.environ) -> "Config":
        def need(name):
            if not env.get(name):
                raise SystemExit(f"{name} is not set (see ops/backup.env.example)")
            return env[name]

        return cls(
            state_dir=Path(env.get("STATE_DIR", "/var/lib/velotheque-backup")),
            photos_dir=Path(need("VELOTHEQUE_DATA_DIR")) / "photos",
            proton_bin=env.get("PROTON_DRIVE_BIN", "proton-drive"),
            proton_version=need("PROTON_DRIVE_VERSION"),
            remote_parent=env.get("REMOTE_PARENT", "/my-files").rstrip("/"),
            remote_folder=env.get("REMOTE_FOLDER", "Velotheque"),
            keep_daily=int(env.get("KEEP_DAILY", "14")),
            keep_weekly=int(env.get("KEEP_WEEKLY", "8")),
        )

    @property
    def remote_root(self) -> str:
        return f"{self.remote_parent}/{self.remote_folder}"


class Drive:
    """The only place that knows the proton-drive CLI (pinned version, see check_version)."""

    def __init__(self, binary: str, timeout: int = 3 * 3600):
        self.binary = binary
        self.timeout = timeout

    def run(self, *args: str) -> str:
        result = subprocess.run(
            [self.binary, *args], capture_output=True, text=True, timeout=self.timeout
        )
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip().splitlines()[-5:]
            raise DriveError(f"proton-drive {' '.join(args[:2])} failed: {' / '.join(tail)}")
        return result.stdout

    def check_version(self, pinned: str) -> str:
        output = self.run("--version")
        if f"cli-drive@{pinned}" not in output:
            first = output.strip().splitlines()[0] if output.strip() else "unknown"
            raise DriveError(
                f"proton-drive is {first!r}, but PROTON_DRIVE_VERSION pins {pinned}. "
                "Check the new version by hand, then update the pin."
            )
        return pinned

    def upload(self, local: Path, remote_parent: str, file_strategy: str) -> None:
        self.run(
            "filesystem",
            "upload",
            "--json",
            "--file-conflict-strategy",
            file_strategy,
            "--folder-conflict-strategy",
            "merge",
            "--skip-thumbnails",
            str(local),
            remote_parent,
        )

    def list_names(self, remote_path: str) -> list[str]:
        return parse_listing(self.run("filesystem", "list", "--json", remote_path))

    def trash(self, remote_paths: list[str]) -> None:
        if remote_paths:
            self.run("filesystem", "trash", "--json", *remote_paths)


def parse_listing(output: str) -> list[str]:
    """Names from `filesystem list --json`, tolerating a JSON array, an object wrapping one,
    or one JSON object per line."""
    output = output.strip()
    if not output:
        return []
    try:
        documents = [json.loads(output)]
    except json.JSONDecodeError:
        documents = [json.loads(line) for line in output.splitlines() if line.strip()]
    names: list[str] = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if isinstance(node.get("name"), str):
                names.append(node["name"])
                return
            for key in ("items", "children", "nodes", "files", "entries", "result", "data"):
                if key in node:
                    walk(node[key])
        elif isinstance(node, str):
            names.append(node)

    for document in documents:
        walk(document)
    return names


def dump_date(name: str) -> datetime | None:
    match = DUMP_NAME.match(name)
    if not match:
        return None
    return datetime.strptime(f"{match[1]}T{match[2]}", "%Y-%m-%dT%H%M").replace(tzinfo=UTC)


def retention(names: list[str], keep_daily: int, keep_weekly: int) -> tuple[set[str], set[str]]:
    """Keep the newest dump of each of the last `keep_daily` days that have one, and of each
    of the last `keep_weekly` ISO weeks. Names that aren't ours are never touched."""
    dated = sorted(
        ((dump_date(n), n) for n in names if dump_date(n)), key=lambda x: x[0], reverse=True
    )
    keep: set[str] = set()
    days: dict[date, str] = {}
    weeks: dict[tuple[int, int], str] = {}
    for when, name in dated:
        days.setdefault(when.date(), name)
        weeks.setdefault(tuple(when.isocalendar())[:2], name)
    keep.update(list(days.values())[:keep_daily])
    keep.update(list(weeks.values())[:keep_weekly])
    prune = {name for _, name in dated} - keep
    return keep, prune


Snapshot = dict[str, tuple[int, int]]


def scan_photos(root: Path) -> Snapshot:
    files: Snapshot = {}
    if not root.exists():
        return files
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for filename in filenames:
            if filename.startswith(".") or filename.endswith(".part"):
                continue
            path = Path(directory) / filename
            stat = path.stat()
            files[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return files


def load_manifest(path: Path) -> Snapshot:
    manifest: Snapshot = {}
    if path.exists():
        for line in path.read_text().splitlines():
            relative, size, mtime = line.rsplit("\t", 2)
            manifest[relative] = (int(size), int(mtime))
    return manifest


def save_manifest(path: Path, manifest: Snapshot) -> None:
    partial = path.with_suffix(".part")
    partial.write_text(
        "".join(f"{rel}\t{size}\t{mtime}\n" for rel, (size, mtime) in sorted(manifest.items()))
    )
    partial.replace(path)


def new_or_changed(current: Snapshot, manifest: Snapshot) -> list[str]:
    return sorted(rel for rel, meta in current.items() if manifest.get(rel) != meta)


def stage(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


@dataclass
class RunReport:
    dump: str = ""
    dump_mb: float = 0.0
    photos_uploaded: int = 0
    photos_total: int = 0
    pruned: list[str] = field(default_factory=list)
    kept: int = 0
    proton_drive: str = ""


def latest_dump(dumps_dir: Path, now: datetime) -> Path:
    candidates = sorted(dumps_dir.glob("velotheque-*.dump"))
    if not candidates:
        raise RuntimeError(f"no dump in {dumps_dir}; did ops/dump-db.sh run?")
    newest = candidates[-1]
    when = dump_date(newest.name)
    if when is None or now - when > MAX_DUMP_AGE:
        raise RuntimeError(f"newest dump {newest.name} is too old; refusing to upload it")
    return newest


def run(config: Config, drive: Drive, now: datetime | None = None) -> RunReport:
    now = now or datetime.now(UTC)
    report = RunReport(proton_drive=drive.check_version(config.proton_version))
    staging = config.state_dir / "staging"

    dump = latest_dump(config.state_dir / "dumps", now)
    shutil.rmtree(staging, ignore_errors=True)
    stage(dump, staging / config.remote_folder / "db" / dump.name)
    drive.upload(staging / config.remote_folder, config.remote_parent, "replace")
    report.dump, report.dump_mb = dump.name, round(dump.stat().st_size / 1e6, 1)

    manifest_path = config.state_dir / "photos-manifest.tsv"
    manifest = load_manifest(manifest_path)
    current = scan_photos(config.photos_dir)
    pending = new_or_changed(current, manifest)
    report.photos_total = len(current)
    for start in range(0, len(pending), PHOTO_BATCH):
        batch = pending[start : start + PHOTO_BATCH]
        shutil.rmtree(staging, ignore_errors=True)
        for relative in batch:
            stage(
                config.photos_dir / relative, staging / config.remote_folder / "photos" / relative
            )
        drive.upload(staging / config.remote_folder, config.remote_parent, "skip")
        manifest.update({relative: current[relative] for relative in batch})
        save_manifest(manifest_path, manifest)
        report.photos_uploaded += len(batch)
    shutil.rmtree(staging, ignore_errors=True)

    remote_db = f"{config.remote_root}/db"
    names = drive.list_names(remote_db)
    if dump.name not in names:
        raise RuntimeError(f"{dump.name} is not listed in {remote_db} after upload")
    keep, prune = retention(names, config.keep_daily, config.keep_weekly)
    drive.trash([f"{remote_db}/{name}" for name in sorted(prune)])
    report.pruned, report.kept = sorted(prune), len(keep)
    return report


def protect_session_files(state_dir: Path) -> None:
    """The CLI's own data dirs hold the session (with PROTON_DRIVE_CREDENTIALS_STORE=
    unsafe_file it is a plain JSON file): keep them readable by the backup user only."""
    for directory in (state_dir / ".local" / "share" / "proton-drive-cli", state_dir / ".gnupg"):
        if directory.exists():
            directory.chmod(0o700)
            for path in directory.rglob("*"):
                path.chmod(0o700 if path.is_dir() else 0o600)


def main() -> int:
    config = Config.from_env()
    result_path = config.state_dir / "last-run.json"
    protect_session_files(config.state_dir)
    try:
        report = run(config, Drive(config.proton_bin))
    except Exception as exc:  # noqa: BLE001 - every failure must reach backup_status
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        status = 1
    else:
        details = {
            "dump": report.dump,
            "dump_mb": report.dump_mb,
            "photos_uploaded": report.photos_uploaded,
            "photos_total": report.photos_total,
            "dumps_kept": report.kept,
            "dumps_pruned": len(report.pruned),
            "proton_drive": report.proton_drive,
        }
        result = {"ok": True, "details": details}
        status = 0
    result["finished_at"] = datetime.now(UTC).isoformat()
    result_path.write_text(json.dumps(result))
    print(json.dumps(result))
    return status


if __name__ == "__main__":
    sys.exit(main())
