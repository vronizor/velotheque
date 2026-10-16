from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import Connection

from velotheque import components
from velotheque.components import ComponentError
from velotheque.tables import INTERVAL_UNITS, RIDE_SCOPES
from velotheque.web.deps import get_conn, templates

router = APIRouter()
DUE_LIST_FROM = 0.5


def _now_input(zone) -> str:
    return datetime.now(UTC).astimezone(zone).strftime(components.LOCAL_INPUT)


def _base_context(conn: Connection, request: Request) -> dict:
    zone = components.local_zone(conn)
    return {
        "zone": zone,
        "now_input": _now_input(zone),
        "bikes": components.bikes(conn),
        "kinds": components.kinds(conn),
        "units": INTERVAL_UNITS,
        "scopes": RIDE_SCOPES,
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
    }


@router.get("/components")
def components_page(request: Request, conn: Connection = Depends(get_conn)):
    context = _base_context(conn, request)
    listed = components.listing(conn)
    usage: dict[int, list] = {}
    for u in components.usage(conn):
        usage.setdefault(u.component_id, []).append(u)
    mounted = [c for c in listed if c.bike_id is not None and c.retired_at is None]
    due = sorted(
        (
            (u, c)
            for c in mounted
            for u in usage.get(c.id, [])
            if (u.fraction or 0) >= DUE_LIST_FROM
        ),
        key=lambda pair: -pair[0].fraction,
    )
    context.update(
        mounted={b.id: [c for c in mounted if c.bike_id == b.id] for b in context["bikes"]},
        empty_bikes={b.id for b in context["bikes"]} - {c.bike_id for c in mounted},
        spares=[c for c in listed if c.bike_id is None and c.retired_at is None],
        retired=[c for c in listed if c.retired_at is not None],
        usage=usage,
        due=due,
        lifetime=components.lifetime(conn),
        schedules=components.with_schedule(conn),
        no_bike=components.rides_without_bike(conn),
    )
    return templates.TemplateResponse(request, "components.html", context)


def _component_or_404(conn: Connection, component_id: int):
    found = components.detail(conn, component_id)
    if found is None:
        raise HTTPException(404, "Component not found")
    return found


@router.get("/components/{component_id}")
def component_page(component_id: int, request: Request, conn: Connection = Depends(get_conn)):
    context = _base_context(conn, request)
    context.update(
        component=_component_or_404(conn, component_id),
        usage=components.usage(conn, [component_id]),
        history=components.history(conn, component_id),
        events=components.events(conn, component_id),
        lifetime=components.lifetime(conn).get(component_id),
        schedules=[s for s in components.with_schedule(conn) if s.id != component_id],
    )
    return templates.TemplateResponse(request, "component.html", context)


def _done(conn: Connection, url: str, action, message: str | None = None) -> RedirectResponse:
    """Run an edit, commit, and go back to `url` with a message or the error."""
    path, _, anchor = url.partition("#")
    try:
        action()
    except ComponentError as error:
        conn.rollback()
        query = {"error": str(error)}
    else:
        conn.commit()
        query = {"message": message} if message else {}
    target = path + (f"?{urlencode(query)}" if query else "")
    return RedirectResponse(target + (f"#{anchor}" if anchor else ""), status_code=303)


def _page(component_id: int, anchor: str = "") -> str:
    return f"/components/{component_id}" + (f"#{anchor}" if anchor else "")


def _back(next_url: str, default: str) -> str:
    return next_url if next_url.startswith("/") and not next_url.startswith("//") else default


@router.post("/components")
def component_add(
    name: str = Form(...),
    kind: str = Form(""),
    brand: str = Form(""),
    model: str = Form(""),
    notes: str = Form(""),
    bike_id: str = Form(""),
    mounted_at: str = Form(""),
    copy_from: str = Form(""),
    conn: Connection = Depends(get_conn),
):
    zone = components.local_zone(conn)
    try:
        component_id = components.create(conn, name, kind, brand, model, notes)
        if bike_id:
            at = components.optional_local(mounted_at, zone) or datetime.now(UTC)
            components.mount(conn, component_id, int(bike_id), at)
        if copy_from:
            components.copy_schedule(conn, int(copy_from), component_id)
    except ComponentError as error:
        conn.rollback()
        return RedirectResponse(f"/components?{urlencode({'error': str(error)})}#add", 303)
    conn.commit()
    return RedirectResponse(_page(component_id) + "?message=Added", status_code=303)


@router.post("/components/{component_id}")
def component_edit(
    component_id: int,
    name: str = Form(...),
    kind: str = Form(""),
    brand: str = Form(""),
    model: str = Form(""),
    notes: str = Form(""),
    conn: Connection = Depends(get_conn),
):
    _component_or_404(conn, component_id)
    return _done(
        conn,
        _page(component_id),
        lambda: components.edit(conn, component_id, name, kind, brand, model, notes),
        "Saved",
    )


@router.post("/components/{component_id}/mount")
def component_mount(
    component_id: int,
    bike_id: int = Form(...),
    at: str = Form(...),
    conn: Connection = Depends(get_conn),
):
    _component_or_404(conn, component_id)
    zone = components.local_zone(conn)
    return _done(
        conn,
        _page(component_id),
        lambda: components.mount(conn, component_id, bike_id, components.parse_local(at, zone)),
        "Mounted",
    )


@router.post("/components/{component_id}/unmount")
def component_unmount(component_id: int, at: str = Form(...), conn: Connection = Depends(get_conn)):
    _component_or_404(conn, component_id)
    zone = components.local_zone(conn)
    return _done(
        conn,
        _page(component_id),
        lambda: components.unmount(conn, component_id, components.parse_local(at, zone)),
        "Unmounted",
    )


