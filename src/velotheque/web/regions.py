from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import Connection

from velotheque import queries
from velotheque.filters import FilterState
from velotheque.web.deps import get_conn, templates
from velotheque.web.explore import ENTITIES

router = APIRouter()


@router.get("/regions/suggest")
def suggest(request: Request, conn: Connection = Depends(get_conn)):
    params = request.query_params
    entity = params.get("entity") if params.get("entity") in ENTITIES else "rides"
    state = FilterState.from_items(params.multi_items())
    found, counts = queries.search_regions(conn, state, params.get("term", ""), entity)
    return templates.TemplateResponse(
        request,
        "_region_suggest.html",
        {"found": found, "region_counts": counts, "state": state, "term": params.get("term")},
    )


@router.get("/regions/outlines.geojson")
def outlines(request: Request, conn: Connection = Depends(get_conn)):
    ids = request.query_params.getlist("region")[:20]
    return Response(queries.region_outlines(conn, ids), media_type="application/geo+json")
