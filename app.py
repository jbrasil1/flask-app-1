# app.py
from flask import Flask, render_template, request, url_for
import requests
from bs4 import BeautifulSoup
import datetime
from collections import defaultdict
import urllib.parse  # For URL encoding

app = Flask(__name__)

# Cache for counties (same as before)
COUNTIES_CACHE = None
CACHE_TIME = None

# New: Cache for geocodes (water|county -> (lat, lon))
GEOCODE_CACHE = {}

def get_counties_from_cdfw():
    global COUNTIES_CACHE, CACHE_TIME
    now = datetime.datetime.now()
    if COUNTIES_CACHE and CACHE_TIME and (now - CACHE_TIME).total_seconds() < 3600:
        return COUNTIES_CACHE

    try:
        response = requests.get("https://nrm.dfg.ca.gov/FishPlants/", timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        counties = set()
        for row in soup.find_all('tr')[1:]:
            cols = row.find_all('td')
            if len(cols) >= 3:
                county_text = cols[2].get_text(strip=True)
                if county_text:
                    counties.add(county_text.title())
        COUNTIES_CACHE = sorted(counties)
        CACHE_TIME = now
        return COUNTIES_CACHE
    except:
        # Fallback list (paste your full list here)
        return sorted([
            "Alameda", "Alpine", "Amador", "Butte", "Calaveras", "Colusa", "Contra Costa",
            "Del Norte", "El Dorado", "Fresno", "Glenn", "Humboldt", "Imperial", "Inyo",
            "Kern", "Kings", "Lake", "Lassen", "Los Angeles", "Madera", "Marin", "Mariposa",
            "Mendocino", "Merced", "Modoc", "Mono", "Monterey", "Napa", "Nevada", "Orange",
            "Placer", "Plumas", "Riverside", "Sacramento", "San Benito", "San Bernardino",
            "San Diego", "San Francisco", "San Joaquin", "San Luis Obispo", "San Mateo",
            "Santa Barbara", "Santa Clara", "Santa Cruz", "Shasta", "Sierra", "Siskiyou",
            "Solano", "Sonoma", "Stanislaus", "Sutter", "Tehama", "Trinity", "Tulare",
            "Tuolumne", "Ventura", "Yolo", "Yuba"
        ])

def get_fish_plants_for_county(county_input):
    url = "https://nrm.dfg.ca.gov/FishPlants/"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
    except Exception as e:
        return None, str(e)

    soup = BeautifulSoup(response.text, 'html.parser')
    table = soup.find('table')
    if not table:
        return None, "Fish planting table not found."

    rows = table.find_all('tr')[1:]
    plants = []
    today = datetime.date.today()

    for row in rows:
        cols = row.find_all('td')
        if len(cols) < 4:
            continue

        # Clean water name
        water_td = cols[1]
        water_name = ""
        for content in water_td.contents:
            if isinstance(content, str):
                water_name += content.strip()
            elif content.name == 'a':
                break
        water_name = water_name.strip()
        if not water_name:
            continue

        county_text = cols[2].get_text(strip=True)
        if county_input.lower() not in county_text.lower():
            continue

        week_text = cols[0].get_text(strip=True)
        species = cols[3].get_text(strip=True)

        start_str = week_text.split(" - ")[0] if " - " in week_text else week_text.split("-")[0]
        try:
            start_date = datetime.datetime.strptime(start_str.strip(), "%m/%d/%Y").date()
        except ValueError:
            continue

        plants.append({
            'water': water_name,
            'date': start_date,
            'week': week_text,
            'species': species
        })

    # Group and find recent/upcoming
    grouped = defaultdict(list)
    for p in plants:
        grouped[p['water']].append(p)

    results = {}
    for water, entries in grouped.items():
        entries.sort(key=lambda x: x['date'])
        recent = next((e for e in reversed(entries) if e['date'] <= today), None)
        upcoming = next((e for e in entries if e['date'] > today), None)
        results[water] = {'recent': recent, 'upcoming': upcoming}

    return dict(sorted(results.items())), None

def geocode_water(water, county):
    key = f"{water.lower()}|{county.lower()}"
    if key in GEOCODE_CACHE:
        return GEOCODE_CACHE[key]

    query = f"{water}, {county} County, California"
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": query,
        "format": "json",
        "limit": 1
    }
    headers = {
        "User-Agent": "FishPlantsApp/1.0 (jbrasil1@csustan.edu)"  # IMPORTANT: Replace with your real email/contact for Nominatim compliance
    }
    try:
        response = requests.get(url, params=params, headers=headers, timeout=5)
        response.raise_for_status()
        data = response.json()
        if data:
            lat = float(data[0]['lat'])
            lon = float(data[0]['lon'])
            GEOCODE_CACHE[key] = (lat, lon)
            return (lat, lon)
    except Exception as e:
        print(f"Geocoding error: {e}")  # For debugging in console
        pass
    GEOCODE_CACHE[key] = None
    return None

@app.route('/')
def index():
    counties = get_counties_from_cdfw()
    return render_template('index.html', counties=counties)

@app.route('/results', methods=['GET'])
def results():
    county = request.args.get('county')
    if not county:
        return render_template('index.html', counties=get_counties_from_cdfw(), error="Please select a county.")

    results, error = get_fish_plants_for_county(county)
    if error:
        return render_template('index.html', counties=get_counties_from_cdfw(), error=error)

    today_str = datetime.date.today().strftime("%B %d, %Y")
    return render_template('results.html', county=county, results=results or {}, today=today_str)

