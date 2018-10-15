import numpy as np
import pandas as pd
import pytest
import xarray as xr
from xclim.temperature import TGMean
from common import tas_series
from xclim import checks



def test_assert_daily():
    tg_mean = TGMean()
    n = 365.  # one day short of a full year
    times = pd.date_range('2000-01-01', freq='1D', periods=n)
    da = xr.DataArray(np.arange(n), [('time', times)], attrs={'units': 'K'})
    tg_mean(da)

    # Bad frequency
    with pytest.raises(ValueError):
        times = pd.date_range('2000-01-01', freq='12H', periods=n)
        da = xr.DataArray(np.arange(n), [('time', times)])
        tg_mean(da)

    # Missing one day between the two years
    with pytest.raises(ValueError):
        times = pd.date_range('2000-01-01', freq='1D', periods=n)
        times = times.append(pd.date_range('2001-01-01', freq='1D', periods=n))
        da = xr.DataArray(np.arange(2*n), [('time', times)])
        tg_mean(da)

    # Duplicate dates
    with pytest.raises(ValueError):
        times = pd.date_range('2000-01-01', freq='1D', periods=n)
        times = times.append(pd.date_range('2000-12-29', freq='1D', periods=n))
        da = xr.DataArray(np.arange(2*n), [('time', times)])
        tg_mean(da)



def test_missing_any(tas_series):
    a = np.arange(360.)
    a[5:10] = np.nan
    ts = tas_series(a)
    out = checks.missing_any(ts, freq='MS')
    assert out[0]
    assert not out[1]

    n = 66
    times = pd.date_range('2001-12-30', freq='1D', periods=n)
    da = xr.DataArray(np.arange(n), [('time', times)])
    miss = checks.missing_any(da, 'MS')
    np.testing.assert_array_equal(miss, [True, False, False, True])

    n = 378
    times = pd.date_range('2001-12-31', freq='1D', periods=n)
    da = xr.DataArray(np.arange(n), [('time', times)])
    miss = checks.missing_any(da, 'YS')
    np.testing.assert_array_equal(miss, [True, False, True])

    miss = checks.missing_any(da, 'Q-NOV')
    np.testing.assert_array_equal(miss, [True, False, False, False, True])
