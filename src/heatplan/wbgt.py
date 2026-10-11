"""Outdoor Wet Bulb Globe Temperature (WBGT) from routine weather variables.

Implements the Liljegren et al. (2008) model ("Modeling the wet bulb globe
temperature using standard meteorological measurements", J. Occup. Environ.
Hyg. 5:645-655), following the structure of the reference C implementation:
an iterative globe temperature and an iterative natural wet-bulb temperature,
combined as WBGT = 0.7*Tnwb + 0.2*Tg + 0.1*Ta.

We deliberately do NOT use the "simplified WBGT" (temperature + humidity only),
which ignores sun and wind and badly overstates heat stress in humid air.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

# Physical constants
STEFANB = 5.6696e-8
CP = 1003.5            # specific heat of dry air, J/(kg K)
M_AIR = 28.97
M_H2O = 18.015
R_GAS = 8314.34
R_AIR = R_GAS / M_AIR
RATIO = CP * M_AIR / M_H2O
PR = CP / (CP + 1.25 * R_AIR)   # Prandtl number

# Instrument properties (standard globe and wick)
EMIS_WICK, ALB_WICK, D_WICK, L_WICK = 0.95, 0.4, 0.007, 0.0254
EMIS_GLOBE, ALB_GLOBE, D_GLOBE = 0.95, 0.05, 0.0508
EMIS_SFC, ALB_SFC = 0.999, 0.45

MIN_SPEED = 0.13       # m/s, lower bound used by Liljegren
CZA_MIN = 0.0872       # sun below ~5 deg: treat all radiation as diffuse
CONVERGENCE = 0.02
MAX_ITER = 50


def _esat(tk: float) -> float:
    """Saturation vapour pressure over water (hPa), Buck (1981) with enhancement."""
    tc = tk - 273.15
    return 6.1121 * math.exp(17.502 * tc / (tc + 240.97)) * 1.004


def _viscosity(tk: float) -> float:
    omega = (tk / 97.0 - 2.9) / 0.4 * (-0.034) + 1.048
    return 2.6693e-6 * math.sqrt(M_AIR * tk) / (3.617 ** 2 * omega)


def _thermal_cond(tk: float) -> float:
    return (CP + 1.25 * R_AIR) * _viscosity(tk)


def _diffusivity(tk: float, p_hpa: float) -> float:
    pcrit13 = (36.4 * 218.0) ** (1.0 / 3.0)
    tcrit512 = (132.0 * 647.3) ** (5.0 / 12.0)
    tcrit12 = math.sqrt(132.0 * 647.3)
    mmix = math.sqrt(1.0 / M_AIR + 1.0 / M_H2O)
    return 3.640e-4 * (tk / tcrit12) ** 2.334 * pcrit13 * tcrit512 * mmix / (p_hpa / 1013.25) * 1.0e-4


def _evap(tk: float) -> float:
    """Latent heat of vaporisation, J/kg."""
    return (313.15 - tk) / 30.0 * (-71100.0) + 2.4073e6


def _h_sphere(tk: float, p_hpa: float, speed: float) -> float:
    density = p_hpa * 100.0 / (R_AIR * tk)
    re = max(speed, MIN_SPEED) * density * D_GLOBE / _viscosity(tk)
    nu = 2.0 + 0.6 * math.sqrt(re) * PR ** 0.3333
    return nu * _thermal_cond(tk) / D_GLOBE


def _h_cylinder(tk: float, p_hpa: float, speed: float) -> float:
    density = p_hpa * 100.0 / (R_AIR * tk)
    re = max(speed, MIN_SPEED) * density * D_WICK / _viscosity(tk)
    nu = 0.281 * re ** 0.6 * PR ** 0.44
    return nu * _thermal_cond(tk) / D_WICK


def _emis_atm(tk: float, rh: float) -> float:
    e = rh * _esat(tk)
    return 0.575 * e ** 0.143


def globe_temperature(ta_c, rh, p_hpa, speed, solar, fdir, cza) -> float:
    tair = ta_c + 273.15
    rh = max(min(rh, 1.0), 0.01)
    emis_atm = _emis_atm(tair, rh)
    tsfc = tair
    tg_prev = tair
    for _ in range(MAX_ITER):
        tref = 0.5 * (tg_prev + tair)
        h = _h_sphere(tref, p_hpa, speed)
        rad = 0.5 * (emis_atm * tair ** 4 + EMIS_SFC * tsfc ** 4)
        sun = 0.0
        if solar > 0:
            direct = fdir * (1.0 / (2.0 * cza) - 1.0) if cza >= CZA_MIN else 0.0
            sun = solar / (2.0 * EMIS_GLOBE * STEFANB) * (1.0 - ALB_GLOBE) * (direct + 1.0 + ALB_SFC)
        val = rad - h / (EMIS_GLOBE * STEFANB) * (tg_prev - tair) + sun
        tg_new = max(val, 1.0) ** 0.25
        if abs(tg_new - tg_prev) < CONVERGENCE:
            return tg_new - 273.15
        tg_prev = 0.9 * tg_prev + 0.1 * tg_new
    return tg_prev - 273.15


def natural_wet_bulb(ta_c, rh, p_hpa, speed, solar, fdir, cza) -> float:
    tair = ta_c + 273.15
    rh = max(min(rh, 1.0), 0.01)
    eair = rh * _esat(tair)
    emis_atm = _emis_atm(tair, rh)
    tsfc = tair
    # start from the dew point
    tdew = 240.97 * math.log(eair / 6.1121 / 1.004) / (17.502 - math.log(eair / 6.1121 / 1.004)) + 273.15
    twb_prev = tdew
    if cza >= CZA_MIN:
        tan_z = math.sqrt(max(1.0 - cza * cza, 0.0)) / cza
        dir_term = fdir * (tan_z / math.pi + 0.25 * D_WICK / L_WICK)
    else:
        fdir = 0.0
        dir_term = 0.0
    for _ in range(MAX_ITER):
        tref = 0.5 * (twb_prev + tair)
        h = _h_cylinder(tref, p_hpa, speed)
        fatm = STEFANB * EMIS_WICK * (0.5 * (emis_atm * tair ** 4 + EMIS_SFC * tsfc ** 4) - twb_prev ** 4)
        if solar > 0:
            fatm += (1.0 - ALB_WICK) * solar * ((1.0 - fdir) * (1.0 + 0.25 * D_WICK / L_WICK) + dir_term + ALB_SFC)
        ewick = _esat(twb_prev)
        density = p_hpa * 100.0 / (R_AIR * tref)
        sc = _viscosity(tref) / (density * _diffusivity(tref, p_hpa))
        twb_new = tair - _evap(tref) / RATIO * (ewick - eair) / (p_hpa - ewick) * (PR / sc) ** 0.56 + fatm / h
        if abs(twb_new - twb_prev) < CONVERGENCE:
            return twb_new - 273.15
        twb_prev = 0.9 * twb_prev + 0.1 * twb_new
    return twb_prev - 273.15


def wind_2m(speed_10m_kmh: float) -> float:
    """10 m wind in km/h -> approximate 2 m wind in m/s (power law, open terrain)."""
    return speed_10m_kmh / 3.6 * (2.0 / 10.0) ** 0.25


def cos_zenith(lat: float, lon: float, when_utc: datetime) -> float:
    """Cosine of the solar zenith angle (NOAA approximation)."""
    doy = when_utc.timetuple().tm_yday
    hour = when_utc.hour + when_utc.minute / 60.0
    g = 2.0 * math.pi / 365.0 * (doy - 1 + (hour - 12) / 24.0)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    tst = hour * 60.0 + eqtime + 4.0 * lon
    ha = math.radians(tst / 4.0 - 180.0)
    la = math.radians(lat)
    return math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)


def wbgt(ta_c: float, rh_pct: float, wind10_kmh: float, shortwave: float, direct: float,
         lat: float, lon: float, when_utc: datetime, p_hpa: float = 1000.0) -> dict:
    """Outdoor WBGT for one hour.

    `shortwave` and `direct` are W/m2 averaged over the preceding hour (Open-Meteo
    convention), so the sun position is taken at the middle of that hour.
    """
    mid = when_utc - timedelta(minutes=30)
    cza = cos_zenith(lat, lon, mid)
    solar = max(shortwave or 0.0, 0.0)
    if cza <= 0:
        solar = 0.0
    fdir = min(max((direct or 0.0) / solar, 0.0), 0.9) if solar > 0 else 0.0
    rh = (rh_pct or 0.0) / 100.0
    speed = wind_2m(wind10_kmh or 0.0)
    tg = globe_temperature(ta_c, rh, p_hpa, speed, solar, fdir, max(cza, 0.0))
    tnwb = natural_wet_bulb(ta_c, rh, p_hpa, speed, solar, fdir, max(cza, 0.0))
    value = 0.7 * tnwb + 0.2 * tg + 0.1 * ta_c
    return {"wbgt": round(value, 1), "tg": round(tg, 1), "tnwb": round(tnwb, 1)}


def stull_wet_bulb(ta_c: float, rh_pct: float) -> float:
    """Psychrometric wet-bulb (Stull 2011), used only as a sanity check."""
    t, rh = ta_c, rh_pct
    return (t * math.atan(0.151977 * math.sqrt(rh + 8.313659)) + math.atan(t + rh)
            - math.atan(rh - 1.676331) + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh) - 4.686035)


IST = timezone(timedelta(hours=5, minutes=30))
