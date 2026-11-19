"""How well street photos and the National Structure Inventory say how Cedar Key's
buildings are built, against the field survey's assessors.

    python scripts/score_field.py --drafts <assessments.json> --json match.json
    python scripts/score_built.py match.json <assessments.json>
"""
import json,csv,collections,sys
m=json.load(open(sys.argv[1]))
rows={r['FID']:r for r in csv.DictReader(open('eval/field/HxHelene_VDA_CedarKey.csv',encoding='utf-8-sig'))}
a={x['id']:x for x in json.load(open(sys.argv[2]))}
st=json.load(open('demo/cedar_key/structures.json'))
MAT={'W':'wood','M':'masonry','C':'concrete','S':'steel','H':'manufactured'}
FND={'S':'slab','C':'crawlspace','P':'piers','I':'piles','F':'fill','W':'solid wall','B':'basement'}
NM={'wood frame':'wood','masonry':'masonry','concrete':'concrete','steel':'steel','manufactured (mobile) home':'manufactured'}
def i(v):
    try: return int(float(v))
    except: return None
res=collections.defaultdict(list)
for bid,s in m.items():
  r=rows[s['fids'][0]]
  truth=dict(material=MAT.get(r['building_type'].strip()),foundation=FND.get(r['foundation_type'].strip()),stories=i(r['num_stories']))
  b=(a.get(bid) or {}).get('built_from_street') or {}
  n=st.get(bid) or {}
  nsi=dict(material=NM.get(n.get('material')),foundation=n.get('foundation'),stories=n.get('stories'))
  for k in truth:
    if truth[k] is None: continue
    res[('street',k)].append((b.get(k),truth[k])); res[('nsi',k)].append((nsi.get(k),truth[k]))
for (src,k),pp in sorted(res.items()):
  given=[p for p in pp if p[0] is not None]
  ok=sum(x==y for x,y in given)
  print(f"{src:6} {k:10} answered {len(given)}/{len(pp)}, right {ok} ({100*ok/max(1,len(given)):.0f}%)", collections.Counter(given).most_common(6))
G={'slab':'at grade','fill':'at grade','crawlspace':'short (crawlspace/piers)','piers':'short (crawlspace/piers)','piles':'tall (piles/wall)','solid wall':'tall (piles/wall)','basement':'at grade'}
for src in ('street','nsi'):
  pp=[(G[x],G[y]) for x,y in res[(src,'foundation')] if x]
  print(src,'foundation grouped', sum(x==y for x,y in pp),'/',len(pp))
