"""Download building coordinates from the Cornell campus map.

Usage: python3 scraper/fetch_buildings.py

The roster's room links point at cornell.edu/about/maps/?q=<Building>, and
<Building> is the map's location Name, so the two join on name directly.
Writes data/raw/buildings.json.
"""
import roster


def main():
    locations = roster.get_json(roster.MAP_DATA_URL)["locations"]
    roster.write_json(roster.RAW_DIR / "buildings.json", locations)
    print(f"{len(locations)} map locations")


if __name__ == "__main__":
    main()
