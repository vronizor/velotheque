import json
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import Connection

from velotheque import queries
from velotheque.config import get_settings
from velotheque.filters import FilterState
from velotheque.filters.fields import NO_REGION
from velotheque.queries import DEFAULT_ROUTE_SORT, DEFAULT_SORT, ROUTE_SORTS, Sort
from velotheque.web.deps import get_conn, templates

router = APIRouter()

SORT_COLUMNS = [
    ("date", "Date"),
    ("name", "Name"),
    ("distance", "km"),
    ("elevation", "m+"),
    ("duration", "Time"),
]
ROUTE_SORT_COLUMNS = [
    ("name", "Name"),
    ("distance", "km"),
    ("elevation", "m+"),
    ("coverage", "Ridden"),
]
ENTITIES = ("rides", "routes", "both")


def _state(request: Request) -> FilterState:
    return FilterState.from_items(request.query_params.multi_items())


def _entity(request: Request) -> str:
    value = request.query_params.get("entity")
    return value if value in ENTITIES else "rides"


def _union(a, b):
    if not a or not b:
        return a or b
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def results_context(
    conn: Connection, state: FilterState, sort: Sort, rsort: Sort, entity: str
) -> dict:
    rides, routes = entity in ("rides", "both"), entity in ("routes", "both")
    keep = {
        "entity": None if entity == "rides" else entity,
        "sort": None if str(sort) == DEFAULT_SORT else str(sort),
        "rsort": None if str(rsort) == DEFAULT_ROUTE_SORT else str(rsort),
    }

    def link(**overrides) -> str:
        query = state.to_query(**{**keep, **overrides})
        return "/" + (f"?{query}" if query else "")

    query = state.to_query()
    context = {
        "state": state,
        "entity": entity,
        "show_rides": rides,
        "show_routes": routes,
        "link": link,
        "sort": sort,
        "rsort": rsort,
        "sort_columns": SORT_COLUMNS,
        "route_sort_columns": ROUTE_SORT_COLUMNS,
        "limit": queries.LIST_LIMIT,
        "on_route": [
            (raw, value, queries.route_name(conn, value[0]))
            for raw, value in zip(
                state.raw.get("overlaps_route", []),
                state.values.get("overlaps_route", []),
                strict=True,
            )
        ],
        "geojson_url": "",
        "routes_geojson_url": "",
        "regions": queries.region_filter(conn, state),
        "region_counts": {},
        "regions_geojson_url": "",
    }
    chosen = [v for v in state.raw.get("region", []) if v != NO_REGION]
    if chosen:
        context["regions_geojson_url"] = "/regions/outlines.geojson?" + urlencode(
            [("region", v) for v in chosen]
        )
    bbox = None
    if rides:
        totals, ride_bbox = queries.ride_totals(conn, state)
        bbox = ride_bbox
        context.update(
            facets=queries.ride_facets(conn, state),
            types=queries.ride_types(conn),
            bikes=queries.bikes(conn),
            rows=queries.ride_rows(conn, state, sort),
            totals=totals,
            geojson_url="/explore/rides.geojson" + (f"?{query}" if query else ""),
        )
    if routes:
        route_totals, route_bbox = queries.route_totals(conn, state)
        bbox = _union(bbox, route_bbox)
        context.update(
            route_facets=queries.route_facets(conn, state),
            tags=queries.all_tags(conn),
            sources=queries.all_sources(conn, suggestions=False),
            source_suggestions=queries.all_sources(conn),
            route_rows=queries.route_rows(conn, state, rsort),
            route_totals=route_totals,
            routes_geojson_url="/explore/routes.geojson" + (f"?{query}" if query else ""),
        )
    context["region_counts"] = {
        name: (facets or {}).get("region", {})
        for name, facets in (
            ("rides", context.get("facets")),
            ("routes", context.get("route_facets")),
        )
        if facets is not None
    }
    context["bbox"] = json.dumps(bbox)
    return context


@router.get("/")
def explore(request: Request, conn: Connection = Depends(get_conn)):
    state = _state(request)
    params = request.query_params
    context = results_context(
        conn,
        state,
        Sort.parse(params.get("sort")),
        Sort.parse(params.get("rsort"), ROUTE_SORTS, DEFAULT_ROUTE_SORT),
        _entity(request),
    )
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


@router.get("/explore/routes.geojson")
def routes_geojson(request: Request, conn: Connection = Depends(get_conn)):
    return Response(
        queries.routes_geojson(conn, _state(request)), media_type="application/geo+json"
    )
