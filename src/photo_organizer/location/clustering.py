"""GPS clustering — group nearby coordinates into distinct places.

Independent of the geocoding cache: the cache keys on *rounded*
coordinates, clustering keys on *physical distance*. Two photos taken a
few meters apart at the same spot must land in one cluster so the day's
dominant-location vote is not split into several places.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class GpsCluster:
    """A group of coordinates within ``radius_m`` of one another."""

    points: tuple[tuple[float, float], ...]
    centroid: tuple[float, float]


class GPSClusterer:
    """Groups coordinates into clusters by physical distance (haversine).

    Any two points closer than ``radius_m`` belong to the same cluster,
    transitively (union-find over the pair graph). A short walk through
    a plaza therefore stays one cluster even if the first and last
    points are farther apart than the radius.
    """

    def __init__(self, radius_m: float = 200.0) -> None:
        if radius_m <= 0:
            raise ValueError("radius_m must be positive")
        self.radius_m = radius_m

    def cluster(self, points: Iterable[tuple[float, float]]) -> list[GpsCluster]:
        """Return the clusters of *points*, each with its centroid."""
        pts = list(points)
        if not pts:
            return []

        parent = list(range(len(pts)))

        def find(index: int) -> int:
            root = index
            while parent[root] != root:
                root = parent[root]
            while parent[index] != index:
                nxt = parent[index]
                parent[index] = root
                index = nxt
            return root

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                if _haversine_m(pts[i], pts[j]) <= self.radius_m:
                    union(i, j)

        groups: dict[int, list[tuple[float, float]]] = {}
        for idx, point in enumerate(pts):
            groups.setdefault(find(idx), []).append(point)

        clusters: list[GpsCluster] = []
        for members in groups.values():
            lat = sum(p[0] for p in members) / len(members)
            lon = sum(p[1] for p in members) / len(members)
            clusters.append(GpsCluster(points=tuple(members), centroid=(lat, lon)))
        return clusters


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lon) points, in meters."""
    lat1, lon1 = a
    lat2, lon2 = b
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.asin(math.sqrt(h))
