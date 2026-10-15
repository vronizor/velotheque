"""Photos live under DATA_DIR/photos/{strava activity id}/, keyed by Strava's photo unique_id
(the export's media file names and the API's `unique_id` are the same UUIDs).

Sources: every media file of the bulk export, plus the API's primary photo for rides the
export doesn't cover yet. Strava documents no endpoint listing all photos of an activity, so
newer rides only get their primary photo until a newer export is imported."""

import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import Connection, delete, select, update
from sqlalchemy.dialects.postgresql import insert

from velotheque.config import Settings
from velotheque.exportrow import media_paths
from velotheque.tables import activity, activity_photo, raw_export_activity, raw_strava_activity

log = logging.getLogger(__name__)

PHOTO_DIR = "photos"
THUMB_DIR = "thumbs"
THUMB_SIZE = (400, 400)
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}
PRIMARY_SIZE = "600"


@dataclass(frozen=True)
class PhotoRef:
    unique_id: str
    source: str
    filename: str
    url: str | None = None


def photo_dir(settings: Settings, source_id: int) -> Path:
    return settings.data_dir / PHOTO_DIR / str(source_id)


def media_filename(member: str) -> str:
    name = PurePosixPath(member).name
    if not name or name in (".", ".."):
        raise ValueError(f"unsafe media path in export: {member}")
    return name


def expected_photos(conn: Connection, source_id: int) -> list[PhotoRef]:
    refs: list[PhotoRef] = []
    export = conn.execute(
        select(raw_export_activity.c.payload)
        .where(raw_export_activity.c.source_id == source_id)
        .order_by(raw_export_activity.c.id.desc())
        .limit(1)
    ).scalar()
    for member in media_paths(export.get("Media") if export else None):
        name = media_filename(member)
        refs.append(PhotoRef(PurePosixPath(name).stem, "export", name))

    detail = conn.execute(
        select(raw_strava_activity.c.payload)
        .where(raw_strava_activity.c.source_id == source_id, raw_strava_activity.c.kind == "detail")
        .order_by(raw_strava_activity.c.id.desc())
        .limit(1)
    ).scalar()
    primary = ((detail or {}).get("photos") or {}).get("primary") or {}
    url = (primary.get("urls") or {}).get(PRIMARY_SIZE)
    unique_id = primary.get("unique_id")
    if unique_id and url and unique_id not in {r.unique_id for r in refs}:
        refs.insert(0, PhotoRef(unique_id, "strava", f"{unique_id}.jpg", url))
    return refs


def _thumbnail(path: Path, thumb: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            size = image.size
            image = image.convert("RGB")
            image.thumbnail(THUMB_SIZE)
            thumb.parent.mkdir(parents=True, exist_ok=True)
            image.save(thumb, "JPEG", quality=82)
            return size
    except (UnidentifiedImageError, OSError):
        log.warning("could not make a thumbnail for %s", path)
        return None


def _download(http: httpx.Client, url: str, target: Path) -> bool:
    response = http.get(url, follow_redirects=True)
    if response.status_code != 200:
        log.warning("photo download failed (%s): %s", response.status_code, url)
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    partial.write_bytes(response.content)
    partial.replace(target)
    return True


def materialize_photos(
    conn: Connection,
    activity_id: int,
    source_id: int,
    settings: Settings,
    http: httpx.Client | None = None,
) -> int:
    """Make activity_photo match the photo files we have; download the API primary photo
    only when an http client is given."""
    directory = photo_dir(settings, source_id)
    kept: list[str] = []
    for position, ref in enumerate(expected_photos(conn, source_id)):
        path = directory / ref.filename
        if not path.exists() and not (
            ref.url and http is not None and _download(http, ref.url, path)
        ):
            continue
        is_video = path.suffix.lower() in VIDEO_SUFFIXES
        thumb = directory / THUMB_DIR / f"{ref.unique_id}.jpg"
        size = None
        if not is_video:
            size = _thumbnail(path, thumb) if not thumb.exists() else _image_size(path)
        relative = path.relative_to(settings.data_dir).as_posix()
        values = {
            "source": ref.source,
            "kind": "video" if is_video else "image",
            "file_path": relative,
            "thumb_path": thumb.relative_to(settings.data_dir).as_posix()
            if thumb.exists()
            else None,
            "width": size[0] if size else None,
            "height": size[1] if size else None,
            "position": position,
        }
        conn.execute(
            insert(activity_photo)
            .values(activity_id=activity_id, unique_id=ref.unique_id, **values)
            .on_conflict_do_update(
                index_elements=[activity_photo.c.activity_id, activity_photo.c.unique_id],
                set_=values,
            )
        )
        kept.append(ref.unique_id)
    conn.execute(
        delete(activity_photo).where(
            activity_photo.c.activity_id == activity_id, activity_photo.c.unique_id.not_in(kept)
        )
    )
    if kept:
        conn.execute(update(activity).where(activity.c.id == activity_id).values(has_photos=True))
    return len(kept)


def _image_size(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as image:
            return ImageOps.exif_transpose(image).size
    except (UnidentifiedImageError, OSError):
        return None
