**DRAFT - For trained assessors only. Not an official determination.**

# Draft Preliminary Damage Assessment Summary - Cedar Key

## 1. Headline
Hurricane storm surge inundated this area on 2024-09-27 (NOAA flight 20240928b-rgb vs USDA NAIP 2023-02-15). Of 150 buildings assessed, 66 are drafted as damaged (55 affected, 4 minor, 2 major, 5 destroyed) and 66 drafts are flagged for human review; 70 buildings had water around them with up to 11.56 ft depth above ground.

## 2. Damage by category
Draft categories from aerial comparison + Segment Anything + flood depth. All are drafts for field verification.

| PDA Category | Count | Needs field check | With ≥1.5 ft water around house* |
|---|---:|---:|---:|
| **Affected** | 55 | 55 | 46 |
| **Minor** | 4 | 4 | 2 |
| **Major** | 2 | 2 | 2 |
| **Destroyed** | 5 | 0 | 5 |
| **None** | 84 | - | - |
| **Total assessed** | **150** | **66** | **70 with any water; deepest 11.56 ft** |

* 1.5 ft+ above ground could put ≥18 inches in living space of a house built at grade = Major under PDA guide.

## 3. Flooding
**How deep:** Water surface modeled at 10.9 ft NAVD88. Depth at each building = surface minus ground elevation. Deepest in area: 11.56 ft at building b145 (29.13974, -83.03506). 70 buildings had water around them; 55 drafts had ≥1.5 ft.

**How measured:** Interpolated from USGS HWM 46592 (10.96 ft at 29.13589, -83.029776 - seed line inside bathroom wall) and NOAA gauge 8727520 (10.843 ft at 29.135, -83.0317, peak 2024-09-27 04:42 UTC).

**Why flood-only houses may be undercounted:** Aerial nadir photos cannot see inside a house and cannot see first-floor height / pilings. Where water stood around a house but the roof looked intact, the draft was set to **affected** (lowest flood entry). The real PDA category may be higher - e.g., 46 affected drafts have ≥1.5 ft of water and need a field check to measure interior high-water marks. Example: b005 and b087 are drafted major only because flood depth suggests living-space flooding, but roof shows no major damage and both are flagged needs_review=true.

## 4. Destroyed and major

All coordinates as lat, lon. Flood depth = ft above ground. Evidence is aerial-only draft.

**Destroyed (5) - all needs_review=false, confirmed by photo comparison:**
- **b114** - destroyed, 7.68 ft, needs_review=false - 29.13576, -83.03639 - Pre-storm house completely missing post-storm - only dark debris/slab remains, 97% structure not found.
- **b107** - destroyed, 7.05 ft, needs_review=false - 29.13591, -83.03648 - Pre-storm house inside yellow outline completely gone post-storm; bare ground/debris with 100% not found.
- **b098** - destroyed, 6.27 ft, needs_review=false - 29.13596, -83.03636 - Roof completely gone, only slab/foundation with rubble/debris, ~69% not found.
- **b094** - destroyed, 5.26 ft, needs_review=false - 29.13601, -83.03625 - Intact roof pre-storm replaced by rubble/debris pile, no walls/roof, 94% not found.
- **b023** - destroyed, 3.56 ft, needs_review=false - 29.13787, -83.03622 - Light-roof building completely absent post-storm, only bare dark ground/slab, 83% not found.

**Major (2) - both needs_review=true, flagged for field check - roof intact from above, category driven by flood depth:**
- **b087** - major, 10.61 ft, needs_review=true - 29.13760, -83.03373 - Roof remains in place (white to light-blue suggesting tarp over ~49% of roof) with 10.6 ft water; first-floor height cannot be seen in blurry nadir views, may be ≥18" interior flooding.
- **b005** - major, 6.79 ft, needs_review=true - 29.13765, -83.03594 - Roof intact with 7% not found, no missing covering/collapse, but 6.8 ft water stood at ground; whether raised on pilings cannot be determined overhead. Checker flagged: measurements show Affected, needs visible damage to justify Major.

