import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "proton_backup", Path(__file__).parents[1] / "ops" / "proton_backup.py"
)
pb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pb)

NOW = datetime(2026, 10, 15, 3, 35, tzinfo=UTC)


def name(when: datetime) -> str:
    return f"velotheque-{when:%Y-%m-%dT%H%M}Z.dump"


def test_retention_keeps_14_daily_and_8_weekly():
    names = [name(NOW - timedelta(days=d)) for d in range(120)]
    names += [name(NOW - timedelta(days=3, hours=2)), "notes.txt", "velotheque-old.dump"]
    keep, prune = pb.retention(names, keep_daily=14, keep_weekly=8)
    days = sorted({pb.dump_date(n).date() for n in keep}, reverse=True)
    assert days[:14] == [(NOW - timedelta(days=d)).date() for d in range(14)]
    weeks = {tuple(pb.dump_date(n).isocalendar())[:2] for n in keep}
    assert len(weeks) == 8
    assert len(keep) == 14 + 8 - 3  # the 14 daily dumps already cover 3 ISO weeks
    assert name(NOW - timedelta(days=3, hours=2)) in prune
    assert "notes.txt" not in prune and "velotheque-old.dump" not in prune
    oldest_kept = min(pb.dump_date(n) for n in keep)
    assert NOW - oldest_kept < timedelta(weeks=8)


def test_retention_with_few_dumps_keeps_everything_once_per_day():
    names = [name(NOW), name(NOW - timedelta(hours=1)), name(NOW - timedelta(days=1))]
    keep, prune = pb.retention(names, 14, 8)
    assert keep == {names[0], names[2]} and prune == {names[1]}


@pytest.mark.parametrize(
    "output",
    [
        '[{"name": "a.dump", "type": "file"}, {"name": "b.dump"}]',
        '{"items": [{"name": "a.dump"}, {"name": "b.dump"}]}',
        '{"name": "a.dump"}\n{"name": "b.dump"}\n',
        '["a.dump", "b.dump"]',
    ],
)
def test_listing_parser_tolerates_shapes(output):
    assert pb.parse_listing(output) == ["a.dump", "b.dump"]


def test_listing_parser_reads_proton_drive_0_9_shape():
    """Shape seen from the real CLI (proton-drive 0.9.0, `filesystem list --json`)."""
    node = {
        "uid": "u1",
        "parentUid": "p",
        "name": {"ok": True, "value": "a.dump"},
        "keyAuthor": {"ok": True, "value": "me@example.com"},
        "type": "file",
        "creationTime": "2026-10-15T11:50:00.000Z",
    }
    undecryptable = {**node, "uid": "u2", "name": {"ok": False, "error": "decryption failed"}}
    other = {**node, "uid": "u3", "name": {"ok": True, "value": "b.dump"}}
    assert pb.parse_listing(json.dumps([node, undecryptable, other])) == ["a.dump", "b.dump"]


class FakeDrive:
    def __init__(self, remote: dict[str, set[str]] | None = None, version="0.9.0"):
        self.remote = remote or {}
        self.version = version
        self.uploads: list[tuple[str, str, list[str]]] = []
        self.trashed: list[str] = []
        self.fail_upload = False

    def check_version(self, pinned):
        if pinned != self.version:
            raise pb.DriveError("version mismatch")
        return pinned

    def upload(self, local: Path, remote_parent: str, strategy: str):
        if self.fail_upload:
            raise pb.DriveError("upload failed: session expired")
        files = sorted(
            p.relative_to(local.parent).as_posix() for p in local.rglob("*") if p.is_file()
        )
        self.uploads.append((remote_parent, strategy, files))
        for f in files:
            folder, _, base = f.rpartition("/")
            self.remote.setdefault(f"{remote_parent}/{folder}", set()).add(base)

    def list_names(self, path):
        return sorted(self.remote.get(path, set()))

    def trash(self, paths):
        self.trashed += paths
        for p in paths:
            folder, _, base = p.rpartition("/")
            self.remote[folder].discard(base)


@pytest.fixture
def config(tmp_path):
    state, data = tmp_path / "state", tmp_path / "data"
    (state / "dumps").mkdir(parents=True)
    (state / "dumps" / name(NOW - timedelta(minutes=5))).write_bytes(b"PGDMP fake")
    for rel in ("123/a.jpg", "123/b.mp4", "123/thumbs/a.jpg", "456/c.jpg"):
        path = data / "photos" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rel.encode())
    return pb.Config(
        state_dir=state, photos_dir=data / "photos", proton_bin="x", proton_version="0.9.0"
    )


def test_run_uploads_dump_and_only_new_photos(config):
    old = {name(NOW - timedelta(days=d)) for d in range(1, 40)}
    drive = FakeDrive({"/my-files/Velotheque/db": set(old)})

    report = pb.run(config, drive, now=NOW)

    assert drive.uploads[0] == (
        "/my-files",
        "replace",
        [f"Velotheque/db/{name(NOW - timedelta(minutes=5))}"],
    )
    assert drive.uploads[1] == (
        "/my-files",
        "skip",
        [
            "Velotheque/photos/123/a.jpg",
            "Velotheque/photos/123/b.mp4",
            "Velotheque/photos/456/c.jpg",
        ],
    )
    assert report.photos_uploaded == 3 and report.photos_total == 3
    assert report.kept == len(drive.remote["/my-files/Velotheque/db"])
    assert all(p.startswith("/my-files/Velotheque/db/velotheque-") for p in drive.trashed)
    assert not (config.state_dir / "staging").exists()

    (config.photos_dir / "456" / "d.jpg").write_bytes(b"new")
    drive.uploads.clear()
    report = pb.run(config, drive, now=NOW)
    assert drive.uploads[1][2] == ["Velotheque/photos/456/d.jpg"] and report.photos_uploaded == 1


def test_run_refuses_stale_dump_and_wrong_version(config):
    with pytest.raises(RuntimeError, match="too old"):
        pb.run(config, FakeDrive(), now=NOW + timedelta(days=1))
    with pytest.raises(pb.DriveError, match="version"):
        pb.run(config, FakeDrive(version="1.0.0"), now=NOW)


def test_failed_upload_leaves_manifest_untouched(config):
    drive = FakeDrive()
    drive.fail_upload = True
    with pytest.raises(pb.DriveError):
        pb.run(config, drive, now=NOW)
    assert not (config.state_dir / "photos-manifest.tsv").exists()


def test_main_writes_result_file_on_failure(config, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(config.state_dir))
    monkeypatch.setenv("VELOTHEQUE_DATA_DIR", str(config.photos_dir.parent))
    monkeypatch.setenv("PROTON_DRIVE_VERSION", "0.9.0")
    monkeypatch.setenv("PROTON_DRIVE_BIN", "/nonexistent/proton-drive")
    assert pb.main() == 1
    result = json.loads((config.state_dir / "last-run.json").read_text())
    assert result["ok"] is False and "proton-drive" in result["error"]


def test_session_files_are_locked_down(tmp_path):
    session = tmp_path / ".local" / "share" / "proton-drive-cli" / "auth-session.json"
    session.parent.mkdir(parents=True)
    session.write_text("{}")
    session.chmod(0o644)
    pb.protect_session_files(tmp_path)
    assert oct(session.stat().st_mode & 0o777) == "0o600"
    assert oct(session.parent.stat().st_mode & 0o777) == "0o700"
