import geopandas as gpd
import numpy as np
import pandas as pd
import pygadm
from space2stats_client import Space2StatsClient
import os
import logging
from pathlib import Path
from functools import lru_cache
from shapely.geometry import box

NINJA_POWER_CURVES_URL = (
    "https://raw.githubusercontent.com/renewables-ninja/vwf/master/power_curves/"
    "Wind%20Turbine%20Power%20Curves%20%7E%205%20(0.01ms%20with%200.00%20w%20smoother).csv"
)


def _download_admin_boundary_wb(
    country_code: str,
    level: int | None = 0,
    admin_name: str | None = None,
    source: str = "WB",
) -> gpd.GeoDataFrame:
    """
    Download administrative boundaries using Space2Stats client (WorldBank).
    https://www.arcgis.com/apps/mapviewer/index.html?layers=d1b630859ed84e92a3782fed6a612b63

    Parameters:
    -----------
    country_code : str
        ISO 3166-1 alpha-3 country code (e.g., 'KEN', 'TUR', 'DEU')
    level : int, default=0
        Administrative level:
        - 0: Country boundary (entire country)
        - 1: Level 1 subdivisions (NAM_1) - e.g., states, provinces
        - 2: Level 2 subdivisions (NAM_2) - e.g., districts, counties
    admin_name : str, optional
        Name of specific region to filter (NAM_1 or NAM_2 value).
        If None, returns all regions at the specified level.
    source : {'WB', 'GB'}, default='WB'
        Boundary source: 'WB' for World Bank or 'GB' for geoBoundaries.

    Returns:
    --------
    gpd.GeoDataFrame
        GeoDataFrame containing the requested administrative boundaries.

    Examples:
    ---------
    # Get entire country boundary
    country_boundary = download_admin_boundary_wb('KEN')

    # Get all level 1 regions
    level1_regions = download_admin_boundary_wb('KEN', level=1)

    # Get specific level 1 region
    nairobi = download_admin_boundary_wb('KEN', level=1, admin_name='Nairobi')

    # Get specific level 2 region
    specific_district = download_admin_boundary_wb('KEN', level=2, admin_name='Mombasa')
    """

    source = source.upper()
    if source not in {"WB", "GB"}:
        raise ValueError(f"source must be 'WB' or 'GB'. Got {source!r}")

    client = Space2StatsClient()

    # Map level to ADM string
    if level == 0:
        adm_string = "ADM0"
    elif level == 1:
        adm_string = "ADM1"
    elif level == 2:
        adm_string = "ADM2"
    else:
        raise ValueError(f"Level must be 0, 1, or 2. Got {level}")

    # Fetch boundaries from Space2Stats.
    # Older space2stats_client versions don't accept a `source` kwarg, so fall back if needed.
    try:
        boundaries = client.fetch_admin_boundaries(
            country_code, adm_string, source=source
        )
    except TypeError as e:
        if "source" not in str(e):
            raise
        if source != "WB":
            print(
                f"Warning: installed space2stats_client version does not support source={source!r}; "
                "falling back to default source (WB)."
            )
        boundaries = client.fetch_admin_boundaries(country_code, adm_string)

    # Filter by region name if specified
    if admin_name is not None:
        if level == 0:
            print("Warning: admin_name is ignored when level=0 (country boundary)")
        else:
            # Column name varies between API versions
            if "shapeName" in boundaries.columns:
                name_col = "shapeName"
            elif f"NAM_{level}" in boundaries.columns:
                name_col = f"NAM_{level}"
            else:
                raise ValueError(
                    f"No recognized name column found. Available columns: {boundaries.columns.tolist()}"
                )

            available = boundaries[name_col].unique().tolist()
            boundaries = boundaries[boundaries[name_col] == admin_name]

            if boundaries.empty:
                print(f"Warning: No region found with {name_col}='{admin_name}'")
                print(f"Available regions: {available}")

    return boundaries


def _download_admin_boundary_gadm(
    country_code: str, level: int | None, admin_name: str | None = None
) -> gpd.GeoDataFrame:
    """
    Download GADM administrative boundaries using pygadm.

    Parameters:
    -----------
    country_code : str
        ISO 3166-1 alpha-3 country code (e.g., 'KEN', 'TUR', 'DEU')
    level : int or None
        Administrative level: 0 for the whole country, 1 or higher for
        subdivisions (matched against the NAME_<level> column).
    admin_name : str, optional
        Name of the region to select at the given level. If None, returns all
        subdivisions; ignored when level=0.

    Returns:
    --------
    gpd.GeoDataFrame
        GeoDataFrame containing the requested boundaries in EPSG:4326.
        Empty if no region matches admin_name.
    """
    if level == 0:
        region = pygadm.Items(admin=country_code)
    else:
        region = pygadm.Items(admin=country_code, content_level=level)
        name_column = f"NAME_{level}"
        if admin_name is not None:
            region = region.loc[region[name_column] == admin_name].copy()
    return region.set_crs("EPSG:4326", allow_override=True)


