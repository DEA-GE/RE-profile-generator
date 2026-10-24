import atlite
import os
import logging
import yaml
from utils import *


logging.basicConfig(level=logging.INFO)

dirname = os.getcwd()

with open(os.path.join(dirname, "config.yaml"), "r", encoding="utf-8") as f:
    CONFIG = yaml.load(f, Loader=yaml.FullLoader)

cutouts_dir = os.path.join(dirname, CONFIG["cutout_dir"])
os.makedirs(cutouts_dir, exist_ok=True)

custom_filename = CONFIG["study_area"].get("custom_study_area_filename")
custom_polygon_path = os.path.join("input_data", custom_filename) if custom_filename else None
cutout_start = CONFIG["cutout_timeframe"]["start"]
cutout_end = CONFIG["cutout_timeframe"]["end"]
study_region_name = CONFIG["study_region_name"]


region = get_study_area_polygon(source=CONFIG["study_area"].get("source"),
                                adm_source=CONFIG["study_area"].get("GADM_source"),
                                country_code=CONFIG.get("country_code"),
                                level=CONFIG["study_area"].get("GADM_level"),
                                admin_name=CONFIG["study_area"].get("GADM_region_name"),
                                custom_polygon_path=custom_polygon_path,
                                bbox=CONFIG["study_area"].get("bbox"))

region.to_file(os.path.join(dirname, 'input_data', f"{study_region_name}.geojson"), index=False)

# calculate bounding box which is 0.3 degrees bigger
if not CONFIG["study_area"].get("source") == "bbox":
    d = 0.3
    bounds = region.total_bounds + [-d, -d, d, d]
elif CONFIG["study_area"].get("source") == "bbox":
    bounds = CONFIG["study_area"].get("bbox")
print(
    f"Bounding box (EPSG:4326): \nminx: {bounds[0]}, miny: {bounds[1]}, maxx: {bounds[2]}, maxy: {bounds[3]}"
)


print(
    f"\nDownloading weather data: \n start: {cutout_start} \n end: {cutout_end}"
)
# download settings
cutout = atlite.Cutout(
    path=os.path.join(
        cutouts_dir,
        f"{CONFIG['cutout_name'].format(study_region_name=study_region_name, year=str(CONFIG["cutout_timeframe"]["start"])[:4])}.nc",
    ),
    module=CONFIG["cutout_modules"],
    x=slice(bounds[0], bounds[2]),
    y=slice(bounds[1], bounds[3]),
    #       time=str(weather_year)
    time=slice(
        f"{cutout_start}", f"{cutout_end}"
    ),
)

# download
#print("\ncheck status: https://cds.climate.copernicus.eu/requests?tab=all\n")
cutout.prepare(
    features=CONFIG["cutout_features"],
#        monthly_requests=True,
#        concurrent_requests=True,
#        compression=None,
)
