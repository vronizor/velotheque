import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import Connection

from velotheque import queries
from velotheque.config import get_settings
from velotheque.filters import FilterState
from velotheque.queries import Sort
from velotheque.web.deps import get_conn, templates

router = APIRouter()

SORT_COLUMNS = [
    ("date", "Date"),
    ("name", "Name"),
    ("distance", "km"),
    ("elevation", "m+"),
    ("duration", "Time"),
]


def _state(request: Request) -> FilterState:
    return FilterState.from_items(request.query_params.multi_items())


def results_context(conn: Connection, state: FilterState, sort: Sort) -> dict:
    totals, bbox = queries.ride_totals(conn, state)
    query = state.to_query()
    return {
        "state": state,
        "facets": queries.ride_facets(conn, state),
        "types": queries.ride_types(conn),
        "bikes": queries.bikes(conn),
        "sort": sort,
        "sort_columns": SORT_COLUMNS,
        "rows": queries.ride_rows(conn, state, sort),
        "totals": totals,
        "limit": queries.LIST_LIMIT,
        "bbox": json.dumps(bbox),
        "geojson_url": "/explore/rides.geojson" + (f"?{query}" if query else ""),
    }


@router.get("/")
def explore(request: Request, conn: Connection = Depends(get_conn)):
    state = _state(request)
    context = results_context(conn, state, Sort.parse(request.query_params.get("sort")))
    if request.headers.get("HX-Request") and request.headers.get("HX-Target") == "results":
        return templates.TemplateResponse(request, "_results.html", {**context, "oob": True})
    context.update(
        views=queries.saved_views(conn),
        map_style_url=get_settings().map_style_url,
    )
    return templates.TemplateResponse(request, "explore.html", context)


@router.get("/explore/rides.geojson")
def rides_geojson(request: Request, conn: Connection = Depends(get_conn)):
    return Response(queries.rides_geojson(conn, _state(request)), media_type="application/geo+json")
