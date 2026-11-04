# based on: https://github.com/GreenDealUkraina/res-profiles


import atlite
import numpy as np
import xarray as xr
import pandas as pd
import geopandas as gpd
import os
import yaml
import logging
import argparse
from atlite.gis import ExclusionContainer
from atlite.resource import get_windturbineconfig
import rasterio
from pathlib import Path
from utils import * 


logging.basicConfig(level=logging.INFO)

dirname = os.getcwd()

# --- Load timeseries settings from CONFIG ---
with open(os.path.join(dirname, "config.yaml"), "r", encoding="utf-8") as f:
    CONFIG = yaml.load(f, Loader=yaml.FullLoader)

# load from CONFIG
show_progress = CONFIG['show_progress']
study_region_name = CONFIG["study_region_name"]

technologies = CONFIG["technologies"]
enabled_techs = technologies.pop(
    "enable", list(technologies.keys())
)  # Extract enable list, default to all
logging.info(f"  Technologies to process: {enabled_techs}")

# --- Load region shapes ---
shapes_path = os.path.join(dirname, CONFIG["shapes_filepath"].format(study_region_name=study_region_name))
logging.info(f"Loading shapes from {shapes_path}")
shapes = gpd.read_file(shapes_path)
name_col = CONFIG.get("shapes_name_column", "name")
shapes = shapes.set_index(name_col).rename_axis("region")
regions = shapes.geometry
logging.info(f"  {len(regions)} regions loaded")

# --- Build time snapshots ---
snapshots_CONFIG = CONFIG["timeseries"]
year = pd.Timestamp(snapshots_CONFIG["start"]).year
end_exclusive = pd.Timestamp(snapshots_CONFIG["end"]) + pd.Timedelta(days=1)
sns = pd.date_range(
    start=snapshots_CONFIG["start"],
    end=end_exclusive,
    freq="h",
    inclusive="left",
)
# if drop leap day
if CONFIG["timeseries"]["drop_leap_day"]: 
    sns = sns[~((sns.month == 2) & (sns.day == 29))]
# if test mode is enabled
if CONFIG["timeseries"]["test_mode"]: 
    sns = sns[:168]  # first week only

# --- Load ERA5 cutout from local folder ---
cutout_path = os.path.join(dirname, CONFIG["cutout_dir"], f"{CONFIG['cutout_name'].format(study_region_name=study_region_name, year=year)}.nc")
if not os.path.exists(cutout_path):
    raise FileNotFoundError(f"Cutout file not found: {cutout_path}")
logging.info(f"  Loading cutout: {cutout_path}")
cutout = atlite.Cutout(cutout_path)
cutout.data = cutout.data.sel(time=sns)

# overwrite the wnd100m if user wants to use corrected wind speeds
wind_enabled = any(technologies[t]["resource"]["method"] == "wind" for t in enabled_techs)
if wind_enabled:
    cutout.data["wnd100m"] = cutout.data[CONFIG["technologies"]["wind_speed_variable"]]

# load available land raster if enabled
if CONFIG["available_land"]["enable"]:
    # Resolve available_land raster path with placeholders
    raster = CONFIG["available_land"]["raster"]
    
    available_land_raster = (
        os.path.join(
            dirname,
            raster.format(study_region_name=study_region_name),
        )
        if raster is not None
        else None
    )
    # get raster crs and resolution
    with rasterio.open(available_land_raster) as src:
        crs = src.crs
        res = src.res[0]
    # add available land raster to exclusion container
    excluder = ExclusionContainer(
        crs=crs,
        res=res,
    )  # crs and resolution
    excluder.add_raster(available_land_raster, codes=1, invert=True, crs=crs)

    indicator = cutout.availabilitymatrix(regions, excluder)

else:
    # --- Compute indicator matrix: which grid cells belong to which region ---

    indicator = cutout.availabilitymatrix(
        regions, ExclusionContainer(res=50, crs=8857), disable_progressbar=not show_progress
    ) # use res=50 instead of default 100m; use crs=8857 instead of default 3035 for Europe

    # below commented out, because if active, all weather cells touched by polygon are weighted equally, not area-proportional
    # indicator = np.ceil(indicator)  # binary 0/1

# Uniform layout: equal weight per grid cell
layout = xr.DataArray(
    np.ones(cutout.shape),
    [cutout.coords["y"], cutout.coords["x"]],
)

# Stack spatial dimensions for matrix multiplication
matrix = indicator.stack(spatial=["y", "x"])

# create output dir
output_dir = os.path.join(dirname, "output")
os.makedirs(output_dir, exist_ok=True)

