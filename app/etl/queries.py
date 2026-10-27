"""Canonical, parameterised SQL for DemandIQ's training-data extracts.

All timestamps are **event time** first: ``COALESCE(occurred_at, created_at)``.
``created_at`` is only the record time; after a late webhook delivery it can be
days after the business event (see StockPilot Phase 0B).

Movement analytics also honour ``sp_movement_exclusions``: the known-bad
2026-08-08 manual-adjustment batch is filtered read-side, never deleted from the
append-only ledger.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# 1. Daily component / product consumption, net of same-day returns
# --------------------------------------------------------------------------
CONSUMPTION_QUERY = """
WITH confirmations AS (
    SELECT m.organization_id,
           m.item_id,
           m.product_id,
           date_trunc('day', COALESCE(m.occurred_at, m.created_at))::date AS ds,
           SUM(ABS(m.qty_delta)) AS consumed
    FROM sp_item_movements m
    WHERE m.movement_type = 'confirm'
      AND m.qty_delta < 0
      AND m.organization_id = :org_id
      AND COALESCE(m.occurred_at, m.created_at) >= :start_date
      AND NOT EXISTS (
          SELECT 1 FROM sp_movement_exclusions e WHERE e.movement_id = m.id
      )
    GROUP BY m.organization_id, m.item_id, m.product_id, ds
),
returns AS (
    SELECT r.organization_id,
           r.item_id,
           r.product_id,
           date_trunc('day', COALESCE(r.occurred_at, r.created_at))::date AS ds,
           SUM(ABS(r.qty_delta)) AS returned
    FROM sp_item_movements r
    WHERE r.movement_type = 'return'
      AND r.organization_id = :org_id
      AND COALESCE(r.occurred_at, r.created_at) >= :start_date
      AND NOT EXISTS (
          SELECT 1 FROM sp_movement_exclusions e WHERE e.movement_id = r.id
      )
    GROUP BY r.organization_id, r.item_id, r.product_id, ds
)
SELECT c.organization_id,
       c.item_id,
       c.product_id,
       c.ds,
       c.consumed,
       COALESCE(r.returned, 0) AS returned,
       GREATEST(c.consumed - COALESCE(r.returned, 0), 0) AS net_consumed
FROM confirmations c
LEFT JOIN returns r
       ON r.organization_id = c.organization_id
      AND r.ds = c.ds
      AND (
           (c.item_id IS NOT NULL AND r.item_id = c.item_id)
           OR (c.item_id IS NULL AND c.product_id IS NOT NULL AND r.product_id = c.product_id)
      )
ORDER BY c.ds, c.item_id, c.product_id
"""

# --------------------------------------------------------------------------
# 2. Order history (one row per order, with line/unit aggregates)
# --------------------------------------------------------------------------
ORDER_HISTORY_QUERY = """
SELECT o.id,
       o.woocommerce_id,
       o.woocommerce_number,
       o.status,
       o.reservation_state,
       o.currency,
       o.total,
       o.subtotal,
       o.discount_total,
       o.shipping_total,
       o.refund_total,
       o.return_charge,
       o.order_created_at,
       o.order_updated_at,
       COUNT(oi.id) AS line_count,
       COALESCE(SUM(oi.quantity), 0) AS total_units,
       COALESCE(SUM(oi.line_total), 0) AS line_revenue
FROM sp_orders o
LEFT JOIN sp_order_items oi ON oi.order_id = o.id
WHERE o.organization_id = :org_id
  AND o.order_created_at >= :start_date
GROUP BY o.id
ORDER BY o.order_created_at
"""

# --------------------------------------------------------------------------
# 3. Bill of materials (item and sub-product component lines)
# --------------------------------------------------------------------------
BOM_QUERY = """
SELECT b.id AS bom_line_id,
       b.product_id,
       b.component_type,
       b.item_id,
       b.component_product_id,
       b.quantity AS qty_per_unit,
       p.name AS product_name,
       CASE b.component_type
           WHEN 'item' THEN i.name
           WHEN 'product' THEN cp.name
       END AS component_name,
       COALESCE(i.sku, cp.sku) AS component_sku
FROM sp_bom_lines b
JOIN sp_products p ON p.id = b.product_id
LEFT JOIN sp_items i ON i.id = b.item_id
LEFT JOIN sp_products cp ON cp.id = b.component_product_id
WHERE p.organization_id = :org_id
  AND p.deleted_at IS NULL
ORDER BY b.product_id, b.component_type, b.id
"""

# --------------------------------------------------------------------------
# 4. Current stock levels (items and finished products)
# --------------------------------------------------------------------------
STOCK_LEVELS_QUERY = """
SELECT 'item' AS entity_type,
       i.id AS entity_id,
       i.organization_id,
       i.name,
       i.sku,
       i.unit,
       i.quantity_on_hand,
       i.quantity_reserved,
       i.quantity_on_hand - i.quantity_reserved AS quantity_available,
       i.low_stock_threshold,
       i.cost_price AS unit_cost
FROM sp_items i
WHERE i.organization_id = :org_id
  AND i.deleted_at IS NULL
UNION ALL
SELECT 'product' AS entity_type,
       p.id AS entity_id,
       p.organization_id,
       p.name,
       p.sku,
       'pcs' AS unit,
       p.quantity_on_hand,
       p.quantity_reserved,
       p.quantity_on_hand - p.quantity_reserved AS quantity_available,
       p.low_stock_threshold,
       p.cost AS unit_cost
FROM sp_products p
WHERE p.organization_id = :org_id
  AND p.deleted_at IS NULL
ORDER BY entity_type, entity_id
"""

EXTRACT_QUERIES = {
    "consumption": CONSUMPTION_QUERY,
    "order_history": ORDER_HISTORY_QUERY,
    "bom": BOM_QUERY,
    "stock_levels": STOCK_LEVELS_QUERY,
}