## 5. Field inspection plan
**First team route: 20 stops, ~1349 m walking (~1.3 km).** Prioritizes severe drafts + low-confidence drafts + flood-only ≥1.5 ft houses. All are drafts requiring ground verification.

| Stop | ID | Cat | Flood ft | Review | lat, lon | Why it matters |
|---:|---|---|---:|---|---|---|
| 1 | b114 | destroyed | 7.68 | false | 29.13576, -83.03639 | destroyed confirmation - 97% loss, slab only |
| 2 | b106 | affected | 6.55 | true | 29.13581, -83.03623 | 6.5 ft water, roof looks intact - verify interior |
| 3 | b098 | destroyed | 6.27 | false | 29.13596, -83.03636 | destroyed - roof gone, rubble |
| 4 | b107 | destroyed | 7.05 | false | 29.13591, -83.03648 | destroyed - 100% not found |
| 5 | b094 | destroyed | 5.26 | false | 29.13601, -83.03625 | destroyed - 94% loss |
| 6 | b137 | minor | 5.71 | true | 29.13531, -83.03602 | tarp-like patch, 5.7 ft water |
| 7 | b006 | minor | 1.36 | true | 29.13717, -83.03589 | intact roof, 1.4 ft water - check shallow flooding |
| 8 | b001 | affected | 7.05 | true | 29.13761, -83.03580 | 7 ft water, roof aligned - interior unknown |
| 9 | b004 | affected | 6.64 | true | 29.13770, -83.03588 | 6.6 ft water around intact roof |
| 10 | b005 | major | 6.79 | true | 29.13765, -83.03594 | major draft flagged - verify pilings/interior |
| 11 | b023 | destroyed | 3.56 | false | 29.13787, -83.03622 | destroyed - 83% loss |
| 12 | b048 | minor | 0.85 | true | 29.13748, -83.03433 | 0.8 ft water - verify if at-grade flooding |
| 13 | b073 | affected | 6.65 | true | 29.13774, -83.03404 | 6.7 ft water, roof in place |
| 14 | b087 | major | 10.61 | true | 29.13760, -83.03373 | 10.6 ft water, tarp - deepest major |
| 15 | b139 | affected | 9.46 | true | 29.13731, -83.03293 | 9.5 ft water, roof looks intact |
| 16 | b123 | affected | 6.65 | true | 29.13670, -83.03338 | 6.7 ft water, intact roof |
| 17 | b064 | affected | 6.58 | true | 29.13668, -83.03444 | 6.6 ft water, intact roof |
| 18 | b105 | minor | 7.63 | true | 29.13918, -83.03485 | addition debris, 7.6 ft water |
| 19 | b128 | affected | 10.50 | true | 29.13960, -83.03505 | 10.5 ft water, auto 100% loss false - roof intact |
| 20 | b145 | affected | 11.56 | true | 29.13974, -83.03506 | deepest water 11.56 ft, auto 100% loss false |

## 6. Limits
**These are aerial drafts only, for trained assessors - not official determinations. Field verification required.**

- Overhead photos cannot see inside, cannot see drywall, insulation, electrical, or mold, and cannot measure first-floor elevation or pile height. Where water stood around a house but the floor height is unknown, the draft conservatively says **affected**; the true category may be minor/major.
- Segment Anything structure loss (e.g., 100% loss at b128/b145) can be shadow/registration error - confirmed by visual check that roofs remain.
- Tarp detection, debris, and roof color change are indicators only.
- Flood depth is modeled from water surface (10.9 ft NAVD88) minus ground elevation, with 2 observation points - local variations possible.
- All affected/minor/major drafts (61 buildings) plus 46 affected with ≥1.5 ft water are flagged needs_review=true and need interior high-water and damage checks before submission to the state.

