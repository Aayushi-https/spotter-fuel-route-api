from __future__ import annotations

import math
import threading
from pathlib import Path

import geonamescache
import pandas as pd
import requests

from django.conf import settings


class RoutePlanningError(Exception):
    pass


# ---------------------------------------------------------
# HTTP SESSION
# ---------------------------------------------------------

_session = requests.Session()

_session.headers.update({
    "User-Agent": (
        "SpotterFuelRouteAssessment/1.0 "
        "(backend coding assessment)"
    )
})


# ---------------------------------------------------------
# SIMPLE MEMORY CACHE
# ---------------------------------------------------------

_cache = {}
_lock = threading.Lock()


def _cached(key, loader):
    with _lock:
        if key in _cache:
            return _cache[key]

    value = loader()

    with _lock:
        _cache[key] = value

    return value


# ---------------------------------------------------------
# EXTERNAL API HELPER
# ---------------------------------------------------------

def _get_json(url, params):
    try:
        response = _session.get(
            url,
            params=params,
            timeout=settings.EXTERNAL_TIMEOUT_SECONDS,
        )

        response.raise_for_status()

        return response.json()

    except requests.RequestException as exc:
        raise RoutePlanningError(
            f"External service error: {exc}"
        ) from exc


# ---------------------------------------------------------
# GEOCODING
# ---------------------------------------------------------

def geocode(query):
    """
    Geocode the user's start/finish locations.

    This is only used for the two locations supplied by
    the user, NOT for every fuel station.
    """

    key = (
        "geocode",
        query.casefold(),
    )

    def load():

        data = _get_json(
            settings.NOMINATIM_URL,
            {
                "q": query,
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "us",
            },
        )

        if not data:
            raise RoutePlanningError(
                f"Could not find a US location for '{query}'."
            )

        item = data[0]

        return {
            "query": query,
            "display_name": item.get(
                "display_name",
                query,
            ),
            "latitude": float(item["lat"]),
            "longitude": float(item["lon"]),
        }

    return _cached(key, load)


# ---------------------------------------------------------
# ROUTING
# ---------------------------------------------------------

def route(start, finish):
    """
    Build a road route using OSRM.
    """

    key = (
        "route",
        round(start["latitude"], 5),
        round(start["longitude"], 5),
        round(finish["latitude"], 5),
        round(finish["longitude"], 5),
    )

    def load():

        coordinates = (
            f'{start["longitude"]},{start["latitude"]};'
            f'{finish["longitude"]},{finish["latitude"]}'
        )

        url = (
            f"{settings.OSRM_URL}/"
            f"{coordinates}"
        )

        data = _get_json(
            url,
            {
                "overview": "full",
                "geometries": "geojson",
                "steps": "false",
            },
        )

        if (
            data.get("code") != "Ok"
            or not data.get("routes")
        ):
            raise RoutePlanningError(
                "The routing service could not build this route."
            )

        route_data = data["routes"][0]

        return {
            "distance_miles": (
                route_data["distance"] / 1609.344
            ),
            "duration_minutes": (
                route_data["duration"] / 60
            ),
            "geometry": route_data["geometry"],
        }

    return _cached(key, load)


# ---------------------------------------------------------
# CSV COLUMN NORMALISATION
# ---------------------------------------------------------

