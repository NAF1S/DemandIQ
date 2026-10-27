from app.etl.traffic_mapping import Product, ProductIndex


def test_product_index_prefers_woo_id_then_slug_then_title():
    index = ProductIndex(
        [
            Product(id=1, woo_product_id=123, name="Belt Black"),
            Product(id=2, woo_product_id=None, name="Wallet, Brown", slug="wallet-brown"),
            Product(id=3, woo_product_id=555, name="Combo & Wallet"),
        ]
    )

    exact = index.resolve(123, "Anything", "https://shop.example.com/product/other/")
    assert exact.product is not None and exact.product.id == 1
    assert exact.method == "woo_id"

    slug = index.resolve(999, "Anything", "https://shop.example.com/product/wallet-brown/")
    assert slug.product is not None and slug.product.id == 2
    assert slug.method == "slug"

    title = index.resolve(998, "Combo & Wallet", "https://shop.example.com/product/whatever/")
    assert title.product is not None and title.product.id == 3
    assert title.method == "title"

    miss = index.resolve(997, "Unknown", "https://shop.example.com/product/unknown/")
    assert miss.product is None
    assert miss.method is None
