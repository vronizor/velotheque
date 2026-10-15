from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import Connection

from velotheque import queries
from velotheque.config import get_settings
from velotheque.web.deps import get_conn, templates
from velotheque.web.jobs import render_widget, submit

router = APIRouter()


@router.get("/rides/{activity_id}")
def ride_page(activity_id: int, request: Request, conn: Connection = Depends(get_conn)):
    ride = queries.ride(conn, activity_id)
    if ride is None:
        raise HTTPException(404, "Ride not found")
    return templates.TemplateResponse(
        request,
        "ride.html",
        {
            "ride": ride,
            "track": queries.ride_track_geojson(conn, activity_id),
            "photos": queries.ride_photos(conn, activity_id),
            "map_style_url": get_settings().map_style_url,
        },
    )


@router.post("/rides/{activity_id}/refresh")
def ride_refresh(activity_id: int, request: Request, conn: Connection = Depends(get_conn)):
    ride = queries.ride(conn, activity_id)
    if ride is None:
        raise HTTPException(404, "Ride not found")
    submit("refresh", ride.source_id)
    return render_widget(request, conn)
