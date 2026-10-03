from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .services import (
    RoutePlanningError,
    build_route_plan,
)


def home(request):
    """
    Serve the Spotter Route Planner web interface.
    """
    return render(request, "index.html")


@require_GET
def health(request):
    """
    Health-check endpoint.
    """
    return JsonResponse({
        "status": "ok",
        "service": "Spotter Fuel Route API",
    })


@require_GET
def route_fuel_plan(request):
    """
    Build an optimized fuel route between two US locations.
    """

    start = request.GET.get("start", "").strip()
    finish = request.GET.get("finish", "").strip()

    # Validate required parameters
    if not start or not finish:
        return JsonResponse(
            {
                "error": (
                    "Both 'start' and 'finish' "
                    "query parameters are required."
                ),
                "example": (
                    "/api/route/"
                    "?start=Chicago, IL"
                    "&finish=Denver, CO"
                ),
            },
            status=400,
        )

    try:
        result = build_route_plan(
            start,
            finish,
        )

        return JsonResponse(
            result,
            status=200,
        )

    except RoutePlanningError as exc:
        return JsonResponse(
            {
                "error": str(exc),
                "error_type": "RoutePlanningError",
            },
            status=400,
        )

    except Exception as exc:
        # Print the complete traceback in the Django terminal.
        # This makes debugging much easier during development.
        import traceback

        traceback.print_exc()

        return JsonResponse(
            {
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
            status=500,
        )