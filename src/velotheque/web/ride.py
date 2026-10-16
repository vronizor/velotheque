from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import Connection

from velotheque import companions, components, queries
from velotheque.config import get_settings
from velotheque.web.deps import get_conn, templates
from velotheque.web.jobs import render_widget, submit

router = APIRouter()


def _ride_or_404(conn: Connection, activity_id: int):
    ride = queries.ride(conn, activity_id)
    if ride is None:
        raise HTTPException(404, "Ride not found")
    return ride


@router.get("/rides/{activity_id}")
def ride_page(activity_id: int, request: Request, conn: Connection = Depends(get_conn)):
    ride = _ride_or_404(conn, activity_id)
    return templates.TemplateResponse(
        request,
        "ride.html",
        {
            "ride": ride,
            "track": queries.ride_track_geojson(conn, activity_id),
            "profile": queries.ride_profile(conn, activity_id),
            "photos": queries.ride_photos(conn, activity_id),
            "routes": queries.ride_routes(conn, activity_id),
            "regions": queries.ride_regions(conn, activity_id),
            "ride_companions": queries.ride_companions(conn, activity_id),
            "companions": queries.companions(conn),
            "ride_components": components.mounted_on_ride(conn, activity_id),
            "map_style_url": get_settings().map_style_url,
        },
    )


@router.get("/rides/{activity_id}/routes.geojson")
def ride_routes_geojson(activity_id: int, conn: Connection = Depends(get_conn)):
    return Response(
        queries.ride_routes_geojson(conn, activity_id), media_type="application/geo+json"
    )


@router.post("/rides/{activity_id}/refresh")
def ride_refresh(activity_id: int, request: Request, conn: Connection = Depends(get_conn)):
    ride = _ride_or_404(conn, activity_id)
    submit("refresh", ride.source_id)
    return render_widget(request, conn)


def _companions(request: Request, conn: Connection, activity_id: int):
    conn.commit()
    return templates.TemplateResponse(
        request,
        "_companions.html",
        {
            "ride": queries.ride(conn, activity_id),
            "ride_companions": queries.ride_companions(conn, activity_id),
            "companions": queries.companions(conn),
        },
    )


@router.post("/rides/{activity_id}/companions")
def companion_add(
    activity_id: int, request: Request, name: str = Form(...), conn: Connection = Depends(get_conn)
):
    _ride_or_404(conn, activity_id)
    companions.add(conn, activity_id, name)
    return _companions(request, conn, activity_id)


@router.post("/rides/{activity_id}/companions/{companion_id}/remove")
def companion_remove(
    activity_id: int, companion_id: int, request: Request, conn: Connection = Depends(get_conn)
):
    _ride_or_404(conn, activity_id)
    companions.remove(conn, activity_id, companion_id)
    return _companions(request, conn, activity_id)
