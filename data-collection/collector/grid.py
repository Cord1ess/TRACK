"""Turns coordinates into tile positions and back.

Online maps cut the world into a grid of square tiles. Zoom in one level and
every tile becomes four. This file does that arithmetic: which tile covers a
given place, where a tile's edges are, and how many metres a pixel is worth.

Everything to do with position goes through here, so that the download and the
later reading of the images agree exactly on where each pixel sits. Counting
starts at the top left corner of the world.
"""

import math

EQ_RES = 156543.03392  # metres per pixel at zoom 0 on the equator (256 px tiles)


def lon_to_tx(lon: float, z: int) -> float:
    """Longitude -> fractional tile x at zoom z."""
    return (lon + 180.0) / 360.0 * (1 << z)


def lat_to_ty(lat: float, z: int) -> float:
    """Latitude -> fractional tile y at zoom z."""
    r = math.radians(lat)
    return (1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * (1 << z)


def tx_to_lon(tx: float, z: int) -> float:
    return tx / (1 << z) * 360.0 - 180.0


def ty_to_lat(ty: float, z: int) -> float:
    n = math.pi - 2.0 * math.pi * ty / (1 << z)
    return math.degrees(math.atan(math.sinh(n)))


def lonlat_to_pixel(lon: float, lat: float, z: int, tile_px: int = 256) -> tuple[float, float]:
    """Global pixel coordinates at zoom z."""
    return lon_to_tx(lon, z) * tile_px, lat_to_ty(lat, z) * tile_px


def pixel_to_lonlat(px: float, py: float, z: int, tile_px: int = 256) -> tuple[float, float]:
    return tx_to_lon(px / tile_px, z), ty_to_lat(py / tile_px, z)


def tile_bounds(x: int, y: int, z: int) -> dict:
    return {
        "north": ty_to_lat(y, z), "south": ty_to_lat(y + 1, z),
        "west": tx_to_lon(x, z), "east": tx_to_lon(x + 1, z),
    }


def metres_per_pixel(lat: float, z: int, tile_px: int = 256) -> float:
    return EQ_RES * math.cos(math.radians(lat)) / (2 ** z) * (256 / tile_px)


def tile_range(bbox: dict, z: int) -> dict:
    """The block of tiles covering an area: first and last tile each way."""
    return {
        "x0": math.floor(lon_to_tx(bbox["west"], z)),
        "x1": math.floor(lon_to_tx(bbox["east"], z)),
        "y0": math.floor(lat_to_ty(bbox["north"], z)),
        "y1": math.floor(lat_to_ty(bbox["south"], z)),
    }


def expected_tiles(bbox: dict, z: int) -> set[tuple[int, int]]:
    r = tile_range(bbox, z)
    return {(x, y) for x in range(r["x0"], r["x1"] + 1) for y in range(r["y0"], r["y1"] + 1)}


def tile_centre(x: int, y: int, z: int) -> tuple[float, float]:
    return ty_to_lat(y + 0.5, z), tx_to_lon(x + 0.5, z)
