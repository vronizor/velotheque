from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import Connection, delete
from sqlalchemy.dialects.postgresql import insert

from velotheque import queries
from velotheque.tables import saved_view
from velotheque.web.deps import get_conn, templates

router = APIRouter()


def _list(request: Request, conn: Connection, message: str | None = None):
    return templates.TemplateResponse(
        request, "_saved_views.html", {"views": queries.saved_views(conn), "message": message}
    )


@router.post("/views")
def save_view(
    request: Request,
    name: str = Form(...),
    query_string: str = Form(""),
    conn: Connection = Depends(get_conn),
):
    name = name.strip()[:80]
    query_string = query_string.lstrip("?")[:4000]
    if name:
        stmt = insert(saved_view).values(name=name, entity="rides", query_string=query_string)
        conn.execute(
            stmt.on_conflict_do_update(
                constraint="uq_saved_view_entity_name", set_={"query_string": query_string}
            )
        )
        conn.commit()
    return _list(request, conn, f"Saved “{name}”" if name else "Give the view a name")


@router.delete("/views/{view_id}")
def delete_view(view_id: int, request: Request, conn: Connection = Depends(get_conn)):
    conn.execute(delete(saved_view).where(saved_view.c.id == view_id))
    conn.commit()
    return _list(request, conn)
