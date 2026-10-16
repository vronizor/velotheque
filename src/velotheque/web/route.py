from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import Connection, delete, update

from velotheque import queries
from velotheque.config import get_settings
from velotheque.routes import clean_tags, import_files
from velotheque.tables import ROUTE_SOURCES, route
from velotheque.web.deps import get_conn, templates
from velotheque.web.explore import SOURCE_LABELS

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
            "sources": SOURCE_LABELS,
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
    if source not in ROUTE_SOURCES:
        source = "file"
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


def _route_or_404(conn: Connection, route_id: int):
    found = queries.route_detail(conn, route_id)
    if found is None:
        raise HTTPException(404, "Route not found")
    return found


@router.get("/routes/{route_id}")
def route_page(route_id: int, request: Request, conn: Connection = Depends(get_conn)):
    found = _route_or_404(conn, route_id)
    return templates.TemplateResponse(
        request,
        "route.html",
        {
            "route": found,
            "map": queries.route_map(conn, route_id),
            "rides": queries.route_rides(conn, route_id),
            "files": queries.route_files(conn, route_id),
            "tags": queries.all_tags(conn),
            "sources": SOURCE_LABELS,
            "map_style_url": get_settings().map_style_url,
            "message": request.query_params.get("message"),
        },
    )


@router.post("/routes/{route_id}")
def route_save(
    route_id: int,
    name: str = Form(...),
    notes: str = Form(""),
    tags: str = Form(""),
    conn: Connection = Depends(get_conn),
):
    _route_or_404(conn, route_id)
    name = name.strip()[:200]
    values = {"notes": notes.strip() or None, "tags": _tags(tags)}
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
