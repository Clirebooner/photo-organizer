"""Unit tests for GPS clustering — pure geometry, no network."""

import pytest

from photo_organizer.location.clustering import GPSClusterer


def test_nearby_points_same_cluster() -> None:
    clusterer = GPSClusterer()  # default radius: 200 m
    a = (22.54000, 113.92000)
    b = (22.54018, 113.92000)  # ~20 m north
    clusters = clusterer.cluster([a, b])
    assert len(clusters) == 1
    assert a in clusters[0].points
    assert b in clusters[0].points


def test_distant_points_different_clusters() -> None:
    clusterer = GPSClusterer()
    a = (22.54000, 113.92000)
    b = (22.54450, 113.92000)  # ~500 m north
    clusters = clusterer.cluster([a, b])
    assert len(clusters) == 2


def test_chain_of_points_merges() -> None:
    """150 m hops chain into one cluster even though the ends are 300 m apart."""
    clusterer = GPSClusterer()
    a = (22.54000, 113.92000)
    b = (22.54135, 113.92000)  # ~150 m from a
    c = (22.54270, 113.92000)  # ~150 m from b
    clusters = clusterer.cluster([a, b, c])
    assert len(clusters) == 1


def test_configurable_radius() -> None:
    clusterer = GPSClusterer(radius_m=600)
    a = (22.54000, 113.92000)
    b = (22.54450, 113.92000)  # ~500 m
    assert len(clusterer.cluster([a, b])) == 1


def test_centroid_is_mean() -> None:
    clusterer = GPSClusterer()
    a = (22.54000, 113.92000)
    b = (22.54100, 113.92000)  # ~111 m -> same cluster
    clusters = clusterer.cluster([a, b])
    assert len(clusters) == 1
    lat, lon = clusters[0].centroid
    assert lat == pytest.approx(22.54050)
    assert lon == pytest.approx(113.92000)


def test_empty_points() -> None:
    assert GPSClusterer().cluster([]) == []
