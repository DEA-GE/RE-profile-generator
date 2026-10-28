import requests
import pandas as pd
import json
import yaml
import os
import logging
import time
import geopandas as gpd
from io import StringIO
import re
from utils import to_52_weeks, build_turbine_assignment, build_turbine_hubheight_runs, split_runs_by_hub_height

logging.basicConfig(level=logging.INFO)

dirname = os.getcwd()
with open(os.path.join(dirname, "config.yaml"), "r", encoding="utf-8") as f:
    CONFIG = yaml.load(f, Loader=yaml.FullLoader)

study_region_name = CONFIG["study_region_name"]
technologies = CONFIG["technologies"]
enabled_techs = technologies.pop("enable", list(technologies.keys()))
technologies.pop("wind_speed_variable", None)
logging.info(f"  Technologies to process: {enabled_techs}")

# --- RE Ninja settings; defaults as in the original download script ---
ninja = CONFIG.get("ninja") or {}
dataset = ninja.get("dataset", "merra2")
capacity = ninja.get("capacity", 1)
system_loss = ninja.get("system_loss", 0.1)
hub_height = ninja.get("height", 100)
default_tilt = ninja.get("tilt", 35)
default_azim = ninja.get("azim", 180)

# REninja API access
api_file = os.path.join(dirname, "REninjaAPI.yaml")
if not os.path.exists(api_file):
    raise FileNotFoundError(f"RE Ninja token file not found: {api_file} (expected content: token: <your token>)")
with open(api_file, "r", encoding="utf-8") as f:
    token = (yaml.safe_load(f) or {}).get("token")
if not token:
    raise ValueError(f"No 'token' entry in {api_file}")
api_base = 'https://www.renewables.ninja/api/'

s = requests.session()
# Send token header with each request
s.headers = {'Authorization': 'Token ' + token}

# --- Load region shapes ---
shapes_path = os.path.join(dirname, CONFIG["shapes_filepath"].format(study_region_name=study_region_name))
logging.info(f"Loading shapes from {shapes_path}")
shapes = gpd.read_file(shapes_path)
shapes = shapes.set_index(CONFIG.get("shapes_name_column", "name")).rename_axis("region")
# one point per polygon, guaranteed to lie inside it
points = shapes.to_crs(4326).geometry.representative_point()
coords = {name: (pt.y, pt.x) for name, pt in points.items()}
logging.info(f"  {len(coords)} regions loaded")

# --- Build time snapshots ---
ts_config = CONFIG["timeseries"]
year = ts_config["year"]
sns = pd.date_range(
    start=ts_config["start"].format(year=year, next_year=year + 1),
    end=ts_config["end"].format(year=year, next_year=year + 1),
    freq="h",
    inclusive="left",
)
if ts_config["drop_leap_day"]:
    sns = sns[~((sns.month == 2) & (sns.day == 29))]
if ts_config["test_mode"]:
    sns = sns[:168]  # first week only
date_from = sns[0].strftime("%Y-%m-%d")
date_to = sns[-1].strftime("%Y-%m-%d")

output_dir = os.path.join(dirname, "output")
os.makedirs(output_dir, exist_ok=True)


def _request(endpoint, args):
    # Wait 1s before making the request
    time.sleep(1)
    r = s.get(api_base + endpoint, params=args)
    if r.status_code != 200:
        raise RuntimeError(f"RE Ninja request failed ({r.status_code}): {r.text[:300]}")
    parsed_response = json.loads(r.text)
    data = pd.read_json(StringIO(json.dumps(parsed_response['data'])), orient='index')
    return data["electricity"], parsed_response['metadata']


def get_solar_data(lat, lon, date_from, date_to, dataset='merra2', capacity=1, system_loss=0.1, tracking=0, tilt=35, azim=180):
    args = {
        'lat': lat,
        'lon': lon,
        'date_from': date_from,
        'date_to': date_to,
        'dataset': dataset,
        'capacity': capacity,
        'system_loss': system_loss,
        'tracking': tracking,
        'tilt': tilt,
        'azim': azim,
        'format': 'json'
    }
    return _request('data/pv', args)


def get_wind_data(lat, lon, turbine, date_from, date_to, capacity=1.0, height=100):
    args = {
        'lat': lat,
        'lon': lon,
        'date_from': date_from,
        'date_to': date_to,
        'capacity': capacity,
        'height': height,
        'turbine': turbine,
        'format': 'json'
    }
    return _request('data/wind', args)


