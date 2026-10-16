from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import Connection, delete, select, update

from velotheque import gpx, queries
from velotheque.config import get_settings
from velotheque.routes import clean_source, clean_tags, import_files
from velotheque.tables import route
from velotheque.web.deps import get_conn, templates

router = APIRouter()


def _tags(value: str) -> list[str]:
    return clean_tags(value.split(","))


def _import_page(request: Request, conn: Connection, report=None, error=None):
    return templates.TemplateResponse(
        request,
        "route_import.html",
        {
            "report": report,
            "error": error,
            "sources": queries.all_sources(conn),
            "tags": queries.all_tags(conn),
        },
    )


@router.get("/routes/import")
def import_form(request: Request, conn: Connection = Depends(get_conn)):
    return _import_page(request, conn)


@router.post("/routes/import")
async def import_upload(
    request: Request,
    files: list[UploadFile] = File(...),
    merge: bool = Form(False),
    name: str = Form(""),
    source: str = Form("file"),
    tags: str = Form(""),
    conn: Connection = Depends(get_conn),
):
    uploads = [(f.filename or "upload.gpx", await f.read()) for f in files if f.filename]
    if not uploads:
        return _import_page(request, conn, error="Choose at least one file")
    if merge and len(uploads) > 1 and not name.strip():
        return _import_page(request, conn, error="Give the merged route a name")
    report = import_files(
        conn, uploads, merge=merge, name=name or None, source=source, tags=_tags(tags)
    )
    conn.commit()
    if len(report.created) == 1 and not report.skipped:
        return RedirectResponse(f"/routes/{report.created[0][0]}", status_code=303)
    return _import_page(request, conn, report=report)


def _back(next_url: str) -> str:
    return next_url if next_url.startswith("/") and not next_url.startswith("//") else "/"


@router.post("/routes/bulk")
def routes_bulk(
    ids: list[int] = Form(...),
    source: str = Form(""),
    add_tags: str = Form(""),
    remove_tags: str = Form(""),
    next_url: str = Form("/?entity=routes", alias="next"),
    conn: Connection = Depends(get_conn),
):
    """Set the source and add or remove tags on the routes ticked in the Explore list."""
    added = _tags(add_tags)
    removed = {t.lower() for t in _tags(remove_tags)}
    values = {"source": clean_source(conn, source)} if source.strip() else {}
    for route_id, tags in conn.execute(select(route.c.id, route.c.tags).where(route.c.id.in_(ids))):
        kept = [t for t in clean_tags([*tags, *added]) if t.lower() not in removed]
        conn.execute(update(route).where(route.c.id == route_id).values(**values, tags=kept))
    conn.commit()
    return RedirectResponse(_back(next_url), status_code=303)


def _route_or_404(conn: Connection, route_id: int):
    found = queries.route_detail(conn, route_id)
    if found is None:
        raise HTTPException(404, "Route not found")
    return found


@router.get("/routes/{route_id}")
def route_page(route_id: int, request: Request, conn: Connection = Depends(get_conn)):
    found = _route_or_404(conn, route_id)
    files = queries.route_files(conn, route_id)
    base = get_settings().base_url.rstrip("/")
    file_paths = [_file_path(route_id, f.id, f.filename) for f in files]
    return templates.TemplateResponse(
        request,
        "route.html",
        {
            "route": found,
            "map": queries.route_map(conn, route_id),
            "profile": queries.route_profile(conn, route_id),
            "rides": queries.route_rides(conn, route_id),
            "regions": queries.route_regions(conn, route_id),
            "files": files,
            "file_paths": file_paths,
            "gpx_studio": gpx.gpx_studio_url([base + p for p in file_paths]) if files else None,
            "tags": queries.all_tags(conn),
            "sources": queries.all_sources(conn),
            "map_style_url": get_settings().map_style_url,
            "message": request.query_params.get("message"),
        },
    )


@router.post("/routes/{route_id}")
def route_save(
    route_id: int,
    name: str = Form(...),
    source: str = Form(""),
    notes: str = Form(""),
    tags: str = Form(""),
    conn: Connection = Depends(get_conn),
):
    _route_or_404(conn, route_id)
    name = name.strip()[:200]
    values = {
        "notes": notes.strip() or None,
        "tags": _tags(tags),
        "source": clean_source(conn, source),
    }
    if name:
        values["name"] = name
    conn.execute(update(route).where(route.c.id == route_id).values(**values))
    conn.commit()
    return RedirectResponse(f"/routes/{route_id}?message=Saved", status_code=303)


@router.post("/routes/{route_id}/delete")
def route_delete(route_id: int, conn: Connection = Depends(get_conn)):
    _route_or_404(conn, route_id)
    conn.execute(delete(route).where(route.c.id == route_id))
    conn.commit()
    return RedirectResponse("/?entity=routes", status_code=303)


@router.get("/routes/{route_id}/rides/{activity_id}.geojson")
def ride_on_route(route_id: int, activity_id: int, conn: Connection = Depends(get_conn)):
    found = queries.ride_on_route(conn, route_id, activity_id)
    if found is None:
        raise HTTPException(404, "This ride doesn't follow this route")
    return Response(found, media_type="application/geo+json")


def _file_path(route_id: int, file_id: int, filename: str) -> str:
    return f"/routes/{route_id}/files/{file_id}/{gpx.file_slug(filename)}"


@router.get("/routes/{route_id}/files/{file_id}")
def route_file_download(route_id: int, file_id: int, conn: Connection = Depends(get_conn)):
    found = queries.route_file_content(conn, route_id, file_id)
    if found is None:
        raise HTTPException(404, "File not found")
    return Response(
        found.content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(found.filename)}"},
    )


@router.get("/routes/{route_id}/files/{file_id}/{filename}")
def route_file_open(
    route_id: int, file_id: int, filename: str, conn: Connection = Depends(get_conn)
):
    """The original file under a URL-safe name, readable by gpx.studio."""
    found = queries.route_file_content(conn, route_id, file_id)
    if found is None:
        raise HTTPException(404, "File not found")
    return Response(
        found.content,
        media_type="application/octet-stream",
        headers={"Access-Control-Allow-Origin": gpx.GPX_STUDIO},
    )
