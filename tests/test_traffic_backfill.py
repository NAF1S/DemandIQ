from datetime import date
from pathlib import Path

import pytest

from app.etl.traffic_backfill import (
    PostView,
    build_validation,
    cart_checkout_views,
    date_range,
    load_overview,
    load_post_views,
    load_referrers,
    map_product_views,
)
from app.etl.traffic_mapping import Product, ProductIndex

DATA_DIR = Path(__file__).resolve().parents[2] / "web-traffic-data"


def write_post_views(tmp_path: Path) -> Path:
    path = tmp_path / "daily_post_product_views.csv"
    path.write_text(
        "Date,Post_ID,Title,Views,URL\n"
        '1/2/2026,100,"Belt, Black",5,https://shop.hidehaven.me/product/belt-black/\n'
        "1/2/2026,8,Cart,2,https://shop.hidehaven.me/cart/\n"
        "1/2/2026,9,Checkout,1,https://shop.hidehaven.me/checkout/\n"
        "1/2/2026,17,Home,10,https://shop.hidehaven.me/\n"
        "1/2/2026,999,Orphan,3,https://shop.hidehaven.me/product/orphan/\n"
        "1/3/2026,100,\"Belt, Black\",7,https://shop.hidehaven.me/product/belt-black/\n",
        encoding="utf-8",
    )
    return path


def write_overview(tmp_path: Path, first_day_views: int = 21) -> Path:
    path = tmp_path / "daily_traffic_overview.csv"
    path.write_text(
        "Date,Views,Visitors,,,,\n"
        f"1/2/2026,{first_day_views},9,,,,\n"
        "1/3/2026,7,5,,,,\n",
        encoding="utf-8",
    )
    return path


def write_referrers(tmp_path: Path) -> Path:
    path = tmp_path / "daily_referrers.csv"
    path.write_text(
        "Date,Referrer_Group,Views\n"
        "1/2/2026,facebook.com,10\n"
        "1/3/2026,Search Engines,5\n"
        "1/3/2026,,2\n",
        encoding="utf-8",
    )
    return path


def test_loaders_parse_dates_products_and_non_product_rows(tmp_path):
    post_views = load_post_views(write_post_views(tmp_path))
    overview = load_overview(write_overview(tmp_path))
    referrers = load_referrers(write_referrers(tmp_path))

    assert len(post_views) == 6
    assert post_views[0].traffic_date == date(2026, 1, 2)
    assert post_views[0].post_id == 100
    assert post_views[0].slug == "belt-black"
    assert post_views[1].slug is None  # Cart
    assert post_views[3].slug is None  # Home

    assert len(overview) == 2
    assert overview[0].page_views == 21
    assert overview[0].unique_visitors == 9

    assert len(referrers) == 2  # empty Referrer_Group is skipped
    assert referrers[0].referrer_group == "facebook.com"


def test_mapping_aggregates_product_days_and_records_misses(tmp_path):
    post_views = load_post_views(write_post_views(tmp_path))
    index = ProductIndex([Product(id=1, woo_product_id=100, name="Belt, Black")])

    product_days, misses, counts = map_product_views(post_views, index)

    assert len(product_days) == 2
    assert {row["traffic_date"]: row["page_views"] for row in product_days} == {
        date(2026, 1, 2): 5,
        date(2026, 1, 3): 7,
    }
    assert len(misses) == 1
    assert misses[0].post_id == 999
    assert counts == {"woo_id": 2, "slug": 0, "title": 0, "miss": 1}


def test_cart_and_checkout_views_use_post_ids(tmp_path):
    post_views = load_post_views(write_post_views(tmp_path))
    cart, checkout = cart_checkout_views(post_views)

    assert cart == {date(2026, 1, 2): 2}
    assert checkout == {date(2026, 1, 2): 1}


def test_validation_passes_when_post_sum_matches_overview(tmp_path):
    post_views = load_post_views(write_post_views(tmp_path))
    overview = load_overview(write_overview(tmp_path))
    referrers = load_referrers(write_referrers(tmp_path))
    index = ProductIndex([Product(id=1, woo_product_id=100, name="Belt, Black")])
    product_days, misses, counts = map_product_views(post_views, index)

    validation = build_validation(post_views, overview, referrers, product_days, misses, counts)

    assert validation["post_vs_overview"]["mismatch_dates"] == 0
    assert validation["post_vs_overview"]["mismatch_threshold_pct"] == 2.0
    assert validation["row_counts"]["product_rows"] == 3
    assert validation["row_counts"]["product_days"] == 2
    assert validation["row_counts"]["mapping_misses"] == 1
    assert validation["referrer_coverage"]["coverage_dates"] == 2


def test_validation_flags_a_material_post_overview_gap(tmp_path):
    post_views = load_post_views(write_post_views(tmp_path))
    overview = load_overview(write_overview(tmp_path, first_day_views=10))
    referrers = load_referrers(write_referrers(tmp_path))
    index = ProductIndex([Product(id=1, woo_product_id=100, name="Belt, Black")])
    product_days, misses, counts = map_product_views(post_views, index)

    validation = build_validation(post_views, overview, referrers, product_days, misses, counts)

    assert validation["post_vs_overview"]["mismatch_dates"] == 1
    assert validation["post_vs_overview"]["mismatches"][0]["date"] == "2026-01-02"
    assert validation["post_vs_overview"]["mismatches"][0]["pct"] == 110.0


def test_date_range():
    rows = [
        PostView(date(2026, 1, 2), 1, "A", 1, "https://x/product/a/", "a"),
        PostView(date(2026, 1, 5), 2, "B", 1, "https://x/product/b/", "b"),
    ]
    assert date_range(rows) == {"first": "2026-01-02", "last": "2026-01-05"}


@pytest.mark.skipif(not DATA_DIR.is_dir(), reason="Jetpack CSV exports are not present")
def test_real_jetpack_exports_have_expected_shape():
    post_views = load_post_views(DATA_DIR / "daily_post_product_views.csv")
    overview = load_overview(DATA_DIR / "daily_traffic_overview.csv")
    referrers = load_referrers(DATA_DIR / "daily_referrers.csv")

    assert len(post_views) == 5391
    assert len(overview) == 365
    assert len(referrers) == 857

    product_rows = [row for row in post_views if row.slug is not None]
    assert len(product_rows) == 3948
    assert len({row.post_id for row in product_rows}) == 30
    assert min(row.traffic_date for row in product_rows) == date(2026, 1, 11)
    assert max(row.traffic_date for row in product_rows) == date(2026, 10, 26)
    assert min(row.traffic_date for row in overview) == date(2025, 10, 27)
    assert max(row.traffic_date for row in overview) == date(2026, 10, 26)
