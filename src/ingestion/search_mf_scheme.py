"""
AMFI identifies mutual funds by numeric scheme codes, not names, so you need
this to find the right code before adding a fund to mf_watchlist.yaml.

Run locally: python -m src.ingestion.search_mf_scheme "parag parikh flexi cap"
"""

import sys
from mftool import Mftool


def search(keyword: str, limit: int = 15):
    mf = Mftool()
    all_schemes = mf.get_scheme_codes()  # dict: {code: name}, ~30k entries

    keyword_lower = keyword.lower()
    matches = [
        (code, name) for code, name in all_schemes.items()
        if keyword_lower in name.lower()
    ]

    if not matches:
        print(f"No schemes found matching '{keyword}'. Try a shorter/different keyword.")
        return

    print(f"Found {len(matches)} matches (showing up to {limit}):\n")
    for code, name in matches[:limit]:
        print(f"  {code}  {name}")

    if len(matches) > limit:
        print(f"\n...and {len(matches) - limit} more. Narrow your keyword to see fewer.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python -m src.ingestion.search_mf_scheme "keyword"')
        sys.exit(1)
    search(" ".join(sys.argv[1:]))