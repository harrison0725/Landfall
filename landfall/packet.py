"""A printable evidence packet for one building: everything a homeowner,
caseworker or adjuster would want in one place when a claim or an
assistance application is filed.

The packet is one self-contained HTML page (the photo pair is embedded), so
it prints, saves as PDF from the browser, or attaches to an email as is. It
carries the same facts as the draft and says plainly that it's a draft from
aerial photos, not an adjuster's or FEMA's determination.
"""

from __future__ import annotations

import base64
import datetime as dt
import html
import json
from pathlib import Path

from .fema import INTERNATIONAL

LABEL = {"none": "No visible damage", "affected": "Affected", "minor": "Minor", "major": "Major", "destroyed": "Destroyed"}


def _read(p: Path):
    return json.loads(p.read_text()) if p.exists() else None


def _usd(x: float | None) -> str:
    return "unknown" if x is None else f"${x:,.0f}"


def _ft(x: float | None) -> str:
    return "unknown" if x is None else f"{x:.1f} ft"


def facts(d: Path, bid: str) -> dict:
    """Every record about one building, from the area folder."""
    drafts = {a["id"]: a for a in _read(d / "assessments.json") or []}
    if bid not in drafts:
        raise KeyError(bid)
    feats = {f["properties"]["id"]: f["properties"] for f in json.loads((d / "buildings.geojson").read_text())["features"]}
    flood = _read(d / "flood.json") or {}
    ground = {b["id"]: b.get("ground_ft") for b in flood.get("buildings", [])}
    cost = {c["id"]: c for c in (_read(d / "costs.json") or {}).get("buildings", [])}
    need = {n["id"]: n for n in (_read(d / "needs.json") or {}).get("buildings", [])}
    parcel = (_read(d / "parcels.json") or {}).get(bid, {})
    summary = (_read(d / "alignment.json") or {}).get("summary", {})
    return dict(
        id=bid,
        area=d.name,
        draft=drafts[bid],
        props=feats.get(bid, {}),
        ground_ft=ground.get(bid),
        flood=flood,
        cost=cost.get(bid),
        need=need.get(bid),
        parcel=parcel,
        photos=summary,
    )


