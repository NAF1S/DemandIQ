from app.etl.queries import (
    BOM_QUERY,
    CONSUMPTION_QUERY,
    EXTRACT_QUERIES,
    ORDER_HISTORY_QUERY,
    STOCK_LEVELS_QUERY,
)


def test_consumption_query_uses_confirm_deductions_and_event_time():
    assert "movement_type = 'confirm'" in CONSUMPTION_QUERY
    assert "qty_delta < 0" in CONSUMPTION_QUERY
    assert "COALESCE(m.occurred_at, m.created_at)" in CONSUMPTION_QUERY
    assert "sp_movement_exclusions" in CONSUMPTION_QUERY
    assert "movement_type = 'return'" in CONSUMPTION_QUERY
    assert "GREATEST(" in CONSUMPTION_QUERY


def test_consumption_query_is_parameterized():
    assert ":org_id" in CONSUMPTION_QUERY
    assert ":start_date" in CONSUMPTION_QUERY
    for query in EXTRACT_QUERIES.values():
        assert ":org_id" in query


def test_order_history_aggregates_lines_and_units():
    assert "COUNT(oi.id) AS line_count" in ORDER_HISTORY_QUERY
    assert "SUM(oi.quantity)" in ORDER_HISTORY_QUERY
    assert "LEFT JOIN sp_order_items" in ORDER_HISTORY_QUERY
    assert "o.order_created_at >= :start_date" in ORDER_HISTORY_QUERY


def test_bom_query_handles_both_component_types():
    assert "component_type" in BOM_QUERY
    assert "component_product_id" in BOM_QUERY
    assert "LEFT JOIN sp_products cp" in BOM_QUERY
    assert "p.deleted_at IS NULL" in BOM_QUERY


def test_stock_levels_query_covers_items_and_products():
    assert "FROM sp_items" in STOCK_LEVELS_QUERY
    assert "FROM sp_products" in STOCK_LEVELS_QUERY
    assert "UNION ALL" in STOCK_LEVELS_QUERY
    assert "quantity_on_hand - i.quantity_reserved" in STOCK_LEVELS_QUERY
    assert "quantity_on_hand - p.quantity_reserved" in STOCK_LEVELS_QUERY


def test_extract_query_registry_has_all_four_datasets():
    assert set(EXTRACT_QUERIES) == {"consumption", "order_history", "bom", "stock_levels"}