# --- Generate and save timeseries profiles for each technology ---
assignments = {}
for tech_name in enabled_techs:
    tech_params = technologies[tech_name]
    logging.info(f"  Technology: {tech_name}")

    base_resource = tech_params["resource"].copy()
    correction_factor = tech_params.get("correction_factor", 1.0)
    clip_threshold = tech_params.get("clip_p_max_pu", None)
    turbine_column = base_resource.pop("turbine_column_name", None)
    hubheight_column = base_resource.pop("hubheight_column_name", None)

    if hubheight_column and hubheight_column not in shapes.columns:
        raise KeyError(f"hubheight_column_name '{hubheight_column}' not found in shapes file")

    method = base_resource.pop("method")
    func = getattr(cutout, method)
    base_resource["show_progress"] = show_progress

    # Build runs: (turbine/panel name, regions to compute, optional hub height)
    if turbine_column:
        if turbine_column not in shapes.columns:
            raise KeyError(f"turbine_column_name '{turbine_column}' not found in shapes file")
        assignment = build_turbine_assignment(shapes, turbine_column)
        assignments[tech_name] = assignment
        if hubheight_column:
            runs = build_turbine_hubheight_runs(
                shapes, turbine_column, hubheight_column
            )
        else:
            runs = [(name, regions, None) for name, regions in assignment.items()]
        skipped = int(shapes[turbine_column].isna().sum())
        logging.info(f"    Turbines from column '{turbine_column}': {list(assignment)} ({skipped} polygons without turbine skipped)")
    elif tech_name == "windspeeds":
        runs = [(None, None, None)]
    elif method == "pv":
        runs = [(base_resource.get("panel"), None, None)]
    else:
        runs = [(base_resource.get("turbine"), None, None)]

    if hubheight_column and not turbine_column:
        runs = split_runs_by_hub_height(runs, shapes, hubheight_column)

    frames = []
    for item_name, run_regions, hub_height_override in runs:
        resource = base_resource.copy()
        if method == "wind" and tech_name != "windspeeds":
            resource["turbine"] = item_name
        elif method == "pv":
            resource["panel"] = item_name

        # Load custom or atlite turbine/panel files
        if resource.get("turbine") is not None:
            resource["turbine"] = load_turbine_or_panel(
                resource["turbine"],
                dirname,
                default_hub_height=CONFIG.get("ninja", {}).get("height", 100),
            )
            if hub_height_override is not None:
                resource["turbine"] = {**get_windturbineconfig(resource["turbine"]), "hub_height": hub_height_override}
        if resource.get("panel") is not None:
            resource["panel"] = load_turbine_or_panel(resource["panel"], dirname)

        ##### Custom turbine for windspeeds technology
        if tech_name == "windspeeds":
            wind_speed = np.arange(0, 100, 1)
            max_wind_speed = float(cutout.data["wnd100m"].max().values)
            helper_turbine = {
                "V": wind_speed,
                "POW": wind_speed,
                "hub_height": 100,
                "P": max_wind_speed,
            }
            resource["turbine"] = helper_turbine
        #####

        run_matrix = matrix if run_regions is None else matrix.sel(region=run_regions)

        profile = func(
            matrix=run_matrix,
            layout=layout,
            index=run_matrix.indexes["region"],
            per_unit=True,
            return_capacity=False,
            **resource,
        )

        if correction_factor != 1.0:
            profile = correction_factor * profile
        if clip_threshold is not None:
            profile = profile.where(profile >= clip_threshold, 0)

        df = profile.to_pandas()

        #### For windspeeds technology, multiply by max_wind_speed before exporting
        if tech_name == "windspeeds":
            df = df * max_wind_speed
        ####

        if CONFIG["timeseries"].get("week52"):
            df = to_52_weeks(df)

        # second header row: turbine/panel name, or "windspeeds"
        second_row = "windspeeds" if tech_name == "windspeeds" else str(item_name)
        df.columns = pd.MultiIndex.from_product([df.columns, [second_row]])
        frames.append(df)

    # one file per technology and year; columns ordered like the shapes file
    combined = pd.concat(frames, axis=1)
    region_order = {r: i for i, r in enumerate(shapes.index)}
    order = sorted(
        range(combined.shape[1]),
        key=lambda i: (region_order[combined.columns[i][0]], i),
    )
    combined = combined.iloc[:, order]
    combined.index.name = None

    out_file = os.path.join(
        output_dir, f"{tech_name}_{year}_{study_region_name}.csv"
    )
    combined.to_csv(out_file)

    logging.info(
        f"    Saved {os.path.basename(out_file)}  "
        f"({len(combined)} timesteps × {combined.shape[1]} columns)"
    )


logging.info("Done — all profiles generated.")

# --- Save technologies dictionary, shapes filename, and cutout name to a text file ---
tech_file = os.path.join(output_dir, f"technologies_{year}_{study_region_name}.txt")
with open(tech_file, "w", encoding="utf-8") as f:
    f.write(f"shapes_file: {os.path.basename(shapes_path)}\n")
    f.write(f"cutout_file: {os.path.basename(cutout_path)}\n\n")
    f.write("technologies:\n")
    import yaml

    yaml.dump(technologies, f, allow_unicode=True, default_flow_style=False)
    if assignments:
        f.write("\nturbine_assignment:\n")
        yaml.dump(assignments, f, allow_unicode=True, default_flow_style=False)
