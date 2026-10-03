# Spotter Fuel Route API

Django backend for the Spotter Backend Django Engineer assessment.

## Features
- US start/finish geocoding
- One OSRM route request after geocoding
- GeoJSON route geometry
- 500-mile maximum range
- 10 MPG vehicle model
- Fuel-stop optimization using the supplied Spotter CSV
- Total gallons and fuel cost
- In-process caching for repeated requests
- Automated tests
- No paid API keys required

## Setup
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py test
python manage.py runserver
```

Put the assessment dataset at `data/fuel_prices.csv`.

## Endpoints
`GET /api/health/`

`GET /api/route/?start=Chicago,%20IL&finish=Denver,%20CO`

## Free APIs
OpenStreetMap Nominatim is used for geocoding and OSRM for driving directions. No API key is required. Please respect the public services' usage policies and rate limits.

## Architecture
1. Geocode start and finish (cached).
2. Call OSRM once for the driving route (cached).
3. Load and normalize the local fuel dataset (cached).
4. Project stations onto the route and keep nearby candidates.
5. Use dynamic programming to select a reachable cost-effective sequence.

## Note
The real Spotter CSV is not included here because it is assessment-provided data. Copy it into `data/fuel_prices.csv` before testing the route endpoint.
