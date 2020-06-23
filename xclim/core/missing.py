"""
Missing values identification
=============================

Algorithms identifying missing values using different criteria:

- missing_any: A result is missing if any input value is missing.
- missing_wmo: A result is missing if 11 days are missing, or 5 consecutive values are missing in a month.
- missing_pct: A result is missing if more than a given fraction of values are missing.
- at_least_n_valid: A result is missing if less than a given number of valid values are present.

"""
import numpy as np
import pandas as pd
import xarray as xr
from boltons.funcutils import wraps

from .options import CHECK_MISSING
from .options import MISSING_METHODS
from .options import MISSING_OPTIONS
from .options import OPTIONS
from .options import register_missing_method

__all__ = [
    "missing_wmo",
    "missing_any",
    "missing_pct",
    "missing_from_context",
    "register_missing_method",
]


def check_is_dataarray(comp):
    r"""Decorator to check that a computation has an instance of xarray.DataArray
     as first argument."""

    @wraps(comp)
    def func(data_array, *args, **kwds):
        assert isinstance(data_array, xr.DataArray)
        return comp(data_array, *args, **kwds)

    return func


# This function can probably be made simpler once CFPeriodIndex is implemented.
class MissingBase:
    def __init__(self, da, freq, **indexer):
        self.null, self.count = self.prepare(da, freq, **indexer)

    @staticmethod
    def split_freq(freq):
        if freq is None:
            return "", None

        if "-" in freq:
            return freq.split("-")

        return freq, None

    @staticmethod
    def is_null(da, freq, **indexer):
        """Return a boolean array indicating which values are null."""
        from xclim.indices import generic

        selected = generic.select_time(da, **indexer)
        if selected.time.size == 0:
            raise ValueError("No data for selected period.")

        null = selected.isnull()
        if freq:
            return null.resample(time=freq)

        return null

    def prepare(self, da, freq, **indexer):
        """Prepare arrays to be fed to the `is_missing` function.

        Parameters
        ----------
        da : xr.DataArray
          Input data.
        freq : str
          Resampling frequency defining the periods defined in
          http://pandas.pydata.org/pandas-docs/stable/timeseries.html#resampling.
        **indexer : {dim: indexer, }, optional
          Time attribute and values over which to subset the array. For example, use season='DJF' to select winter
          values, month=1 to select January, or month=[6,7,8] to select summer months. If not indexer is given,
          all values are considered.

        Returns
        -------
        xr.DataArray, xr.DataArray
          Boolean array indicating which values are null, array of expected number of valid values.

        Notes
        -----
        If `freq=None` and an indexer is given, then missing values during period at the start or end of array won't be
        flagged.
        """
        from xclim.indices import generic

        null = self.is_null(da, freq, **indexer)

        pfreq, anchor = self.split_freq(freq)

        c = null.sum(dim="time")

        # Otherwise simply use the start and end dates to find the expected number of days.
        if pfreq.endswith("S"):
            start_time = c.indexes["time"]
            end_time = start_time.shift(1, freq=freq)
        elif pfreq:
            end_time = c.indexes["time"]
            start_time = end_time.shift(-1, freq=freq)
        else:
            i = da.time.to_index()
            start_time = i[:1]
            end_time = i[-1:]

        if indexer:
            # Create a full synthetic time series and compare the number of days with the original series.
            t0 = str(start_time[0].date())
            t1 = str(end_time[-1].date())
            if isinstance(da.indexes["time"], xr.CFTimeIndex):
                cal = da.time.encoding.get("calendar")
                t = xr.cftime_range(t0, t1, freq="D", calendar=cal)
            else:
                t = pd.date_range(t0, t1, freq="D")

            sda = xr.DataArray(data=np.ones(len(t)), coords={"time": t}, dims=("time",))
            st = generic.select_time(sda, **indexer)
            if freq:
                count = st.notnull().resample(time=freq).sum(dim="time")
            else:
                count = st.notnull().sum(dim="time")

        else:
            n = (end_time - start_time).days
            if freq:
                count = xr.DataArray(n.values, coords={"time": c.time}, dims="time")
            else:
                count = xr.DataArray(n.values[0] + 1)

        return null, count

    def is_missing(self, null, count, **kwargs):
        """Return whether or not the values within each period should be considered missing or not."""
        raise NotImplementedError

    @staticmethod
    def validate(**kwargs):
        """Return whether or not arguments are valid."""
        return True

    def __call__(self, **kwargs):
        if not self.validate(**kwargs):
            raise ValueError("Invalid arguments")
        return self.is_missing(self.null, self.count, **kwargs)


@register_missing_method("any")
class MissingAny(MissingBase):
    def is_missing(self, null, count, **kwargs):
        cond0 = null.count(dim="time") != count  # Check total number of days
        cond1 = null.sum(dim="time") > 0  # Check if any is missing
        return cond0 | cond1


