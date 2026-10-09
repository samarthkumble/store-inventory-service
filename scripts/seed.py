"""Seed the database: 3 Bengaluru stores, 200 products, stock in each store.

Run from the project root:
    python -m scripts.seed            # refuses if data already exists
    python -m scripts.seed --reset    # wipes all tables, then seeds again

A fixed random seed makes every run produce identical data, so numbers you
measure later (test results, response times) are reproducible.
"""
import argparse
import itertools
import random
import sys
from decimal import Decimal

from sqlalchemy import func, insert, select, text

from app.db.session import SessionLocal
from app.models import Product, Stock, Store

RNG_SEED = 42
PRODUCTS_PER_CATEGORY = 40

STORES = [
    {"code": "BLR-WHF-01", "name": "Whitefield", "city": "Bengaluru"},
    {"code": "BLR-JNR-01", "name": "Jayanagar", "city": "Bengaluru"},
    {"code": "BLR-HBL-01", "name": "Hebbal", "city": "Bengaluru"},
]

# Each family is (name template, option lists, base price in rupees).
# Every option is (label, price multiplier). The script builds every
# combination, e.g. "Interior Emulsion, Satin, Ivory, 4 L", then samples 40
# products per category.
CATALOG = {
    "paint": ("PNT", [
        ("Interior Emulsion, {}, {}, {}",
         [[("Matt", 1), ("Satin", 1.15), ("Sheen", 1.3)],
          [("Brilliant White", 1), ("Ivory", 1.05), ("Sky Blue", 1.1), ("Sage Green", 1.1)],
          [("1 L", 1), ("4 L", 3.6), ("10 L", 8.5), ("20 L", 16)]], 320),
        ("Exterior Weatherproof Emulsion, {}, {}",
         [[("White", 1), ("Cream", 1.05), ("Terracotta", 1.1)],
          [("4 L", 1), ("10 L", 2.3), ("20 L", 4.4)]], 1450),
        ("Gloss Enamel Paint, {}, {}",
         [[("Black", 1), ("Signal Red", 1), ("White", 1), ("Dark Brown", 1)],
          [("500 mL", 1), ("1 L", 1.8), ("4 L", 6.5)]], 260),
        ("Wall Primer, {}, {}",
         [[("Water-based", 1), ("Solvent-based", 1.25)],
          [("1 L", 1), ("4 L", 3.5), ("10 L", 8)]], 240),
        ("White Cement Wall Putty, {}", [[("5 kg", 1), ("20 kg", 3.4), ("40 kg", 6.2)]], 330),
        ("Paint Roller with Tray, {}", [[("7 in", 1), ("9 in", 1.25)]], 349),
        ("Synthetic Paint Brush, {}", [[("1 in", 1), ("2 in", 1.6), ("3 in", 2.2), ("4 in", 2.8)]], 79),
        ("Masking Tape, {} x 20 m", [[("24 mm", 1), ("48 mm", 1.7)]], 89),
        ("Sandpaper Sheet, {} Grit, Pack of 10", [[("80", 1), ("120", 1), ("220", 1.1)]], 149),
    ]),
    "plumbing": ("PLB", [
        ("Pillar Tap, {}, {}",
         [[("Chrome-plated Brass", 1), ("Stainless Steel", 1.3)],
          [("Short Body", 1), ("Long Body", 1.2)]], 1190),
        ("Wall Mixer Tap, {}", [[("Chrome", 1), ("Matte Black", 1.25)]], 2890),
        ("PVC Ball Valve, {}", [[("1/2 in", 1), ("3/4 in", 1.3), ("1 in", 1.8)]], 129),
        ("CPVC Pipe, {}, 3 m", [[("1/2 in", 1), ("3/4 in", 1.5), ("1 in", 2.2)]], 289),
        ("CPVC Elbow 90 deg, {}, Pack of 10", [[("1/2 in", 1), ("3/4 in", 1.5), ("1 in", 2.2)]], 119),
        ("PTFE Thread Seal Tape, {}", [[("12 mm x 10 m", 1), ("19 mm x 15 m", 1.6)]], 39),
        ("Tap Washer Kit, {}", [[("Rubber, 20 pcs", 1), ("Fibre, 20 pcs", 1.2)]], 99),
        ("Flexible Connection Hose, {}", [[("12 in", 1), ("18 in", 1.2), ("24 in", 1.4)]], 189),
        ("Health Faucet with Hose, {}", [[("ABS", 1), ("Brass", 2.3)]], 449),
        ("Kitchen Sink Waste Coupling, {}", [[("Plastic", 1), ("Stainless Steel", 2.4)]], 179),
        ("Pipe Wrench, {}", [[("10 in", 1), ("14 in", 1.4), ("18 in", 2)]], 549),
        ("Plunger, {}", [[("Standard", 1), ("Heavy Duty", 1.6)]], 249),
        ("Shower Head, {}", [[("4 in Round", 1), ("6 in Square", 1.5), ("8 in Rain", 2.2)]], 699),
        ("Floor Drain Trap, {} Stainless Steel", [[("4 in", 1), ("5 in", 1.3), ("6 in", 1.7)]], 249),
        ("Water Tank Float Valve, {}", [[("1/2 in", 1), ("3/4 in", 1.3)]], 299),
        ("Angle Valve, {}", [[("Chrome", 1), ("Brass", 1.4)]], 349),
    ]),
    "tools": ("TLS", [
        ("Cordless Drill Driver, {}, {}",
         [[("12 V", 1), ("18 V", 1.5)], [("1 Battery", 1), ("2 Batteries", 1.3)]], 3499),
        ("Impact Drill, {}", [[("500 W", 1), ("650 W", 1.25), ("850 W", 1.6)]], 2299),
        ("Angle Grinder, {}", [[("4 in 720 W", 1), ("5 in 1100 W", 1.45)]], 2799),
        ("Claw Hammer, {}", [[("250 g", 1), ("450 g", 1.3), ("680 g", 1.6)]], 349),
        ("Screwdriver Set, {}", [[("6 pcs", 1), ("12 pcs", 1.7), ("32 pcs Precision", 1.4)]], 399),
        ("Combination Spanner Set, {}", [[("8 pcs", 1), ("12 pcs", 1.4), ("16 pcs", 1.9)]], 899),
        ("Measuring Tape, {}", [[("3 m", 1), ("5 m", 1.4), ("8 m", 2)]], 149),
        ("Spirit Level, {}", [[("300 mm", 1), ("600 mm", 1.5), ("1200 mm", 2.4)]], 299),
        ("Utility Knife, {}", [[("Snap-off 18 mm", 1), ("Heavy Duty Retractable", 1.8)]], 149),
        ("Hacksaw Frame with Blade, {}", [[("12 in", 1)]], 299),
        ("Masonry Drill Bit Set, {}", [[("5 pcs", 1), ("10 pcs", 1.7)]], 349),
        ("Combination Plier, {}", [[("6 in", 1), ("8 in", 1.3)]], 249),
        ("Tool Box, {}", [[("14 in Plastic", 1), ("19 in Plastic", 1.5), ("Metal Cantilever", 3)]], 549),
        ("Allen Key Set, {}", [[("9 pcs Metric", 1), ("9 pcs Ball End", 1.4)]], 199),
        ("Safety Goggles, {}", [[("Clear", 1), ("Anti-fog", 1.5)]], 149),
        ("Work Gloves, {}", [[("Cotton Knitted", 1), ("Leather", 3), ("Nitrile Coated", 1.6)]], 99),
    ]),
    "electrical": ("ELC", [
        ("LED Bulb, {}, {}",
         [[("9 W", 1), ("12 W", 1.35), ("15 W", 1.7)],
          [("Cool Daylight", 1), ("Warm White", 1)]], 99),
        ("LED Batten Tube Light, {}, {}",
         [[("20 W 4 ft", 1), ("36 W 4 ft", 1.5)],
          [("Cool Daylight", 1), ("Warm White", 1)]], 299),
        ("Modular Switch, {}, Pack of 10",
         [[("6 A 1-Way", 1), ("16 A 1-Way", 1.8), ("6 A 2-Way", 1.4)]], 449),
        ("Modular Socket, {}", [[("6 A 3-Pin", 1), ("16 A 3-Pin", 1.6), ("Universal 2-in-1", 1.3)]], 129),
        ("FR PVC Copper Wire, {}, 90 m",
         [[("1 sq mm", 1), ("1.5 sq mm", 1.4), ("2.5 sq mm", 2.2), ("4 sq mm", 3.4)]], 1290),
        ("Extension Board, {}",
         [[("4 Sockets", 1), ("4 Sockets with USB", 1.5), ("6 Sockets with Surge Protector", 1.9)]], 449),
        ("MCB Single Pole, {}", [[("6 A", 1), ("16 A", 1), ("32 A", 1.15)]], 219),
        ("Ceiling Fan, {}, {}",
         [[("1200 mm", 1), ("1400 mm", 1.1)], [("Standard", 1), ("BLDC Energy Saving", 1.9)]], 1890),
        ("PVC Insulation Tape, {}, Pack of 5", [[("Black", 1), ("Multicolour", 1.1)]], 99),
        ("Doorbell, {}", [[("Wired Ding-Dong", 1), ("Wireless Musical", 1.8)]], 299),
        ("LED Panel Light, {}", [[("6 W Round", 1), ("12 W Round", 1.5), ("18 W Square", 2)]], 279),
        ("Wall Bracket Lamp, {}", [[("Indoor", 1), ("Outdoor Weatherproof", 1.6)]], 549),
        ("Voltage Tester Screwdriver, {}", [[("Standard", 1), ("Digital", 2)]], 79),
    ]),
    "garden": ("GDN", [
        ("Garden Hose Pipe, {}, {}",
         [[("1/2 in", 1), ("3/4 in", 1.4)], [("15 m", 1), ("30 m", 1.8)]], 699),
        ("Hose Spray Nozzle, {}", [[("3-Pattern", 1), ("8-Pattern", 1.6)]], 249),
        ("Plastic Planter, {}, {}",
         [[("8 in", 1), ("12 in", 1.8), ("16 in", 2.9)],
          [("Terracotta", 1), ("White", 1), ("Black", 1)]], 149),
        ("Potting Mix, {}", [[("5 kg", 1), ("10 kg", 1.8), ("25 kg", 3.9)]], 199),
        ("Organic Vermicompost, {}", [[("5 kg", 1), ("25 kg", 4.2)]], 249),
        ("Pruning Secateurs, {}", [[("Bypass 8 in", 1), ("Anvil 8 in", 1.1)]], 399),
        ("Garden Hand Tool Set, {}", [[("3 pcs", 1), ("5 pcs", 1.5)]], 349),
        ("Pressure Sprayer, {}", [[("1.5 L", 1), ("5 L", 2.4), ("16 L Backpack", 5)]], 349),
        ("Watering Can, {}", [[("5 L", 1), ("10 L", 1.6)]], 249),
        ("Shade Net 50%, {}", [[("3 x 10 m", 1), ("3 x 20 m", 1.9)]], 1290),
        ("Grass Trimmer Line, {}", [[("1.6 mm x 15 m", 1), ("2.4 mm x 15 m", 1.3)]], 199),
        ("Garden Rake, {}", [[("Leaf", 1), ("Bow", 1.3)]], 349),
        ("Electric Lawn Mower, {}", [[("1200 W 32 cm", 1), ("1600 W 38 cm", 1.35)]], 7999),
        ("Garden Fork, {}", [[("4 Prong", 1)]], 449),
        ("Bird Feeder, {}", [[("Hanging", 1), ("Window", 1.2)]], 399),
    ]),
}


