#!/usr/bin/env python3
import csv, json, os, re, sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from openpyxl import load_workbook

run_dir, template_path, source_path, output_path = map(Path, sys.argv[1:5])

XHS_ACCOUNTS = [
    {"profile":"account-02","name":"广州大学城租房-研寓","operator":"孝西"},
    {"profile":"account-03","name":"广州研寓租房大学城","operator":"嘉明"},
    {"profile":"account-04","name":"大学城捞房长短租随意","operator":"梦琪"},
    {"profile":"account-05","name":"暴走大学城探房版","operator":"淼淼"},
    {"profile":"account-06","name":"广州大学城-研舍公寓","operator":"紫莹"},
    {"profile":"account-07","name":"广州大学城租房-维特","operator":"珂珂"},
    {"profile":"account-08","name":"大学城租房 | 研舍","operator":"传坤"},
    {"profile":"account-09","name":"番禺大学城租房-尚维特","operator":"余路"},
]

def latest_xhs_summary():
    """Locate the newest collector summary without coupling the dashboard to a dated folder."""
    candidates=[]
    configured=os.environ.get("XHS_CONTENT_WEEKLY_JSON","").strip()
    if configured: candidates.append(Path(configured))
    roots=[Path("/home/ubuntu/xhs-account-isolation/data"),Path("/opt/xhs-account-isolation/data")]
    for root in roots:
        if root.exists(): candidates.extend(root.glob("content-stats*/content-weekly-summary-latest.json"))
    existing=[path for path in candidates if path.is_file()]
    return max(existing,key=lambda path:path.stat().st_mtime) if existing else None