def get_study_area_polygon(
    source: str,
    adm_source: str | None = None,
    country_code: str | None = None,
    level: int | None = None,
    admin_name: str | None = None,
    custom_polygon_path: str | None = None,
    bbox: list[float] | tuple[float, ...] | None = None,
) -> gpd.GeoDataFrame:
    """
    Retrieve the study area polygon in EPSG:4326 (matches config 'study_area').

    Parameters:
    -----------
    source : {'adm', 'custom', 'bbox'}
        Type of study area:
        - 'adm': administrative boundary (needs adm_source, country_code, level)
        - 'custom': polygon file (needs custom_polygon_path)
        - 'bbox': rectangle from bbox (needs bbox)
    adm_source : {'gadm', 'wb', 'gb'}, optional
        Administrative boundary provider (GADM, World Bank, geoBoundaries).
        Only used when source='adm'.
    country_code : str, optional
        ISO 3166-1 alpha-3 country code (e.g., 'KEN', 'TUR', 'DEU').
        Only used when source='adm'.
    level : int, optional
        Administrative level (0 = country). Only used when source='adm'.
    admin_name : str, optional
        Name of the administrative area at the given level. If None, all
        boundaries at that level are returned. Only used when source='adm'.
    custom_polygon_path : str, optional
        Path to a polygon file; dissolved into a single geometry.
        Only used when source='custom'.
    bbox : [xmin, ymin, xmax, ymax], optional
        Bounding box in EPSG:4326. Only used when source='bbox'.

    Returns:
    --------
    gpd.GeoDataFrame
        Non-empty GeoDataFrame in EPSG:4326.

    Raises:
    -------
    ValueError
        If the source is unknown, required inputs are missing, a CRS is
        missing, or no boundary is found.
    """
    source = (source or "").lower()
    if source not in {"adm", "custom", "bbox"}:
        raise ValueError(f"source must be 'adm', 'custom' or 'bbox', got: {source!r}")

    if source == "custom":
        if not custom_polygon_path:
            raise ValueError("custom_polygon_path is required when source='custom'.")
        region = gpd.read_file(custom_polygon_path)
        if region.empty:
            raise ValueError(f"Custom polygon file is empty: {custom_polygon_path}")
        if region.crs is None:
            raise ValueError(f"Custom polygon has no CRS: {custom_polygon_path}")
        return region.dissolve().to_crs("EPSG:4326")

    if source == "bbox":
        if bbox is None or len(bbox) != 4:
            raise ValueError("bbox must be [xmin, ymin, xmax, ymax] when source='bbox'.")
        xmin, ymin, xmax, ymax = (float(v) for v in bbox)
        if xmin >= xmax or ymin >= ymax:
            raise ValueError(f"Invalid bbox (need xmin<xmax and ymin<ymax): {bbox}")
        return gpd.GeoDataFrame(
            geometry=[box(xmin, ymin, xmax, ymax)], crs="EPSG:4326"
        )

    # source == "adm"
    if country_code is None:
        raise ValueError("country_code is required when source='adm'.")
    adm_source = (adm_source or "").lower()
    if adm_source == "gadm":
        region = _download_admin_boundary_gadm(country_code, level, admin_name)
    elif adm_source in {"wb", "gb"}:
        region = _download_admin_boundary_wb(
            country_code=country_code,
            level=level,
            admin_name=admin_name,
            source=adm_source,
        )
        if not region.empty:
            if region.crs is None:
                raise ValueError("Downloaded administrative boundary has no CRS.")
            region = region.to_crs("EPSG:4326")
    else:
        raise ValueError(f"adm_source must be 'gadm', 'wb' or 'gb', got: {adm_source!r}")

    if region.empty:
        raise ValueError(
            f"No administrative boundary found for {country_code} at level {level} "
            f"with name '{admin_name}'"
        )
    return region




# --- Helper function to load custom or atlite turbine/panel files ---
@lru_cache(maxsize=1)
def _load_ninja_power_curves():
    logging.info("    Downloading Renewables.ninja turbine power curves")
    return pd.read_csv(NINJA_POWER_CURVES_URL)