@router.post("/components/{component_id}/retire")
def component_retire(component_id: int, at: str = Form(...), conn: Connection = Depends(get_conn)):
    _component_or_404(conn, component_id)
    zone = components.local_zone(conn)
    return _done(
        conn,
        _page(component_id),
        lambda: components.retire(conn, component_id, components.parse_local(at, zone)),
        "Retired",
    )


@router.post("/components/{component_id}/unretire")
def component_unretire(component_id: int, conn: Connection = Depends(get_conn)):
    _component_or_404(conn, component_id)
    return _done(
        conn, _page(component_id), lambda: components.unretire(conn, component_id), "Back in use"
    )


@router.post("/components/{component_id}/delete")
def component_delete(component_id: int, conn: Connection = Depends(get_conn)):
    _component_or_404(conn, component_id)
    return _done(
        conn, "/components", lambda: components.delete_component(conn, component_id), "Deleted"
    )


@router.post("/components/{component_id}/assignments")
def assignment_add(
    component_id: int,
    bike_id: int = Form(...),
    mounted_at: str = Form(...),
    removed_at: str = Form(""),
    conn: Connection = Depends(get_conn),
):
    _component_or_404(conn, component_id)
    zone = components.local_zone(conn)
    return _done(
        conn,
        _page(component_id, "history"),
        lambda: components.add_assignment(
            conn,
            component_id,
            bike_id,
            components.parse_local(mounted_at, zone),
            components.optional_local(removed_at, zone),
        ),
        "Period added",
    )


@router.post("/components/{component_id}/assignments/{assignment_id}")
def assignment_edit(
    component_id: int,
    assignment_id: int,
    bike_id: int = Form(...),
    mounted_at: str = Form(...),
    removed_at: str = Form(""),
    conn: Connection = Depends(get_conn),
):
    _component_or_404(conn, component_id)
    zone = components.local_zone(conn)
    return _done(
        conn,
        _page(component_id, "history"),
        lambda: components.edit_assignment(
            conn,
            component_id,
            assignment_id,
            bike_id,
            components.parse_local(mounted_at, zone),
            components.optional_local(removed_at, zone),
        ),
        "Period saved",
    )


@router.post("/components/{component_id}/assignments/{assignment_id}/delete")
def assignment_delete(component_id: int, assignment_id: int, conn: Connection = Depends(get_conn)):
    _component_or_404(conn, component_id)
    return _done(
        conn,
        _page(component_id, "history"),
        lambda: components.delete_assignment(conn, component_id, assignment_id),
        "Period deleted",
    )


@router.post("/components/{component_id}/service-types")
def service_type_add(
    component_id: int,
    name: str = Form(...),
    level: str = Form("1"),
    interval_value: str = Form(...),
    interval_unit: str = Form("km"),
    rides: str = Form("all"),
    conn: Connection = Depends(get_conn),
):
    _component_or_404(conn, component_id)
    return _done(
        conn,
        _page(component_id, "service"),
        lambda: components.add_service_type(
            conn, component_id, name, level, interval_value, interval_unit, rides
        ),
        "Service added",
    )


@router.post("/components/{component_id}/service-types/{service_type_id}")
def service_type_edit(
    component_id: int,
    service_type_id: int,
    name: str = Form(...),
    level: str = Form("1"),
    interval_value: str = Form(...),
    interval_unit: str = Form("km"),
    rides: str = Form("all"),
    conn: Connection = Depends(get_conn),
):
    _component_or_404(conn, component_id)
    return _done(
        conn,
        _page(component_id, "service"),
        lambda: components.edit_service_type(
            conn, component_id, service_type_id, name, level, interval_value, interval_unit, rides
        ),
        "Service saved",
    )


@router.post("/components/{component_id}/service-types/{service_type_id}/delete")
def service_type_delete(
    component_id: int, service_type_id: int, conn: Connection = Depends(get_conn)
):
    _component_or_404(conn, component_id)
    return _done(
        conn,
        _page(component_id, "service"),
        lambda: components.delete_service_type(conn, component_id, service_type_id),
        "Service deleted",
    )


@router.post("/components/{component_id}/schedule")
def schedule_copy(
    component_id: int, copy_from: int = Form(...), conn: Connection = Depends(get_conn)
):
    _component_or_404(conn, component_id)
    return _done(
        conn,
        _page(component_id, "service"),
        lambda: components.copy_schedule(conn, copy_from, component_id),
        "Schedule copied",
    )


@router.post("/components/{component_id}/events")
def event_add(
    component_id: int,
    service_type_id: str = Form(""),
    at: str = Form(""),
    notes: str = Form(""),
    next_url: str = Form("", alias="next"),
    conn: Connection = Depends(get_conn),
):
    """Log a service (or a note). Without a date it's now: the quick "Log" buttons."""
    _component_or_404(conn, component_id)
    zone = components.local_zone(conn)
    return _done(
        conn,
        _back(next_url, _page(component_id, "log")),
        lambda: components.log_event(
            conn,
            component_id,
            int(service_type_id) if service_type_id else None,
            components.optional_local(at, zone) or datetime.now(UTC),
            notes,
        ),
        "Logged",
    )


@router.post("/components/{component_id}/events/{event_id}/delete")
def event_delete(component_id: int, event_id: int, conn: Connection = Depends(get_conn)):
    _component_or_404(conn, component_id)
    return _done(
        conn,
        _page(component_id, "log"),
        lambda: components.delete_event(conn, component_id, event_id),
        "Entry deleted",
    )
