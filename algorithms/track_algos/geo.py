"""Geographic helpers shared by every algorithm.

Web Mercator tile/pixel math (mirrors data-collection/collector/grid.py so
both sides agree exactly on where a pixel is) plus haversine distance.
"""

import math

import numpy as np

EARTH_R = 6_371_000.0  # metres


def lon_to_tx(lon: float, z: int) -> float:
    return (lon + 180.0) / 360.0 * (1 << z)


def lat_to_ty(lat: float, z: int) -> float:
    r = math.radians(lat)
    return (1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * (1 << z)


def tx_to_lon(tx: float, z: int) -> float:
    return tx / (1 << z) * 360.0 - 180.0


def ty_to_lat(ty: float, z: int) -> float:
    return math.degrees(math.atan(math.sinh(math.pi - 2.0 * math.pi * ty / (1 << z))))


def lonlat_to_pixel(lon: float, lat: float, z: int, tile_px: int = 256) -> tuple[float, float]:
    """Global pixel coordinates at zoom z (x right, y down)."""
    return lon_to_tx(lon, z) * tile_px, lat_to_ty(lat, z) * tile_px


def pixel_to_lonlat(px: float, py: float, z: int, tile_px: int = 256) -> tuple[float, float]:
    return tx_to_lon(px / tile_px, z), ty_to_lat(py / tile_px, z)


def metres_per_pixel(lat: float, z: int) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in metres. Used as the A* heuristic: it never
    overestimates the road distance, so A* stays optimal (admissible)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def polyline_length_m(coords: list) -> float:
    return sum(haversine_m(*coords[i], *coords[i + 1]) for i in range(len(coords) - 1))


# ---------------------------------------------------------------- array forms
# The decoder samples millions of points along the graph, so it needs the same
# maths on whole arrays. These must agree with the scalar versions above (and
# so with data-collection/collector/grid.py) to the last pixel.

def lonlat_to_pixel_np(lon, lat, z: int, tile_px: int = 256):
    """Vectorised lonlat_to_pixel. Returns (px, py) float arrays."""
    lon = np.asarray(lon, dtype=float)
    r = np.radians(np.asarray(lat, dtype=float))
    tx = (lon + 180.0) / 360.0 * (1 << z)
    ty = (1.0 - np.log(np.tan(r) + 1.0 / np.cos(r)) / np.pi) / 2.0 * (1 << z)
    return tx * tile_px, ty * tile_px


def haversine_m_np(lon1, lat1, lon2, lat2):
    """Vectorised haversine_m, metres."""
    p1, p2 = np.radians(np.asarray(lat1, dtype=float)), np.radians(np.asarray(lat2, dtype=float))
    dp = p2 - p1
    dl = np.radians(np.asarray(lon2, dtype=float) - np.asarray(lon1, dtype=float))
    a = np.sin(dp / 2.0) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2.0) ** 2
    return 2.0 * EARTH_R * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def local_xy(lon, lat, lon0: float, lat0: float):
    """Project lon/lat to metres east/north of a local origin (equirectangular).

    Over a city the error against true geodesic distance is well under a
    percent, and it turns "nearest road" into plain Euclidean distance on a
    flat plane, which is what makes the neighbour search a single numpy
    operation instead of a loop of haversines."""
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    k = math.cos(math.radians(lat0))
    return (lon - lon0) * k * (math.pi / 180.0) * EARTH_R, (lat - lat0) * (math.pi / 180.0) * EARTH_R
