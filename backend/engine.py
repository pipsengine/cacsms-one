
import math
from itertools import combinations
from collections import defaultdict

TIERS=(2,5,10,25,100,1000,5000,25000)

def poisson(k, lam):
    return math.exp(-lam)*lam**k/math.factorial(k)

def football_probabilities(home_xg, away_xg, max_goals=12):
    """Independent Poisson baseline. Tail beyond max_goals is normalized."""
    h=[poisson(i,home_xg) for i in range(max_goals+1)]
    a=[poisson(i,away_xg) for i in range(max_goals+1)]
    total=sum(h)*sum(a)
    cells=[(i,j,h[i]*a[j]/total) for i in range(len(h)) for j in range(len(a))]
    return {
        "HOME":sum(p for i,j,p in cells if i>j),
        "DRAW":sum(p for i,j,p in cells if i==j),
        "AWAY":sum(p for i,j,p in cells if i<j),
        "OVER_2_5":sum(p for i,j,p in cells if i+j>=3),
        "UNDER_2_5":sum(p for i,j,p in cells if i+j<=2),
        "BTTS_YES":sum(p for i,j,p in cells if i>0 and j>0),
        "BTTS_NO":sum(p for i,j,p in cells if i==0 or j==0),
    }

def normal_cdf(x):
    return (1+math.erf(x/math.sqrt(2)))/2

def basketball_probabilities(home_mean, away_mean, total_sd=16.5, margin_sd=12):
    """Illustrative Gaussian scoring model, not a calibrated production model."""
    diff=home_mean-away_mean
    return {"HOME":normal_cdf(diff/margin_sd),
            "AWAY":1-normal_cdf(diff/margin_sd),
            "OVER_220_5":1-normal_cdf((220.5-home_mean-away_mean)/total_sd),
            "UNDER_220_5":normal_cdf((220.5-home_mean-away_mean)/total_sd)}

def evaluate(event):
    if event["sport"]=="football":
        stats=event.get("stats",{})
        if not {"home_xg","away_xg"}<=stats.keys(): return []
        probs=football_probabilities(float(stats["home_xg"]),float(stats["away_xg"]))
    elif event["sport"]=="basketball":
        # This illustrative legacy path has no auditable calibrated model or
        # league/market approval. Basketball tickets use the validated pipeline.
        return []
    else: return []
    picks=[]
    for market,odds in event["odds"].items():
        if market not in probs: continue
        odds=float(odds)
        if odds<=1: continue
        probability=probs[market]
        edge=probability*odds-1
        if edge>=0.05 and probability>=0.15:
            picks.append({"event_id":event["id"],"sport":event["sport"],
                "match":event["home"]+" vs "+event["away"],"market":market,
                "odds":odds,"probability":round(probability,5),"edge":round(edge,5),
                "start":event.get("start",""),"source":event.get("source","unknown")})
    return picks

def build_tickets(picks, max_legs=6, per_tier=12, tolerance=.15):
    """Distinct events only; different-event independence is a simplifying assumption."""
    pool=sorted(picks,key=lambda p:(-p["edge"],p["event_id"]))[:18]
    groups=defaultdict(list)
    for size in range(1,min(max_legs,len(pool))+1):
        for legs in combinations(pool,size):
            if len({p["event_id"] for p in legs})!=size: continue
            odds=math.prod(p["odds"] for p in legs)
            targets=[t for t in TIERS if abs(odds/t-1)<=tolerance]
            if not targets: continue
            prob=math.prod(p["probability"] for p in legs)
            for target in targets:
                groups[target].append({"target":target,"odds":round(odds,3),
                    "probability":round(prob,6),"estimated_ev":round(prob*odds-1,5),
                    "legs":list(legs),"booking_code":None,"booking_status":"not_generated"})
    return {str(t):sorted(groups[t],key=lambda x:(-x["estimated_ev"],abs(x["odds"]/t-1)))[:per_tier] for t in TIERS}
