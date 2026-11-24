"""Build one building-by-building table from every public damage survey
Landfall is tested on: eval/research/buildings.csv.

Each row is one surveyed building with
* what Landfall can know about it BEFORE a storm (walls, foundation, stories,
  year built, flood zone, design wind speed, distance to the coast), taken
  from the same kinds of sources Landfall uses (USACE National Structure
  Inventory fields, the county tax roll, FEMA flood zones), never from the
  damage survey's own after-storm observations;
* the storm's measured hazard at the building: peak gust from the NIST/ARA
  wind field (3-second gust, 10 m, open terrain, fit to the storm's wind
  observations), and water above the ground from high-water marks;
* what the survey found.

Surveys (all Open Data Commons Attribution, DesignSafe):
* Helene 2024, Cedar Key (771) and Horseshoe Beach (313): Anton, Amini,
  Sedehi, Cox, Tomiczek, PRJ-6245, doi 10.17603/ds2-q4n1-rf48. Tomiczek
  DS0-6 per component.
* Ian 2022, Fort Myers Beach (3,408): Figueira, Amini, Cox, Barbosa,
  PRJ-5700, doi 10.17603/ds2-c1g8-qy41. Same DS0-6 scale and method.
* Michael 2018, Laura 2020, Irma 2017, Harvey 2017 (3,016 single-family
  houses): WiSPD v1.0, Roueche, Nakayama, Kijewski-Correa, Prevatt,
  PRJ-4186, doi 10.17603/ds2-ety5-qv78. StEER wind damage rating 0-4 and
  roof cover loss (%), with each house's NIST/ARA gust and ASCE 7 design
  speeds.

Wind fields: NIST/ARA rapid response wind field estimates on the DesignSafe
Recon Portal (Helene release 2, Ian release 3), public domain.

What "damaged" and "badly damaged" mean, so all surveys line up with
Landfall's two numbers:
* DS0-6 surveys: damaged = roof, walls/floors or foundation at DS2 or worse;
  badly damaged = DS4 or worse (Landfall's water-damage rates use the same).
* WiSPD: damaged = wind damage rating 2 (moderate) or worse; badly damaged =
  3 (severe) or worse, the StEER ratings that line up with Hazus damage
  states 2 and 3 (Landfall's wind numbers).

Run: python scripts/research_data.py   (downloads about 30 MB into data/research_raw/)
"""

from __future__ import annotations

import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RAW = ROOT / "data" / "research_raw"
OUT = ROOT / "eval" / "research"

DS = "https://www.designsafe-ci.org/api/datafiles/tapis/public/preview/designsafe.storage."
PRJ6245 = "/published-data/PRJ-6245/Mission--an-integrated-pre-and-post-storm-dataset-for-hurricane-helene-2024/data/Geoscience-collection--overall-dataset/data/"
PRJ5700 = "/published-data/PRJ-5700/Mission--an-integrated-pre-and-post-storm-dataset-for-hurricane-ian-2022/data/Geoscience-collection--overall-dataset/data/"
WISPD = "/published-data/PRJ-4186/Project--a-unified-multi-event-windstorm-performance-testbed-for-single-family-residential-buildings/data/WiSPDv1.0.csv"
FILES = {
    "helene_hbck.csv": ("published", PRJ6245 + "HxHelene_VDA_HB_CK_FinalData.csv"),
    "helene_hb.csv": ("published", PRJ6245 + "HxHelene_VDA_HorseshoeBeach.csv"),
    "ian_fmb.csv": ("published", PRJ5700 + "FMB_VDA_FFE_Final.csv"),
    "wispd.csv": ("published", WISPD),
    "helene_wind.xlsx": ("community", "/Recon Portal/2024 Hurricane Helene Perry Florida USA/Hurricane_Helene_Windfield_Release_2/2-Hurricane_Helene_Simulated_Winds_Release_2.xlsx"),
    "ian_wind.xlsx": ("community", "/Recon Portal/2022 Hurricane Ian/NIST-ARA Windfield Data Release 3/3-Hurricane_Ian_Simulated_Winds_Release_3.xlsx"),
}
# UF Envision Heritage pre-storm survey of Cedar Key (2021): year built, walls,
# foundation, stories and laser-scanned first floors for historic-district parcels
UF_CEDAR_KEY = "https://services.arcgis.com/LBbVDC0hKPAnLRpO/arcgis/rest/services/Cedar_Key_Phase2/FeatureServer/0/query"

