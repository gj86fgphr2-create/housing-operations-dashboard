#!/usr/bin/env python3
import json, re, sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from openpyxl import load_workbook

run_dir, template_path, source_path, output_path = map(Path, sys.argv[1:5])

def extract_data(path):
    text = path.read_text(encoding="utf-8")
    match = re.search(r"const DATA\s*=\s*(\{.*?\});\s*\n\s*const \$", text, re.S)
    if not match: raise RuntimeError(f"DATA payload missing: {path}")
    return text, json.loads(match.group(1)), match.span(1)

template, old, payload_span = extract_data(template_path)
_, current, _ = extract_data(source_path)
as_of = datetime.strptime(current["dataDate"], "%Y-%m-%d").date()

def norm(value): return str(value or "").strip().replace(" ", "")
def iso(value):
    if isinstance(value, datetime): return value.date().isoformat()
    if isinstance(value, (int,float)): return (datetime(1899,12,30)+timedelta(days=float(value))).date().isoformat()
    text=str(value or "").strip()
    return text[:10] if re.match(r"\d{4}-\d{2}-\d{2}",text) else ""
def idx(headers, *names):
    cleaned=[norm(x) for x in headers]
    for name in names:
        if norm(name) in cleaned: return cleaned.index(norm(name))
    raise RuntimeError(f"Missing column {names}")

building_rows={row["name"]:row for row in current["buildingData"]}
periods=[
    {"key":"w1","label":f"{as_of.month}W1","range":f"{as_of.month}月1日至7日"},
    {"key":"w2","label":f"{as_of.month}W2","range":f"{as_of.month}月8日至14日"},
    {"key":"w3","label":f"{as_of.month}W3","range":f"{as_of.month}月15日至21日"},
    {"key":"w4","label":f"{as_of.month}W4","range":f"{as_of.month}月22日至28日"},
    {"key":"we","label":f"{as_of.month}WE","range":f"{as_of.month}月29日至月末"},
]
checkout=defaultdict(lambda:defaultdict(int))
seen=set()
for filename in ("在租中合同.xlsx","将搬入合同.xlsx"):
    ws=load_workbook(run_dir/filename,read_only=True,data_only=True).active
    headers=[cell.value for cell in ws[3]]
    ci,bi,di=idx(headers,"合同编号"),idx(headers,"小区/公寓"),idx(headers,"退租时间")
    for row in ws.iter_rows(min_row=4,values_only=True):
        contract=norm(row[ci]); date=iso(row[di]); building=str(row[bi] or "").strip()
        if not contract or contract in seen or not date.startswith(f"{as_of.year:04d}-{as_of.month:02d}-"): continue
        seen.add(contract); day=int(date[8:10]); key="w1" if day<=7 else "w2" if day<=14 else "w3" if day<=21 else "w4" if day<=28 else "we"
        checkout[building][key]+=1

for name,row in building_rows.items():
    row["checkout"]={p["key"]:{"count":checkout[name][p["key"]],"rate":checkout[name][p["key"]]/row["rooms"] if row["rooms"] else 0} for p in periods}

def aggregate(name, names):
    rows=[building_rows[n] for n in names if n in building_rows]
    result={"name":name}
    for field in ("rooms","comprehensiveCount","occupiedCount","vacancyCount","lockCount","preorderCount"):
        result[field]=sum(float(r.get(field,0) or 0) for r in rows)
        if result[field].is_integer(): result[field]=int(result[field])
    rooms=result["rooms"]
    for count,rate in (("comprehensiveCount","comprehensiveRate"),("occupiedCount","occupancyRate"),("vacancyCount","vacancyRate"),("lockCount","lockRate")):
        result[rate]=result[count]/rooms if rooms else 0
    result["checkout"]={p["key"]:{"count":sum(r["checkout"][p["key"]]["count"] for r in rows),"rate":0} for p in periods}
    for p in periods: result["checkout"][p["key"]]["rate"]=result["checkout"][p["key"]]["count"]/rooms if rooms else 0
    return result

all_names=list(building_rows)
bt_initial=[n for n in all_names if re.fullmatch(r"北亭0?[1-3]座",n)]
bt_whole=[n for n in all_names if re.fullmatch(r"北亭(?:0?[5-9]|1[0-5])座",n)]
bt_research=[n for n in all_names if n.startswith("北亭研寓")]
nt_research=[n for n in all_names if re.fullmatch(r"南亭(?:20|2[2-8])座",n)]
self_owned=[n for n in all_names if re.fullmatch(r"南亭(?:0?[1-9]|1[0-4])座",n)]
small_a=[n for n in all_names if n=="城北小筑01座"]
small_b=[n for n in all_names if n in {"城北小筑02座","城北小筑06座","城北小筑07座","城北小筑08座","城北小筑11座","城北小筑12座","城南小筑01座"}]
small_c=[n for n in all_names if n in {"城北小筑03座","城北小筑05座","城北小筑10座"}]

