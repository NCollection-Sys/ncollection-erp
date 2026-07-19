def _create_demo_stock(env):
    """Seed initial on-hand quantities for Fresh Origin products."""
    location = env.ref('stock.stock_location_stock', raise_if_not_found=False)
    if not location:
        return

    stock_per_xmlid = {
        'ncollection_demo_freshorigin.product_orange_juice': 450,
        'ncollection_demo_freshorigin.product_green_juice': 320,
        'ncollection_demo_freshorigin.product_apple_juice': 280,
        'ncollection_demo_freshorigin.product_berry_smoothie': 220,
        'ncollection_demo_freshorigin.product_banana_smoothie': 180,
        'ncollection_demo_freshorigin.product_glass_bottle_500': 1500,
    }

    Quant = env['stock.quant'].with_context(inventory_mode=True)
    for xmlid, qty in stock_per_xmlid.items():
        tmpl = env.ref(xmlid, raise_if_not_found=False)
        if not tmpl:
            continue
        product = tmpl.product_variant_id
        if not product:
            continue
        existing = Quant.search([
            ('product_id', '=', product.id),
            ('location_id', '=', location.id),
        ], limit=1)
        if existing:
            existing.inventory_quantity = qty
            existing.action_apply_inventory()
        else:
            quant = Quant.create({
                'product_id': product.id,
                'location_id': location.id,
                'inventory_quantity': qty,
            })
            quant.action_apply_inventory()
