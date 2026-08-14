import fs from "node:fs/promises";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const baseDir = process.env.YXR_DATA_DIR || "/opt/yuxiaor-automation/data";
const mobile = process.env.YXR_MOBILE;
const password = process.env.YXR_PASSWORD;
if (!mobile || !password) throw new Error("Missing YXR_MOBILE or YXR_PASSWORD");

const dateParts = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
}).formatToParts(new Date());
const getPart = (type) => dateParts.find((part) => part.type === type)?.value;
const today = `${getPart("year")}-${getPart("month")}-${getPart("day")}`;
const timeParts = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
}).formatToParts(new Date());
const getTimePart = (type) => timeParts.find((part) => part.type === type)?.value;
const runStamp = `${getTimePart("hour")}-${getTimePart("minute")}-${getTimePart("second")}`;
const todayDate = new Date(`${today}T00:00:00+08:00`);
const previousMonth = new Date(todayDate.getFullYear(), todayDate.getMonth() - 1, 1);
const previousMonthStart = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, "0")}-01`;
// Keep every hourly export instead of overwriting the files from earlier runs.
const runDir = path.join(baseDir, "runs", today, runStamp);
await fs.mkdir(runDir, { recursive: true });

async function fetchWithRetry(url, options = {}, attempts = 5) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await fetch(url, options);
      if (response.status >= 500 && attempt < attempts) {
        await response.arrayBuffer();
        await new Promise((resolve) => setTimeout(resolve, attempt * 3000));
        continue;
      }
      return response;
    } catch (error) {
      lastError = error;
      if (attempt < attempts) await new Promise((resolve) => setTimeout(resolve, attempt * 3000));
    }
  }
  throw lastError;
}

async function jsonFetch(url, options = {}) {
  const response = await fetchWithRetry(url, options);
  const text = await response.text();
  let data;
  try { data = JSON.parse(text); } catch { throw new Error(`${url}: invalid JSON (${response.status})`); }
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status} ${text.slice(0, 300)}`);
  return data;
}

const login = await jsonFetch("https://www.yuxiaor.com/api/v1/sys/multi/userLogin", {
  method: "POST",
  headers: { "content-type": "application/json;charset=UTF-8", "XXX-YUXIAOR-PC": "pms" },
  body: JSON.stringify({ mobile, password, type: 1, session: "", remember: 1 }),
});
const token = login.access_token ?? login.data?.access_token;
if (!token) throw new Error(`Login failed: ${JSON.stringify(login).slice(0, 500)}`);
const headers = { "XXX-YUXIAOR-PC": "pms", "XXX-YUXIAOR-TOKEN": token };
const taskBase = "https://api.yuxiaor.com/yuxiaor-download/api/v1/pms/downloadTask";

async function saveXlsx(response, destination) {
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (!response.ok || bytes[0] !== 0x50 || bytes[1] !== 0x4b) {
    throw new Error(`Invalid XLSX: ${response.status}, ${bytes.length} bytes`);
  }
  await fs.writeFile(destination, bytes);
  return bytes.length;
}

async function listTasks() {
  const data = await jsonFetch(`${taskBase}/list?pageNum=1&pageSize=50&taskName=`, { headers });
  return data.data?.list ?? data.data ?? data.list ?? [];
}

async function downloadTask(task, destination) {
  const data = await jsonFetch(`${taskBase}/${task.id}`, { headers });
  const url = data.data ?? data.url;
  if (typeof url !== "string" || !url.startsWith("http")) throw new Error(`Missing download URL for task ${task.id}`);
  let lastError;
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      return await saveXlsx(await fetchWithRetry(url), destination);
    } catch (error) {
      lastError = error;
      if (attempt < 5) await new Promise((resolve) => setTimeout(resolve, attempt * 3000));
    }
  }
  throw lastError;
}

const contractColumns = [
  "serialNo", "contractNum", "conTypeStatus", "contractTypeStr", "tagDes", "buildingIdStr", "houseIdStr",
  "roomIdStr", "houseSerialNo", "houseNo", "storeNum", "houseNoAddress", "bizTypeDes", "houseStyle", "space",
  "houseType", "roomTypeStr", "firstName", "mobilePhone", "idNumType", "idNum", "genderDes", "countryDes",
  "birthday", "educationDes", "occupationDes", "hobby", "idNumAddress", "company", "otherMsg", "emergencyContact",
  "rentStart", "rentEnd", "conRentEnd", "rentDate", "freeRent", "rentTypeStr", "restRentDate", "rentUnitPrice",
  "rentUnit", "rentTotalPrice", "deposit", "paymentCycle", "addFee", "depositFee", "rentContainFee", "recordUserName",
  "recordDate", "signUserName", "signDate", "reviewUserName", "reviewDate", "departmentName", "liableUserName",
  "lendHouseUserName", "originDes", "remark", "estateName", "building", "cell", "roomSerialNum",
];