def _normalise_columns(df):
    """
    Convert different possible CSV column names into the
    internal names used by the application.
    """

    original = {
        str(column).strip().lower().replace(" ", "_"): column
    for column in df.columns
    }

    aliases = {

        "latitude": [
            "latitude",
            "lat",
            "y",
            "fuel_latitude",
            "station_latitude",
        ],

        "longitude": [
            "longitude",
            "lon",
            "lng",
            "long",
            "x",
            "fuel_longitude",
            "station_longitude",
        ],

        "price": [
            "price",
            "fuel_price",
            "gas_price",
            "retail_price",
            "regular_price",
            "gasoline_price",
        ],

        "name": [
            "station_name",
            "truckstop name",
            "truckstop_name",
            "name",
            "brand",
            "station",
        ],

        "city": [
            "city",
            "station_city",
        ],

        "state": [
            "state",
            "state_code",
            "state_abbr",
            "province",
        ],
    }

    rename = {}

    for target, choices in aliases.items():

        for choice in choices:

            if choice in original:
                rename[original[choice]] = target
                break

    out = df.rename(
        columns=rename
    ).copy()

    # Latitude/longitude are optional because your Spotter
    # CSV does NOT contain them.
    if "price" not in out.columns:

        raise RoutePlanningError(
            "Fuel CSV is missing required column: price. "
            f"Available columns: {', '.join(map(str, df.columns))}"
        )

    if "city" not in out.columns:

        raise RoutePlanningError(
            "Fuel CSV is missing required column: city."
        )

    if "state" not in out.columns:

        raise RoutePlanningError(
            "Fuel CSV is missing required column: state."
        )

    # -----------------------------------------------------
    # PRICE
    # -----------------------------------------------------

    out["price"] = pd.to_numeric(
        out["price"]
        .astype(str)
        .str.replace(
            "$",
            "",
            regex=False,
        )
        .str.replace(
            ",",
            "",
            regex=False,
        )
        .str.strip(),
        errors="coerce",
    )

    # -----------------------------------------------------
    # CITY / STATE
    # -----------------------------------------------------

    out["city"] = (
        out["city"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    out["state"] = (
        out["state"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # -----------------------------------------------------
    # NAME
    # -----------------------------------------------------

    if "name" not in out.columns:

        out["name"] = "Fuel station"

    else:

        out["name"] = (
            out["name"]
            .fillna("Fuel station")
            .astype(str)
            .str.strip()
        )

    # Remove invalid rows.

    out = out.dropna(
        subset=[
            "price",
        ]
    )

    out = out[
        (out["price"] > 0)
        & (out["city"] != "")
        & (out["state"] != "")
    ]

    return out.reset_index(
        drop=True
    )


# ---------------------------------------------------------
# LOAD FUEL CSV
# ---------------------------------------------------------

def fuel_stations():

    path = Path(
        settings.FUEL_DATA_PATH
    )

    if not path.exists():

        raise RoutePlanningError(
            "Fuel-price dataset not found at "
            f"'{path}'. "
            "Place the Spotter CSV there and name it "
            "fuel_prices.csv."
        )

    key = (
        "fuel",
        str(path.resolve()),
        path.stat().st_mtime_ns,
    )

    def load():

        try:

            df = pd.read_csv(
                path
            )

        except Exception as exc:

            raise RoutePlanningError(
                f"Could not read fuel CSV: {exc}"
            ) from exc

        return _normalise_columns(
            df
        ).to_dict(
            "records"
        )

    return _cached(
        key,
        load,
    )


# ---------------------------------------------------------
# LOCAL GEONAMES DATABASE
# ---------------------------------------------------------

_geonames = geonamescache.GeonamesCache()

_US_CITY_INDEX = None


def _build_us_city_index():

    global _US_CITY_INDEX

    if _US_CITY_INDEX is not None:
        return _US_CITY_INDEX

    cities = _geonames.get_cities()

    index = {}

    for city in cities.values():

        if city.get("countrycode") != "US":
            continue

        name = str(
            city.get(
                "name",
                "",
            )
        ).strip().casefold()

        state = str(
            city.get(
                "admin1code",
                "",
            )
        ).strip().casefold()

        latitude = city.get(
            "latitude"
        )

        longitude = city.get(
            "longitude"
        )

        if (
            not name
            or latitude is None
            or longitude is None
        ):
            continue

        key = (
            f"{name}|{state}"
        )

        index[key] = {
            "latitude": float(latitude),
            "longitude": float(longitude),
        }

    _US_CITY_INDEX = index

    return index


def local_city_coordinates(
    city,
    state,
):
    """
    Find US city coordinates locally.

    No internet request is made here.
    """

    index = _build_us_city_index()

    city_name = (
        str(city)
        .strip()
        .casefold()
    )

    state_name = (
        str(state)
        .strip()
        .casefold()
    )

    exact_key = (
        f"{city_name}|{state_name}"
    )

    result = index.get(
        exact_key
    )

    if result:
        return result

    # Fallback to city-name-only matching.

    for key, location in index.items():

        stored_city, stored_state = (
            key.split(
                "|",
                1,
            )
        )

        if stored_city == city_name:

            return location

    return None


# ---------------------------------------------------------
# DISTANCE
# ---------------------------------------------------------

def haversine_miles(
    lat1,
    lon1,
    lat2,
    lon2,
):

    earth_radius = 3958.7613

    p1 = math.radians(
        lat1
    )

    p2 = math.radians(
        lat2
    )

    delta_lat = math.radians(
        lat2 - lat1
    )

    delta_lon = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(delta_lat / 2) ** 2
        +
        math.cos(p1)
        * math.cos(p2)
        * math.sin(delta_lon / 2) ** 2
    )

    return (
        2
        * earth_radius
        * math.asin(
            math.sqrt(a)
        )
    )


# ---------------------------------------------------------
# ROUTE CUMULATIVE DISTANCE
# ---------------------------------------------------------

def route_cumulative_miles(
    coordinates,
):

    output = [0.0]

    for index in range(
        1,
        len(coordinates),
    ):

        lon1, lat1 = (
            coordinates[index - 1]
        )

        lon2, lat2 = (
            coordinates[index]
        )

        distance = haversine_miles(
            lat1,
            lon1,
            lat2,
            lon2,
        )

        output.append(
            output[-1] + distance
        )

    return output


# ---------------------------------------------------------
# FIND FUEL STATIONS NEAR ROUTE
# ---------------------------------------------------------

def station_candidates(
    route_info,
):

    coordinates = route_info[
        "geometry"
    ][
        "coordinates"
    ]

    cumulative = route_cumulative_miles(
        coordinates
    )

    # -----------------------------------------------------
    # SAMPLE THE ROUTE
    # -----------------------------------------------------

    # Instead of comparing every station against potentially
    # thousands of route coordinates, sample about 150 points.

    stride = max(
        1,
        len(coordinates) // 150,
    )

    route_points = []

    for index in range(
        0,
        len(coordinates),
        stride,
    ):

        lon, lat = coordinates[index]

        route_points.append(
            (
                lon,
                lat,
                cumulative[index],
            )
        )

    # Always include destination.

    if (
        route_points[-1][0],
        route_points[-1][1],
    ) != (
        coordinates[-1][0],
        coordinates[-1][1],
    ):

        route_points.append(
            (
                coordinates[-1][0],
                coordinates[-1][1],
                cumulative[-1],
            )
        )

    # -----------------------------------------------------
    # LOAD STATIONS
    # -----------------------------------------------------

    stations = fuel_stations()

    # City coordinate cache.

    city_cache = {}

    candidates = []

    # -----------------------------------------------------
    # PROCESS STATIONS
    # -----------------------------------------------------

    for station in stations:

        city = str(
            station.get(
                "city",
                "",
            )
        ).strip()

        state = str(
            station.get(
                "state",
                "",
            )
        ).strip()

        if not city or not state:
            continue

        city_key = (
            f"{city.casefold()}|"
            f"{state.casefold()}"
        )

        # Resolve each unique city only once.

        if city_key not in city_cache:

            city_cache[
                city_key
            ] = local_city_coordinates(
                city,
                state,
            )

        location = city_cache[
            city_key
        ]

        if location is None:
            continue

        station_lat = location[
            "latitude"
        ]

        station_lon = location[
            "longitude"
        ]

        # -------------------------------------------------
        # FIND CLOSEST ROUTE POINT
        # -------------------------------------------------

        best_distance = float(
            "inf"
        )

        best_route_mile = None

        for (
            route_lon,
            route_lat,
            route_mile,
        ) in route_points:

            distance = haversine_miles(
                station_lat,
                station_lon,
                route_lat,
                route_lon,
            )

            if distance < best_distance:

                best_distance = distance

                best_route_mile = (
                    route_mile
                )

        # Keep stations within 15 miles
        # of the route.

        if (
            best_route_mile is not None
            and best_distance <= 15
        ):

            station_copy = dict(
                station
            )
            
            station_copy["type"] = "station"

            station_copy[
                "latitude"
            ] = station_lat

            station_copy[
                "longitude"
            ] = station_lon

            station_copy[
                "route_mile"
            ] = float(
                best_route_mile
            )

            station_copy[
                "distance_from_route_miles"
            ] = float(
                best_distance
            )

            candidates.append(
                station_copy
            )

    # -----------------------------------------------------
    # SORT BY ROUTE POSITION
    # -----------------------------------------------------

    candidates.sort(
        key=lambda station: (
            station["route_mile"],
            station["price"],
        )
    )

    # -----------------------------------------------------
    # REDUCE DUPLICATE STATIONS
    # -----------------------------------------------------

    compact = []

    for station in candidates:

        if not compact:

            compact.append(
                station
            )

            continue

        previous = compact[-1]

        if (
            abs(
                station["route_mile"]
                - previous["route_mile"]
            )
            >= 5
        ):

            compact.append(
                station
            )

    return compact


# ---------------------------------------------------------
# FUEL PLAN
# ---------------------------------------------------------

def choose_fuel_plan(
    distance_miles,
    stations,
    max_range=500.0,
    mpg=10.0,
):

    # No fuel stop needed.

    if distance_miles <= max_range:

        return [], 0.0

    nodes = [
        {
            "type": "start",
            "route_mile": 0.0,
            "price": None,
        }
    ]

    nodes.extend(
        stations
    )

    nodes.append(
        {
            "type": "destination",
            "route_mile": distance_miles,
            "price": None,
        }
    )

    nodes.sort(
        key=lambda node:
        node["route_mile"]
    )

    infinity = float(
        "inf"
    )

    cost = [
        infinity
        for _ in nodes
    ]

    previous = [
        None
        for _ in nodes
    ]

    cost[0] = 0.0

    # -----------------------------------------------------
    # DYNAMIC PROGRAMMING
    # -----------------------------------------------------

    for i in range(
        len(nodes)
    ):

        if cost[i] == infinity:
            continue

        for j in range(
            i + 1,
            len(nodes),
        ):

            leg_distance = (
                nodes[j]["route_mile"]
                - nodes[i]["route_mile"]
            )

            if (
                leg_distance
                > max_range
            ):
                break

            if (
                nodes[i]["type"]
                == "destination"
            ):
                continue

            # Starting tank is full.

            if i == 0:

                added_cost = 0.0

            else:

                added_cost = (
                    leg_distance
                    / mpg
                    * float(
                        nodes[i]["price"]
                    )
                )

            new_cost = (
                cost[i]
                + added_cost
            )

            if new_cost < cost[j]:

                cost[j] = new_cost

                previous[j] = i

    destination_index = (
        len(nodes) - 1
    )

    if (
        cost[destination_index]
        == infinity
    ):

        raise RoutePlanningError(
            "The supplied fuel-price dataset "
            "does not provide enough reachable "
            "stations for this route within the "
            "500-mile vehicle range."
        )

    # -----------------------------------------------------
    # REBUILD STOP LIST
    # -----------------------------------------------------

    stops = []

    index = destination_index

    while (
        previous[index]
        is not None
    ):

        index = previous[index]

        if (
            nodes[index]["type"]
            == "station"
        ):

            stops.append(
                nodes[index]
            )

    stops.reverse()

    return (
        stops,
        round(
            cost[destination_index],
            2,
        ),
    )


# ---------------------------------------------------------
# COMPLETE ROUTE PLAN
# ---------------------------------------------------------

def build_route_plan(
    start_query,
    finish_query,
):

    # -----------------------------------------------------
    # 1. GEOCODE START
    # -----------------------------------------------------

    start = geocode(
        start_query
    )

    # -----------------------------------------------------
    # 2. GEOCODE FINISH
    # -----------------------------------------------------

    finish = geocode(
        finish_query
    )

    # -----------------------------------------------------
    # 3. BUILD ROAD ROUTE
    # -----------------------------------------------------

    route_info = route(
        start,
        finish,
    )

    # -----------------------------------------------------
    # 4. FIND FUEL STATIONS
    # -----------------------------------------------------

    stations = station_candidates(
        route_info
    )

    # -----------------------------------------------------
    # 5. CHOOSE FUEL PLAN
    # -----------------------------------------------------

    stops, total_cost = choose_fuel_plan(
        route_info["distance_miles"],
        stations,
    )

    # -----------------------------------------------------
    # 6. RETURN API RESPONSE
    # -----------------------------------------------------

    return {

        "start": start,

        "finish": finish,

        "vehicle": {
            "max_range_miles": 500.0,
            "mpg": 10.0,
            "starting_with_full_tank": True,
        },

        "route": {
            "distance_miles": round(
                route_info[
                    "distance_miles"
                ],
                2,
            ),

            "duration_minutes": round(
                route_info[
                    "duration_minutes"
                ],
                1,
            ),

            "geometry": route_info[
                "geometry"
            ],
        },

        "fuel_plan": {

            "stops": [

                {
                    "name": str(
                        station.get(
                            "name",
                            "Fuel station",
                        )
                    ),

                    "city": str(
                        station.get(
                            "city",
                            "",
                        )
                    ),

                    "state": str(
                        station.get(
                            "state",
                            "",
                        )
                    ),

                    "latitude": float(
                        station[
                            "latitude"
                        ]
                    ),

                    "longitude": float(
                        station[
                            "longitude"
                        ]
                    ),

                    "fuel_price_per_gallon": round(
                        float(
                            station[
                                "price"
                            ]
                        ),
                        3,
                    ),

                    "route_mile": round(
                        float(
                            station[
                                "route_mile"
                            ]
                        ),
                        1,
                    ),

                    "distance_from_route_miles": round(
                        float(
                            station[
                                "distance_from_route_miles"
                            ]
                        ),
                        2,
                    ),
                }

                for station in stops
            ],

            "total_fuel_gallons": round(
                route_info[
                    "distance_miles"
                ] / 10,
                2,
            ),

            "total_fuel_cost_usd": round(
                total_cost,
                2,
            ),

            "stations_considered": len(
                stations
            ),
        },

        "meta": {

            "routing_provider": "OSRM",

            "geocoding_provider": (
                "OpenStreetMap Nominatim "
                "for start/finish only"
            ),

            "fuel_data": (
                "Spotter assessment CSV"
            ),
        },
    }