def to_api_turbine_name(name):
    """Convert 'Nordex.N43.600' (power-curve name) to the API name 'Nordex N43 600'."""
    decimal_model = re.fullmatch(r"([^.]+)\.(\d+)\.(\d+)", name)
    if decimal_model:
        return f"{decimal_model.group(1)} {decimal_model.group(2)}.{decimal_model.group(3)}"

    match = re.fullmatch(r"([^.]+)\.(.+)\.(\d+)", name)
    if match:
        return " ".join(match.groups())
    return name.replace(".", " ", 1)


# --- Download and save timeseries profiles for each technology ---
for tech_name in enabled_techs:
    tech_params = technologies[tech_name]
    resource = tech_params["resource"]
    method = resource["method"]
    correction_factor = tech_params.get("correction_factor", 1.0)
    clip_threshold = tech_params.get("clip_p_max_pu", None)
    logging.info(f"  Technology: {tech_name}")

    if tech_name == "windspeeds":
        logging.warning("    'windspeeds' is not available from RE Ninja, skipped")
        continue

    # runs: (turbine/panel name, regions, hub height override), built as in timeseries.py
    if method == "wind":
        turbine_column = resource.get("turbine_column_name")
        hubheight_column = resource.get("hubheight_column_name")
        for column in (turbine_column, hubheight_column):
            if column and column not in shapes.columns:
                raise KeyError(f"Column '{column}' not found in shapes file")
        if turbine_column and hubheight_column:
            runs = build_turbine_hubheight_runs(shapes, turbine_column, hubheight_column)
        elif turbine_column:
            # polygons with an empty cell are skipped, as in timeseries.py
            runs = [(name, regions, None) for name, regions in build_turbine_assignment(shapes, turbine_column).items()]
        else:
            runs = [(resource.get("turbine"), None, None)]
            if hubheight_column:
                runs = split_runs_by_hub_height(runs, shapes, hubheight_column)
    elif method == "pv":
        runs = [(resource.get("panel"), None, None)]
    else:
        logging.warning(f"    Method '{method}' not supported for RE Ninja, skipped")
        continue

    frames = []
    for item_name, run_regions, run_height in runs:
        for region in (list(coords) if run_regions is None else run_regions):
            lat, lon = coords[region]
            logging.info(f"    {tech_name}: {region} ({item_name}) - {year}")
            if method == "wind":
                series, _ = get_wind_data(
                    lat, lon, to_api_turbine_name(str(item_name)), date_from, date_to,
                    capacity=capacity, height=hub_height if run_height is None else run_height,
                )
            else:
                tracking = resource.get("tracking")
                series, _ = get_solar_data(
                    lat, lon, date_from, date_to,
                    dataset=dataset,
                    capacity=capacity,
                    system_loss=system_loss,
                    tracking=0 if tracking is None else tracking,
                    tilt=resource.get("slope", default_tilt),
                    azim=resource.get("azimuth", default_azim),
                )
            series = series.reindex(sns)
            if series.isna().any():
                logging.warning(f"    {region}: {int(series.isna().sum())} timesteps missing in RE Ninja data")
            frames.append(pd.DataFrame({(region, str(item_name)): series}))

    df = pd.concat(frames, axis=1)
    df.columns = pd.MultiIndex.from_tuples(df.columns)

    if correction_factor != 1.0:
        df = correction_factor * df
    if clip_threshold is not None:
        df = df.where(df >= clip_threshold, 0)
    if ts_config.get("week52"):
        df = to_52_weeks(df)

    # columns ordered like the shapes file
    region_order = {r: i for i, r in enumerate(shapes.index)}
    order = sorted(range(df.shape[1]), key=lambda i: (region_order[df.columns[i][0]], i))
    df = df.iloc[:, order]
    df.index.name = None

    out_file = os.path.join(output_dir, f"{tech_name}_{year}_{study_region_name}_REninja.csv")
    df.to_csv(out_file)
    logging.info(f"    Saved {os.path.basename(out_file)}  ({len(df)} timesteps × {df.shape[1]} columns)")

logging.info("Done — all RE Ninja profiles downloaded.")