async function exportContract(label, status, options = {}) {
  const params = {
    verifyFlag: 1, status, statusList: [], bizType: 0, cityId: 0, liableUserId: 0, lendHouseUserId: 0,
    departmentRouter: "", overdueStatus: 0, tagId: 99, signUserId: 0, dateType: options.dateType ?? 1,
    startDate: options.startDate ?? "", endDate: options.endDate ?? "", contractType: 99, origin: 99,
    bindingStatus: 99, initiator: 99, estateIdStr: [], awayStatus: 99, rentConType: 99, orderModel: 0,
    netSignStatus: 99, rentType: 0, searchKey: "", contractModel: "", renewStatus: "99", searchKeyType: 0,
    refundSignStatus: 99, insurance: 0, contractHouseType: 99,
    columns: [...contractColumns], clientRequestTag: `cloud-${Date.now()}-${status}`,
  };
  if (options.includeExitReason) params.columns.splice(params.columns.indexOf("remark"), 0, "checkOutReason");
  const before = await listTasks();
  const beforeIds = new Set(before.map((task) => String(task.id)));
  const response = await fetch(taskBase, {
    method: "POST",
    headers: { ...headers, "content-type": "application/json;charset=UTF-8" },
    body: JSON.stringify({ taskName: "合同-租客合同", invokeTarget: "contractServiceImpl#fmContractExport", params: JSON.stringify(params) }),
  });
  if (!response.ok) throw new Error(`Create ${label}: HTTP ${response.status} ${(await response.text()).slice(0, 500)}`);
  let task;
  for (let attempt = 0; attempt < 90; attempt += 1) {
    const rows = await listTasks();
    task = rows.filter((item) => !beforeIds.has(String(item.id)) && String(item.taskName ?? item.fileName ?? "").includes("合同-租客合同"))
      .sort((a, b) => Number(b.id) - Number(a.id))[0];
    if (task && Number(task.status) === 9) break;
    if (task && [3, 4, 5, 6, 7, 8, 10].includes(Number(task.status))) throw new Error(`${label} export failed: ${JSON.stringify(task)}`);
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  if (!task || Number(task.status) !== 9) throw new Error(`${label} export timed out`);
  const destination = path.join(runDir, `${label}.xlsx`);
  const bytes = await downloadTask(task, destination);
  return { label, bytes, taskId: task.id, file: destination };
}

const results = [];
results.push(await exportContract("已退租合同", 3, { dateType: 6, startDate: previousMonthStart, endDate: today, includeExitReason: true }));
results.push(await exportContract("在租中合同", 1));
results.push(await exportContract("将搬入合同", 2));

const houseColumns = ["serialNo", "buildingIdStr", "houseIdStr", "roomIdStr", "houseNo", "houseSerialNo", "houseNoAddress",
  "houseType", "roomTypeStr", "styleName", "orientationStr", "space", "totalSpace", "exportStatusStr", "contractStartDate",
  "contractEndDate", "price", "lowestPrice", "maxPrice", "maxLowestPrice", "lastDate", "vacancyDay", "flRentEnd", "cityArea",
  "departmentName", "liableUserName", "lendHouseUserName", "notes", "commonNotes", "lockReason", "estateName", "building", "cell", "roomSerialNum"];
const houseQuery = new URLSearchParams({ bizType: "1", pageNum: "0", pageSize: "0", limit: "0", offset: "0" });
for (const column of houseColumns) houseQuery.append("selectColumnsStr[]", column);
const houseFile = path.join(runDir, "房源详情.xlsx");
results.push({ label: "房源详情", bytes: await saveXlsx(await fetchWithRetry(`https://www.yuxiaor.com/api/v1/houses/list-to-excel?${houseQuery}`, { headers }), houseFile), file: houseFile });

async function fetchAllReservations() {
  const records = [];
  const pageSize = 200;
  let expectedCount = Infinity;
  for (let offset = 0; offset < 20000 && records.length < expectedCount; offset += pageSize) {
    const reserveQuery = new URLSearchParams({
      status: "2", offset: String(offset), limit: String(pageSize),
    });
    const page = await jsonFetch(`https://www.yuxiaor.com/api/v1/contract-reserve/list?${reserveQuery}`, { headers });
    const rows = Array.isArray(page.data) ? page.data : [];
    expectedCount = Number(page.count ?? rows.length);
    records.push(...rows);
    if (rows.length === 0 || records.length >= expectedCount) break;
  }
  if (Number.isFinite(expectedCount) && records.length < expectedCount) {
    throw new Error(`Reservation pagination incomplete: expected ${expectedCount}, received ${records.length}`);
  }
  const invalid = records.filter((record) => Number(record.status) !== 2 || record.statusStr !== "已付定");
  if (invalid.length) throw new Error(`Reservation status validation failed: ${invalid.length} non-paid records`);
  return records;
}

const reservationRecords = await fetchAllReservations();
const reserveJson = path.join(runDir, "预定合同.json");
await fs.writeFile(reserveJson, JSON.stringify(reservationRecords));
const reserveFile = path.join(runDir, "预定合同.xlsx");
const postprocessScript = process.env.YXR_POSTPROCESS_SCRIPT || "/opt/yuxiaor-automation/app/postprocess_downloads.py";
const processed = await execFileAsync("/usr/bin/python3", [postprocessScript, runDir], { maxBuffer: 1024 * 1024 });
const postprocess = JSON.parse(processed.stdout.trim());
const houseResult = results.find((item) => item.label === "房源详情");
houseResult.bytes = (await fs.stat(houseFile)).size;
houseResult.removed = postprocess.houseRemoved;
results.push({
  label: "预定合同", bytes: (await fs.stat(reserveFile)).size,
  records: postprocess.reservationRows, excluded: postprocess.reservationRemoved, file: reserveFile,
});

const manifest = { runDate: today, retiredDateRange: { start: previousMonthStart, end: today }, completedAt: new Date().toISOString(), postprocess, results };
await fs.writeFile(path.join(runDir, "manifest.json"), JSON.stringify(manifest, null, 2));
await fs.rm(path.join(baseDir, "current"), { recursive: true, force: true });
await fs.symlink(runDir, path.join(baseDir, "current"), "dir");
console.log(JSON.stringify(manifest, null, 2));

