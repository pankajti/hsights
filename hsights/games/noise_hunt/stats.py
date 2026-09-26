"""Performance statistics, and the two that make the reveal honest.

``expected_maximum_sharpe`` is the point of the whole game. If you try N rules
on data with no signal, the best of them still has a positive Sharpe — and its
expected value is known. Comparing the player's best against that number turns
"I found something" into "I found exactly as much as chance predicts".

Reference: Bailey and Lopez de Prado (2014), The Deflated Sharpe Ratio.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm

TRADING_DAYS = 252
EULER_MASCHERONI = 0.5772156649015329


def sharpe_ratio(returns, periods=TRADING_DAYS):
    """Annualised Sharpe of a per-period return series, zero risk-free rate."""
    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 2:
        return 0.0
    deviation = values.std(ddof=1)
    if deviation <= 0:
        return 0.0
    return float(values.mean() / deviation * np.sqrt(periods))


def max_drawdown(equity):
    """Deepest peak-to-trough fall, as a negative fraction."""
    values = np.asarray(equity, dtype=float)
    if len(values) == 0:
        return 0.0
    peak = np.maximum.accumulate(values)
    with np.errstate(divide='ignore', invalid='ignore'):
        drawdown = np.where(peak > 0, values / peak - 1.0, 0.0)
    return float(min(0.0, drawdown.min()))


def expected_maximum_sharpe(trials, sharpe_std):
    """Expected best Sharpe from ``trials`` independent attempts on no signal.

    The maximum of N draws from N(0, sharpe_std) is approximated by

        sharpe_std * [(1 - g) * Z(1 - 1/N) + g * Z(1 - 1/(N e))]

    with g the Euler-Mascheroni constant and Z the inverse normal CDF. This is
    the benchmark a backtest has to beat before it means anything.
    """
    if trials is None or trials < 2 or sharpe_std is None or sharpe_std <= 0:
        return 0.0
    count = float(trials)
    first = norm.ppf(1.0 - 1.0 / count)
    second = norm.ppf(1.0 - 1.0 / (count * np.e))
    return float(sharpe_std * ((1.0 - EULER_MASCHERONI) * first
                               + EULER_MASCHERONI * second))


def null_sharpe_std(observations, periods=TRADING_DAYS):
    """Standard error of an annualised Sharpe estimated from pure noise.

    With ``observations`` per-period returns and a true Sharpe of zero the
    estimate is approximately N(0, periods / observations) in annual terms.
    """
    if observations < 2:
        return 0.0
    return float(np.sqrt(periods / observations))


def probabilistic_sharpe(observed, benchmark, observations, skew=0.0, kurtosis=3.0):
    """Probability the true Sharpe exceeds ``benchmark``, given non-normality.

    ``observed`` and ``benchmark`` are per-period (not annualised) Sharpes.
    """
    if observations < 3:
        return 0.5
    variance = (1.0 - skew * observed
                + (kurtosis - 1.0) / 4.0 * observed ** 2)
    if variance <= 0:
        return 0.5
    statistic = (observed - benchmark) * np.sqrt(observations - 1) / np.sqrt(variance)
    return float(norm.cdf(statistic))


def deflated_sharpe(observed, trials, sharpe_std, returns, periods=TRADING_DAYS):
    """Probabilistic Sharpe against the expected maximum under the null.

    Returns the probability that the strategy's true Sharpe is above zero once
    the number of attempts has been accounted for. Below ~0.95 means the result
    is not distinguishable from the best of a lucky search.
    """
    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 3:
        return 0.5
    benchmark_annual = expected_maximum_sharpe(trials, sharpe_std)
    scale = np.sqrt(periods)
    centred = values - values.mean()
    deviation = values.std(ddof=1)
    if deviation <= 0:
        return 0.5
    skew = float((centred ** 3).mean() / deviation ** 3)
    kurtosis = float((centred ** 4).mean() / deviation ** 4)
    return probabilistic_sharpe(observed / scale, benchmark_annual / scale,
                                len(values), skew, kurtosis)