@app.route('/map/<county>/<water>')
def map_view(county, water):
    water = urllib.parse.unquote(water)  # Decode URL-encoded water name

    # Step 1: Try primary geocoder (Census, GNIS, etc.)
    coords = geocode_water(water, county)        # ← your best geocoder here

    # Step 2: Fallback to county seat if primary failed
    if not coords:
        coords = get_county_seat_coords(county)
        message = f"Exact location for '{water}' not found — showing {county} County seat."
    else:
        message = None

    # Step 3: Final safety net – should almost never happen
    if not coords:
        coords = (36.7783, -119.4179)  # Center of California
        message = "Location and county seat not found — showing center of California."

    # Now we are 100% sure coords is a tuple (lat, lon)
    lat, lon = coords

    return render_template(
        'map.html',
        lat=lat,
        lon=lon,
        water=water,
        county=county,
        message=message
    )

COUNTY_SEATS = {
    "Alameda": (37.7799, -122.2827),       # Oakland
    "Alpine": (38.5969, -119.8210),        # Markleeville
    "Amador": (38.3477, -120.7741),        # Jackson
    "Butte": (39.7285, -121.8375),         # Oroville
    "Calaveras": (38.1960, -120.6805),     # San Andreas
    "Colusa": (39.2143, -122.0094),        # Colusa
    "Contra Costa": (37.9735, -122.0311),  # Martinez
    "Del Norte": (41.7542, -124.2006),     # Crescent City
    "El Dorado": (38.7296, -120.7985),     # Placerville
    "Fresno": (36.7378, -119.7871),        # Fresno
    "Glenn": (39.5200, -122.1936),         # Willows
    "Humboldt": (40.8021, -124.1637),      # Eureka
    "Imperial": (32.8473, -115.5694),      # El Centro
    "Inyo": (36.6044, -118.0625),          # Independence
    "Kern": (35.3733, -118.9854),          # Bakersfield
    "Kings": (36.0726, -119.8154),         # Hanford
    "Lake": (38.9444, -122.6264),          # Lakeport
    "Lassen": (40.4163, -120.6620),        # Susanville
    "Los Angeles": (34.0522, -118.2437),   # Los Angeles
    "Madera": (36.9613, -120.0607),        # Madera
    "Marin": (37.9358, -122.5311),         # San Rafael
    "Mariposa": (37.4849, -119.9663),      # Mariposa
    "Mendocino": (39.3077, -123.7995),     # Ukiah
    "Merced": (37.3022, -120.4830),        # Merced
    "Modoc": (41.4899, -120.7243),         # Alturas
    "Mono": (37.9399, -118.8800),          # Bridgeport (very accurate!)
    "Monterey": (36.6002, -121.8947),      # Salinas
    "Napa": (38.2975, -122.2869),          # Napa
    "Nevada": (39.2616, -121.0180),        # Nevada City
    "Orange": (33.7878, -117.8531),        # Santa Ana
    "Placer": (39.0620, -120.7229),        # Auburn
    "Plumas": (39.9380, -120.9033),        # Quincy
    "Riverside": (33.9806, -117.3755),     # Riverside
    "Sacramento": (38.5816, -121.4944),    # Sacramento
    "San Benito": (36.8454, -121.3542),    # Hollister
    "San Bernardino": (34.1083, -117.2898),# San Bernardino
    "San Diego": (32.7157, -117.1611),     # San Diego
    "San Francisco": (37.7749, -122.4194), # San Francisco
    "San Joaquin": (37.9349, -121.2730),   # Stockton
    "San Luis Obispo": (35.2828, -120.6596),# San Luis Obispo
    "San Mateo": (37.5630, -122.3255),     # Redwood City
    "Santa Barbara": (34.4208, -119.6982), # Santa Barbara
    "Santa Clara": (37.3541, -121.9552),   # San Jose
    "Santa Cruz": (36.9741, -122.0308),    # Santa Cruz
    "Shasta": (40.5865, -122.3917),        # Redding
    "Sierra": (39.5920, -120.3680),        # Downieville
    "Siskiyou": (41.5900, -122.6370),      # Yreka
    "Solano": (38.2682, -122.0390),        # Fairfield
    "Sonoma": (38.4404, -122.7141),        # Santa Rosa
    "Stanislaus": (37.5585, -120.9977),    # Modesto
    "Sutter": (39.0646, -121.6147),        # Yuba City
    "Tehama": (40.0240, -122.2350),        # Red Bluff
    "Trinity": (40.7359, -122.9459),       # Weaverville
    "Tulare": (36.2072, -118.8020),        # Visalia
    "Tuolumne": (37.9625, -120.2400),      # Sonora
    "Ventura": (34.2805, -119.2945),       # Ventura
    "Yolo": (38.7585, -121.7439),          # Woodland
    "Yuba": (39.1371, -121.5835),          # Marysville
}

def get_county_seat_coords(county_name):
    """Return (lat, lon) of county seat, or None"""
    # Normalize name
    clean = county_name.replace(" County", "").strip().title()
    return COUNTY_SEATS.get(clean)

if __name__ == '__main__':
    app.run(debug=True)