def render(d: Path, bid: str, pair_png: bytes) -> str:
    f = facts(d, bid)
    a, p, c, n, pr = f["draft"], f["props"], f["cost"], f["need"], f["parcel"]
    e = html.escape
    cat = a.get("category", "none")
    lon, lat = (p.get("centroid") or [None, None])[:2]
    img = "data:image/png;base64," + base64.b64encode(pair_png).decode()
    obs = f["flood"].get("observations", [])

    rows_draft = [
        ("Draft category", LABEL.get(cat, cat)),
        ("International grade", INTERNATIONAL.get(cat, "unknown") + " (Copernicus EMS scale)"),
        ("Confidence", f"{a['confidence']:.2f}" if a.get("confidence") is not None else "unknown"),
        ("Field check needed", "yes" if a.get("needs_human") else "no"),
        ("Roof or structure missing", f"{round(a['structure_loss'] * 100)}%" if a.get("structure_loss") is not None else "not measured"),
        ("Blue tarp on roof", f"{round(a['tarp_share'] * 100)}%" if a.get("tarp_share") else "none found"),
        ("Debris in the yard", f"{round(a['debris_share'] * 100)}%" if a.get("debris_share") else "none found"),
    ]
    if a.get("demolished"):
        rows_draft.append(("Note", "No building stood here during the storm (demolished before it)"))

    rows_water = [
        ("Water above the ground here", _ft(a.get("flood_ft"))),
        ("Ground elevation", f"{f['ground_ft']:.1f} ft NAVD88 (USGS 3DEP)" if f["ground_ft"] is not None else "unknown"),
        ("Peak water surface", f"{f['flood']['surface_ft_navd88']:.1f} ft NAVD88" if f["flood"].get("surface_ft_navd88") is not None else "unknown"),
    ]
    rows_water += [(o["source"], f"{o['elev_ft']:.1f} ft NAVD88. {o.get('note', '')}") for o in obs]

    rows_record = [
        ("Year built", pr.get("year_built") or "not on the current roll"),
        ("Flood zone", f"{pr.get('flood_zone') or 'unknown'}" + (f", base flood elevation {pr['bfe_ft']:g} ft NAVD88" if pr.get("bfe_ft") is not None else "")),
        ("First floor (estimate)", pr.get("basis") or "unknown"),
    ]
    if n:
        lo, hi = n["water_inside_ft"]
        rows_record.append(("Water inside (estimate)", "none" if hi <= 0 else f"{lo:.1f} to {hi:.1f} ft"))
        rows_record.append(("Households on the roll", str(n["households"])))

    rows_cost = []
    if c:
        rows_cost = [
            ("Building value (estimate)", f"{_usd(c['value_usd'])} ({c['sqft']:,} sq ft, {c['stories']} {'story' if c['stories'] == 1 else 'stories'})"),
            ("Repair cost range", f"{_usd(c['cost_low_usd'])} to {_usd(c['cost_high_usd'])} ({round(c['share_low'] * 100)}% to {round(c['share_high'] * 100)}% of value)"),
            ("What drives the high end", c["driver"]),
            (
                "FEMA 50% rule",
                "likely substantially damaged" if c["substantial_damage_likely"] else "possibly substantially damaged" if c["substantial_damage_possible"] else "below 50%",
            ),
        ]

    def table(rows):
        return "<table>" + "".join(f"<tr><th>{e(str(k))}</th><td>{e(str(v))}</td></tr>" for k, v in rows) + "</table>"

    def trace_row(t):
        if t.get("model") == "satellite rule":
            return "<li>Damage seen on satellite photos, which are coarser and taken at an angle: sent to a field check.</li>"
        if t.get("model") == "flood rule":
            return f"<li>Flood rule raised {e(t['raised_from'].lower())} to {e(t['to'].lower())}: surveyed water stood around the building.</li>"
        if t.get("error"):
            return f"<li>{e(str(t['model']).split('/')[-1])}: {e(t['error'][:160])}</li>"
        ans = t.get("answer", {})
        obj = "; ".join(t.get("verifier") or []) or "no objection"
        return f"<li><b>{e(str(t['model']).split('/')[-1])}</b> said {e(str(ans.get('category', '')).lower())} ({ans.get('confidence')}). Checker: {e(obj)}</li>"

    trace = "".join(trace_row(t) for t in a.get("trace", []))
    when = dt.date.today().isoformat()
    photos = f["photos"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Evidence packet {e(bid)}</title>
<style>
:root{{--ink:#1d2329;--mute:#5b6670;--rule:#d7dde2;--bg:#fff;--accent:#0b5c8a}}
body{{font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);background:var(--bg);max-width:820px;margin:24px auto;padding:0 16px}}
h1{{font-size:22px;margin:0 0 2px}} h2{{font-size:15px;margin:22px 0 6px;border-bottom:1px solid var(--rule);padding-bottom:3px;color:var(--accent)}}
.sub{{color:var(--mute);margin:0 0 12px}} .draft{{background:#fff5d6;border:1px solid #e8cf7a;padding:8px 10px;border-radius:6px;margin:10px 0}}
img{{width:100%;border:1px solid var(--rule);border-radius:4px}} .cap{{display:flex;justify-content:space-between;color:var(--mute);font-size:12px}}
table{{border-collapse:collapse;width:100%}} th{{text-align:left;font-weight:600;width:34%;vertical-align:top;padding:4px 8px 4px 0;color:var(--mute)}} td{{padding:4px 0;vertical-align:top}}
tr+tr th,tr+tr td{{border-top:1px solid #eef1f3}} ul{{padding-left:18px;margin:4px 0}} li{{margin:3px 0}}
.print{{position:fixed;top:12px;right:12px;padding:6px 12px;border:1px solid var(--rule);background:#fff;border-radius:6px;cursor:pointer}}
@media print{{.print{{display:none}} body{{margin:0}} h2{{break-after:avoid}} img{{break-inside:avoid}}}}
</style></head><body>
<button class="print" onclick="print()">Print or save as PDF</button>
<h1>Damage evidence packet: building {e(bid)}</h1>
<p class="sub">{e(f['area'].replace('_', ' ').title())} · {f'{lat:.5f}, {lon:.5f}' if lat is not None else ''} · prepared {when} by Landfall</p>
<div class="draft"><b>Draft from aerial photos.</b> This is not an insurance adjuster's estimate or a FEMA determination. It collects the public evidence about this building so the people filing and reviewing a claim start from the same facts.</div>

<h2>Before and after</h2>
<img src="{img}" alt="Before and after aerial photos of building {e(bid)}">
<div class="cap"><span>Before: {e(photos.get('pre_source') or f"USDA NAIP, {photos.get('naip_date', 'unknown date')}")}</span><span>After: {e(photos.get('post_source') or f"NOAA emergency response flight {photos.get('noaa_flight', '')}")} (brightened)</span></div>

<h2>What the photos show</h2>
<p>{e(a.get('evidence', ''))}</p>
{table(rows_draft)}

<h2>Flood water</h2>
{table(rows_water)}

<h2>Property record</h2>
{table(rows_record)}
<p class="sub">From the Florida Department of Revenue statewide tax roll and FEMA's National Flood Hazard Layer. No owner names or addresses are stored.</p>

{f'<h2>Repair cost (planning range)</h2>{table(rows_cost)}<p class="sub">Building value is the local median value per square foot of living area on the tax roll times this building&#39;s floor area. Flood damage follows FEMA P-784 Appendix E bands. A contractor&#39;s or adjuster&#39;s estimate replaces this.</p>' if rows_cost else ''}

<h2>How the draft was made</h2>
<ul>{trace or '<li>No trace recorded.</li>'}</ul>

<h2>For the claim or application</h2>
<ul>
<li>Flood damage is covered by flood insurance (the National Flood Insurance Program or a private flood policy), not a standard homeowners policy; wind damage usually goes to the homeowners or windstorm policy. File with each that applies.</li>
<li>Uninsured or underinsured losses: register with FEMA at DisasterAssistance.gov or 1-800-621-3362.</li>
<li>Add your own photos of every room, the water line on the walls, and damaged belongings before they are thrown out, plus receipts for repairs and lodging.</li>
<li>If the repair cost is near half the building&#39;s value, ask the county floodplain manager about the substantial-damage determination before rebuilding; it can require raising the building, and flood policies with Increased Cost of Compliance coverage help pay for that.</li>
</ul>
</body></html>"""