def retail_price(amount: float) -> Decimal:
    """Round to a shelf-style price ending in 9, e.g. 1243.7 -> 1239.00, 1247.0 -> 1249.00."""
    return Decimal(max(9, round(amount / 10) * 10 - 1)).quantize(Decimal("0.01"))


def build_products(rng: random.Random) -> list[dict]:
    products = []
    for category, (prefix, families) in CATALOG.items():
        candidates = []
        for template, option_lists, base_price in families:
            for combo in itertools.product(*option_lists):
                labels = [label for label, _ in combo]
                multiplier = 1.0
                for _, m in combo:
                    multiplier *= m
                candidates.append((template.format(*labels), retail_price(base_price * multiplier)))
        if len(candidates) < PRODUCTS_PER_CATEGORY:
            raise ValueError(f"{category}: only {len(candidates)} candidate products")
        chosen = sorted(rng.sample(candidates, PRODUCTS_PER_CATEGORY))
        for i, (name, price) in enumerate(chosen, start=1):
            products.append(
                {"sku": f"{prefix}-{i:05d}", "name": name, "category": category, "price": price}
            )
    return products


def build_stock(rng: random.Random, store_ids: list[int], products: list[dict]) -> list[dict]:
    """Each store has its own layout, so the same SKU sits in different aisles."""
    categories = list(CATALOG)
    rows = []
    for store_id in store_ids:
        layout = categories[:]
        rng.shuffle(layout)  # a different category -> aisle order per store
        aisles_for = {cat: list(range(i * 5 + 1, i * 5 + 6)) for i, cat in enumerate(layout)}
        for p in products:
            if rng.random() < 0.08:  # ~8% of products aren't carried in this store
                continue
            reorder_point = rng.randint(5, 20)
            roll = rng.random()
            if roll < 0.10:
                on_hand = 0  # out of stock
            elif roll < 0.25:
                on_hand = rng.randint(1, reorder_point)  # low: due for reorder
            else:
                on_hand = rng.randint(reorder_point + 1, 120)
            rows.append({
                "store_id": store_id,
                "sku": p["sku"],
                "on_hand": on_hand,
                "reserved": 0,
                "reorder_point": reorder_point,
                "aisle": str(rng.choice(aisles_for[p["category"]])),
                "bay": f"{rng.choice('ABCDEF')}{rng.randint(1, 12):02d}",
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete all data first")
    args = parser.parse_args()

    rng = random.Random(RNG_SEED)
    with SessionLocal() as db, db.begin():  # one transaction: all of it or none of it
        if args.reset:
            # RESTART IDENTITY resets the stores id sequence, so ids start at 1 again.
            db.execute(text(
                "TRUNCATE order_items, orders, stock, products, stores RESTART IDENTITY"
            ))
        elif db.scalar(select(func.count()).select_from(Product)):
            print("Database already has products. Use --reset to wipe and reseed.")
            return 1

        # RETURNING gives back the ids Postgres actually assigned. We never
        # assume they are 1, 2, 3 (sequences can have gaps).
        store_ids = list(db.scalars(insert(Store).returning(Store.id), STORES))
        products = build_products(rng)
        db.execute(insert(Product), products)
        stock = build_stock(rng, store_ids, products)
        db.execute(insert(Stock), stock)

    out_of_stock = sum(1 for r in stock if r["on_hand"] == 0)
    low = sum(1 for r in stock if 0 < r["on_hand"] <= r["reorder_point"])
    print(f"Seeded {len(store_ids)} stores, {len(products)} products, {len(stock)} stock rows "
          f"({out_of_stock} out of stock, {low} at or below reorder point).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
