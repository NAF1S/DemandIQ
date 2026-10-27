"""Resolve Jetpack post rows to portal products."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.traffic import normalize_title, url_slug


@dataclass(frozen=True)
class Product:
    id: int
    woo_product_id: int | None
    name: str
    slug: str | None = None


@dataclass(frozen=True)
class Resolution:
    product: Product | None
    method: str | None  # woo_id | slug | title | None


class ProductIndex:
    """First-hit-wins lookup: woo_product_id, then slug, then normalised title."""

    def __init__(self, products: Iterable[Product]):
        self.by_woo: dict[int, Product] = {}
        self.by_slug: dict[str, Product] = {}
        self.by_title: dict[str, Product] = {}

        for product in products:
            if product.woo_product_id is not None:
                self.by_woo.setdefault(int(product.woo_product_id), product)
            if product.slug:
                self.by_slug.setdefault(product.slug.strip().strip("/").lower(), product)
            if product.name:
                self.by_title.setdefault(normalize_title(product.name), product)

    def resolve(self, post_id: int, title: str, url: str) -> Resolution:
        product = self.by_woo.get(int(post_id))
        if product is not None:
            return Resolution(product, "woo_id")

        slug = url_slug(url)
        product = self.by_slug.get(slug.lower())
        if product is not None:
            return Resolution(product, "slug")

        product = self.by_title.get(normalize_title(title or ""))
        if product is not None:
            return Resolution(product, "title")

        return Resolution(None, None)


async def load_products(session: AsyncSession, organization_id: int, store_id: int) -> list[Product]:
    """Load the org/store catalog; use sp_products.slug when that column exists."""
    has_slug = await session.scalar(
        text(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'sp_products'
                  AND column_name = 'slug'
            )
            """
        )
    )

    slug_select = "slug" if has_slug else "NULL AS slug"

    result = await session.execute(
        text(
            f"""
            SELECT id, woo_product_id, name, {slug_select}
            FROM sp_products
            WHERE organization_id = :org_id
              AND store_id = :store_id
              AND deleted_at IS NULL
            ORDER BY id
            """
        ),
        {"org_id": organization_id, "store_id": store_id},
    )

    products: list[Product] = []
    for row in result.mappings().all():
        products.append(
            Product(
                id=int(row["id"]),
                woo_product_id=int(row["woo_product_id"]) if row["woo_product_id"] is not None else None,
                name=str(row["name"] or ""),
                slug=str(row["slug"]) if row["slug"] else None,
            )
        )

    return products
