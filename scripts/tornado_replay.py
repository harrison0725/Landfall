"""Replays real tornadoes to check how much damage landfall/tornadoes.py expects from one.

For the 12 largest mapped EF3+ tornadoes (NWS Damage Assessment Toolkit EF contours, matched to
the SPC record; very long tracks skipped) and the 2013 Moore EF5:

- from the map: homes (NSI RES1/RES2) inside the surveyed EF0 outline, each given its mapped EF
  level, damage from the EF damage-indicator curves. This is the best wind information there is.
- new: homes inside the SPC length x largest-width box, wind by the surveyed shares (tornado_wind.json).
- old: the same box with the earlier Rankine profile (the rated wind along the whole path).

All three use the same damage curves, so this checks the wind in the path. For Moore, the NWS
survey points of 1,232 houses give a floor on houses damaged.

Run: python scripts/tornado_replay.py   (needs shapely, internet; writes eval/tornado_replay.json)
"""
import json,collections,math,datetime,sys,warnings,urllib.request,urllib.parse
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
from shapely.geometry import shape, Point
from shapely.ops import unary_union
from shapely.prepared import prep
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from landfall import hazards, tornadoes as T, structures
EF=T.EF_MPH
BM={(k,w):np.mean([hazards.tornado(v,{'material':'wood frame'})[w] for v in np.linspace(*EF[k],21)]) for k in range(6) for w in (0,1)}
SH=T.wind_shares()
import importlib.util
spec=importlib.util.spec_from_file_location('bw', ROOT/'scripts'/'build_tornado_wind.py'); bw=importlib.util.module_from_spec(spec); spec.loader.exec_module(bw)
feats=bw.dat_polygons()
g=collections.defaultdict(list)
for f in feats:
    pr=f['properties']
    if not f.get('geometry'): continue
    key=pr['path_guid'] if pr['path_guid'] and not pr['path_guid'].startswith('{0000') else (pr['event_id'],pr['stormdate'])
    g[key].append(f)
d=pd.read_csv(sys.argv[1] if len(sys.argv)>1 else bw.SPC,low_memory=False); d=d[d.yr>=2008]; d['date']=pd.to_datetime(d.date)
cands=[]
for k,v in g.items():
    lev=collections.defaultdict(list)
    for f in v:
        e=(f['properties']['efscale'] or '').upper().replace('EF','')
        if e.isdigit(): lev[int(e)].append(shape(f['geometry']).buffer(0))
    moore=any((f['properties'].get('event_id') or '').startswith('Newcastle-OKC') for f in v)
    if 0 not in lev or (max(lev)<3): continue
    U={e:unary_union(p) for e,p in lev.items()}
    c=U[0].centroid; kx=math.cos(math.radians(c.y))*69.17
    a0=U[0].area*kx*69.17
    if a0<1 and not moore: continue
    sd=v[0]['properties']['stormdate']
    if not sd: continue
    day=datetime.datetime.utcfromtimestamp(sd/1000)
    cand=d[(abs((d.date-pd.Timestamp(day.date())).dt.days)<=1)]
    if cand.empty: continue
    ax=(cand.slon-c.x)*kx; ay=(cand.slat-c.y)*69.17; bx=(cand.elon.where(cand.elon<0,cand.slon)-c.x)*kx; by=(cand.elat.where(cand.elat>0,cand.slat)-c.y)*69.17
    dx,dy=bx-ax,by-ay; t=np.clip(-(ax*dx+ay*dy)/np.maximum(dx*dx+dy*dy,1e-9),0,1); dist=np.hypot(ax+t*dx,ay+t*dy)
    j=dist.idxmin(); top=max(e for e in U if U[e].area>0)
    if dist[j]>2 or (cand.loc[j].mag!=top and not moore): continue
    cands.append((1e9 if moore else a0,k,U,cand.loc[j],v[0]['properties']['event_id'],day.date()))
