"""Pure unit tests for services/attendance.py's match_group_location -
the QR/GPS independence matrix (Rule 9) and multi-location matching
(Rule 8), isolated from HTTP, the DB, and the fake-GPS heuristic (which
these tests never touch, unlike the live integration tests)."""
from dataclasses import dataclass
from typing import Optional

from services.attendance import match_group_location


@dataclass(frozen=True)
class _Loc:
    id: str
    latitude: float
    longitude: float
    radius_meters: float
    qr_token: Optional[str]


ZONE_A = _Loc(id="zone-a", latitude=30.0, longitude=30.0, radius_meters=100, qr_token="qr-a")
ZONE_B = _Loc(id="zone-b", latitude=40.0, longitude=40.0, radius_meters=100, qr_token="qr-b")
# ~0.0005 degrees is roughly 55m at the equator - well inside a 100m radius.
INSIDE_A = (30.0005, 30.0005)
INSIDE_B = (40.0005, 40.0005)
OUTSIDE_BOTH = (1.0, 1.0)


class TestBothRequired:
    def test_correct_qr_and_matching_zone_same_location_succeeds(self):
        loc_id, distance = match_group_location(
            [ZONE_A, ZONE_B], "qr-a", *INSIDE_A, require_qr=True, require_zone=True,
        )
        assert loc_id == "zone-a"
        assert distance is not None

    def test_correct_qr_but_wrong_zone_for_that_location_fails(self):
        # qr-a belongs to Zone A, but the employee is physically at Zone B's
        # coordinates - a QR photographed elsewhere must not be usable here.
        loc_id, distance = match_group_location(
            [ZONE_A, ZONE_B], "qr-a", *INSIDE_B, require_qr=True, require_zone=True,
        )
        assert loc_id is None

    def test_wrong_qr_correct_zone_fails(self):
        loc_id, _ = match_group_location(
            [ZONE_A, ZONE_B], "not-a-real-token", *INSIDE_A, require_qr=True, require_zone=True,
        )
        assert loc_id is None

    def test_neither_matches_fails(self):
        loc_id, _ = match_group_location(
            [ZONE_A, ZONE_B], "not-a-real-token", *OUTSIDE_BOTH, require_qr=True, require_zone=True,
        )
        assert loc_id is None


class TestQrOnlyRequired:
    def test_correct_qr_regardless_of_location_succeeds(self):
        loc_id, distance = match_group_location(
            [ZONE_A, ZONE_B], "qr-a", *OUTSIDE_BOTH, require_qr=True, require_zone=False,
        )
        assert loc_id == "zone-a"
        assert distance is None  # no radius check at all for QR-only

    def test_wrong_qr_fails(self):
        loc_id, _ = match_group_location(
            [ZONE_A, ZONE_B], "nope", *INSIDE_A, require_qr=True, require_zone=False,
        )
        assert loc_id is None

    def test_no_qr_provided_fails(self):
        loc_id, _ = match_group_location(
            [ZONE_A, ZONE_B], None, *INSIDE_A, require_qr=True, require_zone=False,
        )
        assert loc_id is None


class TestZoneOnlyRequired:
    def test_inside_zone_a_succeeds_regardless_of_qr(self):
        loc_id, distance = match_group_location(
            [ZONE_A, ZONE_B], None, *INSIDE_A, require_qr=False, require_zone=True,
        )
        assert loc_id == "zone-a"
        assert distance is not None

    def test_inside_zone_b_succeeds(self):
        loc_id, _ = match_group_location(
            [ZONE_A, ZONE_B], None, *INSIDE_B, require_qr=False, require_zone=True,
        )
        assert loc_id == "zone-b"

    def test_outside_both_zones_fails(self):
        loc_id, _ = match_group_location(
            [ZONE_A, ZONE_B], None, *OUTSIDE_BOTH, require_qr=False, require_zone=True,
        )
        assert loc_id is None

    def test_wrong_qr_does_not_matter_when_only_zone_required(self):
        loc_id, _ = match_group_location(
            [ZONE_A, ZONE_B], "totally-wrong", *INSIDE_A, require_qr=False, require_zone=True,
        )
        assert loc_id == "zone-a"

    def test_no_gps_at_all_fails(self):
        loc_id, _ = match_group_location(
            [ZONE_A, ZONE_B], None, None, None, require_qr=False, require_zone=True,
        )
        assert loc_id is None

    def test_nearest_location_wins_when_multiple_overlap(self):
        near = _Loc(id="near", latitude=30.0, longitude=30.0, radius_meters=1000, qr_token="near-qr")
        far_but_still_in_range = _Loc(id="far", latitude=30.005, longitude=30.005, radius_meters=1000, qr_token="far-qr")
        loc_id, distance = match_group_location(
            [far_but_still_in_range, near], None, 30.0005, 30.0005, require_qr=False, require_zone=True,
        )
        assert loc_id == "near"


class TestNeitherRequired:
    def test_succeeds_with_no_qr_and_no_gps(self):
        loc_id, distance = match_group_location(
            [ZONE_A, ZONE_B], None, None, None, require_qr=False, require_zone=False,
        )
        assert loc_id is None
        assert distance is None

    def test_still_records_location_if_a_valid_qr_was_scanned_anyway(self):
        # Traceability only - not a gate, since neither is required.
        loc_id, _ = match_group_location(
            [ZONE_A, ZONE_B], "qr-b", *OUTSIDE_BOTH, require_qr=False, require_zone=False,
        )
        assert loc_id == "zone-b"


class TestNoLocationsConfigured:
    def test_empty_locations_list_never_matches(self):
        loc_id, distance = match_group_location([], "qr-a", *INSIDE_A, require_qr=True, require_zone=True)
        assert loc_id is None
        assert distance is None