@register_missing_method("wmo")
class MissingWMO(MissingAny):
    def __init__(self, da, freq, **indexer):
        # Force computation on monthly frequency
        if not freq.startswith("M"):
            raise ValueError
        super().__init__(da, freq, **indexer)

    def is_missing(self, null, count, nm=11, nc=5):
        import xclim.indices.run_length as rl

        # Check total number of days
        cond0 = null.count(dim="time") != count

        # Check if more than threshold is missing
        cond1 = null.sum(dim="time") >= nm

        # Check for consecutive missing values
        cond2 = null.map(rl.longest_run, dim="time") >= nc

        return cond0 | cond1 | cond2

    @staticmethod
    def validate(nm, nc):
        return nm < 31 and nc < 31


@register_missing_method("pct")
class MissingPct(MissingBase):
    def is_missing(self, null, count, tolerance=0.1):
        if tolerance < 0 or tolerance > 1:
            raise ValueError("tolerance should be between 0 and 1.")

        n = count - null.count(dim="time") + null.sum(dim="time")
        return n / count >= tolerance

    @staticmethod
    def validate(tolerance):
        return 0 <= tolerance <= 1


@register_missing_method("at_least_n")
class AtLeastNValid(MissingBase):
    def is_missing(self, null, count, n=20):
        """The result of a reduction operation is considered missing if less than `n` values are valid."""
        nvalid = null.count(dim="time") - null.sum(dim="time")
        return nvalid < n

    @staticmethod
    def validate(n):
        return n > 0


def missing_any(da, freq, **indexer):
    r"""Return whether there are missing days in the array.

    Parameters
    ----------
    da : DataArray
      Input array at daily frequency.
    freq : str
      Resampling frequency.
    **indexer : {dim: indexer, }, optional
      Time attribute and values over which to subset the array. For example, use season='DJF' to select winter values,
      month=1 to select January, or month=[6,7,8] to select summer months. If not indexer is given, all values are
      considered.

    Returns
    -------
    out : DataArray
      A boolean array set to True if period has missing values.
    """
    return MissingAny(da, freq, **indexer)()


def missing_wmo(da, freq, nm=11, nc=5, **indexer):
    r"""Return whether a series fails WMO criteria for missing days.

    The World Meteorological Organisation recommends that where monthly means are computed from daily values,
    it should considered missing if either of these two criteria are met:

      – observations are missing for 11 or more days during the month;
      – observations are missing for a period of 5 or more consecutive days during the month.

    Stricter criteria are sometimes used in practice, with a tolerance of 5 missing values or 3 consecutive missing
    values.

    Parameters
    ----------
    da : DataArray
      Input array at daily frequency.
    freq : str
      Resampling frequency.
    nm : int
      Number of missing values per month that should not be exceeded.
    nc : int
      Number of consecutive missing values per month that should not be exceeded.
    **indexer : {dim: indexer, }, optional
      Time attribute and values over which to subset the array. For example, use season='DJF' to select winter values,
      month=1 to select January, or month=[6,7,8] to select summer months. If not indexer is given, all values are
      considered.

    Returns
    -------
    out : DataArray
      A boolean array set to True if period has missing values.
    """
    missing = MissingWMO(da, "M", **indexer)(nm=nm, nc=nc)
    return missing.resample(time=freq).any()


def missing_pct(da, freq, tolerance, **indexer):
    r"""Return whether there are more missing days in the array than a given percentage.

    Parameters
    ----------
    da : DataArray
      Input array at daily frequency.
    freq : str
      Resampling frequency.
    tolerance : float
      Fraction of missing values that is tolerated.
    **indexer : {dim: indexer, }, optional
      Time attribute and values over which to subset the array. For example, use season='DJF' to select winter
      values,
      month=1 to select January, or month=[6,7,8] to select summer months. If not indexer is given, all values are
      considered.

    Returns
    -------
    out : DataArray
      A boolean array set to True if period has missing values.
    """
    return MissingPct(da, freq, **indexer)(tolerance=tolerance)


def at_least_n_valid(da, freq, n=1, **indexer):
    r"""Return whether there are at least a given number of valid values.

        Parameters
        ----------
        da : DataArray
          Input array at daily frequency.
        freq : str
          Resampling frequency.
        n : int
          Minimum of valid values required.
        **indexer : {dim: indexer, }, optional
          Time attribute and values over which to subset the array. For example, use season='DJF' to select winter
          values, month=1 to select January, or month=[6,7,8] to select summer months. If not indexer is given,
          all values are considered.

        Returns
        -------
        out : DataArray
          A boolean array set to True if period has missing values.
        """
    return AtLeastNValid(da, freq, **indexer)(n=n)


def missing_from_context(da, freq, **indexer):
    """Return whether each element of the resampled da should be considered missing according
    to the currently set options in `xclim.set_options`.

    See `xclim.set_options` and `xclim.core.options.register_missing_method`.
    """
    name = OPTIONS[CHECK_MISSING]
    cls = MISSING_METHODS[name]
    opts = OPTIONS[MISSING_OPTIONS][name]

    return cls(da, freq, **indexer)(**opts)