def build_xhs_content(fallback):
    summary_path=latest_xhs_summary()
    if not summary_path: return fallback or {"generatedAt":"","month":"","weeks":[],"accounts":XHS_ACCOUNTS,"dailyReading":[]}
    summary=json.loads(summary_path.read_text(encoding="utf-8"))
    end_date=datetime.strptime(summary["period"]["end_date"],"%Y-%m-%d").date()
    month_prefix=str(end_date.month)
    week_rows=[row for row in summary.get("weekly_totals",[]) if row.get("week_label","").startswith(month_prefix+"W") and row.get("week_start","")<=end_date.isoformat()]
    weeks=[{"key":row["week_label"][len(month_prefix):].lower(),"label":row["week_label"][len(month_prefix):],"start":row["week_start"],"end":row["week_end"]} for row in week_rows]
    by_profile_week={(row.get("profile"),row.get("week_label")):row for row in summary.get("account_week_rows",[])}
    accounts=[]
    for account in XHS_ACCOUNTS:
        metrics={}
        for week in weeks:
            source=by_profile_week.get((account["profile"],month_prefix+week["label"]))
            metrics[week["key"]]={
                "notes":source.get("note_count") if source else None,
                "views":source.get("cumulative_views") if source else None,
                "exposures":source.get("cumulative_exposures") if source else None,
            }
        accounts.append({**account,"metrics":metrics})
    daily_totals=defaultdict(int)
    daily_path=summary_path.with_name("daily-reading-latest.csv")
    if daily_path.is_file():
        with daily_path.open(encoding="utf-8-sig",newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("status") == "ok" and row.get("date"):
                    daily_totals[row["date"]]+=int(row.get("reading_count") or 0)
    return {"generatedAt":summary.get("generated_at","")[:19].replace("T"," "),"month":f"{end_date.year:04d}-{end_date.month:02d}","weeks":weeks,"accounts":accounts,"dailyReading":[{"date":date,"readingCount":count} for date,count in sorted(daily_totals.items(),reverse=True)]}

def extract_data(path):
    text = path.read_text(encoding="utf-8")
    match = re.search(r"const DATA\s*=\s*(\{.*?\});", text, re.S)
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

def recent_performance():
    """Build seven-day totals by day, project, and signer from hourly exports."""
    start = as_of - timedelta(days=6)
    rows = {start + timedelta(days=i): {"newCount":0,"renewalCount":0,"actualCheckoutCount":0} for i in range(7)}
    projects = defaultdict(lambda:{"newCount":0,"renewalCount":0,"actualCheckoutCount":0})
    people = defaultdict(lambda:{"newCount":0,"renewalCount":0,"actualCheckoutCount":0})
    seen_signing, seen_checkout = set(), set()
    for filename in ("在租中合同.xlsx", "将搬入合同.xlsx", "已退租合同.xlsx"):
        ws = load_workbook(run_dir/filename, read_only=True, data_only=True).active
        headers = [cell.value for cell in ws[3]]
        ci, ti, si, ri = idx(headers,"合同编号"), idx(headers,"签约来源"), idx(headers,"签约时间"), idx(headers,"预退/实退")
        pi, person_i = idx(headers,"所属部门"), idx(headers,"签约人")
        for row in ws.iter_rows(min_row=4, values_only=True):
            contract = norm(row[ci])
            if not contract: continue
            sign_date, checkout_date = iso(row[si]), iso(row[ri])
            project = mapping.get(str(row[pi] or "").strip(), str(row[pi] or "").strip() or "未归类")
            person = str(row[person_i] or "").strip() or "未填写"
            if contract not in seen_signing and sign_date:
                signed = datetime.strptime(sign_date,"%Y-%m-%d").date()
                if signed in rows:
                    source = norm(row[ti])
                    is_renewal = source in {"续租","重签"}
                    field = "renewalCount" if is_renewal else "newCount"
                    rows[signed][field] += 1; projects[project][field] += 1; people[person][field] += 1
                seen_signing.add(contract)
            if filename == "已退租合同.xlsx" and contract not in seen_checkout and checkout_date:
                checked_out = datetime.strptime(checkout_date,"%Y-%m-%d").date()
                if checked_out in rows:
                    rows[checked_out]["actualCheckoutCount"] += 1
                    projects[project]["actualCheckoutCount"] += 1
                    people[person]["actualCheckoutCount"] += 1
                seen_checkout.add(contract)
    def summed(name, parts):
        return {"name":name,**{field:sum(projects[p][field] for p in parts) for field in ("newCount","renewalCount","actualCheckoutCount")}}
    projects["北亭项目"] = summed("北亭项目",["北亭初期项目","北亭整体项目"])
    projects["小谷围项目"] = summed("小谷围项目",["北亭研寓项目","南亭研寓项目"])
    projects["小筑项目"] = summed("小筑项目",["城北小筑A","城北小筑B","城北小筑C"])
    project_rows = [{"name":name,**projects[name]} for name in base_names]
    people_rows = [{"name":name,**values} for name,values in sorted(people.items(),key=lambda item:(-sum(item[1].values()),item[0])) if sum(values.values())]
    return [{"date":day.isoformat(),**values} for day,values in rows.items()], project_rows, people_rows

building_rows={row["name"]:row for row in current["buildingData"]}

def apply_house_monitoring():
    """Use 房源详情 as the authoritative source for locks and vacancy availability."""
    def contract_room_ids(filename, header_row, required_status, *status_names):
        contract_ws=load_workbook(run_dir/filename,read_only=True,data_only=True).active
        contract_headers=[cell.value for cell in contract_ws[header_row]]
        room_i=idx(contract_headers,"房源ID")
        contract_status_i=idx(contract_headers,*status_names)
        return {
            norm(row[room_i])
            for row in contract_ws.iter_rows(min_row=header_row+1,values_only=True)
            if norm(row[room_i]) and norm(row[contract_status_i]) == required_status
        }
    preorder_room_ids=contract_room_ids("预定合同.xlsx",1,"已付定","状态")
    moving_room_ids=contract_room_ids("将搬入合同.xlsx",3,"将搬入","合同状态","状态")
    ws=load_workbook(run_dir/"房源详情.xlsx",read_only=True,data_only=True).active
    headers=[cell.value for cell in ws[3]]
    building_i, room_i, lock_i, status_i=idx(headers,"小区/公寓"),idx(headers,"房源ID"),idx(headers,"锁房备注"),idx(headers,"状态")
    locks, rentable, unrentable=defaultdict(int),defaultdict(int),defaultdict(int)
    mapped_unknown=defaultdict(int)
    occupied, preordered, moving_in, unknown_status=defaultdict(int),defaultdict(int),defaultdict(int),defaultdict(int)
    for row in ws.iter_rows(min_row=4,values_only=True):
        building=str(row[building_i] or "").strip()
        room_id=norm(row[room_i])
        state=norm(row[status_i])
        remark=norm(row[lock_i])
        monitored=bool(remark) or state in {"已出租","在租中","空房可租","空房不可租","未知状态"}
        if monitored and not building: raise RuntimeError("Monitored room is missing building name")
        if remark: locks[building]+=1
        if state in {"已出租","在租中"}: occupied[building]+=1
        if state == "空房可租": rentable[building]+=1
        if state in {"空房不可租","未知状态"}:
            unrentable[building]+=1
            if state == "未知状态": mapped_unknown[building]+=1
            if room_id in moving_room_ids or any(word in remark for word in ("将搬入","待搬入")): moving_in[building]+=1
            elif room_id in preorder_room_ids or any(word in remark for word in ("已预订","已预定","预订","预定")): preordered[building]+=1
        if state not in {"已出租","在租中","空房可租","空房不可租","未知状态"}: unknown_status[building]+=1
    unknown=sorted((set(locks)|set(rentable)|set(unrentable)|set(occupied)|set(preordered)|set(moving_in)|set(mapped_unknown)|set(unknown_status))-set(building_rows))
    if unknown: raise RuntimeError(f"Locked rooms contain unknown buildings: {unknown}")
    for name,row in building_rows.items():
        locked=locks[name]
        row["lockCount"]=locked
        row["rentableVacancyCount"]=rentable[name]
        row["unrentableVacancyCount"]=unrentable[name]
        row["vacancyCount"]=rentable[name]+unrentable[name]
        row["occupiedCount"]=occupied[name]
        row["preorderCount"]=preordered[name]
        row["moveInCount"]=moving_in[name]
        row["qualifyingUnavailableCount"]=preordered[name]+moving_in[name]
        rooms=int(row.get("rooms",0) or 0)
        row["lockRate"]=locked/rooms if rooms else 0
        row["occupancyRate"]=row["occupiedCount"]/rooms if rooms else 0
        row["vacancyRate"]=row["vacancyCount"]/rooms if rooms else 0
        row["comprehensiveCount"]=row["occupiedCount"]+row["qualifyingUnavailableCount"]
        row["comprehensiveRate"]=row["comprehensiveCount"]/rooms if rooms else 0
    return {"lock":sum(locks.values()),"rentable":sum(rentable.values()),"unrentable":sum(unrentable.values()),"occupied":sum(occupied.values()),"preordered":sum(preordered.values()),"movingIn":sum(moving_in.values()),"mappedUnknown":sum(mapped_unknown.values()),"unknownStatus":sum(unknown_status.values())}

house_monitor=apply_house_monitoring()
lock_total=house_monitor["lock"]
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
    for field in ("rooms","comprehensiveCount","occupiedCount","vacancyCount","lockCount","rentableVacancyCount","unrentableVacancyCount","preorderCount","moveInCount","qualifyingUnavailableCount"):
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
contract_stats["recentPerformance"], contract_stats["recentProjectPerformance"], contract_stats["recentPeoplePerformance"] = recent_performance()
contract_stats.setdefault("projectMonthlyUnmapped",{"newCount":0,"renewalCount":0,"actualCheckoutCount":0,"checkoutRenewalCount":0})
contract_stats.setdefault("totals",{})
contract_stats["uniqueContracts"]=sum(1 for f in ("在租中合同.xlsx","将搬入合同.xlsx","已退租合同.xlsx") for _ in load_workbook(run_dir/f,read_only=True).active.iter_rows(min_row=4,values_only=True))

payload={"dataDate":current["dataDate"],"generatedDate":datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),"projectData":project_data,"buildingData":list(building_rows.values()),"contractStats":contract_stats,"baseProjectNames":base_names,"checkoutPeriods":periods,"xhsContent":build_xhs_content(old.get("xhsContent"))}
rendered=template[:payload_span[0]]+json.dumps(payload,ensure_ascii=False,separators=(",",":"))+template[payload_span[1]:]
required=['class="nav desktop-nav"','data-dashboard-view="operations-brief"','data-dashboard-view="overview"','data-dashboard-view="performance"','data-dashboard-view="occupancy"','class="mobile-nav-shell"','data-mobile-menu="primary"','data-mobile-module="xiaohongshu"','data-mobile-module="yuxiaor"','data-mobile-menu="xiaohongshu"','data-mobile-menu="yuxiaor"','5%以下绿色','brief-daily-table','brief-project-table','brief-person-table','id="xhs-account"','xhs-account-table','邮箱密码不匹配','xhs-account-status-list','xhs-note-count-table','xhs-view-count-table','xhs-exposure-count-table','xhs-daily-reading-chart']
if any(x not in rendered for x in required): raise RuntimeError("Full dashboard style validation failed")
if 'data-dashboard-view="checkout"' in rendered: raise RuntimeError("Legacy checkout navigation detected")
if len(building_rows)<50 or project_data[0]["rooms"]!=692: raise RuntimeError("Data reconciliation failed")
if project_data[0]["lockCount"]!=lock_total: raise RuntimeError("Lock count reconciliation failed")
if project_data[0]["rentableVacancyCount"]+project_data[0]["unrentableVacancyCount"]!=project_data[0]["vacancyCount"]: raise RuntimeError("Vacancy availability reconciliation failed")
if project_data[0]["comprehensiveCount"]!=project_data[0]["occupiedCount"]+project_data[0]["preorderCount"]+project_data[0]["moveInCount"]: raise RuntimeError("Comprehensive occupancy reconciliation failed")
if house_monitor["occupied"]+house_monitor["rentable"]+house_monitor["unrentable"]+house_monitor["unknownStatus"]!=project_data[0]["rooms"]: raise RuntimeError("Room status reconciliation failed")
if house_monitor["preordered"]+house_monitor["movingIn"]>house_monitor["unrentable"]: raise RuntimeError("Unavailable room classification failed")
output_path.parent.mkdir(parents=True,exist_ok=True)
tmp=output_path.with_suffix(".tmp"); tmp.write_text(rendered,encoding="utf-8"); tmp.replace(output_path)
print(json.dumps({"dataDate":payload["dataDate"],"rooms":project_data[0]["rooms"],"lockCount":lock_total,"lockField":"锁房备注","rentableVacancyCount":house_monitor["rentable"],"unrentableVacancyCount":house_monitor["unrentable"],"mappedUnknownCount":house_monitor["mappedUnknown"],"occupiedCount":house_monitor["occupied"],"preorderCount":house_monitor["preordered"],"moveInCount":house_monitor["movingIn"],"unknownStatusCount":house_monitor["unknownStatus"],"comprehensiveDefinition":"在租中/已出租+空房不可租中的已预订和将搬入","vacancyField":"状态（未知状态映射为其他锁房）","buildings":len(building_rows),"projects":len(project_data),"style":"latest-full"},ensure_ascii=False))
