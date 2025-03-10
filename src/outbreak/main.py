import os
from functools import cached_property
from typing import Optional, Collection, Type, Any

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import find_peaks

from src.utils.path import DATA_ROOTPATH
from utils.plot import save_figure


def _ffx_index(cumulative_x, threshold):
    x_max = cumulative_x.iloc[-1]

    if threshold > x_max:
        return None

    return np.argmax(cumulative_x > threshold)


class Outbreak:
    required_fields = ["cases", "deaths", "recoveries"]

    def __init__(self, region: str, cases: Optional[pd.Series] = None, deaths: Optional[pd.Series] = None,
                 recoveries: Optional[pd.Series] = None, df: Optional[pd.DataFrame] = None, smoothing_window: int = 3,
                 epidemic: Optional = None, **kwargs):

        self.region = region
        self.epidemic = epidemic

        if (cases is None) and (deaths is None) and (recoveries is None):
            assert df is not None
            assert all(field in df.columns for field in Outbreak.required_fields)

            self._df: pd.DataFrame = df
        else:
            self._df: pd.DataFrame = pd.DataFrame({"cases": cases, "deaths": deaths, "recoveries": recoveries, **kwargs})

            # join with df if attribute present
            if df is not None:
                self._df = self._df.join(df)

        # construct the cumulative columns if they are not present in the df
        for field in Outbreak.required_fields:
            self._df[f"cumulative_{field}"] = self._df[field].cumsum()

        # must be set after df is created
        self.smoothing_window = smoothing_window

    def __len__(self):
        return self._df.shape[0]

    def __getitem__(self, item):
        if type(item) is slice:
            df = self._df.drop(columns=[f"cumulative_{field}" for field in Outbreak.required_fields])

            return Outbreak(
                self.region,
                df=df.iloc[item].copy()
            )

        return getattr(self, item)

    def __getattr__(self, item):
        if item in self._df.columns:
            return self._df[item]

        if item.startswith("smooth"):
            item = item[item.find("_") + 1:]
            return self._smooth_df[item]

        return self.__getattribute__(item)

    @cached_property
    def name(self):
        if self.epidemic is None:
            return self.region

        return f"{self.epidemic.name}_{self.region}"

    @cached_property
    def start(self):
        return self._df.index.min()

    @cached_property
    def cumulative_resolved_cases(self):
        return self.cumulative_deaths + self.cumulative_recoveries

    @cached_property
    def df(self):
        return self._df

    @cached_property
    def peak_date(self):
        # extract smooth peak idx (normalize daily anomalies due to changes in counting strategies...)
        smooth_peak_idx, _ = find_peaks(self.smooth_deaths, prominence=1)

        if len(smooth_peak_idx) > 0:
            if len(smooth_peak_idx) == 1:
                smooth_peak_id = smooth_peak_idx[0]
            else:
                smooth_peak_idxmax = self.smooth_deaths.iloc[smooth_peak_idx].idxmax()
                smooth_peak_id = self.smooth_deaths.index.get_loc(smooth_peak_idxmax)

            peak_idxmax = self.deaths.iloc[
                          max(smooth_peak_id - self.smoothing_window, 0):min(smooth_peak_id + self.smoothing_window,
                                                                             len(self) - 1)].idxmax()
            peak_id = self.deaths.index.get_loc(peak_idxmax)

            if self._verify_peak(peak_id):
                return peak_id

        return -1

    @cached_property
    def is_peak_reached(self):
        return self.peak_date != -1

    def _verify_peak(self, peak_id, min_gradient_since_peak_threshold=0.05, min_days_since_peak_threshold=7):
        peak_deaths = self.smooth_deaths.iloc[peak_id]
        last_deaths = self.smooth_deaths.iloc[-1]

        gradient_since_peak = (peak_deaths - last_deaths) / peak_deaths

        days_since_peak = (self.smoothed_deaths.shape[0] - 1) - peak_id

        return days_since_peak > min_days_since_peak_threshold and gradient_since_peak > min_gradient_since_peak_threshold

    @property
    def smoothing_window(self):
        return self._smoothing_window

    @smoothing_window.setter
    def smoothing_window(self, smoothing_window):
        assert 0 < smoothing_window < 60

        self._smoothing_window = smoothing_window
        self._smooth_df = self._build_smooth_df(smoothing_window)

    def _build_smooth_df(self, smoothing_window):
        return self.df.copy().rolling(smoothing_window, center=True).mean().dropna()

    def ffx(self, *xs, x_type="cases"):
        cumulative_x = self._df[f"cumulative_{x_type}"]

        if len(xs) > 1:
            return [_ffx_index(cumulative_x, x) for x in xs]

        return _ffx_index(cumulative_x, xs[0])

    def build_estimates(self, *Estimators: Type[Any], start: int = 0, window_size: int = 7):
        assert len(Estimators) >= 1

        estimators = [Estimator(self) for Estimator in Estimators]

        # ensure that t > 0 with this max
        windows = np.arange(max(start, 1), len(self), window_size)

        result = pd.DataFrame(index=self._df.index[windows])

        for t in windows:
            for estimator in estimators:
                result.loc[t, estimator.__class__.name] = estimator.estimate(t, start=start)

        return result

    @save_figure(lambda outbreak: f"outbreaks/{outbreak.region}.pdf")
    def plot(self, cumulative: bool = False):
        ax = plt.gca()

        plt.suptitle(self.region)

        ax.set_ylabel("# of people")

        if cumulative:
            self.cumulative_cases.plot(ax=ax, label="cumulative_cases")
            self.cumulative_deaths.plot(ax=ax, label="cumulative_deaths")
            self.cumulative_recoveries.plot(ax=ax, label="cumulative_recoveries")
        else:
            self.cases.plot(ax=ax, label="cases")
            self.deaths.plot(ax=ax, label="deaths")
            self.recoveries.plot(ax=ax, label="recoveries")

        plt.legend()

    @staticmethod
    def from_csv(region, epidemic="covid", datetime_index: bool = True, **kwargs):
        filepath = DATA_ROOTPATH / f"{epidemic}/{region}.csv"

        if os.path.isfile(filepath):
            parse_dates = [0] if datetime_index else None

            outbreak_df = pd.read_csv(filepath, index_col=0, parse_dates=parse_dates)

            return Outbreak(region, df=outbreak_df, **kwargs)

        raise Exception(f"File for {region} not found.")

    def to_csv(self, epidemic_name: Optional[str] = None):
        if epidemic_name is None:
            epidemic_name = self.epidemic.name

        if os.path.exists(DATA_ROOTPATH / f"{epidemic_name}") is False:
            os.makedirs(DATA_ROOTPATH / f"{epidemic_name}")

        self._df.to_csv(DATA_ROOTPATH / f"{epidemic_name}/{self.region}.csv")

