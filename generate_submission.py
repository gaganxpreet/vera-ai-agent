import json
from pathlib import Path
from bot import compose

def generate_submission():
    test_pairs_path = Path("dataset/expanded/test_pairs.json")
    if not test_pairs_path.exists():
        print(f"Error: {test_pairs_path} not found.")
        return

    with open(test_pairs_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    pairs = data.get("pairs", [])

    expanded_dir = Path("dataset/expanded")
    
    # Preload categories
    categories = {}
    for cat_file in (expanded_dir / "categories").glob("*.json"):
        with open(cat_file, "r", encoding="utf-8") as f:
            cat_data = json.load(f)
            categories[cat_data.get("slug", cat_file.stem)] = cat_data

    # Preload merchants
    merchants = {}
    for m_file in (expanded_dir / "merchants").glob("*.json"):
        with open(m_file, "r", encoding="utf-8") as f:
            m_data = json.load(f)
            merchants[m_data.get("merchant_id")] = m_data

    # Preload customers
    customers = {}
    for c_file in (expanded_dir / "customers").glob("*.json"):
        with open(c_file, "r", encoding="utf-8") as f:
            c_data = json.load(f)
            customers[c_data.get("customer_id")] = c_data

    # Preload triggers
    triggers = {}
    for t_file in (expanded_dir / "triggers").glob("*.json"):
        with open(t_file, "r", encoding="utf-8") as f:
            t_data = json.load(f)
            triggers[t_data.get("id")] = t_data

    out_file = Path("submission.jsonl")
    lines_written = 0

    with open(out_file, "w", encoding="utf-8") as out:
        for pair in pairs:
            test_id = pair["test_id"]
            tid = pair["trigger_id"]
            mid = pair["merchant_id"]
            cid = pair.get("customer_id")

            trg = triggers.get(tid)
            merchant = merchants.get(mid)
            cat_slug = merchant.get("category_slug") if merchant else None
            category = categories.get(cat_slug) if cat_slug else None
            customer = customers.get(cid) if cid else None

            if not trg or not merchant or not category:
                print(f"Skipping {test_id}: missing entity ({tid}, {mid}, {cat_slug})")
                continue

            result = compose(category, merchant, trg, customer)
            entry = {
                "test_id": test_id,
                "body": result["body"],
                "cta": result["cta"],
                "send_as": result["send_as"],
                "suppression_key": result["suppression_key"],
                "rationale": result["rationale"]
            }
            out.write(json.dumps(entry, ensure_ascii=False) + "\n")
            lines_written += 1

    print(f"Successfully generated {lines_written} lines in submission.jsonl")

if __name__ == "__main__":
    generate_submission()