cands.sort(key=lambda z:-z[0])
print(len(cands),'EF3+ candidates')
out=[]
for a0,k,U,s,name,day in cands[:40]:
    w,so,e,n=U[0].bounds
    if (e-w)*(n-so)>0.6: continue   # skip very long tracks (NSI query size)
    try: nsi=structures._query((w,so,e,n),timeout=300)
    except Exception as ex: print('nsi fail',name,ex); continue
    homes=[x for x in nsi if (x.get('occtype') or '').startswith(('RES1','RES2'))]
    P={kk:prep(vv) for kk,vv in U.items()}
    levs=[]
    for x in homes:
        p=Point(x['x'],x['y'])
        if P[0].contains(p): levs.append(max(kk for kk in P if P[kk].contains(p)))
    if len(levs)<100: continue
    cd=sum(BM[(l,0)] for l in levs); cb=sum(BM[(l,1)] for l in levs)
    # model on the SPC rectangle
    lat0=(s.slat+s.elat)/2; kk_=math.cos(math.radians(lat0))
    L=max(s.len,0.05); h=s.wid/1760/2
    sx,sy,ex,ey=s.slon,s.slat,(s.elon if s.elon<0 else s.slon),(s.elat if s.elat>0 else s.slat)
    pad=h/69+0.01
    try: nsi2=structures._query((min(sx,ex)-pad/kk_,min(sy,ey)-pad,max(sx,ex)+pad/kk_,max(sy,ey)+pad),timeout=300)
    except Exception as ex: print('nsi2 fail',ex); continue
    h2=[x for x in nsi2 if (x.get('occtype') or '').startswith(('RES1','RES2'))]
    cx,cy=(sx+ex)/2,(sy+ey)/2
    ax=np.array([(x['x']-cx)*kk_*69.17 for x in h2]); ay=np.array([(x['y']-cy)*69.17 for x in h2])
    ux,uy=(ex-sx)*kk_*69.17,(ey-sy)*69.17; nn=math.hypot(ux,uy) or 1; ux,uy=ux/nn,uy/nn
    along=ax*ux+ay*uy; cross=np.abs(ax*uy-ay*ux); inr=(np.abs(along)<=L/2)&(cross<=h)
    u=cross[inr]/h; R=int(s.mag)
    nd=nb=0.0
    for uu in u:
        ks=[q for q,sv in enumerate(SH[R]) if uu<=sv]
        if ks: nd+=BM[(max(ks),0)]; nb+=BM[(max(ks),1)]
    lo,hi=EF[R]; vmax=(lo+hi)/2; core=h*65/vmax
    v=np.where(cross[inr]<=core,vmax,vmax*core/np.maximum(cross[inr],1e-9))
    od=sum(hazards.tornado(x,{'material':'wood frame'})[0] for x in v); ob=sum(hazards.tornado(x,{'material':'wood frame'})[1] for x in v)
    row=dict(name=name,date=str(day),rating=R,homes_in_mapped_path=len(levs),from_map_damaged=round(cd),from_map_bad=round(cb),homes_in_spc_box=int(inr.sum()),new_damaged=round(nd),new_bad=round(nb),old_damaged=round(od),old_bad=round(ob))
    print(row); out.append(row)
    if len(out)>=13: break
tot={k:int(sum(r[k] for r in out)) for k in ('from_map_damaged','from_map_bad','new_damaged','new_bad','old_damaged','old_bad')}
pts=[f['attributes'] for f in json.load(urllib.request.urlopen('https://services.dat.noaa.gov/arcgis/rest/services/nws_damageassessmenttoolkit/DamageViewer/FeatureServer/0/query?'+urllib.parse.urlencode({'where':"damage=2",'geometry':'-97.8,35.2,-97.3,35.45','geometryType':'esriGeometryEnvelope','inSR':4326,'outFields':'dod,stormdate','f':'json','resultRecordCount':5000}),timeout=300))['features']]
mo=[p['dod'] for p in pts if p['stormdate'] and abs(p['stormdate']/86400000-15845)<1]
res={'tornadoes':out,'totals':tot,'moore_survey_houses':len(mo),'moore_survey_dod4_plus':sum(x>=4 for x in mo),'moore_survey_dod6_plus':sum(x>=6 for x in mo)}
(ROOT/'eval'/'tornado_replay.json').write_text(json.dumps(res,indent=1)); print(json.dumps(res['totals']), res['moore_survey_dod4_plus'], res['moore_survey_dod6_plus'])
