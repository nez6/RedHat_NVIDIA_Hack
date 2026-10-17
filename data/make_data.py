"""Generates the synthetic NC Triangle demo dataset (deterministic, seed 7)."""
import csv, random, json, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
random.seed(7)
# name, city, lat, lon, safety(0-100), schools(1-10), median $/sqft, walk
hoods = [
("Five Points","Raleigh",35.8105,-78.6455,82,8,330,78),
("Oakwood","Raleigh",35.7860,-78.6300,68,6,310,85),
("North Hills","Raleigh",35.8380,-78.6420,80,8,295,72),
("Brier Creek","Raleigh",35.9060,-78.7860,77,7,220,48),
("Southeast Raleigh","Raleigh",35.7600,-78.5900,52,5,185,55),
("Cameron Village","Raleigh",35.7930,-78.6580,79,8,350,88),
("Downtown Cary","Cary",35.7880,-78.7810,86,9,265,70),
("West Cary","Cary",35.7700,-78.8700,90,9,240,35),
("Apex Peakway","Apex",35.7320,-78.8500,91,9,225,40),
("Wake Forest Historic","Wake Forest",35.9790,-78.5100,84,8,215,58),
("Durham Downtown","Durham",35.9940,-78.8990,60,6,280,90),
("Trinity Park","Durham",36.0080,-78.9050,70,7,300,80),
("Southpoint","Durham",35.9050,-78.9450,74,7,210,38),
("Chapel Hill Southern Village","Chapel Hill",35.8800,-79.0650,88,10,290,62),
("Carrboro","Carrboro",35.9100,-79.0750,80,10,305,75),
("Holly Springs","Holly Springs",35.6510,-78.8330,89,8,205,30),
("Garner","Garner",35.7110,-78.6140,72,6,185,35),
("Morrisville","Morrisville",35.8240,-78.8250,87,9,235,45),
]
feat_pool=["garage","home office","fenced yard","pool","solar panels","EV charger","open floor plan","finished basement","screened porch","hardwood floors","updated kitchen","first-floor primary","walk-in closet","mature trees","sidewalks","near greenway","smart home","energy efficient"]
streets=["Oak","Maple","Pine","Magnolia","Dogwood","Willow","Hillsborough","Glenwood","Wade","Ridge","Lake","Creek","Laurel","Cedar","Holly","Poplar"]
rows=[]
lid=1001
for h in hoods:
    for _ in range(random.randint(3,4)):
        ptype=random.choices(["Single Family","Townhouse","Condo"],[0.65,0.22,0.13])[0]
        beds={"Condo":random.choice([1,2,2,3]),"Townhouse":random.choice([2,3,3,4]),"Single Family":random.choice([3,3,4,4,5])}[ptype]
        baths=max(1,beds-random.choice([0,1,1]))+random.choice([0,0.5])
        sqft=int(beds*random.randint(430,620)+random.randint(-150,350))
        ppsf=h[6]*random.uniform(0.82,1.2)
        price=int(round(sqft*ppsf/1000)*1000)
        year=random.randint(1925 if h[0] in("Oakwood","Five Points","Trinity Park") else 1975,2025)
        hoa=0 if ptype=="Single Family" and random.random()<0.6 else random.choice([45,85,150,220,310])
        lot=round(random.uniform(0.08,0.6),2) if ptype=="Single Family" else 0.0
        feats=random.sample(feat_pool,random.randint(3,7))
        if ptype!="Single Family" and "fenced yard" in feats: feats.remove("fenced yard")
        rows.append(dict(id=lid,address=f"{random.randint(100,9800)} {random.choice(streets)} {random.choice(['St','Ave','Dr','Ln','Ct','Way'])}",
            neighborhood=h[0],city=h[1],lat=round(h[2]+random.uniform(-.008,.008),5),lon=round(h[3]+random.uniform(-.008,.008),5),
            price=price,beds=beds,baths=baths,sqft=sqft,year_built=year,lot_acres=lot,hoa_monthly=hoa,property_type=ptype,
            features="|".join(feats),days_on_market=random.randint(1,90)))
        lid+=1
with open("listings.csv","w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0].keys()));w.writeheader();w.writerows(rows)
with open("neighborhoods.json","w") as f:
    json.dump([dict(name=h[0],city=h[1],lat=h[2],lon=h[3],safety_index=h[4],school_rating=h[5],median_ppsf=h[6],walk_score=h[7]) for h in hoods],f,indent=1)
print(len(rows))