MATERIAL = {"W": "wood frame", "M": "masonry", "C": "concrete", "S": "steel", "H": "manufactured (mobile) home"}
FOUNDATION = {"S": "slab", "C": "crawlspace", "P": "piers", "I": "piles", "F": "fill", "W": "solid wall", "B": "basement"}
STATE = {"Hurricane Michael (2018)": "FL", "Hurricane Irma": "FL", "Hurricane Laura (2020)": "LA", "Hurricane Harvey": "TX"}
MS_TO_MPH = 2.23694

# Water: Cedar Key 10.9 ft NAVD88 (USGS high-water mark 10.96 ft, NOAA gauge
# 8727520 10.84 ft). Horseshoe Beach has no USGS high-water mark; NHC's Helene
# report (Hagen et al. 2025) gives 8 to 12 ft above ground there, so the
# surface is the town's median ground (4.6 ft NAVD88) + 10 ft, +-2 ft.
CEDAR_KEY_SURFACE_FT = 10.9
HORSESHOE_AGL_FT = (8.0, 12.0)


def fetch(name: str) -> Path:
    p = RAW / name
    if p.exists():
        return p
    RAW.mkdir(parents=True, exist_ok=True)
    system, path = FILES[name]
    d = json.load(urllib.request.urlopen(DS + system + urllib.parse.quote(path), timeout=120))
    href = d["href"]
    if "officeapps" in href:  # Office files come wrapped in a viewer link
        href = urllib.parse.unquote(href.split("src=", 1)[1])
    p.write_bytes(urllib.request.urlopen(href, timeout=900).read())
    return p


def uf_cedar_key() -> list[dict]:
    p = RAW / "uf_cedar_key_2021.json"
    if not p.exists():
        q = urllib.parse.urlencode({"where": "1=1", "outFields": "Year_Built,Structural_Type,Foundation_Type,Number_Stories,FFE_NAVD88,FFE_Grade,Current_Use",
                                    "returnGeometry": "true", "outSR": 4326, "f": "geojson"})
        p.write_bytes(urllib.request.urlopen(UF_CEDAR_KEY + "?" + q, timeout=120).read())
    return json.loads(p.read_text())["features"]


