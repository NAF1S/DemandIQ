from datetime import date

from app.etl.traffic import (
    group_post_views,
    map_products,
    overlap_window,
    parse_traffic_date,
    read_rows,
    slugify,
    url_slug,
    validate_export,
)


def test_parse_traffic_date_accepts_single_digit_month_and_day():
    assert parse_traffic_date("1/2/2026") == date(2026, 1, 2)
    assert parse_traffic_date("10/26/2026") == date(2026, 10, 26)


def test_read_rows_handles_bom_and_quoted_commas(tmp_path):
    path = tmp_path / "views.csv"
    path.write_text(
        '\ufeffDate,Post_ID,Title,Views,URL\n'
        '1/2/2026,123,"Belt, Black",10,https://shop.example.com/product/belt-black/\n',
        encoding="utf-8",
    )

    headers, rows = read_rows(path)

    assert headers == ["Date", "Post_ID", "Title", "Views", "URL"]
    assert rows[0][2] == "Belt, Black"
    assert rows[0][1] == "123"


def test_validate_export_reports_range_and_duplicates(tmp_path):
    path = tmp_path / "views.csv"
    path.write_text(
        "Date,Post_ID,URL\n"
        "1/2/2026,1,https://shop.example.com/product/a/\n"
        "1/2/2026,1,https://shop.example.com/product/a/\n"
        "1/3/2026,2,https://shop.example.com/product/b/\n",
        encoding="utf-8",
    )

    result = validate_export(path, key_columns=("Date", "Post_ID", "URL"))

    assert result["rows"] == 3
    assert result["date_min"] == "2026-01-02"
    assert result["date_max"] == "2026-01-03"
    assert result["duplicate_keys"] == 1
    assert result["bom"] is False


def test_group_post_views_filters_non_product_rows():
    rows = [
        {"date": date(2026, 1, 1), "post_id": 1, "title": "Home", "views": 5, "url": "https://shop.example.com/"},
        {"date": date(2026, 1, 1), "post_id": 2, "title": "Belt", "views": 3, "url": "https://shop.example.com/product/belt/"},
        {"date": date(2026, 1, 2), "post_id": 2, "title": "Belt", "views": 4, "url": "https://shop.example.com/product/belt/"},
    ]

    groups = group_post_views(rows, product_only=True)

    assert list(groups) == [2]
    assert groups[2]["views"] == 7
    assert groups[2]["days"] == 2
    assert groups[2]["first"] == date(2026, 1, 1)
    assert groups[2]["last"] == date(2026, 1, 2)


def test_map_products_primary_and_fallback_keys():
    groups = {
        123: {"post_id": 123, "title": "Belt, Black", "url": "https://shop.example.com/product/belt-black/", "views": 10, "days": 1, "first": date(2026, 1, 1), "last": date(2026, 1, 1)},
        456: {"post_id": 456, "title": "Wallet, Brown", "url": "https://shop.example.com/product/wallet-brown/", "views": 5, "days": 1, "first": date(2026, 1, 1), "last": date(2026, 1, 1)},
        789: {"post_id": 789, "title": "Different title", "url": "https://shop.example.com/product/belt-black/", "views": 2, "days": 1, "first": date(2026, 1, 1), "last": date(2026, 1, 1)},
        999: {"post_id": 999, "title": "Unknown", "url": "https://shop.example.com/product/unknown/", "views": 1, "days": 1, "first": date(2026, 1, 1), "last": date(2026, 1, 1)},
    }
    products = [
        {"id": 1, "name": "Belt Black", "woo_product_id": 123},
        {"id": 2, "name": "Wallet, Brown", "woo_product_id": 555},
        {"id": 3, "name": "No Traffic", "woo_product_id": 777},
    ]

    result = map_products(groups, products)

    assert result["match_counts"]["woo_id"] == 1
    assert result["match_counts"]["title"] == 1
    assert result["match_counts"]["slug"] == 1
    assert len(result["unmatched"]) == 1
    assert result["unmatched"][0]["post_id"] == 999
    assert [item["product_id"] for item in result["products_without_traffic"]] == [3]


def test_slug_helpers():
    assert slugify("Embossed Stitched & Classic (Brown)") == "embossed-stitched-classic-brown"
    assert url_slug("https://shop.example.com/product/belt-black/") == "belt-black"


def test_overlap_window_uses_max_start_and_min_end():
    result = overlap_window(date(2026, 5, 11), date(2026, 10, 26), date(2026, 1, 11), date(2026, 10, 26))

    assert result == {"start": "2026-05-11", "end": "2026-10-26", "days": 169, "valid": True}
