"""Repair cost ranges, substantial-damage flags and households likely
displaced for an assessed area. Writes costs.json and needs.json."""

from landfall.pipeline import cli

if __name__ == "__main__":
    cli("costs", __doc__)