project_specs=[
 ("全部房源汇总",all_names),("北亭项目",bt_initial+bt_whole),("北亭初期项目",bt_initial),("北亭整体项目",bt_whole),
 ("小谷围项目",bt_research+nt_research),("北亭研寓项目",bt_research),("南亭研寓项目",nt_research),("公司自持项目",self_owned),
 ("小筑项目",small_a+small_b+small_c),("城北小筑A",small_a),("城北小筑B",small_b),("城北小筑C",small_c),
 ("南亭15座独立项目",["南亭15座"]),("南亭18座独立项目",["南亭18座"]),("南亭21座独立项目",["南亭21座"]),("城北小筑09座独立项目",["城北小筑09座"]),
]
project_data=[aggregate(name,names) for name,names in project_specs]
project_data += [aggregate("南亭区域汇总",[n for n in all_names if "南" in n]),aggregate("北亭区域汇总",[n for n in all_names if "北" in n])]

mapping={
 "整体项目-北亭初期框架":"北亭初期项目","整体项目-北亭整体框架":"北亭整体项目","整体项目-北亭研寓框架":"北亭研寓项目",
 "整体项目-南亭研寓框架":"南亭研寓项目","整体项目-自持物业框架":"公司自持项目","整体项目-城北小筑A":"城北小筑A",
 "整体项目-城北小筑B":"城北小筑B","整体项目-城北小筑C":"城北小筑C","独立项目-南亭15座":"南亭15座独立项目",
 "独立项目-南亭18座":"南亭18座独立项目","独立项目-南亭21座":"南亭21座独立项目","独立项目-城北小筑01座":"城北小筑09座独立项目",
}
cs=current["contractStats"]
monthly={mapping.get(r["name"],r["name"]):{**r,"name":mapping.get(r["name"],r["name"])} for r in cs.get("projectMonthly",[])}
def sum_monthly(name,parts):
    return {"name":name,**{k:sum(monthly.get(p,{}).get(k,0) for p in parts) for k in ("newCount","renewalCount","actualCheckoutCount","checkoutRenewalCount")}}
monthly["北亭项目"]=sum_monthly("北亭项目",["北亭初期项目","北亭整体项目"])
monthly["小谷围项目"]=sum_monthly("小谷围项目",["北亭研寓项目","南亭研寓项目"])
monthly["小筑项目"]=sum_monthly("小筑项目",["城北小筑A","城北小筑B","城北小筑C"])
base_names=[name for name,_ in project_specs[1:]]
contract_stats={**old["contractStats"],**cs,"asOfDate":current["dataDate"],"projectMonthly":[monthly.get(n,{"name":n,"newCount":0,"renewalCount":0,"actualCheckoutCount":0,"checkoutRenewalCount":0}) for n in base_names]}
contract_stats.setdefault("projectMonthlyUnmapped",{"newCount":0,"renewalCount":0,"actualCheckoutCount":0,"checkoutRenewalCount":0})
contract_stats.setdefault("totals",{})
contract_stats["uniqueContracts"]=sum(1 for f in ("在租中合同.xlsx","将搬入合同.xlsx","已退租合同.xlsx") for _ in load_workbook(run_dir/f,read_only=True).active.iter_rows(min_row=4,values_only=True))

payload={"dataDate":current["dataDate"],"generatedDate":datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),"projectData":project_data,"buildingData":list(building_rows.values()),"contractStats":contract_stats,"baseProjectNames":base_names,"checkoutPeriods":periods}
rendered=template[:payload_span[0]]+json.dumps(payload,ensure_ascii=False,separators=(",",":"))+template[payload_span[1]:]
required=['data-dashboard-view="overview"','data-dashboard-view="checkout"','data-dashboard-view="performance"','data-dashboard-view="occupancy"','5%以下绿色']
if any(x not in rendered for x in required): raise RuntimeError("Full dashboard style validation failed")
if len(building_rows)<50 or project_data[0]["rooms"]!=692: raise RuntimeError("Data reconciliation failed")
output_path.parent.mkdir(parents=True,exist_ok=True)
tmp=output_path.with_suffix(".tmp"); tmp.write_text(rendered,encoding="utf-8"); tmp.replace(output_path)
print(json.dumps({"dataDate":payload["dataDate"],"rooms":project_data[0]["rooms"],"buildings":len(building_rows),"projects":len(project_data),"style":"latest-full"},ensure_ascii=False))