def gust_at(wind_xlsx: Path, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    from scipy.spatial import cKDTree

    w = pd.read_excel(wind_xlsx)
    k = math.cos(math.radians(float(np.mean(lat))))
    tree = cKDTree(np.c_[w["lon"].to_numpy() * k, w["lat"].to_numpy()])
    # inverse-distance average of the 4 nearest grid points
    d, i = tree.query(np.c_[lon * k, lat], k=4)
    wt = 1 / np.maximum(d, 1e-6)
    return (w["gust_mph"].to_numpy()[i] * wt).sum(1) / wt.sum(1)


def num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def ds_num(s: pd.Series) -> pd.Series:
    return num(s.astype(str).str.extract(r"(\d)")[0])


def helene() -> pd.DataFrame:
    from landfall import hazards

    a = pd.read_csv(fetch("helene_hbck.csv"), encoding="utf-8-sig")
    hb_ids = set(pd.read_csv(fetch("helene_hb.csv"), encoding="utf-8-sig")["FID"])
    a["town"] = np.where(a["FID"].isin(hb_ids), "Horseshoe Beach", "Cedar Key")
    ck = pd.read_csv(ROOT / "eval" / "field" / "cedar_key_buildings.csv")  # Cedar Key's 2026 tax-roll year built
    a = a.merge(ck[["FID", "year_built"]], on="FID", how="left")
    eff = num(a["eff_yr_built"])
    # year built: the tax roll's actual year where it has one; else the county's
    # effective year from the survey's property records (98% are within a few
    # years of the actual year, and never earlier); else the UF 2021 survey
    a["year_source"] = np.where(a["year_built"].notna(), "tax roll 2026", np.where(eff.notna(), "property appraiser effective year", ""))
    a["year_built"] = a["year_built"].fillna(eff)
    a = add_uf_year(a)
    out = pd.DataFrame({
        "event": "Helene 2024", "town": a["town"], "id": "H" + a["FID"].astype(str), "lon": a["x"], "lat": a["y"], "state": "FL",
        "material": a["bldgtype"].str.strip().map(MATERIAL), "foundation": a["found_type"].str.strip().map(FOUNDATION),
        "stories": num(a["num_story"]).fillna(1).clip(1, 3).astype(int), "year_built": a["year_built"], "year_source": a["year_source"],
        "flood_zone": a["firmzone"].where(a["firmzone"].isin(["AE", "VE", "X", "A", "AO", "V"]), None),
        "ground_ft": num(a["grd_el_ft"]), "first_floor_navd_ft": num(a["FFE_NAVD88_ft"]), "value": num(a["val_struct"]),
        "obs_roof": ds_num(a["DS_roof"]), "obs_walls": ds_num(a["DS_walls/floors"]), "obs_foundation": ds_num(a["DS_foundation"]),
        "survey_material": a["building_type"].str.strip().map(MATERIAL), "survey_foundation": a["foundation_type"].str.strip().map(FOUNDATION),
    })
    out["v_ult"] = np.where(out["town"] == "Cedar Key", hazards.design_wind(-83.035, 29.138) or 131, hazards.design_wind(-83.29, 29.44) or 127)
    out["gust_mph"] = gust_at(fetch("helene_wind.xlsx"), out["lon"].to_numpy(), out["lat"].to_numpy())
    hb_surface = out.loc[out["town"] == "Horseshoe Beach", "ground_ft"].median() + np.mean(HORSESHOE_AGL_FT)
    surface = np.where(out["town"] == "Cedar Key", CEDAR_KEY_SURFACE_FT, hb_surface)
    out["water_surface_ft"] = surface
    out["water_sd_ft"] = np.where(out["town"] == "Cedar Key", 0.5, (HORSESHOE_AGL_FT[1] - HORSESHOE_AGL_FT[0]) / 4)
    return out


def add_uf_year(a: pd.DataFrame) -> pd.DataFrame:
    from shapely.geometry import Point, shape
    from shapely.strtree import STRtree

    feats = [f for f in uf_cedar_key() if (f["properties"].get("Year_Built") or "").strip()[:4].isdigit()]
    geoms = [shape(f["geometry"]) for f in feats]
    tree = STRtree(geoms)
    years = []
    for x, y, town in zip(a["x"], a["y"], a["town"]):
        p = Point(x, y)
        hit = [i for i in tree.query(p) if geoms[i].contains(p)] if town == "Cedar Key" else []
        years.append(int(feats[hit[0]]["properties"]["Year_Built"].strip()[:4]) if hit else np.nan)
    uf = pd.Series(years, index=a.index)
    fill = a["year_built"].isna() & uf.notna()
    a.loc[fill, "year_source"] = "UF Envision Heritage survey 2021"
    a["year_built"] = a["year_built"].fillna(uf)
    return a


def ian() -> pd.DataFrame:
    from landfall import hazards

    f = pd.read_csv(fetch("ian_fmb.csv"), encoding="utf-8-sig", low_memory=False)
    yb = num(f["TA_ActYearBuilt"]).where(lambda s: s > 1800)
    stories = num(f["VDA_number_of_stories"]).fillna(1).clip(1, 3).astype(int)
    out = pd.DataFrame({
        "event": "Ian 2022", "town": "Fort Myers Beach", "id": "I" + f["VDA_id"].astype(str), "lon": f["x"], "lat": f["y"], "state": "FL",
        "material": f["NSI_bldgtype"].str.strip().map(MATERIAL), "foundation": f["NSI_found_type"].str.strip().map(FOUNDATION),
        "stories": stories, "year_built": yb, "year_source": np.where(yb.notna(), "Lee County tax roll (pre-storm)", ""),
        "flood_zone": f["NSI_firmzone"], "ground_ft": num(f["NSI_ground_elv_ft_NAVD88"]).fillna(num(f["FFE_grnd_elev_ft_NAVD88"])),
        "first_floor_navd_ft": num(f["FFE_grnd_elev_ft_NAVD88"]) + num(f["FFE_ffe_ft"]), "value": num(f["NSI_val_struct"]),
        "obs_roof": ds_num(f["VDA_DS_roof"]), "obs_walls": ds_num(f["VDA_DS_walls"]), "obs_foundation": ds_num(f["VDA_DS_foundation"]),
        "survey_material": None, "survey_foundation": None,
    })
    out["v_ult"] = hazards.design_wind(-81.93, 26.43) or 159
    out["gust_mph"] = gust_at(fetch("ian_wind.xlsx"), out["lon"].to_numpy(), out["lat"].to_numpy())
    # still water from the survey's fit to USGS and StEER high-water marks (m NAVD88)
    out["water_surface_ft"] = num(f["SurgeElev_Avg_NAVD88_m"]) * 3.28084
    out["water_sd_ft"] = 0.34 * 3.28084
    out["surge_wave_surface_ft"] = num(f["SurgeWave_Avg_NAVD88_m"]) * 3.28084
    return out


def wispd() -> pd.DataFrame:
    w = pd.read_csv(fetch("wispd.csv"), low_memory=False)
    w = w[w["_project"].str.startswith("Hurricane") & (w["wind_damage_rating"] >= 0)]
    yb = num(w["year_built"]).where(lambda s: s > 1800)
    return pd.DataFrame({
        "event": w["_project"].str.replace(r" \(\d+\)", "", regex=True) + w["_project"].map({"Hurricane Michael (2018)": " 2018", "Hurricane Laura (2020)": " 2020", "Hurricane Irma": " 2017", "Hurricane Harvey": " 2017"}),
        "town": w["address_city"], "id": "W" + w["_record_id"].astype(str), "lon": w["_longitude"], "lat": w["_latitude"], "state": w["_project"].map(STATE),
        "material": None, "foundation": None, "stories": num(w["number_of_stories"]).fillna(1).clip(1, 3).astype(int),
        "year_built": yb, "year_source": np.where(yb.notna(), "WiSPD (county records)", ""), "flood_zone": None,
        "ground_ft": np.nan, "first_floor_navd_ft": np.nan, "value": np.nan,
        "v_ult": (num(w["wind_speed_ms_710"]) * MS_TO_MPH).round(), "gust_mph": num(w["wind_speed_ms"]) * MS_TO_MPH,
        "water_surface_ft": np.nan, "water_sd_ft": np.nan,
        "wind_rating": num(w["wind_damage_rating"]), "surge_rating": num(w["surge_damage_rating"]),
        "roof_cover_loss_pct": num(w["roof_cover_damage"]), "roof_cover": w["roof_cover"], "roof_shape": w["roof_shape"],
        "hazards_present": w["hazards_present"],
    })


def finish(t: pd.DataFrame) -> pd.DataFrame:
    from landfall import hazards

    t["event"] = t["event"].str.replace("Hurricane ", "", regex=False)
    t["water_ft"] = (t["water_surface_ft"] - t["ground_ft"]).clip(lower=0)
    ds = t[["obs_roof", "obs_walls", "obs_foundation"]]
    has_ds = ds.notna().any(axis=1)
    worst = ds.max(axis=1)
    t["obs_damaged"] = np.where(has_ds, worst >= 2, np.where(t["wind_rating"].notna(), t["wind_rating"] >= 2, np.nan))
    t["obs_severe"] = np.where(has_ds, worst >= 4, np.where(t["wind_rating"].notna(), t["wind_rating"] >= 3, np.nan))
    # wind-only measure on every survey: roof damage (DS2+ = Hazus-like moderate roof loss)
    t["obs_roof_damaged"] = np.where(t["obs_roof"].notna(), t["obs_roof"] >= 2, np.where(t["wind_rating"].notna(), t["wind_rating"] >= 2, np.nan))
    t["obs_wet"] = np.where(t["first_floor_navd_ft"].notna() & t["water_surface_ft"].notna(), t["water_surface_ft"] > t["first_floor_navd_ft"], np.nan)
    t["coast_miles"] = [hazards.coast_miles(round(x, 3), round(y, 3)) for x, y in zip(t["lon"], t["lat"])]
    return t[t["obs_damaged"].notna()].reset_index(drop=True)


if __name__ == "__main__":
    t = finish(pd.concat([helene(), ian(), wispd()], ignore_index=True))
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "buildings.csv", index=False)
    g = t.groupby(["event", "town"] if False else "event")
    print(g.agg(buildings=("id", "size"), gust_median=("gust_mph", "median"), water_median=("water_ft", "median"),
                year_known=("year_built", lambda s: s.notna().mean()), damaged=("obs_damaged", "mean"), severe=("obs_severe", "mean")).round(2))