def get_ninja_turbine(name, hub_height=100, dirname=None):
    """Return a named Renewables.ninja curve as an atlite turbine config."""
    power_curves = _load_ninja_power_curves()
    if name not in power_curves.columns:
        raise KeyError(f"Renewables.ninja power curve '{name}' was not found")

    project_dir = Path(dirname) if dirname is not None else Path(__file__).resolve().parent
    ratings = pd.read_csv(project_dir / "input_data" / "ninja_turbines_power.csv", index_col="turbine")
    rated_power = float(ratings.loc[name, "kWp"]) / 1000

    return {
        "name": name,
        "V": power_curves["speed"].to_numpy(dtype=float),
        "POW": power_curves[name].to_numpy(dtype=float) * rated_power,
        "P": rated_power,
        "hub_height": hub_height,
    }


def load_turbine_or_panel(name, dirname, default_hub_height=None):
    """
    Load a turbine or panel: custom file first, then atlite, then Renewables.ninja (turbines only).

    Args:
        name: Name of the turbine/panel (e.g., "HW_farm", "Vestas_V112_3MW", "CSi")
        dirname: Working directory path
        default_hub_height: Default hub height for Renewables.ninja turbines

    Returns:
        A custom YAML path, atlite name, or atlite turbine configuration dictionary
    """
    if name is None:
        return None

    # Check if custom file exists in advanced_settings folder
    custom_path = os.path.join(dirname, "configs/advanced_settings", f"{name}.yaml")
    if os.path.exists(custom_path):
        logging.info(f"    Loading custom turbine/panel from: {custom_path}")
        return Path(custom_path)

    # Turbines (identified by default_hub_height): search atlite first, then Renewables.ninja
    if default_hub_height is not None and not name.startswith("oedb:"):
        from atlite.resource import windturbines

        if name.replace(".yaml", "") in windturbines:
            logging.info(f"    Using atlite internal turbine: {name}")
            return name
        logging.info(f"    Loading Renewables.ninja turbine: {name}")
        return get_ninja_turbine(name, default_hub_height, dirname=dirname)

    # Fall back to atlite's internal files (pass name as string)
    logging.info(f"    Using atlite internal turbine/panel: {name}")
    return name

def to_52_weeks(df, weeks=52):
    """Return `weeks` full weeks starting at the first Monday 00:00; a missing tail is filled from the start of df."""
    idx = df.index
    start = idx[(idx.dayofweek == 0) & (idx.normalize() == idx)][0]
    step = idx[1] - idx[0]
    n = int(pd.Timedelta(weeks=weeks) / step)
    out = df.loc[start:].iloc[:n]
    missing = n - len(out)
    if missing > 0:
        fill = df.iloc[np.arange(missing) % len(df)].copy()
        fill.index = pd.date_range(out.index[-1] + step, periods=missing, freq=step)
        out = pd.concat([out, fill])
        logging.info(f"    Filled {missing} trailing steps from the start of the series")
    return out

def split_runs_by_hub_height(runs, shapes, column):
    """Split (name, regions, hub_height) runs into one run per hub height; empty cells give None (turbine default)."""
    height_runs = []
    for item_name, run_regions, _ in runs:
        regions_to_run = shapes.index if run_regions is None else run_regions
        grouped_regions = {}
        for region in regions_to_run:
            value = shapes.at[region, column]
            height = None if pd.isna(value) else int(value)
            grouped_regions.setdefault(height, []).append(region)
        height_runs.extend(
            (item_name, group, height) for height, group in grouped_regions.items()
        )
    return height_runs

def build_turbine_hubheight_runs(shapes, turbine_column, hubheight_column, separator=';'):
    """Build runs by pairing semicolon-separated turbines and hub heights per region."""
    grouped_regions = {}
    for region, turbine_value in shapes[turbine_column].items():
        if pd.isna(turbine_value):
            continue

        turbines = [value.strip() for value in str(turbine_value).split(separator)]
        turbines = [turbine for turbine in turbines if turbine]
        if not turbines:
            continue

        height_value = shapes.at[region, hubheight_column]
        if pd.isna(height_value) or not str(height_value).strip():
            heights = [None] * len(turbines)
        else:
            heights = [
                int(value.strip()) if value.strip() else None
                for value in str(height_value).split(separator)
            ]
            if len(heights) != len(turbines):
                raise ValueError(
                    f"Region '{region}' has {len(turbines)} turbines in "
                    f"'{turbine_column}' but {len(heights)} hub heights in "
                    f"'{hubheight_column}'"
                )

        for turbine, height in zip(turbines, heights):
            grouped_regions.setdefault((turbine, height), []).append(region)

    return [
        (turbine, regions, height)
        for (turbine, height), regions in grouped_regions.items()
    ]

def build_turbine_assignment(shapes, column, separator=';'):
    """Return {turbine_name: [region names]} from a shapes column; empty cells are skipped."""
    assignment = {}
    for region, value in shapes[column].items():
        if pd.isna(value):
            continue
        for turbine in str(value).split(separator):
            turbine = turbine.strip()
            if turbine:
                assignment.setdefault(turbine, []).append(region)
    return assignment
