# Renewable energy profile generator

This tool creates hourly wind and solar generation profiles from weather
data using [atlite](https://atlite.readthedocs.io/) and [Renewables.ninja API](https://www.renewables.ninja/).

## Setup
If you have no python setup yet, follow the instructions under "Prerequisites" [here](https://lava-tool.readthedocs.io/en/latest/home/getting_started/) in order to install conda, VSCode and Git.

From the repository root, create and activate the Conda environment:

```terminal
conda env create -f env.yaml
```

```terminal
conda activate re-profiles
```

To access the weather data you need have access to the data providers via APIs:
- via atlite from Earth Data Hub or Copernicus, follow the instructions [here](https://atlite.readthedocs.io/en/master/installation.html#:~:text=Earth%20Data%20Hub%20(recommended)) to obtain your API key.
- [Renewables.ninja](https://www.renewables.ninja/): create an account on renewables.ninja, go to your profile and copy the API Token. Create `REninjaAPI.yaml` in
the repository root and paste your API token in the structure: `token: YOUR_RENEWABLES_NINJA_TOKEN`

Copy `config_template.yaml` and rename the copy to **`config.yaml`**. 


## Run
### Configuration
The tool reads a .geojson file with polygons for renewable energy areas from the input folder (see example `windfarmsDNK.geojson`). Every area should have a name. If wind profiles are generated, it is handy to also have the hub height and turbines model in the attribute table of the polygon file. All turbine models available in atlite and REninja can be selected. For REninja turbines see `input/ninja_turbines_power.csv`.

Edit `config.yaml` for the study region, weather-data period, input shapes,
and technologies to process. Set `timeseries.start` and `timeseries.end` as
`YYYY-MM-DD` dates, just like `cutout_timeframe.start` and
`cutout_timeframe.end`; both dates are included, and the cutout must cover the
entire time-series period. The polygons file must be available at the configured
`shapes_filepath`, and `shapes_name_column` must name its area identifier
column. Configure any credentials required by the selected atlite weather-data
modules before downloading a cutout.

Select the technologies to be processed (onwind, offwind, windspeeds, solar) in the list under `timeseries.enable` in the config file.


### Weather data sources and workflow overview

| Workflow | Wind weather data | Solar weather data | Wind power curves |
|---|---|---|---|
| **atlite** | ERA5 or corrected ERA5 | ERA5, corrected ERA5, or SARAH (globally) | [atlite](https://github.com/PyPSA/atlite/tree/master/atlite/resources/windturbine) and [Renewables.ninja](https://github.com/DEA-GE/RE-profile-generator/blob/main/input_data/ninja_turbines_power.csv) |
| **REninja** | MERRA-2 (corrected for Europe) | MERRA-2 or SARAH (Europe only) | [Renewables.ninja](https://github.com/DEA-GE/RE-profile-generator/blob/main/input_data/ninja_turbines_power.csv) only |


### Data via atlite

Run commands from the repository root, in order:

```terminal
python cutout_download.py
```

```terminal
python timeseries.py
```

The first command downloads and saves the weather cutout under `cutouts/`. The
second computes profiles from that cutout and the configured shape file.

### Data via REninja

```terminal
python reninja.py
```

## Results

Results are written to `output/`. The atlite workflow writes one CSV per enabled
technology (`<technology>_<year>_<region>.csv`) and a text file recording the
technology configuration and source files. Wind technology CSVs include
`uncorrected` or `corrected` after the technology name, according to
`technologies.wind_speed_variable`. The Renewables.ninja workflow writes CSV
files with an `_REninja` suffix and a GeoJSON of representative points.

## Notes

- [Web Viewer](https://jome1.github.io/ERA5_webapp/) for inspecting cutouts
- [Time Series Explorer](https://www.unditas.com/) for inspecting RE profiles (.csv files)
