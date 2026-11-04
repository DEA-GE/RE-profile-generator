# Renewable energy profile generator

This tool creates hourly wind and solar generation profiles from weather
data using [atlite](https://atlite.readthedocs.io/) and [Renewables.ninja API](https://www.renewables.ninja/).

## Setup

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

Edit `config.yaml` for the study region, weather-data period, input shapes,
and technologies to process. Set `timeseries.start` and `timeseries.end` as
`YYYY-MM-DD` dates, just like `cutout_timeframe.start` and
`cutout_timeframe.end`; both dates are included, and the cutout must cover the
entire time-series period. The shape file must be available at the configured
`shapes_filepath`, and `shapes_name_column` must name its region identifier
column. Configure any credentials required by the selected atlite weather-data
modules before downloading a cutout.



## Run

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
technology configuration and source files. The Renewables.ninja workflow writes
CSV files with an `_REninja` suffix and a GeoJSON of representative points.

## Notes

- use [this web viewer](https://jome1.github.io/ERA5_webapp/) in order to inspect cutouts 
