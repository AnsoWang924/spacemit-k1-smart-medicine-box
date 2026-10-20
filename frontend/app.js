const STORAGE_KEY = "smart-medicine-ui-v2-members";
const REPORT_KEY = "smart-medicine-ui-v2-reports";
const INVENTORY_LEDGER_KEY = "smart-medicine-ui-v2-inventory-ledger";
const CABINET_STATE_KEY = "smart-medicine-ui-v2-cabinet-state";
const PROGRESS_KEY = "smart-medicine-ui-v2-progress";
const VOICE_TONE_KEY = "smart-medicine-ui-v2-voice-tone";
const FLOW_STATE_KEY = "smart-medicine-ui-v2-flow-state";
const FLOW_STATE_SCHEMA_VERSION = 2;
const FACE_DRAFT_KEY = "smart-medicine-ui-v2-face-draft";
const CABINET_ASSIGNMENT_KEY = "smart-medicine-ui-v2-cabinet-assignments";
const TWO_MEMBER_ROSTER_MIGRATION_KEY = "smart-medicine-ui-v2-k1-two-member-roster-20260721";
// Upgrade only the two built-in demonstration profiles. This is intentionally
// separate from the roster migration so existing browser data is not silently
// replaced again after this prescription revision has been applied once.
const DEMO_PRESCRIPTION_MIGRATION_KEY = "smart-medicine-ui-v2-demo-prescriptions-20260729";
let k1GatewayClientPromise = null;
let k1HealthRequestToken = 0;
const K1_GATEWAY_TIMEOUT_MS = 45000;
const K1_MEDICINE_BOX_OCR_TIMEOUT_MS = 55000;
const K1_MEDICINE_BOX_LEASE_RENEW_MS = 40000;
const K1_ASSISTANT_LEASE_RENEW_MS = 90000;
const K1_PRESCRIPTION_OCR_TIMEOUT_MS = 120000;
const K1_ASR_POLL_MS = 500;
const K1_ASR_MODEL_TIMEOUT_MS = 45000;
const K1_ASR_RECOGNITION_TIMEOUT_MS = 35000;
const CPU_METRICS_POLL_MS = 2000;
const REMINDER_GRACE_MS = 2 * 60 * 1000;
// Button-triggered voice must begin in the originating user gesture. A timer
// here makes Chromium reject Audio.play() for every non-special workflow.
const PAGE_ANNOUNCE_DELAY_MS = 0;
const REMINDER_VOICE_NAMES = [
  "Xiaoxiao Online (Natural) - Chinese (Mainland)",
  "Yunyang Online (Natural) - Chinese (Mainland)",
];
const REMINDER_VOICE_LABELS = {
  "Xiaoxiao Online (Natural) - Chinese (Mainland)": "Xiaoxiao Online",
  "Yunyang Online (Natural) - Chinese (Mainland)": "Yunyang Online",
};
const REMINDER_VOICE_KEYWORDS = {
  "Xiaoxiao Online (Natural) - Chinese (Mainland)": ["xiaoxiao", "晓晓", "女声"],
  "Yunyang Online (Natural) - Chinese (Mainland)": ["yunyang", "云扬", "男声"],
};

const pages = {
  home: ["开机首页", "全家庭成员用药提醒、今日进度和播报队列"],
  adherence: ["患者每日服药情况", "按日期查看患者服药率曲线"],
  "today-status": ["今日服用情况", "按当前使用人查看今日各时段服药状态"],
  members: ["成员管理", "成员档案、药品信息"],
  "member-form": ["新增成员", "药单识别、药品卡片、取药密码"],
  plan: ["服药方案", "按成员逐页查看提醒时间和药品安排"],
  cabinet: ["药柜管理", "12 个药柜按家庭成员和药品独立分配"],
  "cabinet-detail": ["药柜详情", "柜门、库存和锁控参数"],
  assistant: ["智能用药助手", "成员用药答疑与药物咨询"],
  reports: ["记录报告", "服药日志、纠错、异常和风险记录"],
  k1: ["K1 状态", "边缘 AI、摄像头、数据库和硬件状态"],
  settings: ["设置", "比赛测试状态与本地数据管理"],
  alarm: ["服药闹钟", "全家庭成员到点提醒播报"],
  face: ["人脸核验", "按当前成员的人脸库进行核验"],
  "med-confirm": ["确认药物", "请确认本次药物名称及其数量"],
  take: ["取药监督", "按药柜分配逐项取药"],
  "load-guide": ["放药引导", "按 12 个药柜和多位成员连续放药"],
};

const PLAN_EXPLANATION_DELAY_MS = 5000;
const wangUncleDemoProfile = {
  demoProfile: "wang-uncle",
  gender: "男",
  age: "66",
  disease: "呼吸道感染伴腹泻、轻度脱水风险",
  schedule: { wake: "06:45", breakfast: "07:35", lunch: "12:05", dinner: "18:05", sleep: "21:15" },
  medicines: [
    { name: "盐酸多西环素片", times: "2", amount: "1", unit: "片", stock: "6", note: "" },
    { name: "口服补液盐散（III）", times: "1", amount: "1", unit: "袋", stock: "3", note: "" },
    { name: "蒙脱石散", times: "1", amount: "1", unit: "袋", stock: "3", note: "" },
  ],
  planOverrides: [
    { time: "07:45", slot: "早餐后", medicines: [{ name: "盐酸多西环素片", amount: "1", unit: "片" }] },
    { time: "12:15", slot: "午餐后", medicines: [{ name: "口服补液盐散（III）", amount: "1", unit: "袋" }] },
    { time: "18:15", slot: "晚餐后", medicines: [{ name: "盐酸多西环素片", amount: "1", unit: "片" }] },
    { time: "21:05", slot: "睡觉前", medicines: [{ name: "蒙脱石散", amount: "1", unit: "袋" }] },
  ],
  // The plan page always renders the Python engine result.  This remains only
  // as an old-profile compatibility field for other demo flows.
  planExplanation: "",
};
const heGrandpaDemoProfile = {
  demoProfile: "he-grandpa",
  gender: "男",
  age: "72",
  disease: "呼吸道细菌感染恢复期伴食欲不佳、脾胃虚弱",
  schedule: { wake: "07:10", breakfast: "08:20", lunch: "12:50", dinner: "18:50", sleep: "22:00" },
  medicines: [
    { name: "阿莫西林胶囊", times: "2", amount: "1", unit: "粒", stock: "6", note: "" },
    { name: "维生素D滴剂", times: "1", amount: "1", unit: "滴", stock: "3", note: "" },
    { name: "人参健脾片", times: "1", amount: "2", unit: "片", stock: "6", note: "" },
  ],
  planOverrides: [
    { time: "08:30", slot: "早餐后", medicines: [{ name: "阿莫西林胶囊", amount: "1", unit: "粒" }, { name: "维生素D滴剂", amount: "1", unit: "滴" }] },
    { time: "13:00", slot: "午餐后", medicines: [{ name: "人参健脾片", amount: "2", unit: "片" }] },
    { time: "19:00", slot: "晚餐后", medicines: [{ name: "阿莫西林胶囊", amount: "1", unit: "粒" }] },
  ],
  planExplanation: "",
};
const wangGrandpaDemoProfile = {
  demoProfile: "wang-grandpa",
  gender: "男",
  age: "65",
  disease: "感冒合并过敏性鼻炎",
  schedule: { wake: "07:00", breakfast: "08:00", lunch: "12:30", dinner: "18:30", sleep: "21:30" },
  medicines: [
    { name: "小柴胡颗粒", times: "3", amount: "1", unit: "袋", stock: "9", note: "" },
    { name: "复合维生素B片", times: "3", amount: "2", unit: "片", stock: "18", note: "" },
    { name: "盐酸西替利嗪片", times: "1", amount: "1", unit: "片", stock: "3", note: "睡前服用" },
  ],
  planOverrides: [
    { time: "08:10", slot: "早餐后", medicines: [{ name: "小柴胡颗粒", amount: "1", unit: "袋" }, { name: "复合维生素B片", amount: "2", unit: "片" }] },
    { time: "12:40", slot: "午餐后", medicines: [{ name: "小柴胡颗粒", amount: "1", unit: "袋" }, { name: "复合维生素B片", amount: "2", unit: "片" }] },
    { time: "18:40", slot: "晚餐后", medicines: [{ name: "小柴胡颗粒", amount: "1", unit: "袋" }, { name: "复合维生素B片", amount: "2", unit: "片" }] },
    { time: "21:20", slot: "睡觉前", medicines: [{ name: "盐酸西替利嗪片", amount: "1", unit: "片" }] },
  ],
  planExplanation: "",
};
const namedDemoProfiles = { "王叔叔": wangUncleDemoProfile, "何爷爷": heGrandpaDemoProfile, "王爷爷": wangGrandpaDemoProfile };
const TWO_MEMBER_ROSTER_NAMES = new Set(["王叔叔", "何爷爷"]);
function cloneDemoProfile(profile) { return JSON.parse(JSON.stringify(profile)); }
function createDemoMember(id, name, profile, current = false) {
  return { id, name, current, planExplanationGeneratedAt: "preset", faceEnrolled: false, faceUpdatedAt: "", ...cloneDemoProfile(profile) };
}
const defaultMembers = [
  createDemoMember("demo-wang-uncle", "王叔叔", wangUncleDemoProfile, true),
  createDemoMember("demo-he-grandpa", "何爷爷", heGrandpaDemoProfile),
];

const baseMedicineWords = [
  "阿司匹林",
  "阿莫西林",
  "布洛芬",
  "头孢克肟颗粒",
  "降压片",
  "二甲双胍",
  "硝酸甘油",
  "奥美拉唑",
  "缬沙坦",
  "氨氯地平",
  "美托洛尔",
  "辛伐他汀",
  "瑞舒伐他汀",
  "硝苯地平",
  "氯雷他定",
  "孟鲁司特钠",
  "蒲地蓝消炎片",
  "连花清瘟胶囊",
  "感冒灵颗粒",
  "维生素C",
  "葡萄糖酸钙",
  "云南白药",
  "蒙脱石散",
  "开塞露",
  "胰岛素",
];

const baseNameWords = ["王叔叔", "何爷爷", "王爷爷"];
const baseDiseaseWords = ["高血压", "糖尿病", "慢性咽炎", "口腔炎", "冠心病", "胃炎", "哮喘", "失眠", "关节炎", "高血脂", "脑梗恢复期"];
const baseCommonWords = ["饭后", "饭前", "早餐后", "午饭后", "晚饭后", "睡前", "必要时", "按疗程坚持", "每日一次", "每日两次", "外用", "口服"];
const baseUnitWords = ["片", "粒", "包", "瓶", "盒", "袋", "支", "毫升", "克"];

const pinyinCandidates = {
  a: ["阿", "啊"],
  ai: ["爱", "矮"],
  an: ["安", "按"],
  ao: ["奥"],
  ba: ["八", "把"],
  bao: ["包", "保"],
  bei: ["杯", "备"],
  ben: ["本"],
  bing: ["病"],
  bu: ["不", "布"],
  buluofen: ["布洛芬"],
  chen: ["陈"],
  ci: ["次"],
  dai: ["袋"],
  dan: ["单"],
  ding: ["定"],
  er: ["二"],
  fan: ["饭"],
  fang: ["方", "房"],
  fei: ["肺"],
  fen: ["芬", "分"],
  fu: ["服", "复"],
  gan: ["肝", "甘"],
  gao: ["高"],
  gaoxueya: ["高血压"],
  gaoxuezhi: ["高血脂"],
  guan: ["冠", "管"],
  guanxinbing: ["冠心病"],
  hao: ["号", "好"],
  hou: ["后"],
  hua: ["华"],
  ji: ["剂", "急"],
  jian: ["健", "减"],
  jiang: ["降"],
  jiangyapian: ["降压片"],
  jiao: ["胶"],
  jie: ["节"],
  jin: ["今"],
  ke: ["克", "咳"],
  kou: ["口"],
  koufu: ["口服"],
  li: ["李", "粒", "里"],
  lin: ["林"],
  liu: ["刘"],
  luo: ["洛", "罗"],
  ma: ["麻"],
  mei: ["美"],
  meng: ["孟"],
  mi: ["米"],
  nai: ["奶", "耐", "乃"],
  nainai: ["奶奶"],
  niao: ["尿"],
  ou: ["欧"],
  pian: ["片"],
  qian: ["前"],
  qing: ["清"],
  ri: ["日"],
  san: ["三", "散"],
  shan: ["山"],
  shao: ["少"],
  shu: ["叔"],
  shushu: ["叔叔"],
  si: ["司"],
  suan: ["酸"],
  tang: ["糖"],
  tangniaobing: ["糖尿病"],
  teng: ["疼"],
  tou: ["头"],
  toubao: ["头孢"],
  wai: ["外"],
  wang: ["王", "网", "往"],
  wei: ["胃", "维"],
  weiyan: ["胃炎"],
  wu: ["五"],
  xi: ["西", "希"],
  xin: ["心"],
  xiaosuan: ["硝酸"],
  xiaosuanganyou: ["硝酸甘油"],
  xue: ["血"],
  ya: ["压", "牙"],
  yan: ["炎"],
  yao: ["药", "要"],
  ye: ["爷"],
  yeye: ["爷爷"],
  yi: ["一", "医"],
  yidaosu: ["胰岛素"],
  ying: ["应"],
  yong: ["用"],
  you: ["油"],
  zhang: ["张", "章"],
  zhao: ["赵"],
  zhi: ["支", "脂"],
  zhong: ["中"],
  zhou: ["周"],
  zhu: ["主"],
  asipilin: ["阿司匹林"],
  amoxilin: ["阿莫西林"],
  erjiashuanggua: ["二甲双胍"],
  aomeilazuo: ["奥美拉唑"],
};

let members = loadMembers();
let editingId = null;
let draftMemberId = "";
let draftFaceEnrollment = null;
let formDemoProfile = null;
let prescriptionOcrGeneration = 0;
let prescriptionOcrSessionId = "";
let prescriptionOcrPreloadPromise = null;
let prescriptionOcrBusy = false;
let prescriptionOcrDraft = null;
const planEngineStates = new Map();
let takeIndex = 0;
let takeQueue = [];
let pillConfirmed = false;
let doorConfirmed = false;
let loadStep = 0;
let loadItemIndex = 0;
let activeLoadMemberId = "";
let loadDrawerOpen = false;
let loadDoorState = "unknown";
let loadGuideFinished = false;
let loadOpenedItemKey = "";
let loadDoorPollTimer = 0;
let loadDoorPollKey = "";
let loadMedicineBoxSessionId = "";
let loadMedicineBoxPreloadPromise = null;
let loadMedicineBoxReady = false;
let loadMedicineBoxLeaseTimer = 0;
let loadMedicineBoxBusy = false;
let loadWorkflowToken = 0;
let loadCloseAdvanceTimer = 0;
let loadPromptedItemKey = "";
let loadDoorObservedOpen = false;
let loadDoorPollFailures = 0;
let assistantMode = "member";
let reports = loadReports();
let inventoryLedger = loadInventoryLedger();
let completedDoseIds = loadProgress();
let currentTakeDoseId = "";
let activeKeyboardInput = null;
let keyboardMode = "pinyin";
let keyboardLarge = false;
let keyboardScale = 1;
let pinyinBuffer = "";
let homeReminderPage = 0;
let planPage = 0;
let selectedCabinetIndex = 0;
let activeDoseId = "";
let voiceQueueIds = [];
const announcedDoseIds = new Set();
let reminderRuntimeDate = todayKey();
let reminderAudioContext = null;
let reminderAudioReady = false;
let selectedReminderVoiceName = localStorage.getItem(VOICE_TONE_KEY) || REMINDER_VOICE_NAMES[0];
let takeOcrTimer = null;
let flowExitTargetPage = "";
let flowExitAfterSave = null;
let pendingDeleteConfirmAction = null;
let reminderFollowupTimer = null;
let reminderFinalTimer = null;
let reminderAwaitingDoseId = "";
let timedVoiceSequence = false;
let speechRepeatTimer = null;
let lastPageVoiceKey = "";
let pageAnnounceTimer = null;
let announcementGeneration = 0;
let speechVoices = [];
let activePageId = "";
let previousPageId = "home";
let cpuMetricsTimer = 0;
let cpuMetricsRequestToken = 0;
let cpuMetricsRequestPending = false;
let activeAdherenceMemberId = "";
let assistantVoiceState = "idle";
let assistantVoiceTargetId = "";
let assistantVoiceJobId = "";
let assistantOcrBusy = false;
let assistantOcrToken = 0;
let assistantLifecycleSessionId = "";
let assistantLifecyclePreload = null;
let assistantLifecycleReady = false;
let assistantLifecycleLeaseRenewedAt = 0;
let assistantLifecycleLeaseTimer = 0;
let assistantMedicineBoxLeaseTimer = 0;
let faceRecognitionStatus = "idle";

const els = {
  pageTitle: document.querySelector("#pageTitle"),
  pageSub: document.querySelector("#pageSub"),
  brandSub: document.querySelector("#brandSub"),
  cpuStatus: document.querySelector("#cpuStatus"),
  cpuUsageValue: document.querySelector("#cpuUsageValue"),
  cpuCoreCount: document.querySelector("#cpuCoreCount"),
  cpuStatusDot: document.querySelector("#cpuStatusDot"),
  cpuStatusText: document.querySelector("#cpuStatusText"),
  homeVoiceToneSlot: document.querySelector("#homeVoiceToneSlot"),
  nav: document.querySelector(".nav"),
  voiceToneSelect: document.querySelector("#voiceToneSelect"),
  cabinetVoiceVolume: document.querySelector("#cabinetVoiceVolume"),
  cabinetVoiceVolumeValue: document.querySelector("#cabinetVoiceVolumeValue"),
  memberList: document.querySelector("#memberList"),
  medicineList: document.querySelector("#medicineList"),
  prescriptionOcrStatus: document.querySelector("#prescriptionOcrStatus"),
  prescriptionOcrStart: document.querySelector("#prescriptionOcrStart"),
  prescriptionOcrReview: document.querySelector("#prescriptionOcrReview"),
  toast: document.querySelector("#toast"),
  formTitle: document.querySelector("#formTitle"),
  planList: document.querySelector("#planList"),
  cabinetGrid: document.querySelector("#cabinetGrid"),
  reportList: document.querySelector(".report-list"),
  assistantMode: document.querySelector("#assistantMode"),
  assistantChat: document.querySelector("#assistantChat"),
  patientSelector: document.querySelector("#patientSelector"),
  chatMessages: document.querySelector("#chatMessages"),
  chatTitle: document.querySelector("#chatTitle"),
  takeDrug: document.querySelector("#takeDrug"),
  takeHint: document.querySelector("#takeHint"),
  takeState: document.querySelector("#takeState"),
  loadStepTitle: document.querySelector("#loadStepTitle"),
  loadStepText: document.querySelector("#loadStepText"),
  drawerNumber: document.querySelector("#drawerNumber"),
  drawerStatus: document.querySelector("#drawerStatus"),
  loadProgressText: document.querySelector("#loadProgressText"),
  loadProgressFill: document.querySelector("#loadProgressFill"),
  loadDoorState: document.querySelector("#loadDoorState"),
  keyboard: document.querySelector("#touchKeyboard"),
  keyboardTitle: document.querySelector("#keyboardTitle"),
  keyboardPreview: document.querySelector("#keyboardPreview"),
  keyboardQuick: document.querySelector("#keyboardQuick"),
  keyboardKeys: document.querySelector("#keyboardKeys"),
  keyboardModes: document.querySelector("#keyboardModes"),
  keyboardSize: document.querySelector("#keyboardSize"),
  keyboardZoomOut: document.querySelector("#keyboardZoomOut"),
  keyboardZoomIn: document.querySelector("#keyboardZoomIn"),
  keyboardZoomText: document.querySelector("#keyboardZoomText"),
};

const keyLayouts = {
  pinyin: [
    ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
    ["a", "s", "d", "f", "g", "h", "j", "k", "l"],
    ["shift", "z", "x", "c", "v", "b", "n", "m", "退格"],
    ["符", "123", "，", "语音", "。", "中/英", "完成"],
  ],
  abc: [
    ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
    ["a", "s", "d", "f", "g", "h", "j", "k", "l"],
    ["shift", "z", "x", "c", "v", "b", "n", "m", "退格"],
    ["符", "123", ",", "语音", ".", "拼音", "完成"],
  ],
  number: [
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
    ["-", "/", ":", ";", "(", ")", "$", "&", "@", "退格"],
    ["符", "拼音", "，", "语音", "。", "中/英", "完成"],
  ],
  symbol: [
    ["，", "。", "、", "？", "！", "：", "；", "（", "）", "退格"],
    ["@", "#", "%", "&", "+", "-", "*", "/", "=", "清空"],
    ["拼音", "123", "，", "语音", "。", "中/英", "完成"],
  ],
};

function loadMembers() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY));
    return Array.isArray(stored) ? normalizeMembers(stored) : cloneDefaultMembers();
  } catch {
    return cloneDefaultMembers();
  }
}

function loadReports() {
  try {
    const stored = JSON.parse(localStorage.getItem(REPORT_KEY));
    return Array.isArray(stored) ? stored : [];
  } catch {
    return [];
  }
}

function saveReports() {
  localStorage.setItem(REPORT_KEY, JSON.stringify(reports));
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function formatDateTime(date = new Date()) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

function reportSortValue(report) {
  if (report?.timestamp) return Number(report.timestamp);
  const parsed = Date.parse(String(report?.time || "").replace(" ", "T"));
  if (Number.isFinite(parsed)) return parsed;
  return 0;
}

function reportDisplayTime(report) {
  const time = String(report?.time || "");
  if (time.includes("-")) return time;
  return report?.date ? `${report.date} ${time}` : time;
}

function recentReportsChronological(limit = 8) {
  return [...reports]
    .sort((a, b) => reportSortValue(b) - reportSortValue(a))
    .slice(0, limit)
    .sort((a, b) => reportSortValue(a) - reportSortValue(b));
}

function addReport(type, detail, memberName = "系统") {
  const now = new Date();
  reports.unshift({
    id: `r${Date.now()}`,
    time: formatDateTime(now),
    date: todayKey(now),
    timestamp: now.getTime(),
    type,
    detail,
    memberName,
  });
  reports = reports.slice(0, 80);
  saveReports();
  renderReports();
  renderAdherence();
}

function loadInventoryLedger() {
  try {
    const stored = JSON.parse(localStorage.getItem(INVENTORY_LEDGER_KEY));
    return Array.isArray(stored) ? stored : [];
  } catch {
    return [];
  }
}

function saveInventoryLedger() {
  localStorage.setItem(INVENTORY_LEDGER_KEY, JSON.stringify(inventoryLedger.slice(-240)));
}

function currentCabinetState() {
  return {
    selectedCabinetIndex,
    pageId: ["cabinet", "cabinet-detail", "load-guide"].includes(activePageId) ? activePageId : "cabinet",
    members,
    inventoryLedger: inventoryLedger.slice(-240),
    savedAt: Date.now(),
  };
}

function saveCabinetState(source = "manual") {
  try {
    saveMembers();
    saveInventoryLedger();
    localStorage.setItem(CABINET_STATE_KEY, JSON.stringify({ ...currentCabinetState(), source }));
    return true;
  } catch {
    return false;
  }
}

function persistAllLocalState(source = "lifecycle") {
  try {
    if (shouldAskFlowExit()) saveCurrentFlowState(activePageId);
    saveProgress();
    saveReports();
    return saveCabinetState(source);
  } catch {
    return false;
  }
}

function loadCabinetState() {
  try {
    const state = JSON.parse(localStorage.getItem(CABINET_STATE_KEY) || "null");
    return state && typeof state === "object" ? state : null;
  } catch {
    return null;
  }
}

function restoreCabinetState() {
  const state = loadCabinetState();
  if (!state) return;
  if (Array.isArray(state.inventoryLedger)) {
    inventoryLedger = state.inventoryLedger;
    saveInventoryLedger();
  }
  selectedCabinetIndex = Math.min(11, Math.max(0, Number(state.selectedCabinetIndex || 0)));
}

function inventoryKey(member, med) {
  return `${member?.id || "unknown"}::${String(med?.name || "").trim()}`;
}

function recordInventoryOperation(kind, item, quantity, source = "") {
  if (!item?.member || !item?.med) return;
  const amount = Math.max(0, Number(quantity || 0));
  if (!amount) return;
  const key = inventoryKey(item.member, item.med);
  const date = todayKey();
  if (source && inventoryLedger.some((entry) => entry.kind === kind && entry.key === key && entry.date === date && entry.source === source)) return;
  inventoryLedger.push({
    id: `inv${Date.now()}${Math.random().toString(16).slice(2)}`,
    key,
    kind,
    quantity: amount,
    unit: item.med.unit || "",
    source,
    date,
    time: formatDateTime(),
  });
  saveInventoryLedger();
  saveCabinetState(kind);
}

function hasLoadRecord(item) {
  if (!item?.member || !item?.med) return false;
  const key = inventoryKey(item.member, item.med);
  return inventoryLedger.some((entry) => entry.kind === "load" && entry.key === key && numericMedicineAmount(entry.quantity, 0) > 0);
}

function loadedMedicineItems() {
  return allMedicineItems().filter(hasLoadRecord);
}

function loadedCabinetItem(drawer) {
  return loadedMedicineItems().find((item) => item.drawer === Number(drawer)) || null;
}

function isMemberFullyLoaded(member) {
  const items = allMedicineItems().filter((item) => item.member.id === member?.id);
  return items.length > 0 && items.every(hasLoadRecord);
}

function seedCompetitionBaselineLoad(memberIds) {
  const selectedIds = new Set(memberIds);
  allMedicineItems()
    .filter((item) => selectedIds.has(item.member.id))
    .forEach((item) => recordInventoryOperation("load", item, item.med.stock, `competition-reset-baseline:${item.member.id}:${item.drawer}:${item.med.name}`));
}

function loadProgressHistory() {
  try {
    const stored = JSON.parse(localStorage.getItem(PROGRESS_KEY));
    if (stored?.history && typeof stored.history === "object") return stored.history;
    if (stored?.date && Array.isArray(stored.ids)) return { [stored.date]: stored.ids };
    return {};
  } catch {
    return {};
  }
}

function loadProgress() {
  const history = loadProgressHistory();
  const ids = history[todayKey()];
  return new Set(Array.isArray(ids) ? ids : []);
}

function saveProgress() {
  const date = todayKey();
  const history = loadProgressHistory();
  history[date] = [...completedDoseIds];
  localStorage.setItem(PROGRESS_KEY, JSON.stringify({ date, ids: history[date], history }));
}

function todayKey(date = new Date()) {
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
}

function timeToTodayDate(time, base = new Date()) {
  const [hour = 0, minute = 0] = String(time || "00:00").split(":").map(Number);
  const dueAt = new Date(base);
  dueAt.setHours(hour, minute, 0, 0);
  return dueAt;
}

function isPastScheduleRow(row, now = new Date()) {
  return now.getTime() > timeToTodayDate(row?.time, now).getTime();
}

function isFutureScheduleRow(row, now = new Date()) {
  return timeToTodayDate(row?.time, now).getTime() >= now.getTime();
}

function isReminderDueNow(row, now = new Date()) {
  const dueAt = timeToTodayDate(row?.time, now);
  const diff = now.getTime() - dueAt.getTime();
  return diff >= 0 && diff <= REMINDER_GRACE_MS;
}

function resetDailyReminderState() {
  const currentDate = todayKey();
  if (currentDate === reminderRuntimeDate) return;
  reminderRuntimeDate = currentDate;
  completedDoseIds = loadProgress();
  announcedDoseIds.clear();
  activeDoseId = "";
  voiceQueueIds = [];
  renderAll();
}

function canonicalMedicineName(value) {
  const name = String(value || "").trim();
  return name === "口服补液盐散三" ? "口服补液盐散（III）" : name;
}

function normalizeMembers(list) {
  const fallbackSchedule = { wake: "07:00", breakfast: "08:00", lunch: "12:30", dinner: "18:30", sleep: "21:30" };
  const normalized = list.map((member, index) => ({
    ...member,
    id: member.id || `m${Date.now()}${index}`,
    current: Boolean(member.current),
    medicines: Array.isArray(member.medicines)
      ? member.medicines.map(({ time, timing, ...med }) => ({ ...med, name: canonicalMedicineName(med.name) }))
      : [],
    planOverrides: Array.isArray(member.planOverrides)
      ? member.planOverrides.map((plan) => ({
        ...plan,
        medicines: Array.isArray(plan.medicines)
          ? plan.medicines.map((med) => ({ ...med, name: canonicalMedicineName(med.name) }))
          : plan.medicines,
      }))
      : member.planOverrides,
    schedule: { ...fallbackSchedule, ...(member.schedule || {}) },
    faceEnrolled: Boolean(member.faceEnrolled),
    faceUpdatedAt: member.faceUpdatedAt || "",
  }));
  const migrated = normalized.map((member) =>
    (TWO_MEMBER_ROSTER_NAMES.has(member.name) || isWangGrandpaMember(member)) && member.demoProfile
      ? { ...member, planExplanationGeneratedAt: "preset" }
      : member,
  );
  if (migrated.length && !migrated.some((member) => member.current)) migrated[0].current = true;
  return migrated;
}

function cloneDefaultMembers() {
  return JSON.parse(JSON.stringify(defaultMembers));
}

function saveMembers() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(members));
}

function migrateToTwoMemberRoster() {
  if (localStorage.getItem(TWO_MEMBER_ROSTER_MIGRATION_KEY) === "done") return false;
  members = cloneDefaultMembers();
  reports = [];
  inventoryLedger = [];
  completedDoseIds = new Set();
  localStorage.setItem(PROGRESS_KEY, JSON.stringify({ date: todayKey(), ids: [], history: {} }));
  localStorage.removeItem(FLOW_STATE_KEY);
  saveMembers();
  saveReports();
  saveInventoryLedger();
  saveCabinetState("two-member-roster-migration");
  localStorage.setItem(TWO_MEMBER_ROSTER_MIGRATION_KEY, "done");
  return true;
}

function isBuiltInDemoMember(member, id, name) {
  return member?.id === id || (member?.name === name && member?.demoProfile === (id === "demo-wang-uncle" ? "wang-uncle" : "he-grandpa"));
}

function replaceBuiltInDemoProfile(member, profile) {
  const next = cloneDemoProfile(profile);
  return {
    ...member,
    demoProfile: next.demoProfile,
    gender: next.gender,
    age: next.age,
    disease: next.disease,
    schedule: next.schedule,
    medicines: next.medicines,
    planOverrides: next.planOverrides,
    planExplanation: next.planExplanation,
    planExplanationGeneratedAt: "preset",
  };
}

function migrateBuiltInDemoPrescriptions() {
  if (localStorage.getItem(DEMO_PRESCRIPTION_MIGRATION_KEY) === "done") return false;

  const targets = new Set(["demo-wang-uncle", "demo-he-grandpa"]);
  let migrated = false;
  members = members.map((member) => {
    if (isBuiltInDemoMember(member, "demo-wang-uncle", "王叔叔")) {
      migrated = true;
      targets.add(member.id);
      return replaceBuiltInDemoProfile(member, wangUncleDemoProfile);
    }
    if (isBuiltInDemoMember(member, "demo-he-grandpa", "何爷爷")) {
      migrated = true;
      targets.add(member.id);
      return replaceBuiltInDemoProfile(member, heGrandpaDemoProfile);
    }
    return member;
  });

  if (migrated) {
    // Old load/take events reference the old medicine names. Replace records
    // for these two demo profiles only, then seed their current demo stock.
    // Records of Wang Grandpa and manually added members are preserved.
    inventoryLedger = inventoryLedger.filter((entry) => !targets.has(String(entry?.key || "").split("::")[0]));
    seedCompetitionBaselineLoad([...targets]);
    saveInventoryLedger();
    saveCabinetState("demo-prescription-migration-20260729");
  }
  localStorage.setItem(DEMO_PRESCRIPTION_MIGRATION_KEY, "done");
  return migrated;
}

function showToast(text) {
  els.toast.textContent = text;
  els.toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => els.toast.classList.remove("show"), 2200);
}

function selectedReminderVoice() {
  const voices = refreshSpeechVoices();
  if (!voices.length) return null;
  const target = selectedReminderVoiceName;
  const normalizedTarget = target.toLowerCase();
  const keywords = REMINDER_VOICE_KEYWORDS[target] || [];
  return (
    voices.find((voice) => voice.name === target) ||
    voices.find((voice) => voice.name.includes(target)) ||
    voices.find((voice) => target.includes(voice.name)) ||
    voices.find((voice) => voice.name.toLowerCase().includes(normalizedTarget.split(" ")[0])) ||
    voices.find((voice) => keywords.some((keyword) => voice.name.toLowerCase().includes(keyword.toLowerCase()))) ||
    voices.find((voice) => voice.lang?.toLowerCase().startsWith("zh") && /microsoft|chinese|中文|普通话/i.test(voice.name)) ||
    voices.find((voice) => voice.lang?.toLowerCase().startsWith("zh")) ||
    null
  );
}

function refreshSpeechVoices() {
  speechVoices = [];
  return speechVoices;
}

function renderVoiceToneSelect(extraClass = "") {
  const options = REMINDER_VOICE_NAMES.map(
    (name) => `<option value="${escapeHtml(name)}" ${name === selectedReminderVoiceName ? "selected" : ""}>${escapeHtml(REMINDER_VOICE_LABELS[name] || name)}</option>`,
  ).join("");
  return `<label class="voice-tone-control ${extraClass}" data-voice-tone-control>
    <span>音色</span>
    <select class="voice-tone-select" aria-label="语音播报音色">${options}</select>
  </label>`;
}

function syncVoiceToneControls() {
  document.querySelectorAll(".voice-tone-select").forEach((select) => {
    select.value = selectedReminderVoiceName;
  });
}

function renderHeaderVoiceTone(pageId = activePageId || "home") {
  const slot = els.homeVoiceToneSlot || document.querySelector("#homeVoiceToneSlot");
  if (!slot) return;
  slot.hidden = true;
  slot.innerHTML = "";
}

function speakOnce(text, onDone = null) {
  const finish = () => onDone?.();
  if (!text || !window.K1StaticVoice) return finish();
  window.K1StaticVoice.playText(text).then(finish, finish);
}

async function requestK1VoiceCache(cacheId, shouldPlay = true) {
  // Static assets are selected from text and never use the removed cache API.
  return { id: String(cacheId || ""), play: Boolean(shouldPlay), state: "static_only" };
}

async function playK1VoiceCache(cacheId, repeat = 1) {
  return requestK1VoiceCache(cacheId, false);
}

const WANG_GRANDPA_AUDIO_ASSETS = Object.freeze({
  take_alarm: "assets/audio/wang-grandpa/take_alarm.wav",
  take_face: "assets/audio/wang-grandpa/take_face.wav",
  take_confirm: "assets/audio/wang-grandpa/take_confirm.wav",
  take_drawer_7: "assets/audio/wang-grandpa/take_drawer_7.wav",
  take_drawer_8: "assets/audio/wang-grandpa/take_drawer_8.wav",
  take_count_correct: "assets/audio/wang-grandpa/take_count_correct.wav",
  take_short_1: "assets/audio/wang-grandpa/take_short_1.wav",
  take_short_2: "assets/audio/wang-grandpa/take_short_2.wav",
  take_short_3: "assets/audio/wang-grandpa/take_short_3.wav",
  take_short_4: "assets/audio/wang-grandpa/take_short_4.wav",
  take_extra_1: "assets/audio/wang-grandpa/take_extra_1.wav",
  take_extra_2: "assets/audio/wang-grandpa/take_extra_2.wav",
  take_extra_3: "assets/audio/wang-grandpa/take_extra_3.wav",
  take_extra_4: "assets/audio/wang-grandpa/take_extra_4.wav",
  take_clear_taken: "assets/audio/wang-grandpa/take_clear_taken.wav",
  take_clear_closed: "assets/audio/wang-grandpa/take_clear_closed.wav",
  take_clear_both: "assets/audio/wang-grandpa/take_clear_both.wav",
  take_next: "assets/audio/wang-grandpa/take_next.wav",
  take_complete: "assets/audio/wang-grandpa/take_complete.wav",
  load_prompt_7: "assets/audio/wang-grandpa/load_prompt_7.wav",
  load_success_7: "assets/audio/wang-grandpa/load_success_7.wav",
  load_put_7: "assets/audio/wang-grandpa/load_put_7.wav",
  load_error_7: "assets/audio/wang-grandpa/load_error_7.wav",
  load_closed_7: "assets/audio/wang-grandpa/load_closed_7.wav",
  load_prompt_8: "assets/audio/wang-grandpa/load_prompt_8.wav",
  load_success_8: "assets/audio/wang-grandpa/load_success_8.wav",
  load_put_8: "assets/audio/wang-grandpa/load_put_8.wav",
  load_error_8: "assets/audio/wang-grandpa/load_error_8.wav",
  load_closed_8: "assets/audio/wang-grandpa/load_closed_8.wav",
  load_prompt_9: "assets/audio/wang-grandpa/load_prompt_9.wav",
  load_success_9: "assets/audio/wang-grandpa/load_success_9.wav",
  load_put_9: "assets/audio/wang-grandpa/load_put_9.wav",
  load_error_9: "assets/audio/wang-grandpa/load_error_9.wav",
  load_closed_9: "assets/audio/wang-grandpa/load_closed_9.wav",
  load_complete: "assets/audio/wang-grandpa/load_complete.wav",
});

const WANG_GRANDPA_MEDICINES = Object.freeze({
  "小柴胡颗粒": 7,
  "复合维生素B": 8,
  "复合维生素B片": 8,
  "盐酸西替利嗪片": 9,
});
let wangGrandpaAudioPreloaded = false;
let activeWangGrandpaAudio = null;
let activeWangGrandpaAudioResolve = null;
let wangGrandpaClearanceMessage = "";
let wangGrandpaClearanceAt = 0;
const wangGrandpaAudioPool = new Map();

function normalizedMedicineName(value) {
  return String(value || "")
    .replace(/\s+/g, "")
    .replace(/[()（）]/g, "")
    .replace(/Ⅲ/g, "III")
    .replace(/口服补液盐散三/g, "口服补液盐散III")
    .replace(/銀/g, "银")
    .toLowerCase();
}

function isWangGrandpaMember(member) {
  return String(member?.name || "").trim() === "王爷爷";
}


function isWangGrandpaLoadItem(item) {
  const expectedDrawer = wangGrandpaExpectedDrawer(item);
  return expectedDrawer > 0 && Number(item?.drawer) === expectedDrawer;
}


function isWangGrandpaTakeItem(item) {
  const expectedDrawer = wangGrandpaExpectedDrawer(item);
  return expectedDrawer > 0 && Number(item?.drawer) === expectedDrawer && isWangGrandpaTakeRow(reminderById(currentTakeDoseId || activeDoseId));
}

function wangGrandpaTakeDrawerAudioKey(item) {
  const expectedDrawer = wangGrandpaExpectedDrawer(item);
  if (![7, 8].includes(expectedDrawer) || Number(item?.drawer) !== expectedDrawer) return "";
  return `take_drawer_${expectedDrawer}`;
}

function wangGrandpaLoadAudioKey(item, stage) {
  if (!isWangGrandpaLoadItem(item)) return "";
  return `load_${stage}_${item.drawer}`;
}

function preloadWangGrandpaAudio() {
  // Legacy assets are deliberately retained on disk but are no longer preloaded.
  wangGrandpaAudioPreloaded = true;
}

function stopWangGrandpaAudio() {
  window.K1StaticVoice?.stop();
  const audio = activeWangGrandpaAudio;
  const resolve = activeWangGrandpaAudioResolve;
  activeWangGrandpaAudio = null;
  activeWangGrandpaAudioResolve = null;
  if (audio) {
    audio.onended = null;
    audio.onerror = null;
    audio.pause();
    audio.currentTime = 0;
  }
  if (resolve) resolve(false);
}

function playWangGrandpaAudio(key, fallbackText = "") {
  void key;
  return fallbackText && window.K1StaticVoice ? window.K1StaticVoice.playText(fallbackText) : Promise.resolve(false);
}

function speak(text) {
  clearTimeout(speechRepeatTimer);
  void window.K1StaticVoice?.playText(text);
}

function speakRepeated(text, repeat = 1) {
  void repeat;
  speak(text);
}

function getReminderAudioContext() {
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return null;
    if (!reminderAudioContext) reminderAudioContext = new AudioContext();
    reminderAudioContext.resume?.();
    return reminderAudioContext;
  } catch {
    return null;
  }
}

function warmReminderAudio() {
  getReminderAudioContext();
  reminderAudioReady = true;
}

function playReminderTone() {
  // The approved voice package is the only audible output for this workflow.
}

function stopVoiceAnnouncements({ clearPageKey = false } = {}) {
  announcementGeneration += 1;
  clearTimeout(pageAnnounceTimer);
  pageAnnounceTimer = null;
  if (clearPageKey) lastPageVoiceKey = "";
  window.K1StaticVoice?.stop();
}

function announce(text, withTone = false, repeat = 1, cacheId = "") {
  void withTone;
  void repeat;
  void cacheId;
  const voice = window.K1StaticVoice;
  if (!voice) {
    showToast("语音暂不可用，请按屏幕提示操作");
    return Promise.resolve(false);
  }
  const token = ++announcementGeneration;
  clearTimeout(pageAnnounceTimer);
  pageAnnounceTimer = null;
  voice.stop();
  return voice.supportsText(text).then((supported) => {
    if (token !== announcementGeneration) return false;
    if (!supported) {
      return false;
    }
    return voice.playText(text);
  }).catch(() => {
    if (token === announcementGeneration) showToast("语音播放失败，请按屏幕提示操作");
    return false;
  });
}

function announcePageOnce(pageId, text, withTone = false, repeat = 1, cacheId = "") {
  const message = String(text || "").trim();
  if (!message) return;
  const key = `${pageId}:${message}`;
  if (lastPageVoiceKey === key) return;
  lastPageVoiceKey = key;
  clearTimeout(pageAnnounceTimer);
  const playAnnouncement = () => {
    if (activePageId !== pageId) return;
    announce(message, withTone, repeat, cacheId);
  };
  if (PAGE_ANNOUNCE_DELAY_MS > 0) {
    pageAnnounceTimer = window.setTimeout(playAnnouncement, PAGE_ANNOUNCE_DELAY_MS);
  } else {
    playAnnouncement();
  }
}

function activeMember() {
  return members.find((member) => member.current) || members[0];
}

function memberForActiveDose() {
  const row = reminderById(currentTakeDoseId || activeDoseId);
  return row?.member || activeMember();
}

function updateMemberSecurity(memberId, patch) {
  if (!memberId) return null;
  let updated = null;
  members = members.map((member) => {
    if (member.id !== memberId) return member;
    updated = { ...member, ...patch };
    return updated;
  });
  if (updated) {
    saveMembers();
    saveCabinetState("security");
    renderMembers();
  }
  return updated;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function unique(items) {
  return [...new Set(items.filter(Boolean).map((item) => String(item).trim()).filter(Boolean))];
}

function displayDrawer(item) {
  return item ? `${item.drawer}号柜` : "未分配";
}

function numericMedicineAmount(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function ledgerQuantity(kind, item) {
  if (!item?.member || !item?.med) return 0;
  const key = inventoryKey(item.member, item.med);
  return inventoryLedger
    .filter((entry) => entry.kind === kind && entry.key === key)
    .reduce((sum, entry) => sum + numericMedicineAmount(entry.quantity, 0), 0);
}

function completedQuantityForMedicine(member, med) {
  return todaySchedule()
    .filter((row) => row.done && row.member.id === member.id)
    .reduce((sum, row) => {
      const rowAmount = row.meds
        .filter((rowMed) => rowMed.name === med.name)
        .reduce((medSum, rowMed) => medSum + numericMedicineAmount(rowMed.amount, 0), 0);
      return sum + rowAmount;
    }, 0);
}

function cabinetInventoryForItem(item) {
  if (!item?.med) return { loaded: 0, taken: 0, remaining: 0, percent: 0 };
  const member = item.member;
  const med = item.med;
  const stockFromMember = numericMedicineAmount(med.stock, 0);
  const loadedFromFlow = ledgerQuantity("load", item);
  const loaded = loadedFromFlow || stockFromMember;
  const takenFromFlow = ledgerQuantity("take", item);
  const takenFromSchedule = completedQuantityForMedicine(member, med);
  const taken = Math.max(takenFromFlow, takenFromSchedule);
  const remaining = Math.max(0, loaded - taken);
  const percent = loaded ? Math.min(100, Math.round((taken / loaded) * 100)) : 0;
  return { loaded, taken, remaining, percent };
}

function doseId(member, slot, time) {
  return `${todayKey()}-${member.id}-${time}-${slot}`;
}

function completeDose(row, source = "服药完成") {
  if (!row?.id || completedDoseIds.has(row.id)) return false;
  clearReminderEscalation(row.id);
  completedDoseIds.add(row.id);
  saveProgress();
  addReport(source, `${row.time} ${row.slot}：${row.meds.map((med) => `${med.name}${med.amount}${med.unit}`).join("、")} 已完成`, row.member.name);
  return true;
}

function medicineNote(med) {
  return String(med?.note || "").trim();
}

function baseMedicineSlots(member) {
  return {
    wake: { after: "起床后", before: "起床后", time: member.schedule?.wake || "07:00" },
    breakfast: { after: "早餐后", before: "早餐前", time: member.schedule?.breakfast || "08:00" },
    lunch: { after: "午饭后", before: "午饭前", time: member.schedule?.lunch || "12:30" },
    afternoon: { after: "下午用药", before: "下午用药", time: "15:00" },
    dinner: { after: "晚饭后", before: "晚饭前", time: member.schedule?.dinner || "18:30" },
    sleep: { after: "睡前", before: "睡前", time: member.schedule?.sleep || "21:30" },
  };
}

function slotKeysForMedicine(med) {
  const note = medicineNote(med);
  const count = Math.max(1, Math.min(6, Number(med.times || 1)));
  if (count === 1) {
    if (/睡前|睡觉前|临睡/i.test(note)) return ["sleep"];
    if (/午饭|午餐/i.test(note)) return ["lunch"];
    if (/晚饭|晚餐/i.test(note)) return ["dinner"];
    return ["breakfast"];
  }
  if (count === 2) return ["breakfast", "dinner"];
  if (count === 3) return ["breakfast", "lunch", "dinner"];
  if (count === 4) return ["breakfast", "lunch", "dinner", "sleep"];
  if (count === 5) return ["wake", "breakfast", "lunch", "dinner", "sleep"];
  return ["wake", "breakfast", "lunch", "afternoon", "dinner", "sleep"];
}

function generatedMedicineSlotLabel(base, med, count) {
  const note = medicineNote(med);
  if (/疼痛时|胸闷时|必要时|按需/i.test(note) && count === 1) return "症状出现时服用";
  if (/睡前|睡觉前|临睡/i.test(note) || base.after === "睡前") return "睡前";
  if (/餐前|饭前/i.test(note)) return base.before;
  return base.after;
}

function generatedMedicineSlots(member, med) {
  const slots = baseMedicineSlots(member);
  const keys = slotKeysForMedicine(med);
  return keys.map((key) => {
    const base = slots[key] || slots.breakfast;
    return [generatedMedicineSlotLabel(base, med, keys.length), base.time];
  });
}

function medicineTimeFallback(member, med) {
  return member.schedule?.breakfast || "08:00";
}

function medicineSlotLabel(med) {
  return "服药时间";
}

function pushScheduleGroup(rows, groups, member, slot, time, med) {
  const key = `${member.id}-${time}-${slot}`;
  if (!groups.has(key)) groups.set(key, { id: doseId(member, slot, time), member, slot, time, meds: [] });
  groups.get(key).meds.push(med);
}

function todaySchedule() {
  const rows = [];
  members.forEach((member) => {
    const groups = new Map();
    const planRows = Array.isArray(member.planOverrides)
      ? member.planOverrides.filter((plan) =>
        String(plan?.time || "").trim()
        && String(plan?.slot || "").trim()
        && Array.isArray(plan?.medicines)
        && plan.medicines.some((med) => String(med?.name || "").trim())
      )
      : [];
    if (planRows.length) {
      planRows.forEach((plan) => {
        plan.medicines.forEach((planMed) => {
          const name = canonicalMedicineName(planMed?.name || "");
          if (!name) return;
          const memberMed = (member.medicines || []).find((med) =>
            normalizedMedicineName(med.name) === normalizedMedicineName(name)
          );
          const med = {
            ...memberMed,
            ...planMed,
            name,
            amount: planMed?.amount ?? memberMed?.amount ?? "",
            unit: planMed?.unit ?? memberMed?.unit ?? "",
          };
          pushScheduleGroup(rows, groups, member, String(plan.slot).trim(), String(plan.time).trim(), med);
        });
      });
    } else {
      (member.medicines || []).forEach((med) => {
        const generatedSlots = generatedMedicineSlots(member, med);
        if (generatedSlots.length) generatedSlots.forEach(([slot, time]) => pushScheduleGroup(rows, groups, member, slot, time, med));
        if (!generatedSlots.length) pushScheduleGroup(rows, groups, member, medicineSlotLabel(med), medicineTimeFallback(member, med), med);
      });
    }
    groups.forEach((row) => rows.push({ ...row, done: completedDoseIds.has(row.id) }));
  });
  return rows.sort((a, b) => a.time.localeCompare(b.time) || a.member.name.localeCompare(b.member.name, "zh-CN"));
}
function nextReminder() {
  const rows = todaySchedule();
  const now = new Date();
  return rows.find((row) => !row.done && isFutureScheduleRow(row, now)) || null;
}

function reminderById(id) {
  return todaySchedule().find((row) => row.id === id) || null;
}

function voiceTextForRow(row) {
  if (!row) return "暂无待播报用药。";
  return `${row.member.name}，${row.time}${row.slot}请按时服药：${row.meds.map((med) => `${med.name}${med.amount}${med.unit}`).join("，")}。`;
}

function homeVoiceRows() {
  const now = new Date();
  return todaySchedule().map((row) => {
    const overdue = !row.done && isPastScheduleRow(row, now);
    return {
      ...row,
      overdue,
      text: `${row.member.name}，${row.time}${row.slot}请按时服药：${row.meds.map((med) => `${med.name}${med.amount}${med.unit}`).join("，")}。`,
      status: row.done ? "已服用" : overdue ? "未服用" : "未到时间",
    };
  });
}

function progressVoiceTextForRows(member, rows) {
  return progressVoiceLinesForRows(member, rows).join("。");
}

function progressVoiceLinesForRows(member, rows) {
  if (!member) return ["暂无成员，无法播报今日服用进度"];
  if (!rows.length) return [`成员：${member.name}`, "今日服用进度：暂无服药安排"];
  const doneRows = rows.filter((row) => row.done);
  const pendingRows = rows.filter((row) => !row.done);
  const doneText = doneRows.length ? doneRows.map((row) => `${row.time}${row.slot}`).join("，") : "目前还没有已完成的服药记录";
  const pendingText = pendingRows.length
    ? pendingRows.map((row) => `${row.time}${row.slot}，${row.meds.map((med) => `${med.name}${med.amount}${med.unit}`).join("，")}`).join("；")
    : "今日服用进度已全部完成";
  return [
    `成员：${member.name}`,
    `今日服用进度：共 ${rows.length} 项`,
    `已完成：${doneRows.length} 项`,
    `未完成：${pendingRows.length} 项`,
    `已完成记录：${doneText}`,
    `待完成：${pendingText}`,
  ];
}

function progressVoiceHtmlForRows(member, rows) {
  return progressVoiceLinesForRows(member, rows)
    .map((line, index) => `<p class="progress-voice-line ${index === 0 ? "primary-line" : ""}">${escapeHtml(line)}</p>`)
    .join("");
}

function nextVoiceBroadcastRow() {
  const now = new Date();
  return nextReminder() || todaySchedule().find((row) => !row.done && isPastScheduleRow(row, now)) || todaySchedule()[0] || null;
}

function memberVoiceBroadcastRow(member) {
  if (!member?.id) return null;
  const now = new Date();
  const rows = todaySchedule().filter((row) => row.member.id === member.id);
  return (
    rows.find((row) => !row.done && isFutureScheduleRow(row, now)) ||
    rows.find((row) => !row.done && isPastScheduleRow(row, now)) ||
    rows.find((row) => !row.done) ||
    rows[0] ||
    null
  );
}

function alarmVoiceTextForRow(row) {
  if (!row) return "暂无待播报用药。";
  const medicines = row.meds.map((med) => `${med.name}${med.amount}${med.unit}`).join("，");
  return `${row.member.name}，该服药了。${row.time}${row.slot}，请来到药盒前。本次药物：${medicines}。`;
}

function sameTimeVoiceRows(member, targetRow = null, options = {}) {
  const memberOnly = Boolean(options.memberOnly && member?.id);
  const pending = todaySchedule().filter((row) => !row.done && (!memberOnly || row.member.id === member.id));
  const target = targetRow ? pending.find((row) => row.id === targetRow.id) : null;
  const preferred = target || (member ? pending.find((row) => row.member.id === member.id) : null);
  const first = preferred || pending[0] || null;
  if (!first) return [];
  const rows = pending.filter((row) => row.time === first.time && row.slot === first.slot);
  return rows.sort((a, b) => {
    if (preferred && a.member.id === preferred.member.id) return -1;
    if (preferred && b.member.id === preferred.member.id) return 1;
    return a.member.name.localeCompare(b.member.name, "zh-CN");
  });
}

function clearReminderEscalation(doseId = "") {
  if (!doseId || reminderAwaitingDoseId === doseId) {
    clearTimeout(reminderFollowupTimer);
    clearTimeout(reminderFinalTimer);
    reminderFollowupTimer = null;
    reminderFinalTimer = null;
    reminderAwaitingDoseId = "";
  }
}

function isReminderStillWaiting(doseId) {
  return reminderAwaitingDoseId === doseId && activeDoseId === doseId && !completedDoseIds.has(doseId);
}

function scheduleNoResponseReminder(row) {
  if (!row?.id) return;
  clearReminderEscalation();
  reminderAwaitingDoseId = row.id;
  reminderFollowupTimer = window.setTimeout(() => {
    const current = reminderById(row.id);
    if (!current || !isReminderStillWaiting(row.id)) return;
    announce(alarmVoiceTextForRow(current), true, 1, "medicine_reminder_example");
    reminderFinalTimer = window.setTimeout(() => {
      const latest = reminderById(row.id);
      if (!latest || !isReminderStillWaiting(row.id)) return;
      addReport("服药未响应", `${latest.time} ${latest.slot} 提醒两轮后仍未操作，已停止再次播报`, latest.member.name);
      reminderAwaitingDoseId = "";
      showToast("已记录未响应日志");
    }, 3 * 60 * 1000);
  }, 3 * 60 * 1000);
}

function markReminderOperated() {
  clearReminderEscalation(activeDoseId);
}

function beginVoiceSequence(member, targetRow = null, options = {}) {
  const rows = sameTimeVoiceRows(member, targetRow, { memberOnly: options.memberOnly });
  if (!rows.length) return showToast("暂无待播报用药");
  voiceQueueIds = rows.map((row) => row.id);
  timedVoiceSequence = Boolean(options.markAnnounced);
  if (options.markAnnounced) rows.forEach((row) => announcedDoseIds.add(row.id));
  activeDoseId = voiceQueueIds[0];
  const row = reminderById(activeDoseId);
  setPage("alarm");
  announcePageOnce("alarm", alarmVoiceTextForRow(row), true, 1, "medicine_reminder_example");
  if (timedVoiceSequence) scheduleNoResponseReminder(row);
  if (!reminderAudioReady) showToast("如未听到声音，请先点击页面一次后再点播报");
}

function advanceVoiceSequence(completedId) {
  clearReminderEscalation(completedId);
  voiceQueueIds = voiceQueueIds.filter((id) => id !== completedId && !completedDoseIds.has(id));
  activeDoseId = voiceQueueIds[0] || "";
  if (!activeDoseId) {
    timedVoiceSequence = false;
    return false;
  }
  const row = reminderById(activeDoseId);
  renderAll();
  setPage("alarm");
  announcePageOnce("alarm", alarmVoiceTextForRow(row), true, 1, "medicine_reminder_example");
  if (timedVoiceSequence) scheduleNoResponseReminder(row);
  showToast(`请继续为 ${row.member.name} 完成本时段用药`);
  return true;
}

function currentReminderPageRows() {
  const rows = todaySchedule();
  const pageSize = 3;
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  homeReminderPage = Math.min(homeReminderPage, pageCount - 1);
  return { rows, visible: rows.slice(homeReminderPage * pageSize, homeReminderPage * pageSize + pageSize), pageCount };
}

function syncHomeReminderPageToCurrent() {
  const currentIndex = members.findIndex((member) => member.current);
  if (currentIndex >= 0) homeReminderPage = currentIndex;
}

function currentHomeMemberProgress() {
  const pageCount = Math.max(1, members.length);
  homeReminderPage = Math.min(homeReminderPage, pageCount - 1);
  const member = members[homeReminderPage] || members[0] || null;
  const rows = member ? todaySchedule().filter((row) => row.member.id === member.id) : [];
  return { member, rows, pageCount };
}

function dynamicWords() {
  const memberNames = members.map((member) => member.name);
  const diseases = members.flatMap((member) => String(member.disease || "").split(/[、,，\s]+/));
  const meds = members.flatMap((member) => (member.medicines || []).map((med) => med.name));
  return unique([...baseNameWords, ...baseMedicineWords, ...baseDiseaseWords, ...baseCommonWords, ...baseUnitWords, ...memberNames, ...diseases, ...meds]);
}

function ensureKeyboardModes() {
}

function inputLabel(input) {
  return input.closest("label")?.childNodes?.[0]?.textContent?.trim() || input.placeholder || "输入";
}

function quickList(input) {
  const memberNames = members.map((member) => member.name);
  const memberTimeWords = memberNames.map((name) => `${name}的服药时间`);
  const medNames = unique([...baseMedicineWords, ...members.flatMap((member) => (member.medicines || []).map((med) => med.name))]);
  const diseases = unique([...baseDiseaseWords, ...members.flatMap((member) => String(member.disease || "").split(/[、,，\s]+/))]);
  if (input.id === "assistantQuickInput" || input.id === "chatInput") {
    return [
      "也可文字或语音咨询",
      "这是什么药？",
      "什么时候吃？",
      "漏服怎么办？",
      "能和其他药同吃吗？",
      "帮我看一下用药风险",
      "查询当前患者服药时间",
      ...memberTimeWords,
    ];
  }
  if (input.id === "memberName") return sortWords(unique([...memberNames, ...baseNameWords])).slice(0, 12);
  if (input.id === "memberDisease") return sortWords(diseases).slice(0, 12);
  if (input.dataset.med === "name") return sortWords(medNames).slice(0, 12);
  if (input.dataset.med === "unit") return sortWords(baseUnitWords);
  if (input.dataset.med === "timing") return sortWords(["早餐后", "午饭后", "晚饭后", "饭后", "饭前", "睡前", "必要时"]);
  return sortWords([...baseCommonWords, ...memberTimeWords, "也可文字或语音咨询"]);
}

function wordRank(word, query = "") {
  const text = String(word || "");
  let score = 0;
  const activeContext = activeKeyboardInput;
  if (activeContext?.id === "memberName" && members.some((member) => member.name === text)) score -= 30;
  if (activeContext?.dataset?.med === "name" && members.some((member) => (member.medicines || []).some((med) => med.name === text))) score -= 30;
  if (activeContext?.id === "memberDisease" && members.some((member) => String(member.disease || "").includes(text))) score -= 30;
  if (members.some((member) => member.name === text)) score -= 20;
  if (members.some((member) => (member.medicines || []).some((med) => med.name === text))) score -= 20;
  if (query && spellHint(text).startsWith(query)) score -= 12;
  if (query && text.includes(query)) score -= 4;
  return score;
}

function sortWords(words, query = "") {
  return unique(words).sort((a, b) => wordRank(a, query) - wordRank(b, query) || spellHint(a).localeCompare(spellHint(b)) || a.localeCompare(b, "zh-Hans-CN"));
}

function isNumericInput(input) {
  return input.inputMode === "numeric" || input.id === "memberAge" || input.dataset.med === "stock" || input.dataset.med === "amount";
}

function renderKeyboard(input) {
  ensureKeyboardModes();
  if (isNumericInput(input) && keyboardMode !== "pinyin") keyboardMode = "number";
  const layout = keyboardMode === "quick" ? [] : keyLayouts[keyboardMode] || keyLayouts.pinyin;
  const isAssistantInput = input.id === "assistantQuickInput" || input.id === "chatInput";
  els.keyboardTitle.textContent = isAssistantInput ? "输入" : keyboardMode === "quick" ? "常用" : inputLabel(input);
  els.keyboardPreview.textContent = pinyinBuffer ? `拼音：${pinyinBuffer}` : input.value || "";
  if (els.keyboardZoomText) els.keyboardZoomText.textContent = `${Math.round(keyboardScale * 100)}%`;
  els.keyboard.style.setProperty("--keyboard-scale", keyboardScale);
  updateKeyboardMetrics();
  els.keyboardModes.querySelectorAll("button").forEach((button) => button.classList.toggle("active", button.dataset.mode === keyboardMode));
  const showQuickList = keyboardMode === "quick" || Boolean(pinyinBuffer);
  els.keyboard.classList.toggle("quick-mode", showQuickList);
  els.keyboard.classList.toggle("assistant-keyboard", isAssistantInput);
  const candidates = pinyinBuffer ? candidateWords(pinyinBuffer) : quickList(input);
  els.keyboardQuick.innerHTML = showQuickList ? candidates.map((word) => `<button type="button" data-word="${escapeHtml(word)}">${escapeHtml(word)}</button>`).join("") : "";
  els.keyboardKeys.classList.toggle("numeric", keyboardMode === "number");
  els.keyboardKeys.classList.toggle("sogou", keyboardMode !== "number");  if (keyboardMode === "quick") {
    els.keyboardKeys.innerHTML = "";
    return;
  }
  els.keyboardKeys.innerHTML = layout.map((row) => `<div class="keyboard-row">${row.map(renderKeyButton).join("")}</div>`).join("");
}

function renderKeyButton(key) {
  const value = Array.isArray(key) ? key[0] : key;
  const cls =
    value === "清空"
      ? "danger key-special"
      : value === "完成" || value === "拼音" || value === "中/英" || value === "123" || value === "符" || value === "shift" || value === "语音"
        ? "action key-special"
        : value === "，" || value === "。" || value === "," || value === "."
          ? "punct-key"
          : "letter-key";
  const label = value === "shift" ? "⇧" : value === "退格" ? "删除" : value === "完成" ? "↵" : value;
  return `<button class="${cls}" type="button" data-key="${escapeHtml(value)}"><b>${escapeHtml(label)}</b></button>`;
}

function candidateWords(value) {
  const key = String(value || "").toLowerCase();
  const exact = pinyinCandidates[key] || [];
  const starts = Object.entries(pinyinCandidates)
    .filter(([itemKey]) => itemKey.startsWith(key) && itemKey !== key)
    .flatMap(([, words]) => words);
  const wordStarts = dynamicWords().filter((word) => word.toLowerCase().includes(key) || spellHint(word).startsWith(key));
  const fallback = key ? [key] : [];
  return sortWords([...exact, ...starts, ...wordStarts, ...fallback], key).slice(0, 10);
}

function spellHint(word) {
  const hints = {
    王叔叔: "wangshushu",
    何爷爷: "heyeye",
    王爷爷: "wangyeye",
    阿司匹林: "asipilin",
    阿莫西林: "amoxilin",
    布洛芬: "buluofen",
    头孢克肟颗粒: "toubaokewukeli",
    降压片: "jiangyapian",
    二甲双胍: "erjiashuanggua",
    硝酸甘油: "xiaosuanganyou",
    奥美拉唑: "aomeilazuo",
    高血压: "gaoxueya",
    糖尿病: "tangniaobing",
    冠心病: "guanxinbing",
    慢性咽炎: "manxingyanyan",
  };
  return hints[word] || "";
}

function openKeyboard(input) {
  if (input.readOnly || input.disabled || input.type === "time") return;
  activeKeyboardInput = input;
  pinyinBuffer = "";
  keyboardMode = isNumericInput(input) ? "number" : "pinyin";
  keyboardLarge = false;
  els.keyboard.classList.remove("large");
  if (els.keyboardSize) els.keyboardSize.textContent = "放大";
  keyboardScale = 1.02;
  renderKeyboard(input);
  els.keyboard.classList.add("show");
  document.body.classList.add("keyboard-open");
  updateKeyboardMetrics();
  setTimeout(() => {
    updateKeyboardMetrics();
    keepInputAboveKeyboard(input);
  }, 30);
}

function keepInputAboveKeyboard(input) {
  const keyboardRect = els.keyboard.getBoundingClientRect();
  const inputRect = input.getBoundingClientRect();
  const overlap = inputRect.bottom + 14 - keyboardRect.top;
  if (overlap <= 0) return;
  const scroller = input.closest(".form-scroll, .chat-messages, .content") || document.scrollingElement;
  if (scroller) scroller.scrollTop += overlap + 26;
  requestAnimationFrame(() => {
    const nextInputRect = input.getBoundingClientRect();
    const nextKeyboardRect = els.keyboard.getBoundingClientRect();
    if (nextInputRect.bottom + 14 > nextKeyboardRect.top && scroller) scroller.scrollTop += nextInputRect.bottom + 14 - nextKeyboardRect.top;
  });
}

function updateKeyboardMetrics() {
  window.requestAnimationFrame?.(() => {
    const height = els.keyboard?.classList.contains("show") ? els.keyboard.offsetHeight : 0;
    document.querySelector(".device")?.style.setProperty("--keyboard-height", `${height || 228}px`);
  });
}
function closeKeyboard() {
  if (pinyinBuffer) flushPinyinBuffer();
  els.keyboard.classList.remove("show");
  document.body.classList.remove("keyboard-open");
  updateKeyboardMetrics();
  activeKeyboardInput = null;
  pinyinBuffer = "";
}

function writeKeyboardText(text) {
  if (!activeKeyboardInput) return;
  const input = activeKeyboardInput;
  if (isNumericInput(input) && !/^\d+$/.test(text)) return;
  insertIntoActiveInput(text);
  renderKeyboard(input);
}

function insertIntoActiveInput(text) {
  if (!activeKeyboardInput) return;
  const input = activeKeyboardInput;
  const maxLength = Number(input.getAttribute("maxlength") || 0);
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? input.value.length;
  let next = input.value.slice(0, start) + text + input.value.slice(end);
  if (maxLength) next = next.slice(0, maxLength);
  input.value = next;
  const pos = Math.min(start + text.length, input.value.length);
  input.focus({ preventScroll: true });
  input.setSelectionRange?.(pos, pos);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function flushPinyinBuffer() {
  if (!pinyinBuffer) return;
  const first = candidateWords(pinyinBuffer)[0] || pinyinBuffer;
  const hold = pinyinBuffer;
  pinyinBuffer = "";
  insertIntoActiveInput(first === hold ? hold : first);
  if (activeKeyboardInput) renderKeyboard(activeKeyboardInput);
}

function backspaceKeyboard() {
  if (!activeKeyboardInput) return;
  if (keyboardMode === "pinyin" && pinyinBuffer) {
    pinyinBuffer = pinyinBuffer.slice(0, -1);
    renderKeyboard(activeKeyboardInput);
    return;
  }
  const input = activeKeyboardInput;
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? input.value.length;
  if (start !== end) {
    input.value = input.value.slice(0, start) + input.value.slice(end);
    input.setSelectionRange?.(start, start);
  } else if (start > 0) {
    input.value = input.value.slice(0, start - 1) + input.value.slice(start);
    input.setSelectionRange?.(start - 1, start - 1);
  }
  input.dispatchEvent(new Event("input", { bubbles: true }));
  renderKeyboard(input);
}

function clearActiveInput() {
  if (!activeKeyboardInput) return;
  pinyinBuffer = "";
  activeKeyboardInput.value = "";
  activeKeyboardInput.dispatchEvent(new Event("input", { bubbles: true }));
  renderKeyboard(activeKeyboardInput);
}

function renderFaceVerification() {
  const page = document.querySelector("#page-face");
  if (!page) return;
  faceRecognitionStatus = "idle";
  const member = memberForActiveDose();
  const text = page.querySelector(".verify-layout article p");
  const camera = page.querySelector(".camera-box span");
  const button = page.querySelector("#faceBackend");
  if (camera) camera.textContent = member ? `${member.name} 人脸核验预览` : "摄像头预览";
  if (text) {
    text.textContent = member?.faceEnrolled
      ? `正在核验成员管理中已录入的 ${member.name} 人脸信息，核验通过后进入确认药物。`
      : `${member?.name || "当前成员"} 尚未在成员管理中录入人脸，请先完成录入。`;
  }
  if (button) {
    button.textContent = member?.faceEnrolled ? "开始核验" : "未录入人脸";
    button.classList.toggle("disabled-face", !member?.faceEnrolled);
    button.disabled = !member?.faceEnrolled;
  }
  setFaceRecognitionStatus("idle");
}

function setFaceRecognitionStatus(status) {
  faceRecognitionStatus = status;
  const statusEl = document.querySelector("#faceRecognitionStatus");
  if (!statusEl) return;
  const labels = {
    idle: "暂未开启识别",
    recognizing: "识别中",
    failure: "识别失败",
    success: "识别成功",
  };
  statusEl.textContent = labels[status] || labels.idle;
  statusEl.dataset.state = status;
}

function setPage(pageId) {
  if (pageId !== "take") releasePillCount();
  if (!["face", "member-form"].includes(pageId)) void releaseFacePreview();
  if (activePageId && activePageId !== pageId) {
    stopWangGrandpaAudio();
    if (activePageId === "load-guide") stopLoadGuideWorkflow(true);
    if (activePageId === "assistant") releaseAssistantResources({ keepWarm: true });
    if (activePageId === "member-form") resetPrescriptionOcrWorkflow({ clearDraft: true });
    previousPageId = activePageId;
    stopVoiceAnnouncements({ clearPageKey: true });
  }
  warmReminderAudio();
  activePageId = pageId;
  document.querySelector(".device")?.setAttribute("data-page", pageId);
  document.querySelectorAll(".page").forEach((page) => page.classList.toggle("active", page.id === `page-${pageId}`));
  document.querySelectorAll(".nav button").forEach((button) => button.classList.toggle("active", button.dataset.page === pageId));
  const [title, sub] = pages[pageId] || pages.home;
  const headerTitle = pageId === "member-form" && editingId ? "修改成员" : title;
  els.pageTitle.textContent = headerTitle;
  els.pageSub.textContent = sub;
  els.brandSub.textContent = headerTitle;
  renderHeaderVoiceTone(pageId);
  renderAll();
  if (pageId === "alarm") renderAlarm();
  if (pageId === "face") {
    renderFaceVerification();
    void ensureFacePreview("take-face").catch(() => {});
  }
  if (pageId === "member-form") void ensureFacePreview("member-form").catch(() => {});
  if (pageId === "assistant") startAssistant("quick");
  if (pageId === "k1") void refreshK1HealthStatus();
  requestAnimationFrame(syncAllTouchScrollRails);
}

function renderMembers() {
  els.memberList.innerHTML = members
    .map((member) => {
      const loaded = isMemberFullyLoaded(member);
      const loadLabel = loaded ? "已放药" : "放药";
      return `
        <article class="member-card">
          <div class="avatar">${escapeHtml(member.name.slice(0, 1) || "员")}</div>
          <div>
            <h3>${escapeHtml(member.name)} <small>${escapeHtml(member.age)}岁 · ${escapeHtml(member.gender)}</small></h3>
            <div class="chips">${String(member.disease || "")
              .split(/[、,，]/)
              .filter(Boolean)
              .slice(0, 3)
              .map((item) => `<span class="chip">${escapeHtml(item)}</span>`)
              .join("")}</div>
          </div>
          <div class="card-actions">
            <button class="soft" data-edit="${member.id}" type="button">修改</button>
            <button class="danger" data-delete="${member.id}" type="button">删除</button>
            <button class="${loaded ? "soft" : "primary"} load-member-btn${loaded ? " loaded" : ""}" data-load-member="${member.id}" type="button" ${loaded ? "disabled aria-disabled=\"true\"" : ""}>${loadLabel}</button>

            <button class="ghost" data-plan="${member.id}" type="button">看方案</button>
          </div>
        </article>`;
    })
    .join("");
}

function fixedDemoProfile(name) {
  const profile = namedDemoProfiles[String(name || "").trim()];
  return profile ? cloneDemoProfile(profile) : null;
}

function fillDemoMemberForm() {
  resetPrescriptionOcrWorkflow({ clearDraft: true });
  closeKeyboard();
  const nameInput = document.querySelector("#memberName");
  let profile = fixedDemoProfile(nameInput?.value);
  if (!profile) return showToast("固定病例仅支持王叔叔、何爷爷、王爷爷");
  if (String(nameInput?.value || "").trim() === "王爷爷") {
    profile = fixedWangGrandpaPlan({ name: "王爷爷", ...profile });
  }
  formDemoProfile = profile;
  document.querySelector("#memberGender").value = profile.gender;
  document.querySelector("#memberAge").value = profile.age;
  document.querySelector("#memberDisease").value = profile.disease;
  document.querySelector("#wakeTime").value = profile.schedule.wake;
  document.querySelector("#breakfastTime").value = profile.schedule.breakfast;
  document.querySelector("#lunchTime").value = profile.schedule.lunch;
  document.querySelector("#dinnerTime").value = profile.schedule.dinner;
  document.querySelector("#sleepTime").value = profile.schedule.sleep;
  els.medicineList.innerHTML = "";
  profile.medicines.forEach(addMedicineCard);
}

function openForm(memberId = null) {
  resetPrescriptionOcrWorkflow({ clearDraft: true });
  editingId = memberId;
  draftMemberId = memberId || createDraftMemberId();
  draftFaceEnrollment = null;
  const member = members.find((item) => item.id === memberId) || {
    name: "",
    gender: "女",
    age: "",
    disease: "",
    schedule: { wake: "07:00", breakfast: "08:00", lunch: "12:30", dinner: "18:30", sleep: "21:30" },
    medicines: [],
  };
  formDemoProfile = member.demoProfile ? {
    demoProfile: member.demoProfile,
    planOverrides: cloneDemoProfile(member.planOverrides || []),
    planExplanation: member.planExplanation || "",
  } : null;
  els.formTitle.textContent = memberId ? "修改成员" : "新增成员";
  document.querySelector("#memberName").value = member.name;
  document.querySelector("#memberGender").value = member.gender;
  document.querySelector("#memberAge").value = member.age;
  document.querySelector("#memberDisease").value = member.disease;
  document.querySelector("#wakeTime").value = member.schedule?.wake || "07:00";
  document.querySelector("#breakfastTime").value = member.schedule?.breakfast || "08:00";
  document.querySelector("#lunchTime").value = member.schedule?.lunch || "12:30";
  document.querySelector("#dinnerTime").value = member.schedule?.dinner || "18:30";
  document.querySelector("#sleepTime").value = member.schedule?.sleep || "21:30";
  const securityText = document.querySelector(".security-grid p");
  if (securityText) securityText.textContent = member.faceEnrolled ? `已保存 ${member.name} 的人脸信息。更换密码或重新录入人脸会立即保存。` : "人脸录入入口已保留；请先保存成员并设置 4 位密码，再录入人脸。";
  els.medicineList.innerHTML = "";
  (member.medicines?.length ? member.medicines : [{ name: "", times: "1", amount: "", unit: "", stock: "", note: "" }]).forEach(addMedicineCard);
  updateMemberFormFaceStatus();
  setPage("member-form");
}

function addMedicineCard(data = {}) {
  const card = document.createElement("article");
  card.className = "medicine-card";
  card.innerHTML = `
    <div class="medicine-card-head">
      <h3>药品 ${els.medicineList.children.length + 1}</h3>
      <button class="danger" data-remove-med type="button">删除</button>
    </div>
    <div class="medicine-grid">
      <label>药品名称<input data-med="name" data-required="药品名称" value="${escapeHtml(data.name || "")}" /></label>
      <label>每日次数<select data-med="times">${[1, 2, 3, 4, 5, 6].map((n) => `<option ${String(data.times || 1) === String(n) ? "selected" : ""}>${n}</option>`).join("")}</select></label>
      <label>每次用量<input data-med="amount" data-required="每次用量" value="${escapeHtml(data.amount || "")}" inputmode="numeric" /></label>
      <label>单位<input data-med="unit" data-required="单位" value="${escapeHtml(data.unit || "")}" /></label>
      <label>总量<input data-med="stock" data-required="总量" value="${escapeHtml(data.stock || "")}" inputmode="numeric" /></label>
      <label>说明<input data-med="note" value="${escapeHtml(data.note || "")}" /></label>
    </div>`;
  els.medicineList.appendChild(card);
  renumberMedicineCards();
}

function renumberMedicineCards() {
  [...els.medicineList.querySelectorAll(".medicine-card")].forEach((card, index) => {
    const heading = card.querySelector(".medicine-card-head h3");
    if (heading) heading.textContent = `药品 ${index + 1}`;
  });
}

function normalizePrescriptionLineIndexes(value) {
  const items = Array.isArray(value) ? value : value === undefined || value === null ? [] : [value];
  return [...new Set(items.map(Number).filter((item) => Number.isInteger(item) && item >= 0))].sort((a, b) => a - b);
}

function prescriptionLineText(line) {
  return String(line?.rawText ?? line?.text ?? "").trim();
}

function prescriptionLineIndexesForField(candidate, field, fallback) {
  const sources = candidate?.fieldSources || candidate?.fieldSource || candidate?.sources || {};
  const aliases = {
    name: ["name", "medicineName", "medicine_name"],
    times: ["times", "timesPerDay", "times_per_day", "frequency"],
    amount: ["amount", "dose"],
    unit: ["unit", "doseUnit", "dose_unit"],
    stock: ["stock", "total", "quantity"],
    note: ["note", "instructions", "usage"],
  };
  let value = null;
  for (const key of aliases[field] || [field]) {
    if (sources && Object.prototype.hasOwnProperty.call(sources, key)) {
      value = sources[key];
      break;
    }
  }
  if (value && typeof value === "object" && !Array.isArray(value)) {
    value = value.sourceLineIndexes ?? value.lineIndexes ?? value.lines ?? value.indexes;
  }
  const indexes = normalizePrescriptionLineIndexes(value);
  return indexes.length ? indexes : normalizePrescriptionLineIndexes(fallback);
}

function prescriptionSourceLabel(indexes) {
  const normalized = normalizePrescriptionLineIndexes(indexes);
  return normalized.length ? normalized.map((index) => "第" + (index + 1) + "行").join("、") : "未标注";
}

function prescriptionChineseNumber(value) {
  return { 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6 }[String(value || "")] || 0;
}

function prescriptionTimesValue(candidate) {
  const direct = candidate?.timesPerDay ?? candidate?.times_per_day ?? candidate?.times;
  const numeric = Number(direct);
  if (Number.isInteger(numeric) && numeric >= 1 && numeric <= 6) return String(numeric);
  const frequency = String(candidate?.frequency || candidate?.usageFrequency || "").trim();
  const abbreviation = frequency.toLowerCase();
  if (abbreviation === "qd" || abbreviation === "qhs" || abbreviation === "hs") return "1";
  if (abbreviation === "bid") return "2";
  if (abbreviation === "tid") return "3";
  if (abbreviation === "qid") return "4";
  if (/早中晚/.test(frequency)) return "3";
  if (/早晚/.test(frequency)) return "2";
  const match = frequency.match(/([1-6一二两三四五六])\s*次/);
  if (!match) return "";
  const parsed = Number(match[1]) || prescriptionChineseNumber(match[1]);
  return parsed >= 1 && parsed <= 6 ? String(parsed) : "";
}

function prescriptionDoseValues(candidate) {
  let amount = String(candidate?.amount ?? candidate?.doseAmount ?? "").trim();
  let unit = String(candidate?.unit ?? candidate?.doseUnit ?? "").trim();
  const dose = String(candidate?.dose ?? candidate?.dosage ?? "").trim().replace(/^每次\s*/, "");
  if ((!amount || !unit) && dose) {
    const match = dose.match(/^(\d+(?:\.\d+)?|半|[一二两三四五六七八九十]+)\s*(.+)$/);
    if (match) {
      if (!amount) amount = match[1];
      if (!unit) unit = match[2];
    }
  }
  return { amount, unit };
}

function normalizePrescriptionCandidate(candidate, result, candidateIndex) {
  const lines = Array.isArray(result?.lines) ? result.lines : [];
  const sourceLineIndexes = normalizePrescriptionLineIndexes(
    candidate?.sourceLineIndexes ?? candidate?.lineIndexes ?? candidate?.sourceLines,
  );
  const sourceLines = sourceLineIndexes
    .map((lineIndex) => lines.find((line, fallbackIndex) => Number(line?.index ?? fallbackIndex) === lineIndex) || lines[lineIndex])
    .filter(Boolean);
  const rawText = String(candidate?.rawText || sourceLines.map(prescriptionLineText).filter(Boolean).join(" / ")).trim();
  const confidenceValue = Number(candidate?.confidence);
  const confidence = Number.isFinite(confidenceValue) ? Math.max(0, Math.min(1, confidenceValue)) : 0;
  const times = prescriptionTimesValue(candidate);
  const { amount, unit } = prescriptionDoseValues(candidate);
  const directStock = String(candidate?.stock ?? candidate?.total ?? candidate?.quantity ?? "").trim();
  const frequency = String(candidate?.frequency || candidate?.usageFrequency || "").trim();
  const route = String(candidate?.route || candidate?.administrationRoute || "").trim();
  const note = String(candidate?.note || candidate?.instructions || candidate?.usage || [route, frequency].filter(Boolean).join("；")).trim();
  const warnings = (Array.isArray(result?.warnings) ? result.warnings : [])
    .filter((warning) => {
      const warningIndexes = normalizePrescriptionLineIndexes(warning?.sourceLineIndexes);
      return !warningIndexes.length || warningIndexes.some((index) => sourceLineIndexes.includes(index));
    })
    .map((warning) => String(warning?.message || warning?.code || "").trim())
    .filter(Boolean);
  const fallbackSources = sourceLineIndexes;
  return {
    candidateIndex,
    name: String(candidate?.name || candidate?.medicineName || "").trim(),
    times,
    amount,
    unit,
    stock: directStock,
    stockDefault: !directStock,
    note,
    rawText,
    confidence,
    correctionApplied: candidate?.correctionApplied === true,
    sourceLineIndexes,
    warnings,
    sources: {
      name: prescriptionLineIndexesForField(candidate, "name", fallbackSources),
      times: prescriptionLineIndexesForField(candidate, "times", fallbackSources),
      amount: prescriptionLineIndexesForField(candidate, "amount", fallbackSources),
      unit: prescriptionLineIndexesForField(candidate, "unit", fallbackSources),
      stock: prescriptionLineIndexesForField(candidate, "stock", []),
      note: prescriptionLineIndexesForField(candidate, "note", fallbackSources),
    },
  };
}

function prescriptionDraftFieldLabel(label) {
  return "<span>" + escapeHtml(label) + "</span>";
}

function renderPrescriptionCandidate(candidate, index) {
  const timesOptions = ['<option value="">待确认</option>']
    .concat([1, 2, 3, 4, 5, 6].map((value) => '<option value="' + value + '"' + (candidate.times === String(value) ? " selected" : "") + ">" + value + "</option>"))
    .join("");
  return [
    '<div class="prescription-draft-row" data-rx-candidate="' + index + '">',
    '<div class="prescription-draft-row-head">',
    '<label class="prescription-draft-check"><input type="checkbox" data-rx-select checked /><span>候选药品 ' + (index + 1) + "</span></label>",
    "</div>",
    '<div class="prescription-draft-fields">',
    "<label>" + prescriptionDraftFieldLabel("药品名称") + '<input data-rx-field="name" value="' + escapeHtml(candidate.name) + '" placeholder="请确认药品名称" /></label>',
    "<label>" + prescriptionDraftFieldLabel("每日次数") + '<select data-rx-field="times">' + timesOptions + "</select></label>",
    "<label>" + prescriptionDraftFieldLabel("每次用量") + '<input data-rx-field="amount" value="' + escapeHtml(candidate.amount) + '" inputmode="numeric" placeholder="请确认" /></label>',
    "<label>" + prescriptionDraftFieldLabel("单位") + '<input data-rx-field="unit" value="' + escapeHtml(candidate.unit) + '" placeholder="请确认" /></label>',
    "<label>" + prescriptionDraftFieldLabel("总量") + '<input data-rx-field="stock" value="' + escapeHtml(candidate.stock) + '" inputmode="numeric" placeholder="请填写" /></label>',
    "<label>" + prescriptionDraftFieldLabel("说明") + '<input data-rx-field="note" value="' + escapeHtml(candidate.note) + '" /></label>',
    "</div>",
    "</div>",
  ].join("");
}

function applyPrescriptionMemberDraft(member) {
  if (!member?.correctionApplied) return false;
  const fields = {
    "#memberName": member.name,
    "#memberGender": member.gender,
    "#memberAge": member.age,
    "#memberDisease": member.disease,
    "#wakeTime": member.schedule?.wake,
    "#breakfastTime": member.schedule?.breakfast,
    "#lunchTime": member.schedule?.lunch,
    "#dinnerTime": member.schedule?.dinner,
    "#sleepTime": member.schedule?.sleep,
  };
  Object.entries(fields).forEach(([selector, value]) => {
    const input = document.querySelector(selector);
    if (input && value !== undefined && value !== null) input.value = String(value);
  });
  // Keep the existing fixed-plan compatibility data.  The normal save path
  // remains the single source of truth for persistence.
  formDemoProfile = fixedDemoProfile(member.name);
  updateMemberFormFaceStatus();
  return true;
}

function renderPrescriptionOcrDraft(result) {
  if (!els.prescriptionOcrReview) return;
  const memberFilled = applyPrescriptionMemberDraft(result?.member);
  const candidates = (Array.isArray(result?.medicines) ? result.medicines : [])
    .map((candidate, index) => normalizePrescriptionCandidate(candidate, result, index));
  prescriptionOcrDraft = { result, candidates };
  const header = [
    '<div class="prescription-review-head">',
    '<div><strong>识别草稿</strong><span id="prescriptionDraftCount">' + candidates.length + " 条候选</span></div>",
    '<div class="prescription-review-actions">',
    '<button class="ghost" data-rx-discard type="button">放弃草稿</button>',
    candidates.length ? '<button class="primary" data-rx-confirm type="button">确认所选</button>' : "",
    "</div>",
    "</div>",
  ].join("");
  if (candidates.length) {
    els.prescriptionOcrReview.innerHTML = header + '<div class="prescription-draft-list">' + candidates.map(renderPrescriptionCandidate).join("") + "</div>";
  } else {
    els.prescriptionOcrReview.innerHTML = header + '<p class="prescription-empty-result">未识别到可审核的药品条目</p>';
  }
  els.prescriptionOcrReview.hidden = false;
  updatePrescriptionDraftSelectionState();
  requestAnimationFrame(syncAllTouchScrollRails);
}

function updatePrescriptionDraftSelectionState() {
  if (!els.prescriptionOcrReview || els.prescriptionOcrReview.hidden) return;
  const rows = [...els.prescriptionOcrReview.querySelectorAll("[data-rx-candidate]")];
  const selected = rows.filter((row) => row.querySelector("[data-rx-select]")?.checked);
  rows.forEach((row) => row.classList.toggle("is-unselected", !row.querySelector("[data-rx-select]")?.checked));
  const count = els.prescriptionOcrReview.querySelector("#prescriptionDraftCount");
  if (count) count.textContent = rows.length ? selected.length + "/" + rows.length + " 条已选" : "无结构化候选";
  const confirm = els.prescriptionOcrReview.querySelector("[data-rx-confirm]");
  if (confirm) confirm.disabled = selected.length === 0;
}

function clearPrescriptionOcrDraft(statusText = "药单识别待命") {
  prescriptionOcrDraft = null;
  if (els.prescriptionOcrReview) {
    els.prescriptionOcrReview.hidden = true;
    els.prescriptionOcrReview.innerHTML = "";
  }
  if (statusText === "识别草稿已放弃") restorePlaceholderMedicineCard();
  setPrescriptionOcrUiState("idle", statusText);
  requestAnimationFrame(syncAllTouchScrollRails);
}

function isUntouchedPlaceholderMedicineCard() {
  const cards = [...els.medicineList.querySelectorAll(".medicine-card")];
  if (cards.length !== 1) return false;
  const values = {};
  cards[0].querySelectorAll("[data-med]").forEach((input) => {
    values[input.dataset.med] = String(input.value || "").trim();
  });
  return !values.name && values.times === "1" && !values.amount && !values.unit && !values.stock && !values.note;
}

function removeUntouchedPlaceholderMedicineCard() {
  if (isUntouchedPlaceholderMedicineCard()) {
    els.medicineList.querySelector(".medicine-card")?.remove();
  }
}

function restorePlaceholderMedicineCard() {
  if (!els.medicineList.children.length) addMedicineCard();
}

function confirmPrescriptionOcrSelection() {
  if (!els.prescriptionOcrReview) return;
  const selectedRows = [...els.prescriptionOcrReview.querySelectorAll("[data-rx-candidate]")]
    .filter((row) => row.querySelector("[data-rx-select]")?.checked);
  if (!selectedRows.length) return showToast("请先勾选要加入的药品");
  const fieldLabels = { name: "药品名称", times: "每日次数", amount: "每次用量", unit: "单位", stock: "总量" };
  const medicines = [];
  for (const [rowIndex, row] of selectedRows.entries()) {
    const medicine = {};
    row.querySelectorAll("[data-rx-field]").forEach((input) => {
      medicine[input.dataset.rxField] = String(input.value || "").trim();
    });
    for (const [field, label] of Object.entries(fieldLabels)) {
      if (!medicine[field]) {
        showToast("候选药品 " + (rowIndex + 1) + "：请填写" + label);
        row.querySelector('[data-rx-field="' + field + '"]')?.focus();
        return;
      }
    }
    medicines.push(medicine);
  }
  if (isUntouchedPlaceholderMedicineCard()) els.medicineList.querySelector(".medicine-card")?.remove();
  medicines.forEach(addMedicineCard);
  selectedRows.forEach((row) => row.remove());
  renumberMedicineCards();
  const remaining = els.prescriptionOcrReview.querySelectorAll("[data-rx-candidate]").length;
  if (!remaining) clearPrescriptionOcrDraft("药品草稿已加入");
  else updatePrescriptionDraftSelectionState();
  showToast("已加入 " + medicines.length + " 种药品，请核对表单后再保存");
  requestAnimationFrame(syncAllTouchScrollRails);
}

function collectForm() {
  const required = [...document.querySelectorAll("#page-member-form [data-required]")];
  for (const input of required) {
    if (!String(input.value || "").trim()) {
      showToast(`请填写${input.dataset.required}`);
      input.focus();
      return null;
    }
  }
  const medicines = [...els.medicineList.querySelectorAll(".medicine-card")].map((card) => {
    const med = {};
    card.querySelectorAll("[data-med]").forEach((input) => {
      med[input.dataset.med] = input.value.trim();
    });
    return med;
  });
  const existing = members.find((member) => member.id === editingId) || {};
  const formName = document.querySelector("#memberName").value.trim();
  const draftReady = Boolean(draftFaceEnrollment && !draftFaceEnrollment.stale && draftFaceEnrollment.name === formName);
  const existingFaceStillValid = Boolean(existing.faceEnrolled && existing.name === formName);
  const demoProfile = formDemoProfile || (existing.demoProfile ? {
    demoProfile: existing.demoProfile,
    planOverrides: existing.planOverrides || [],
    planExplanation: existing.planExplanation || "",
  } : null);
  return {
    id: editingId || draftMemberId || `m${Date.now()}`,
    name: formName,
    gender: document.querySelector("#memberGender").value,
    age: document.querySelector("#memberAge").value.trim(),
    disease: document.querySelector("#memberDisease").value.trim(),
    faceEnrolled: Boolean(existingFaceStillValid || draftReady),
    faceUpdatedAt: draftReady ? draftFaceEnrollment.updatedAt : existingFaceStillValid ? existing.faceUpdatedAt || "" : "",
    current: members.length === 0,
    schedule: {
      wake: document.querySelector("#wakeTime").value,
      breakfast: document.querySelector("#breakfastTime").value,
      lunch: document.querySelector("#lunchTime").value,
      dinner: document.querySelector("#dinnerTime").value,
      sleep: document.querySelector("#sleepTime").value,
    },
    medicines,
    planExplanationGeneratedAt: demoProfile && (TWO_MEMBER_ROSTER_NAMES.has(document.querySelector("#memberName").value.trim()) || document.querySelector("#memberName").value.trim() === "王爷爷") ? "preset" : demoProfile ? "" : existing.planExplanationGeneratedAt || "",
    ...(demoProfile ? {
      demoProfile: demoProfile.demoProfile,
      planOverrides: cloneDemoProfile(demoProfile.planOverrides || []),
      planExplanation: demoProfile.planExplanation || "",
    } : {}),
  };
}

function renderHome() {
  const nextPanel = document.querySelector(".next-panel");
  const voiceCard = document.querySelector(".voice-card");
  const todayCard = document.querySelector(".today-card");
  document.querySelector(".home-grid")?.classList.remove("home-progress-focus");
  document.querySelector(".home-grid")?.classList.add("home-dashboard");
  if (voiceCard) voiceCard.hidden = false;
  if (!members.length) {
    nextPanel.innerHTML = `<div class="home-user-card"><span class="eyebrow">当前使用人</span><strong>暂无成员</strong><button class="primary" data-page-jump="members" type="button">去绑定成员</button></div>`;
    todayCard.innerHTML = `<div class="today-title-link">今日服用进度</div><div class="empty-progress">请先录入成员和药品。</div>`;
    if (voiceCard) {
      voiceCard.innerHTML = `
        <article class="voice-broadcast">
          <div class="voice-progress-head">
            <h3>语音播报</h3>
            <div class="voice-action-pair">
              <button class="primary compact medicine-voice-play" id="playHomeVoice" type="button" title="用药提醒播报" aria-label="用药提醒播报">播报</button>
            </div>
          </div>
          <div class="scroll-body all-member-voice-list progress-voice-lines">${progressVoiceHtmlForRows(null, [])}</div>
        </article>`;
    }
    return;
  }
  const { member, rows, pageCount } = currentHomeMemberProgress();
  const doneCount = rows.filter((row) => row.done).length;
  const totalMeds = rows.reduce((sum, row) => sum + row.meds.reduce((medSum, med) => medSum + Number(med.amount || 0), 0), 0);
  const doneMeds = rows.filter((row) => row.done).reduce((sum, row) => sum + row.meds.reduce((medSum, med) => medSum + Number(med.amount || 0), 0), 0);
  const percent = rows.length ? Math.round((doneCount / rows.length) * 100) : 0;
  const patientNext = rows.find((row) => !row.done && isFutureScheduleRow(row)) || null;
  const overdueRows = rows.filter((row) => !row.done && isPastScheduleRow(row));
  const nextMeds = patientNext?.meds || [];
  const overdueDetail = overdueRows
    .slice(0, 2)
    .map((row) => `${row.time} ${row.slot}：${row.meds.map((med) => `${med.name}${med.amount}${med.unit}`).join("、")}`)
    .join("；");
  const overdueAlert = overdueRows.length
    ? `<div class="home-overdue-alert">
        <strong>重点提醒：${escapeHtml(member.name)} 有 ${overdueRows.length} 项已过时间仍未服用，请优先处理。</strong>
        <span>${escapeHtml(`${overdueDetail}${overdueRows.length > 2 ? `；另有 ${overdueRows.length - 2} 项` : ""}`)}</span>
      </div>`
    : "";
  const userOptions = members
    .map(
      (item) => `<button class="${item.id === member.id ? "selected" : ""}" data-current="${item.id}" type="button">
        <span>${escapeHtml(item.name)}</span>
        <small>${escapeHtml(item.disease || "未填写病症")}</small>
      </button>`,
    )
    .join("");
  nextPanel.innerHTML = `
    <article class="home-user-card">
      <div>
        <span class="eyebrow">当前使用人</span>
        <div class="home-user-row"><b>${escapeHtml(member?.name?.slice(0, 1) || "员")}</b><strong>${escapeHtml(member?.name || "暂无成员")}</strong></div>
        <p>${escapeHtml(member?.disease || "未填写病症")} / ${patientNext ? `${escapeHtml(patientNext.slot)}有固定用药` : "今日暂无待用药"}</p>
      </div>
      <div class="home-user-switch">
        <button class="primary compact" data-toggle-home-user type="button">切换使用人</button>
        <div class="home-user-menu">${userOptions}</div>
      </div>
    </article>
    <article class="home-next-card">
      <div class="next-title">
        <span>下一次用药</span>
        <button class="soft compact" data-plan="${escapeHtml(member?.id || "")}" type="button">本地方案</button>
      </div>
      <h2>${patientNext ? `${escapeHtml(patientNext.time)} ${escapeHtml(patientNext.slot)}` : "暂无待提醒"}</h2>
      <table class="home-med-table">
        <thead><tr><th>药品</th><th>数量</th><th>方式</th><th>说明</th></tr></thead>
        <tbody>
          ${
            nextMeds.length
              ? nextMeds
                  .map((med) => `<tr><td>${escapeHtml(med.name)}</td><td>${escapeHtml(med.amount || "-")}${escapeHtml(med.unit || "")}</td><td>${String(med.note || med.timing || "").includes("外用") ? "外用" : "口服"}</td><td>${String(med.note || med.timing || "").includes("外用") ? "局部/外用，单独提醒" : "到点语音提醒"}</td></tr>`)
                  .join("")
              : `<tr><td colspan="4">${overdueRows.length ? "今日后续暂无预告" : "该患者暂无未完成用药事项"}</td></tr>`
          }
        </tbody>
      </table>
      <button class="soft adherence-entry" data-page-jump="adherence" type="button">患者服药情况</button>
    </article>`;
  todayCard.innerHTML = `
    <div class="today-head compact-head">
      <div class="today-title-link">今日服用进度</div>
    </div>
    <div class="ring big-ring" style="--progress:${percent * 3.6}deg"><span>${doneCount}/${rows.length || 0}</span><small>已完成</small></div>
    <div class="home-page-switch">
      <button class="ghost" data-home-page="-1" type="button">上一位</button>
      <span>第 ${homeReminderPage + 1}/${pageCount} 位</span>
      <button class="ghost" data-home-page="1" type="button">下一位</button>
    </div>
    <p class="home-progress-text">已服用 ${doneMeds}，余量 ${Math.max(0, totalMeds - doneMeds)}</p>`;
  if (voiceCard) {
    const progressVoiceText = progressVoiceTextForRows(member, rows);
    voiceCard.innerHTML = `
      <article class="voice-broadcast${overdueRows.length ? " has-overdue-alert" : ""}">
        <div class="voice-progress-head">
          <h3>语音播报</h3>
          <div class="voice-action-pair">
            <button class="primary compact medicine-voice-play" id="playHomeVoice" type="button" title="用药提醒播报" aria-label="用药提醒播报">播报</button>
          </div>
        </div>
        ${overdueAlert}
        <div class="scroll-body all-member-voice-list progress-voice-lines" aria-label="${escapeHtml(progressVoiceText)}">
          ${progressVoiceHtmlForRows(member, rows)}
        </div>
      </article>`;
    syncVoiceToneControls();
  }
}

function planScheduledMedicineText(med) {
  return `${escapeHtml(med.name || "未命名药品")} ${escapeHtml(med.amount || "-")}${escapeHtml(med.unit || "")}`;
}

function planEngineRequest(member) {
  return {
    patient: member.name,
    schedule: member.schedule,
    prescription: (member.medicines || []).map((med) => ({
      name: med.name,
      times_per_day: Number(med.times ?? med.times_per_day ?? 0),
      amount: Number(med.amount),
      unit: med.unit,
    })),
  };
}

function planEngineFingerprint(member) {
  return JSON.stringify(planEngineRequest(member));
}

async function startPlanEngine(member) {
  if (!member?.id || !member.medicines?.length) return;
  const fingerprint = planEngineFingerprint(member);
  const current = planEngineStates.get(member.id);
  if (current?.fingerprint === fingerprint && ["loading", "ready", "needs_review", "error"].includes(current.status)) return;

  planEngineStates.set(member.id, { status: "loading", fingerprint });
  if (activePageId === "plan") renderPlan();
  try {
    const gateway = await getK1GatewayClient();
    const result = await gateway.medicationSchedule.generate(planEngineRequest(member));
    if (result?.status === "ok") {
      planEngineStates.set(member.id, { status: "ready", fingerprint, result });
    } else if (result?.status === "needs_review") {
      planEngineStates.set(member.id, {
        status: "needs_review",
        fingerprint,
        reason: result.reason || "当前处方需要人工确认",
        reasons: result.reasons || [],
      });
    } else {
      throw new Error("medication_schedule_invalid_response");
    }
  } catch (error) {
    planEngineStates.set(member.id, {
      status: "error",
      fingerprint,
      message: error?.message || "服药方案引擎暂时不可用",
    });
  }
  if (activePageId === "plan") renderPlan();
}

function planExplanationMarkup(state, member) {
  if (!member?.medicines?.length) {
    return `<div class="plan-explanation-content"><h3>服药方案说明</h3><p>请先在成员管理中录入药品和作息时间。</p></div>`;
  }
  if (!state || state.status === "loading") {
    return `<div class="plan-explanation-loading" role="status" aria-live="polite"><span class="plan-explanation-spinner" aria-hidden="true"></span><strong>正在推理服药方案</strong><p>正在校验处方次数、时段规则与药物关系...</p></div>`;
  }
  if (state.status === "needs_review") {
    return `<div class="plan-explanation-content"><h3>需要人工确认</h3><p>${escapeHtml(state.reason)}</p></div>`;
  }
  if (state.status === "error") {
    return `<div class="plan-explanation-content"><h3>服药方案说明</h3><p>方案推理暂时不可用：${escapeHtml(state.message)}</p></div>`;
  }
  return `<div class="plan-explanation-content"><h3>服药方案说明</h3><p>${escapeHtml(state.result.planExplanation)}</p></div>`;
}

function todayStatusSlotText(row) {
  const slot = String(row?.slot || "").trim();
  return /饭前|饭后|餐前|餐后/.test(slot) ? slot : "";
}

function todayStatusMedicineText(med) {
  const base = `${escapeHtml(med.name)} ${escapeHtml(med.amount)}${escapeHtml(med.unit)}`;
  const note = String(med.note || "").trim();
  const timing = String(med.timing || "").trim();
  const mealTiming = /饭前|饭后|餐前|餐后/.test(timing) ? timing : "";
  const extra = note || mealTiming;
  return extra ? `${base} · ${escapeHtml(extra)}` : base;
}

function renderTodayStatus() {
  const page = document.querySelector("#page-today-status");
  if (!page) return;
  const { member, rows, pageCount } = currentHomeMemberProgress();
  const items = rows.length
    ? rows
        .map((row) => {
          const slotText = todayStatusSlotText(row);
          const medicines = row.meds.length ? row.meds.map(todayStatusMedicineText).join("；") : "暂无药品";
          const overdue = !row.done && isPastScheduleRow(row);
          const stateClass = row.done ? "done" : overdue ? "overdue" : "pending";
          const stateText = row.done ? "已服用" : overdue ? "未服用" : "未到时间";
          return `<article class="today-status-item ${stateClass}">
            <div>
              <strong>${escapeHtml(row.time)}${slotText ? ` ${escapeHtml(slotText)}` : ""}</strong>
              <p>${medicines}</p>
            </div>
            <span>${stateText}</span>
          </article>`;
        })
        .join("")
    : `<article class="today-status-item pending"><div><strong>暂无今日服药安排</strong><p>请先在成员管理中录入药品和作息时间。</p></div><span>暂无</span></article>`;
  page.innerHTML = `
    <div class="today-status-board">
      <div class="today-status-toolbar">
        <button class="ghost" data-page-jump="home" type="button">返回</button>
        <strong>${escapeHtml(member?.name || "当前使用人")}的今日服用情况</strong>
        <div class="home-page-switch today-status-switch">
          <button class="ghost" data-home-page="-1" type="button">上一位</button>
          <span>第 ${homeReminderPage + 1}/${pageCount} 位</span>
          <button class="ghost" data-home-page="1" type="button">下一位</button>
        </div>
      </div>
      <div class="today-status-list">${items}</div>
    </div>`;
}
function renderPlan() {
  const pageCount = Math.max(1, members.length);
  planPage = Math.min(planPage, pageCount - 1);
  const member = members[planPage] || activeMember();
  const hasMedicines = Boolean(member?.medicines?.length);
  const fingerprint = member && hasMedicines ? planEngineFingerprint(member) : "";
  const state = member ? planEngineStates.get(member.id) : null;
  if (member && hasMedicines && activePageId === "plan" && (!state || state.fingerprint !== fingerprint)) {
    void startPlanEngine(member);
  }
  const rows = state?.status === "ready" ? state.result.schedule : [];
  const planCards = rows.length
    ? rows
        .map(
          (row) => `<article><strong>${escapeHtml(row.time)} ${escapeHtml(row.slot)}</strong><p>${row.meds.length ? row.meds.map(planScheduledMedicineText).join("；") : "暂无药品"}</p></article>`,
        )
        .join("")
    : `<article class="empty-plan-card"><strong>${state?.status === "needs_review" ? "需要人工确认" : state?.status === "error" ? "服药方案推理失败" : "正在推理服药方案"}</strong><p>${hasMedicines ? (state?.status === "needs_review" ? escapeHtml(state.reason) : state?.status === "error" ? escapeHtml(state.message) : "正在根据系统药品规则库生成时间表。") : "请先在成员管理中录入药品信息，再生成服药方案。"}</p></article>`;
  els.planList.innerHTML = `
    <div class="plan-head">
      <div class="plan-title-line">
        <button class="ghost" data-plan-page="-1" type="button">上一位</button>
        <strong>${escapeHtml(member?.name || "暂无成员")}的服药时间 <span class="plan-count-inline">（第 ${planPage + 1}/${pageCount} 位成员）</span></strong>
        <button class="ghost" data-plan-page="1" type="button">下一位</button>
      </div>
      <div class="plan-generate-actions">
        <button class="ghost" data-page-jump="members" type="button">返回成员管理</button>
      </div>
    </div>
    <div class="plan-body">
      <div class="plan-scroll">
        ${planCards}
      </div>
      <section class="plan-note-box plan-explanation-box" aria-label="服药方案说明">${planExplanationMarkup(state, member)}</section>
    </div>`;
}

function renderCabinets() {
  const loadedByDrawer = new Map(loadedMedicineItems().map((item) => [item.drawer, item]));
  const drawers = Array.from({ length: 12 }, (_, index) => {
    const item = loadedByDrawer.get(index + 1) || null;
    const med = item?.med;
    const open = index === selectedCabinetIndex && document.querySelector("#page-load-guide")?.classList.contains("active");
    const inventory = item ? cabinetInventoryForItem(item) : { taken: 0, remaining: 0, percent: 0 };
    const pillPercent = inventory.percent;
    return `<article class="drawer ${open || index === selectedCabinetIndex ? "open" : ""}" data-cabinet="${index}" role="button" tabindex="0">
      <div class="drawer-head">
        <strong>${index + 1}号柜${item ? `（${escapeHtml(item.member.name)}）` : ""}</strong>
      </div>
      <span class="drawer-med-name">${med ? escapeHtml(med.name) : "空柜"}</span>
      <div class="drawer-progress">
        <i style="--progress:${pillPercent * 3.6}deg"><b>${pillPercent}%</b></i>
        <small>${med ? `已服${inventory.taken}${med.unit || ""}，剩余${inventory.remaining}${med.unit || ""}` : "暂无进度"}</small>
      </div>
    </article>`;
  });
  els.cabinetGrid.innerHTML = drawers.join("");
}

function openCabinetDetail(index) {
  selectedCabinetIndex = Number(index || 0);
  const item = loadedCabinetItem(selectedCabinetIndex + 1);
  const inventory = item ? cabinetInventoryForItem(item) : { loaded: 0, taken: 0, remaining: 0 };
  let page = document.querySelector("#page-cabinet-detail");
  if (!page) {
    page = document.createElement("section");
    page.id = "page-cabinet-detail";
    page.className = "page";
    document.querySelector(".content").appendChild(page);
  }
  page.innerHTML = `
    <div class="cabinet-detail">
      <article class="panel cabinet-big">
        <span class="eyebrow">药柜 ${selectedCabinetIndex + 1}</span>
        <h2>${item ? escapeHtml(item.med.name) : "空柜"}</h2>
        <p>${item ? `${escapeHtml(item.member.name)} · ${escapeHtml(item.med.amount)}${escapeHtml(item.med.unit)} · ${escapeHtml(item.med.timing || "按计划服用")} · 已服${inventory.taken}${escapeHtml(item.med.unit || "")}，剩余${inventory.remaining}${escapeHtml(item.med.unit || "")}` : "当前可分配给其他成员药品。"}</p>
        <div class="drawer-demo detail-demo">
          <strong>${selectedCabinetIndex + 1}号柜</strong>
          <span id="detailDoorText">柜门：关闭</span>
        </div>
      </article>
      <article class="panel cabinet-controls">
        <h2>参数控制</h2>
        <label>库存数量<input id="cabinetStock" inputmode="numeric" value="${escapeHtml(item?.med.stock || "")}" /></label>
        <label>开锁权限<select id="cabinetLock"><option>人脸 + 密码</option><option>仅人脸</option><option>仅密码</option></select></label>
        <div class="control-grid">
          <button class="secondary" id="unlockDrawer" type="button">打开柜门</button>
          <button class="soft" id="closeDrawer" type="button">刷新柜门状态</button>
          <button class="primary" id="saveCabinetParam" type="button">保存参数</button>
          <button class="ghost" data-page-jump="cabinet" type="button">返回药柜</button>
        </div>
      </article>
    </div>`;
  setPage("cabinet-detail");
  saveCabinetState("cabinet-detail");
}

function saveCabinetParam() {
  const item = allMedicineItems()[selectedCabinetIndex];
  if (item) {
    const nextStock = document.querySelector("#cabinetStock").value.replace(/\D/g, "");
    item.med.stock = nextStock;
    saveMembers();
    addReport("药柜参数", `${selectedCabinetIndex + 1}号柜库存调整为 ${nextStock || "未填写"}${item.med.unit || ""}`, item.member.name);
    renderCabinets();
    saveCabinetState("cabinet-param");
  }
  showToast("药柜参数已保存");
}

function renderAll() {
  renderHome();
  renderMembers();
  renderPlan();
  renderTodayStatus();
  renderCabinets();
  renderAssistantHome();
  renderReports();
  renderAdherence();
  renderHeaderVoiceTone(activePageId || "home");
}

function getK1GatewayClient() {
  if (!k1GatewayClientPromise) {
    k1GatewayClientPromise = import("./api/gateway-client.js?v=20260803-prescription-ocr-v3").then(({ gateway }) => gateway);
  }
  return k1GatewayClientPromise;
}

function setK1HealthValue(name, value, state = "unknown") {
  const valueElement = document.querySelector(`[data-k1-health="${name}"] strong`);
  if (!valueElement) return;
  valueElement.textContent = value;
  valueElement.dataset.state = state;
}

function k1HealthLabel(ready) {
  return ready === true ? "就绪" : "未就绪";
}

function k1CapabilityLabel(capability) {
  if (capability?.available === true) return "可用";
  if (capability?.reason === "tts_disabled") return "已禁用";
  return "不可用";
}

async function refreshK1HealthStatus() {
  const requestToken = ++k1HealthRequestToken;
  ["gateway", "asr", "qa", "llm", "medicineBox", "face", "yolo", "prescriptionOcr"].forEach((name) => setK1HealthValue(name, "正在读取"));

  try {
    const gateway = await getK1GatewayClient();
    const settled = (request) => request.then((data) => ({ data, error: null })).catch((error) => ({ data: error?.data || null, error }));
    const readyResult = settled(gateway.health.ready());
    const [live, readyOutcome, capabilities, faceOutcome, yoloOutcome] = await Promise.all([
      gateway.health.live(),
      readyResult,
      gateway.health.capabilities(),
      settled(gateway.face.status("")),
      settled(gateway.pillCount.status("")),
    ]);

    if (requestToken !== k1HealthRequestToken || activePageId !== "k1") return;
    if (readyOutcome.error && readyOutcome.error.code !== "gateway_not_ready") throw readyOutcome.error;

    const components = readyOutcome.data?.components || {};
    const modules = capabilities?.modules || {};
    setK1HealthValue("gateway", live?.status === "live" ? "在线" : "未就绪", live?.status === "live" ? "ready" : "not-ready");
    ["asr", "qa", "llm"].forEach((name) => {
      const ready = components[name]?.ready === true;
      setK1HealthValue(name, k1HealthLabel(ready), ready ? "ready" : "not-ready");
    });
    const medicineBox = modules.medicineBox;
    const medicineBoxReady = medicineBox?.available === true;
    setK1HealthValue("medicineBox", k1CapabilityLabel(medicineBox), medicineBoxReady ? "ready" : "not-ready");
    const visionStatus = (status) => {
      if (!status) return { label: "无法读取", state: "error" };
      if (status.busy === true) return { label: "识别中", state: "busy" };
      if (status.ready === true && status.bootState !== "loading") return { label: "就绪", state: "ready" };
      if (status.bootState === "loading" || status.state === "warming_up") return { label: "加载中", state: "warming" };
      return { label: "未就绪", state: "not-ready" };
    };
    const face = visionStatus(faceOutcome.data);
    setK1HealthValue("face", face.label, face.state);
    const yolo = visionStatus(yoloOutcome.data);
    setK1HealthValue("yolo", yolo.label, yolo.state);
    const prescriptionOcr = modules.prescriptionOcr;
    const prescriptionOcrReady = prescriptionOcr?.available === true;
    setK1HealthValue("prescriptionOcr", prescriptionOcrReady ? "就绪" : "未接入", prescriptionOcrReady ? "ready" : "not-ready");
  } catch (error) {
    if (requestToken !== k1HealthRequestToken || activePageId !== "k1") return;
    setK1HealthValue("gateway", "暂时不可用", "error");
    ["asr", "qa", "llm", "medicineBox", "face", "yolo", "prescriptionOcr"].forEach((name) => setK1HealthValue(name, "无法读取", "error"));
    console.warn("K1 health status request failed", error);
  }
}

function setCpuMetricsState(usagePercent = null, state = "unavailable", options = {}) {
  const detail = options.detail || "暂不可用";
  const cores = Number(options.cores);
  const sampleSeconds = Number(options.sampleSeconds);
  const usage = Number(usagePercent);
  const hasUsage = Number.isFinite(usage) && usage >= 0 && usage <= 100;
  const rounded = hasUsage ? Math.round(usage) : null;
  const hasMetricDetail = Number.isInteger(cores) && cores > 0 && Number.isFinite(sampleSeconds) && sampleSeconds > 0;
  if (els.cpuUsageValue) els.cpuUsageValue.textContent = hasUsage ? `${rounded}%` : "--%";
  if (els.cpuCoreCount) els.cpuCoreCount.textContent = hasMetricDetail ? `${cores} 核` : "";
  if (els.cpuStatusDot) els.cpuStatusDot.hidden = !hasMetricDetail;
  if (els.cpuStatusText) els.cpuStatusText.textContent = hasMetricDetail ? `${sampleSeconds} 秒均值` : detail;
  if (els.cpuStatus) {
    els.cpuStatus.dataset.state = state;
    const description = hasMetricDetail ? `${cores} 核，${sampleSeconds} 秒均值` : detail;
    els.cpuStatus.setAttribute("aria-label", hasUsage ? `CPU 占用率 ${rounded}%，${description}` : `CPU 占用率${description}`);
  }
}

async function refreshCpuMetrics() {
  if (document.visibilityState === "hidden" || cpuMetricsRequestPending) return;
  const requestToken = ++cpuMetricsRequestToken;
  cpuMetricsRequestPending = true;
  try {
    const gateway = await getK1GatewayClient();
    const data = await gateway.system.metrics();
    if (requestToken !== cpuMetricsRequestToken) return;
    const cpu = data?.cpu;
    if (cpu?.state === "warming_up" && cpu?.usagePercent === null) {
      setCpuMetricsState(null, "warming", { detail: "正在采样" });
      return;
    }
    const usage = Number(cpu?.usagePercent);
    const cores = Number(cpu?.logicalCores);
    const sampleWindowMs = Number(cpu?.sampleWindowMs);
    if (!Number.isFinite(usage) || usage < 0 || usage > 100 || !Number.isInteger(cores) || cores < 1 || !Number.isFinite(sampleWindowMs) || sampleWindowMs <= 0) {
      throw new Error("invalid_system_metrics_contract");
    }
    setCpuMetricsState(usage, "ready", {
      cores,
      sampleSeconds: Math.round(CPU_METRICS_POLL_MS / 1000),
    });
  } catch (error) {
    if (requestToken === cpuMetricsRequestToken) setCpuMetricsState(null, "error", { detail: "暂不可用" });
    console.warn("K1 CPU metrics request failed", error);
  } finally {
    if (requestToken === cpuMetricsRequestToken) cpuMetricsRequestPending = false;
  }
}

function startCpuMetricsPolling() {
  window.clearInterval(cpuMetricsTimer);
  cpuMetricsTimer = 0;
  if (document.visibilityState === "hidden") return;
  void refreshCpuMetrics();
  cpuMetricsTimer = window.setInterval(() => void refreshCpuMetrics(), CPU_METRICS_POLL_MS);
}

function stopCpuMetricsPolling() {
  window.clearInterval(cpuMetricsTimer);
  cpuMetricsTimer = 0;
  cpuMetricsRequestToken += 1;
  cpuMetricsRequestPending = false;
}

function adherenceDates() {
  return Array.from({ length: 7 }, (_, index) => {
    const date = new Date();
    date.setDate(date.getDate() - (6 - index));
    return date;
  });
}

function adherenceRateFor(member, date) {
  const key = todayKey(date);
  const rows = todaySchedule().filter((row) => row.member.id === member.id);
  if (!rows.length) return 0;
  const history = loadProgressHistory();
  const doneIds = key === todayKey() ? completedDoseIds : new Set(Array.isArray(history[key]) ? history[key] : []);
  const done = rows.filter((row) => doneIds.has(`${key}-${row.member.id}-${row.time}-${row.slot}`)).length;
  return Math.round((done / rows.length) * 100);
}

function renderAdherence() {
  const tabs = document.querySelector("#adherenceTabs");
  const chart = document.querySelector("#adherenceChart");
  if (!tabs || !chart) return;
  if (!members.length) {
    tabs.innerHTML = "";
    chart.innerHTML = `<div class="empty-progress">请先录入患者。</div>`;
    return;
  }
  if (!activeAdherenceMemberId || !members.some((member) => member.id === activeAdherenceMemberId)) {
    activeAdherenceMemberId = activeMember()?.id || members[0].id;
  }
  const member = members.find((item) => item.id === activeAdherenceMemberId) || members[0];
  tabs.innerHTML = members
    .map((item) => `<button class="${item.id === member.id ? "active" : ""}" data-adherence-member="${item.id}" type="button">${escapeHtml(item.name)}</button>`)
    .join("");
  const dates = adherenceDates();
  const points = dates.map((date) => ({ date, rate: adherenceRateFor(member, date) }));
  const width = 640;
  const height = 250;
  const padX = 48;
  const padY = 28;
  const plotW = width - padX * 2;
  const plotH = height - padY * 2;
  const coords = points.map((point, index) => {
    const x = padX + (plotW / Math.max(1, points.length - 1)) * index;
    const y = padY + plotH - (point.rate / 100) * plotH;
    return { ...point, x, y };
  });
  const path = coords.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
  const area = `${path} L${coords.at(-1).x.toFixed(1)},${height - padY} L${coords[0].x.toFixed(1)},${height - padY} Z`;
  const avg = Math.round(points.reduce((sum, point) => sum + point.rate, 0) / points.length);
  chart.innerHTML = `
    <div class="adherence-summary">
      <strong>${escapeHtml(member.name)}</strong>
      <span>近 7 日平均服药率</span>
      <b>${avg}%</b>
    </div>
    <svg class="adherence-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(member.name)} 近 7 日服药率曲线">
      <line x1="${padX}" y1="${padY}" x2="${padX}" y2="${height - padY}" class="axis" />
      <line x1="${padX}" y1="${height - padY}" x2="${width - padX}" y2="${height - padY}" class="axis" />
      ${[0, 25, 50, 75, 100].map((rate) => {
        const y = padY + plotH - (rate / 100) * plotH;
        return `<line x1="${padX}" y1="${y}" x2="${width - padX}" y2="${y}" class="grid" /><text x="10" y="${y + 4}" class="rate-label">${rate}%</text>`;
      }).join("")}
      <path d="${area}" class="curve-area" />
      <path d="${path}" class="curve-line" />
      ${coords.map((point) => `<circle cx="${point.x}" cy="${point.y}" r="6" class="curve-point" /><text x="${point.x}" y="${point.y - 12}" class="point-label">${point.rate}%</text>`).join("")}
      ${coords.map((point) => `<text x="${point.x}" y="${height - 6}" class="date-label">${point.date.getMonth() + 1}/${point.date.getDate()}</text>`).join("")}
    </svg>`;
}

function showAssistantFloatingHint(text, duration = 2200) {
  const page = document.querySelector("#page-assistant");
  if (!page) return;
  let hint = page.querySelector(".assistant-floating-hint");
  if (!hint) {
    hint = document.createElement("div");
    hint.className = "assistant-floating-hint";
    page.appendChild(hint);
  }
  hint.textContent = text;
  hint.classList.add("show");
  clearTimeout(showAssistantFloatingHint.timer);
  if (duration > 0) showAssistantFloatingHint.timer = setTimeout(() => hint.classList.remove("show"), duration);
}

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function clientRequestInput(options = {}) {
  if (options.body === undefined || options.body === null || options.body === "") return {};
  return typeof options.body === "string" ? JSON.parse(options.body) : options.body;
}

function safeClientRequestInput(options = {}) {
  try {
    return clientRequestInput(options);
  } catch {
    return {};
  }
}

function clientRequestOptions(options = {}, fallbackTimeoutMs = K1_GATEWAY_TIMEOUT_MS) {
  return {
    timeoutMs: options.timeoutMs ?? fallbackTimeoutMs,
    keepalive: Boolean(options.keepalive),
    headers: options.headers || {},
  };
}

function translateClientTimeout(error, timeoutCode) {
  if (error?.code === "gateway_timeout") error.contextCode = timeoutCode;
  throw error;
}
function annotateOcrError(error, context = {}) {
  if (error?.ocrContext) {
    error.ocrContext = { ...context, ...error.ocrContext };
    return error;
  }
  const path = context.path || "unknown";
  const sessionId = context.sessionId || "";
  const page = context.page || "";
  const source = context.source || "";
  const meta = [
    path ? `path=${path}` : "",
    page ? `page=${page}` : "",
    source ? `source=${source}` : "",
    sessionId ? `sessionId=${sessionId}` : "sessionId=missing",
  ].filter(Boolean).join(", ");
  const target = error instanceof Error ? error : new Error(error?.message || "unknown_ocr_error");
  target.code = error?.code || target.code || "ocr_failed";
  target.status = error?.status ?? target.status ?? 0;
  target.requestId = error?.requestId || target.requestId || "";
  target.retryable = error?.retryable ?? target.retryable ?? false;
  target.data = error?.data ?? target.data ?? null;
  target.stage = error?.stage || target.stage || "";
  target.taskId = error?.taskId || target.taskId || "";
  target.sessionId = error?.sessionId || context.sessionId || target.sessionId || "";
  target.causeCode = error?.causeCode || target.causeCode || "";
  target.released = error?.released ?? target.released;
  target.activeTask = error?.activeTask || target.activeTask || null;
  target.ocrContext = { ...(target.ocrContext || {}), ...context };
  target.contextSummary = meta;
  return target;
}

function requireOcrSessionId(sessionId, context = {}) {
  const value = String(sessionId || "").trim();
  if (!value) throw annotateOcrError(new Error("ocr_session_missing"), context);
  return value;
}

async function waitForMedicineBoxReady(sessionId, context = {}, options = {}) {
  const start = Date.now();
  const timeoutMs = options.timeoutMs || 5000;
  const pollMs = options.pollMs || 500;
  let lastError = null;
  while (Date.now() - start < timeoutMs) {
    try {
      const status = await requestMedicineBoxOcr("/status", {
        body: { sessionId: requireOcrSessionId(sessionId, context) },
        path: context.path || "/status",
        page: context.page || "",
        source: context.source || "",
        timeoutMs: options.statusTimeoutMs || 5000,
      });
      if (status?.ready === true && status?.busy !== true) return status;
      lastError = new Error(status?.busy === true ? "ocr_busy" : "ocr_not_ready");
    } catch (error) {
      lastError = error;
    }
    await delay(pollMs);
  }
  throw annotateOcrError(lastError || new Error("ocr_not_ready"), context);
}

function diagnoseLevelText(level) {
  return { OK: "正常", WARNING: "注意", ERROR: "异常" }[level] || "未知";
}

function ensureDiagnosePopup() {
  let popup = document.querySelector("#taskDiagnosePopup");
  if (popup) return popup;
  popup = document.createElement("section");
  popup.id = "taskDiagnosePopup";
  popup.className = "task-diagnose-popup";
  popup.hidden = true;
  popup.innerHTML = `<article><h2 id="taskDiagnosePopupTitle">任务异常</h2><div id="taskDiagnosePopupBody"></div><button class="primary" id="taskDiagnosePopupClose" type="button">知道了</button></article>`;
  document.querySelector(".device")?.appendChild(popup);
  popup.querySelector("#taskDiagnosePopupClose")?.addEventListener("click", () => { popup.hidden = true; });
  return popup;
}

function taskDiagnoseRows(data, originalError) {
  const status = data?.status || data?.task || {};
  const lease = status.sessionLease || {};
  const lastError = status.lastError || {};
  const details = lastError.details || {};
  const rows = [
    ["原始错误", originalError?.message || "未知错误"],
    ["定位结果", data?.message || "未获得诊断结果"],
    ["建议处理", data?.suggestion || "请稍后重试"],
    ["错误码", data?.code || status.error || "OK"],
    ["ready", String(status.ready ?? "-")],
    ["busy", String(status.busy ?? "-")],
    ["页面会话", lease.page || status.sessionId || "-"],
    ["Provider", Array.isArray(status.provider) ? status.provider.join(" + ") : (status.provider || "-")],
  ];
  if (status.activeStage) rows.push(["activeStage", status.activeStage]);
  if (lastError.code) rows.push(["rootCause", lastError.code]);
  if (lastError.stage) rows.push(["errorStage", lastError.stage]);
  if (lastError.message) rows.push(["rootMessage", lastError.message]);
  if (lastError.suggestion) rows.push(["rootSuggestion", lastError.suggestion]);
  if (details.returncode !== undefined && details.returncode !== null) rows.push(["returncode", details.returncode]);
  if (details.stdoutTail) rows.push(["stdoutTail", details.stdoutTail]);
  if (details.stderrTail) rows.push(["stderrTail", details.stderrTail]);
  return rows;
}

async function showTaskDiagnostic(task, originalError) {
  const popup = ensureDiagnosePopup();
  const title = popup.querySelector("#taskDiagnosePopupTitle");
  const body = popup.querySelector("#taskDiagnosePopupBody");
  const names = { ocr: "OCR 药盒识别", prescription_ocr: "药单 OCR", yolo: "YOLO 药片识别", face: "人脸识别", llm: "LLM", asr: "ASR" };
  if (title) title.textContent = `${names[task] || task}异常`;
  if (body) body.innerHTML = `<p>正在读取 ${task} 轻量诊断...</p>`;
  popup.hidden = false;
  try {
    const data = await (await getK1GatewayClient()).diagnostics.task(task, { timeoutMs: 3000 });
    const rows = taskDiagnoseRows(data, originalError).map(([key, value]) => `<tr><th>${key}</th><td>${String(value)}</td></tr>`).join("");
    if (body) body.innerHTML = `<p class="task-diagnose-summary ${String(data.level || "warning").toLowerCase()}">${diagnoseLevelText(data.level)}：${data.message || "已读取当前任务状态"}</p><table>${rows}</table>`;
  } catch (error) {
    if (body) body.innerHTML = `<p class="task-diagnose-summary error">诊断读取失败：${error.message}</p><p>原始错误：${originalError?.message || "未知错误"}</p>`;
  }
}
let k1TaskGate = Promise.resolve();

function runK1TaskGate(taskName, action) {
  const run = k1TaskGate.catch(() => {}).then(async () => {
    return action();
  });
  k1TaskGate = run.then(() => {}, () => {});
  return run;
}
async function requestMedicineBoxOcr(path, options = {}) {
  const inputForError = safeClientRequestInput(options);
  try {
    const medicineBox = (await getK1GatewayClient()).medicineBox;
    const input = clientRequestInput(options);
    const requestOptions = clientRequestOptions(options, K1_MEDICINE_BOX_OCR_TIMEOUT_MS);
    if (path === "/preload") return await medicineBox.preload(input, requestOptions);
    if (path === "/status") return await medicineBox.status(requireOcrSessionId(input.sessionId, { path, page: input.page, source: input.source }), requestOptions);
    if (path === "/recognitions") return await medicineBox.recognize({ ...input, sessionId: requireOcrSessionId(input.sessionId, { path, page: input.page, source: input.source }) }, requestOptions);
    if (path === "/release") return await medicineBox.release({ ...input, sessionId: requireOcrSessionId(input.sessionId, { path, page: input.page, source: input.source }) }, requestOptions);
    throw new Error(`unsupported_medicine_box_operation:${path}`);
  } catch (error) {
    const context = { path: options?.path || path || "medicine-box", page: options?.page || inputForError.page, source: options?.source || inputForError.source, sessionId: inputForError.sessionId };
    throw annotateOcrError(error, context);
  }
}

function setPrescriptionOcrUiState(state, text) {
  if (els.prescriptionOcrStatus) {
    els.prescriptionOcrStatus.dataset.state = state;
    els.prescriptionOcrStatus.textContent = text;
  }
  if (els.prescriptionOcrStart) {
    els.prescriptionOcrStart.disabled = prescriptionOcrBusy;
    els.prescriptionOcrStart.textContent = prescriptionOcrBusy ? "正在识别药单" : "药单ocr识别";
  }
}

function prescriptionOcrFailureMessage(error) {
  const code = String(error?.code || error?.causeCode || "").trim();
  if (["model_unavailable", "module_not_ready", "prescription_model_missing", "prescription_model_unavailable", "prescription_ocr_not_ready"].includes(code)) {
    return "药单 OCR 模型未加载，请稍后重试";
  }
  if (code === "recognition_timeout" || code === "gateway_timeout") {
    return "药单识别超时：请调整药单位置后重新点击“药单ocr识别”";
  }
  return "药单识别失败：请调整药单位置并重新点击“药单ocr识别”";
}

async function requestPrescriptionOcr(path, options = {}) {
  const inputForError = safeClientRequestInput(options);
  try {
    const prescriptionOcr = (await getK1GatewayClient()).prescriptionOcr;
    const input = clientRequestInput(options);
    const requestOptions = clientRequestOptions(options, K1_PRESCRIPTION_OCR_TIMEOUT_MS);
    if (path === "/preload") return await prescriptionOcr.preload(input, requestOptions);
    if (path === "/status") return await prescriptionOcr.status(requireOcrSessionId(input.sessionId, { path, page: input.page, source: input.source }), requestOptions);
    if (path === "/recognitions") return await prescriptionOcr.recognize({ ...input, sessionId: requireOcrSessionId(input.sessionId, { path, page: input.page, source: input.source }) }, requestOptions);
    if (path === "/release") return await prescriptionOcr.release({ ...input, sessionId: requireOcrSessionId(input.sessionId, { path, page: input.page, source: input.source }) }, requestOptions);
    throw new Error("unsupported_prescription_ocr_operation:" + path);
  } catch (error) {
    const context = {
      path: options?.path || path || "prescription-ocr",
      page: options?.page || inputForError.page,
      source: options?.source || inputForError.source,
      sessionId: inputForError.sessionId,
    };
    throw annotateOcrError(error, context);
  }
}

async function waitForPrescriptionOcrReady(sessionId, options = {}) {
  const startedAt = Date.now();
  const timeoutMs = options.timeoutMs || 120000;
  const pollMs = options.pollMs || 400;
  let lastError = null;
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const status = await requestPrescriptionOcr("/status", {
        body: { sessionId },
        path: "/status",
        page: "member-form",
        source: "member-form-prescription",
        timeoutMs: options.statusTimeoutMs || 5000,
      });
      if (status?.ready === true && status?.busy !== true) return status;
      lastError = new Error(status?.busy === true ? "prescription_ocr_busy" : "prescription_ocr_not_ready");
    } catch (error) {
      lastError = error;
    }
    await delay(pollMs);
  }
  throw annotateOcrError(lastError || new Error("prescription_ocr_not_ready"), {
    path: "/status",
    page: "member-form",
    source: "member-form-prescription",
    sessionId,
  });
}

async function releasePrescriptionOcrSession(sessionId, preloadPromise = null) {
  if (!sessionId) return;
  if (preloadPromise) await Promise.resolve(preloadPromise).catch(() => {});
  await requestPrescriptionOcr("/release", {
    method: "POST",
    body: JSON.stringify({ sessionId, page: "member-form", source: "member-form-prescription" }),
    timeoutMs: 5000,
    keepalive: true,
  }).catch(() => {});
}

function resetPrescriptionOcrWorkflow({ clearDraft = false } = {}) {
  const sessionId = prescriptionOcrSessionId;
  const pendingPreload = prescriptionOcrPreloadPromise;
  prescriptionOcrGeneration += 1;
  prescriptionOcrSessionId = "";
  prescriptionOcrPreloadPromise = null;
  prescriptionOcrBusy = false;
  if (clearDraft) clearPrescriptionOcrDraft();
  else setPrescriptionOcrUiState("idle", "药单识别待命");
  if (sessionId) void releasePrescriptionOcrSession(sessionId, pendingPreload);
}

function startPrescriptionOcrWorkflow() {
  if (prescriptionOcrBusy) return Promise.resolve();
  removeUntouchedPlaceholderMedicineCard();
  const generation = ++prescriptionOcrGeneration;
  const sessionId = "prescription-member-form-" + Date.now() + "-" + generation;
  prescriptionOcrSessionId = sessionId;
  prescriptionOcrBusy = true;
  setPrescriptionOcrUiState("loading", "正在准备共享 OCR");
  return runK1TaskGate("prescription-ocr", () => recognizePrescriptionForMemberForm(generation, sessionId));
}

async function recognizePrescriptionForMemberForm(generation, sessionId) {
  if (generation !== prescriptionOcrGeneration || activePageId !== "member-form") return;
  let preloadPromise = null;
  try {
    preloadPromise = requestPrescriptionOcr("/preload", {
      method: "POST",
      body: JSON.stringify({ sessionId, page: "member-form", source: "member-form-prescription" }),
    });
    prescriptionOcrPreloadPromise = preloadPromise;
    let preloadError = null;
    try {
      const preload = await preloadPromise;
      if (preload?.ready !== true) throw new Error("prescription_ocr_not_ready");
    } catch (error) {
      if (!isAssistantOcrPreloadRecoveryCandidate(error)) throw error;
      preloadError = error;
    }
    if (generation !== prescriptionOcrGeneration || activePageId !== "member-form") return;
    try {
      await waitForPrescriptionOcrReady(sessionId);
    } catch (error) {
      if (preloadError) throw preloadError;
      throw error;
    }
    if (generation !== prescriptionOcrGeneration || activePageId !== "member-form") return;
    setPrescriptionOcrUiState("capturing", "正在抓取并识别药单");
    const result = await requestPrescriptionOcr("/recognitions", {
      method: "POST",
      body: JSON.stringify({ sessionId, page: "member-form", source: "member-form-prescription" }),
    });
    if (generation !== prescriptionOcrGeneration || activePageId !== "member-form") return;
    if (result?.status === "no_text") {
      restorePlaceholderMedicineCard();
      if (els.prescriptionOcrReview) {
        els.prescriptionOcrReview.hidden = true;
        els.prescriptionOcrReview.innerHTML = "";
      }
      const message = "未检测到药单，请将药单放到识别区，并点击“药单ocr识别”按钮";
      setPrescriptionOcrUiState("error", message);
      showToast(message);
      return;
    }
    renderPrescriptionOcrDraft(result || {});
    setPrescriptionOcrUiState("review", result?.medicines?.length ? "识别完成，等待确认" : "识别完成，请人工核对");
  } catch (error) {
    if (generation !== prescriptionOcrGeneration || activePageId !== "member-form") return;
    restorePlaceholderMedicineCard();
    const message = prescriptionOcrFailureMessage(error);
    setPrescriptionOcrUiState("error", message);
    showToast(message);
    void showTaskDiagnostic("prescription_ocr", error);
  } finally {
    if (prescriptionOcrPreloadPromise === preloadPromise) prescriptionOcrPreloadPromise = null;
    if (prescriptionOcrSessionId === sessionId) prescriptionOcrSessionId = "";
    await releasePrescriptionOcrSession(sessionId);
    if (generation === prescriptionOcrGeneration) {
      prescriptionOcrBusy = false;
      setPrescriptionOcrUiState(
        els.prescriptionOcrStatus?.dataset.state || "idle",
        els.prescriptionOcrStatus?.textContent || "药单识别待命",
      );
    }
  }
}

async function requestAssistantLifecycle(path, options = {}) {
  const inputForError = safeClientRequestInput(options);
  const fallbackSessionId = String(inputForError.sessionId || assistantLifecycleSessionId || "").trim();
  try {
    const assistant = (await getK1GatewayClient()).assistant;
    const input = clientRequestInput(options);
    const sessionId = String(input.sessionId || currentAssistantLifecycleSession() || "").trim();
    const requestOptions = clientRequestOptions(options);
    const context = { path: `assistant${path}`, page: input.page || "assistant", source: "assistant", sessionId };
    if (!sessionId) throw annotateOcrError(new Error("assistant_session_missing"), context);
    const payload = { ...input, page: input.page || "assistant", sessionId };
    if (path === "/preload") return await assistant.preload(payload, requestOptions);
    if (path === "/release") return await assistant.release(payload, requestOptions);
    throw new Error(`unsupported_assistant_operation:${path}`);
  } catch (error) {
    const context = { path: `assistant${path}`, page: inputForError.page || "assistant", source: "assistant", sessionId: fallbackSessionId || assistantLifecycleSessionId || "" };
    throw annotateOcrError(error, context);
  }
}

function cabinetDoorStateFromGpio(payload) {
  return payload?.source === "gpio";
}

function cabinetClosedFromPayload(payload) {
  return cabinetDoorStateFromGpio(payload) && payload?.closed === true;
}

function cabinetOpenFromPayload(payload) {
  return cabinetDoorStateFromGpio(payload) && payload?.closed === false;
}

async function openCabinetViaGateway(cabinetId, item = null) {
  return (await getK1GatewayClient()).cabinet.open({
      cabinetId,
      memberId: item?.member?.id || "",
      medicineName: item?.med?.name || "",
  });
}

async function refreshCabinetStateViaGateway(cabinetId) {
  return (await getK1GatewayClient()).cabinet.state(cabinetId);
}

async function openSelectedCabinetFromDetail() {
  const cabinetId = selectedCabinetIndex + 1;
  const item = allMedicineItems()[selectedCabinetIndex];
  const text = document.querySelector("#detailDoorText");
  try {
    await openCabinetViaGateway(cabinetId, item);
    if (text) text.textContent = "柜门：已发送打开指令";
    showToast(`${cabinetId}号柜已发送真实开柜指令`);
  } catch (error) {
    if (text) text.textContent = `柜门：打开失败（${error.message}）`;
    showToast(`开柜失败：${error.message}`);
  }
}

async function refreshSelectedCabinetDoorState() {
  const cabinetId = selectedCabinetIndex + 1;
  const text = document.querySelector("#detailDoorText");
  try {
    const state = await refreshCabinetStateViaGateway(cabinetId);
    const closed = cabinetClosedFromPayload(state);
    if (text) text.textContent = `柜门：${closed ? "关闭" : "打开"}`;
    showToast(`${cabinetId}号柜当前${closed ? "已关闭" : "仍处于打开状态"}`);
  } catch (error) {
    if (text) text.textContent = `柜门：状态读取失败（${error.message}）`;
    showToast(`柜门状态读取失败：${error.message}`);
  }
}



async function refreshCurrentTakeDoorState() {
  const item = takeQueue[takeIndex];
  if (!item) return;
  try {
    const state = await refreshCabinetStateViaGateway(item.drawer);
    if (!cabinetDoorStateFromGpio(state)) throw new Error("cabinet_state_not_from_gpio");
    const closed = cabinetClosedFromPayload(state);
    takeDrawerOpen = !closed;
    if (takeDrawerOpen) takeDoorObservedOpen = true;
    doorConfirmed = closed && takeDoorObservedOpen;
    takeDoorState = closed ? "closed" : "open";
    updateTakeVisualState();
    renderTakeRecognitionControls();
    const text = doorConfirmed
      ? (pillConfirmed ? "柜门状态已确认关闭，正在复检识别区。" : "柜门已关闭，请完成数量识别后再进入下一项。")
      : takeDrawerOpen
      ? "柜门已打开，请取药后放入识别区核验。"
      : "柜门未打开，请等待药柜打开或刷新状态。";
    els.takeState.textContent = text;
    if (takeDrawerOpen) scheduleTakeDoorMonitoring(item, takeWorkflowToken);
    tryNextDrug();
  } catch (error) {
    takeDoorState = "error";
    renderTakeRecognitionControls();
    els.takeState.textContent = `柜门状态读取失败：${error.message}`;
  }
}

async function requestK1Asr(path, options = {}) {
  const asr = (await getK1GatewayClient()).asr;
  const input = clientRequestInput(options);
  const requestOptions = clientRequestOptions(options);
  if (path === "/asr/preload") return asr.preload(input, requestOptions);
  if (path === "/asr/recordings") return asr.start(input, requestOptions);
  const stopped = path.match(/^\/asr\/recordings\/([^/]+)\/stop$/);
  if (stopped) return asr.stop(decodeURIComponent(stopped[1]), input, requestOptions);
  const status = path.match(/^\/asr\/recordings\/([^/]+)$/);
  if (status) return asr.status(decodeURIComponent(status[1]), requestOptions);
  throw new Error(`unsupported_asr_operation:${path}`);
}

function assistantVoiceInput(targetId = "") {
  const explicit = targetId ? document.querySelector(`#${targetId}`) : null;
  if (explicit) return explicit;
  if (activeKeyboardInput && ["assistantQuickInput", "chatInput"].includes(activeKeyboardInput.id)) return activeKeyboardInput;
  return els.assistantChat && !els.assistantChat.hidden ? document.querySelector("#chatInput") : document.querySelector("#assistantQuickInput");
}

function setAssistantVoiceButtons(state) {
  assistantVoiceState = state;
  const label = state === "recording" ? "结束" : state === "loading" ? "加载" : state === "recognizing" ? "识别" : "语音";
  document.querySelectorAll("#assistantVoice, #voiceBtn").forEach((button) => {
    button.textContent = label;
    button.classList.toggle("listening", state === "recording");
    button.classList.toggle("recognizing", state === "loading" || state === "recognizing");
    button.disabled = state === "loading" || state === "recognizing";
    button.setAttribute("aria-pressed", state === "recording" ? "true" : "false");
  });
}

function writeVoiceTextToInput(input, text) {
  const value = String(text || "").trim();
  if (!input || !value) return;
  const prefix = input.value.trim();
  input.value = prefix ? `${prefix}${value}` : value;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.focus({ preventScroll: true });
}

async function waitForK1AsrModel() {
  const start = Date.now();
  await requestK1Asr("/asr/preload", { method: "POST", body: JSON.stringify({}) });
  while (Date.now() - start < K1_ASR_MODEL_TIMEOUT_MS) {
    const status = await requestK1Asr("/asr/preload", { method: "POST", body: JSON.stringify({}) });
    if (status.model?.ready) return status;
    if (status.state === "error") throw new Error("asr_model_error");
    showAssistantFloatingHint("语音模型加载中，请稍等...", 0);
    await delay(K1_ASR_POLL_MS);
  }
  throw new Error("model_load_timeout");
}

async function waitForK1AsrResult(expectedJobId = "") {
  const start = Date.now();
  while (Date.now() - start < K1_ASR_RECOGNITION_TIMEOUT_MS) {
    const status = await requestK1Asr(`/asr/recordings/${encodeURIComponent(expectedJobId)}`);
    if (status.state === "done") return status.text || "";
    if (status.state === "error") throw new Error(status.errorCode || "recognition_error");
    showAssistantFloatingHint("正在识别语音，请稍等...", 0);
    await delay(K1_ASR_POLL_MS);
  }
  throw new Error("recognition_timeout");
}

async function startK1VoiceInputInner(targetId = "") {
  const input = assistantVoiceInput(targetId);
  assistantVoiceTargetId = input?.id || targetId || "assistantQuickInput";
  closeKeyboard();
  setAssistantVoiceButtons("loading");
  try {
    showAssistantFloatingHint("正在连接 K1 语音模型...", 0);
    await waitForK1AsrModel();
    const started = await requestK1Asr("/asr/recordings", {
      method: "POST",
      body: JSON.stringify({ sessionId: assistantSessionId(), source: "assistant" }),
    });
    assistantVoiceJobId = started.jobId || "";
    if (!assistantVoiceJobId) throw new Error("asr_job_missing");
    setAssistantVoiceButtons("recording");
    showAssistantFloatingHint("正在录音，说完后点“结束”。", 0);
    return assistantVoiceJobId;
  } catch (error) {
    setAssistantVoiceButtons("idle");
    showAssistantFloatingHint(`语音启动失败：${error.message}`);
    void showTaskDiagnostic("asr", error);
    return "";
  }
}

async function stopK1VoiceInputInner() {
  setAssistantVoiceButtons("recognizing");
  try {
    showAssistantFloatingHint("录音结束，正在转文字...", 0);
    if (!assistantVoiceJobId) throw new Error("asr_job_missing");
    const stopped = await requestK1Asr(`/asr/recordings/${encodeURIComponent(assistantVoiceJobId)}/stop`, { method: "POST", body: JSON.stringify({}) });
    const text = await waitForK1AsrResult(stopped.jobId || assistantVoiceJobId);
    writeVoiceTextToInput(assistantVoiceInput(assistantVoiceTargetId), text);
    showAssistantFloatingHint(text ? "语音已转成文字并填入输入框。" : "没有识别到清晰语音，请再试一次。", 2600);
  } catch (error) {
    showAssistantFloatingHint(`语音识别失败：${error.message}`, 3200);
    void showTaskDiagnostic("asr", error);
  } finally {
    assistantVoiceJobId = "";
    assistantVoiceTargetId = "";
    setAssistantVoiceButtons("idle");
  }
}

function startK1VoiceInput(targetId = "") {
  return runK1TaskGate("asr-start", () => startK1VoiceInputInner(targetId));
}

function stopK1VoiceInput() {
  return runK1TaskGate("asr-stop", () => stopK1VoiceInputInner());
}
function toggleK1VoiceInput(targetId = "") {
  if (assistantVoiceState === "recording") return stopK1VoiceInput();
  if (assistantVoiceState !== "idle") {
    showAssistantFloatingHint("语音正在处理中，请稍等。", 1600);
    return Promise.resolve();
  }
  return startK1VoiceInput(targetId);
}

function assistantSessionId() {
  return currentAssistantLifecycleSession();
}

async function requestMedicineQa(question) {
  const member = activeMember();
  return (await getK1GatewayClient()).qa.ask({
      question,
      sessionId: assistantSessionId(),
      memberId: member?.id || "",
      context: {
        medicines: (member?.medicines || []).map((medicine) => medicine.name).filter(Boolean),
        diseases: String(member?.disease || "")
          .split(/[、,，\s]+/)
          .filter(Boolean),
      },
  });
}

async function speakAssistantAnswer(text) {
  // Open-ended assistant replies are text-only in static-audio mode.
  void text;
}

function warmAssistantTts() {
  // Static workflow clips have no model warmup step.
}

function renderReports() {
  if (!els.reportList) return;
  if (!reports.length) {
    els.reportList.innerHTML = `<article class="empty-report"><strong>暂无记录</strong><span>成员新增、药单 OCR、放药引导、取药监督和手动完成后，会自动写入这里。</span></article>`;
    return;
  }
  els.reportList.innerHTML = reports
    .map(
      (report) => `<article>
        <div class="report-copy">
          <strong>${escapeHtml(reportDisplayTime(report))} ${escapeHtml(report.type)}</strong>
          <span>${escapeHtml(report.memberName)} · ${escapeHtml(report.detail)}</span>
        </div>
        <button class="report-delete" data-report-delete="${escapeHtml(report.id)}" type="button" aria-label="删除这条记录">×</button>
      </article>`,
    )
    .join("");
}

function renderAssistantHome() {
  const tabs = els.assistantMode?.querySelector(".assistant-tabs");
  if (tabs) {
    tabs.innerHTML = `
      <button class="mode-card selected" data-assistant="quick" type="button"><strong>快速咨询</strong><span>文字、语音快速提问</span></button>
      <div class="assistant-patient-menu">
        <button class="mode-card" data-toggle-patient-menu type="button"><strong>成员答疑</strong><span>选择患者精准答疑</span></button>
        <div class="patient-menu-list">
          ${members
            .map(
              (member) => `<button class="${member.current ? "selected" : ""}" data-patient="${member.id}" type="button">
                <span>${escapeHtml(member.name)}</span>
                <small>${escapeHtml(member.disease || "成员用药资料")}</small>
              </button>`,
            )
            .join("")}
        </div>
      </div>`;
  }
  if (els.patientSelector) els.patientSelector.innerHTML = "";
  const quickInput = document.querySelector("#assistantQuickInput");
  if (quickInput) quickInput.placeholder = "输入想咨询的问题";
  const more = document.querySelector("#assistantMore");
  if (more) more.textContent = "发送";
}


function beginLoadGuide(source = "manual", memberId = "") {
  closeKeyboard();
  const expectedMemberId = memberId || activeMember()?.id || "";
  const expectedMember = members.find((item) => item.id === expectedMemberId) || activeMember();
  if (isWangGrandpaMember(expectedMember)) preloadWangGrandpaAudio();
  stopLoadGuideWorkflow(true);
  loadPromptedItemKey = "";
  if (restoreSavedFlowState("load", expectedMemberId)) return;
  clearSavedFlowState("load", expectedMemberId);
  loadStep = 0;
  loadItemIndex = 0;
  activeLoadMemberId = expectedMemberId;
  loadDrawerOpen = false;
  loadDoorState = "unknown";
  loadGuideFinished = false;
  loadOpenedItemKey = "";
  setPage("load-guide");
  updateLoadGuide();
  const member = members.find((item) => item.id === activeLoadMemberId) || activeMember();
  addReport("放药引导", source === "ocr" ? "药单 OCR 后开始放药引导" : `${member?.name || "当前成员"} 开始放药引导`, member?.name || "系统");
  if (source === "ocr") showToast("OCR 已生成药品清单，请按引导放入药柜");
}

function focusChatInput() {
  window.setTimeout(() => {
    const input = document.querySelector("#chatInput");
    input?.focus({ preventScroll: true });
  }, 0);
}

function startAssistant(mode, options = {}) {
  assistantMode = mode || "quick";
  closeKeyboard();
  els.assistantMode.hidden = true;
  els.assistantChat.hidden = false;
  els.assistantMode.classList.remove("chatting");
  els.chatTitle.textContent = "智能用药助手";
  const member = activeMember();
  if (els.assistantChat.dataset.active === "1") {
    if (options.focusChat) focusChatInput();
    return;
  }
  els.assistantChat.dataset.active = "1";
  void preloadAssistantCoreResources().catch((error) => {
    showAssistantFloatingHint(`助手资源预加载失败：${error.message}`, 3200);
  });
  const today = new Date();
  const dateText = `${today.getMonth() + 1}月${today.getDate()}日`;
  els.chatMessages.innerHTML = `
    <div class="chat-date">${dateText}</div>
    <div class="msg">${mode === "member" ? `已选择 ${escapeHtml(member?.name || "当前患者")}，我会围绕该成员服药方案答疑。` : "可以文字或语音咨询。"}</div>`;
  if (options.focusChat) focusChatInput();
}

async function sendAssistantTextInner(text, options = {}) {
  const question = String(text || "").trim();
  if (!question) return;
  if (!options.hideQuestion) {
    els.chatMessages.insertAdjacentHTML("beforeend", `<div class="msg user">${escapeHtml(options.displayQuestion || question)}</div>`);
  }
  const pendingId = `reply-${Date.now()}`;
  els.chatMessages.insertAdjacentHTML("beforeend", `<div class="msg pending-reply" id="${pendingId}">正在通过本地问药模型生成回答...</div>`);
  els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
  try {
    await preloadAssistantCoreResources();
    let result;
    try {
      result = await requestMedicineQa(question);
    } catch (error) {
      // A lost browser timer or a gateway restart can invalidate only the lease.
      // The rejected request never reaches QA, so one repair-and-retry is safe.
      if (!isAssistantSessionRecoveryCandidate(error)) throw error;
      assistantLifecycleReady = false;
      assistantLifecycleLeaseRenewedAt = 0;
      await preloadAssistantCoreResources({ force: true });
      result = await requestMedicineQa(question);
    }
    const answer = String(result.answer || "本地问药助手暂时没有生成回答，请换一种问法再试。").trim();
    const pending = document.querySelector(`#${pendingId}`);
    if (pending) {
      pending.classList.remove("pending-reply");
      pending.textContent = answer;
      if (result.risk_level) pending.dataset.risk = result.risk_level;
    }
    els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
    speakAssistantAnswer(answer);
  } catch (error) {
    const pending = document.querySelector(`#${pendingId}`);
    if (pending) pending.textContent = `本地问药模型暂时不可用：${error.message}`;
    els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
    void showTaskDiagnostic("llm", error);
  }
}

function sendAssistantText(text) {
  return runK1TaskGate("medicine-qa", () => sendAssistantTextInner(text));
}

function currentAssistantLifecycleSession() {
  if (!assistantLifecycleSessionId) assistantLifecycleSessionId = "assistant-lifecycle-" + Date.now();
  return assistantLifecycleSessionId;
}

function isAssistantSessionRecoveryCandidate(error) {
  const code = String(error?.code || error?.causeCode || "").trim();
  return code === "assistant_session_not_ready" || code === "assistant_session_expired";
}

async function preloadAssistantCoreResources({ force = false } = {}) {
  const sessionId = currentAssistantLifecycleSession();
  const leaseIsFresh = assistantLifecycleLeaseRenewedAt > 0 &&
    Date.now() - assistantLifecycleLeaseRenewedAt < K1_ASSISTANT_LEASE_RENEW_MS;
  if (!force && assistantLifecycleReady && sessionId && leaseIsFresh) return sessionId;
  if (assistantLifecyclePreload) return assistantLifecyclePreload;
  assistantLifecyclePreload = (async () => {
    const assistant = await requestAssistantLifecycle("/preload", {
      method: "POST",
      body: JSON.stringify({ sessionId, page: "assistant" }),
    });
    if (assistant?.llm?.ready !== true || assistant?.qa?.ready !== true) throw new Error("assistant_not_ready");
    assistantLifecycleReady = true;
    assistantLifecycleLeaseRenewedAt = Date.now();
    scheduleAssistantLifecycleLeaseRenewal(sessionId);
    return sessionId;
  })();
  try {
    return await assistantLifecyclePreload;
  } catch (error) {
    if (sessionId === assistantLifecycleSessionId) {
      assistantLifecycleReady = false;
      assistantLifecycleLeaseRenewedAt = 0;
    }
    throw error;
  } finally {
    assistantLifecyclePreload = null;
  }
}

function clearAssistantLifecycleLeaseRenewal() {
  window.clearTimeout(assistantLifecycleLeaseTimer);
  assistantLifecycleLeaseTimer = 0;
}

function scheduleAssistantLifecycleLeaseRenewal(sessionId) {
  clearAssistantLifecycleLeaseRenewal();
  if (!sessionId || activePageId !== "assistant") return;
  assistantLifecycleLeaseTimer = window.setTimeout(async () => {
    if (sessionId !== assistantLifecycleSessionId || activePageId !== "assistant") return;
    try {
      await preloadAssistantCoreResources({ force: true });
    } catch {
      // The next question will retry the lease repair before it reaches QA.
    }
    if (sessionId === assistantLifecycleSessionId && activePageId === "assistant") {
      scheduleAssistantLifecycleLeaseRenewal(sessionId);
    }
  }, K1_ASSISTANT_LEASE_RENEW_MS);
}

async function preloadAssistantResources() {
  const sessionId = currentAssistantLifecycleSession();
  const ocr = await requestMedicineBoxOcr("/preload", {
    method: "POST",
    body: JSON.stringify({ sessionId, source: "assistant", page: "assistant" }),
  });
  if (ocr?.ready !== true) throw new Error("ocr_not_ready");
  scheduleAssistantMedicineBoxLeaseRenewal(sessionId);
  return sessionId;
}

function clearAssistantMedicineBoxLeaseRenewal() {
  window.clearTimeout(assistantMedicineBoxLeaseTimer);
  assistantMedicineBoxLeaseTimer = 0;
}

function scheduleAssistantMedicineBoxLeaseRenewal(sessionId) {
  clearAssistantMedicineBoxLeaseRenewal();
  if (!sessionId || activePageId !== "assistant") return;
  assistantMedicineBoxLeaseTimer = window.setTimeout(async () => {
    if (sessionId !== assistantLifecycleSessionId || activePageId !== "assistant") return;
    try {
      await requestMedicineBoxOcr("/preload", {
        method: "POST",
        body: JSON.stringify({ sessionId, source: "assistant", page: "assistant" }),
        timeoutMs: 5000,
        keepalive: true,
      });
    } catch {
      // The backend lease remains the fallback if the browser loses connectivity.
    }
    if (sessionId === assistantLifecycleSessionId && activePageId === "assistant") {
      scheduleAssistantMedicineBoxLeaseRenewal(sessionId);
    }
  }, K1_MEDICINE_BOX_LEASE_RENEW_MS);
}

function releaseAssistantResources({ keepWarm = false } = {}) {
  const sessionId = assistantLifecycleSessionId;
  if (assistantVoiceState === "recording" && assistantVoiceJobId) {
    const abandonedJobId = assistantVoiceJobId;
    assistantVoiceJobId = "";
    assistantVoiceTargetId = "";
    setAssistantVoiceButtons("idle");
    void requestK1Asr(`/asr/recordings/${encodeURIComponent(abandonedJobId)}/stop`, {
      method: "POST",
      body: JSON.stringify({ reason: "assistant_page_left" }),
      timeoutMs: 5000,
      keepalive: true,
    }).catch(() => {});
  }
  clearAssistantLifecycleLeaseRenewal();
  clearAssistantMedicineBoxLeaseRenewal();
  assistantLifecycleSessionId = "";
  assistantLifecyclePreload = null;
  assistantLifecycleReady = false;
  assistantLifecycleLeaseRenewedAt = 0;
  els.assistantChat.dataset.active = "0";
  if (!sessionId) return;
  void Promise.allSettled([
    requestAssistantLifecycle("/release", {
      method: "POST",
      body: JSON.stringify({ sessionId, page: "assistant", keepWarm }),
      timeoutMs: 5000,
      keepalive: true,
    }),
    requestMedicineBoxOcr("/release", {
      method: "POST",
      body: JSON.stringify({ sessionId, source: "assistant", page: "assistant", purpose: "assistant-photo" }),
      timeoutMs: 5000,
      keepalive: true,
    }),
  ]);
}

function setAssistantOcrButtons(busy) {
  document.querySelectorAll("#assistantPhoto, #chatPhoto").forEach((button) => {
    button.disabled = busy;
    button.textContent = busy ? "识别中" : "拍照";
  });
}

function appendRecognizedMedicinePrompt(medicineName) {
  const name = String(medicineName || "").trim();
  if (!name || !els.chatMessages) return;
  els.chatMessages.insertAdjacentHTML(
    "beforeend",
    `<div class="msg user">${escapeHtml(name)}</div>`,
  );
  els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
}

function isAssistantOcrPreloadRecoveryCandidate(error) {
  const code = error?.code || error?.message;
  return code === "gateway_timeout" ||
    code === "ocr_timeout" ||
    code === "gateway_request_failed" ||
    error?.name === "TypeError";
}

async function ensureAssistantOcrReadyForRecognition() {
  let sessionId = "";
  let preloadError = null;
  try {
    sessionId = await preloadAssistantResources();
  } catch (error) {
    sessionId = currentAssistantLifecycleSession();
    if (!isAssistantOcrPreloadRecoveryCandidate(error)) throw annotateOcrError(error, { path: error?.ocrContext?.path || "assistant/preload", page: "assistant", source: error?.ocrContext?.source || "assistant", sessionId });
    // A preload response may be lost after the gateway has acquired the session.
    // Probe the already-acquired session instead of issuing another preload or recognition command.
    preloadError = error;
  }

  try {
    await waitForMedicineBoxReady(sessionId, { path: "/status", page: "assistant", source: "assistant" }, { timeoutMs: 5000, pollMs: 500 });
    scheduleAssistantMedicineBoxLeaseRenewal(sessionId);
    return sessionId;
  } catch (error) {
    if (preloadError) throw annotateOcrError(preloadError, { path: "assistant/preload", page: "assistant", source: "assistant", sessionId });
    throw error;
  }
}

async function captureMedicineBoxFromAssistantInner(token) {
  if (els.assistantChat.hidden) startAssistant("quick");
  let sessionId = "";
  const pendingId = `medicine-box-${Date.now()}`;
  els.chatMessages.insertAdjacentHTML("beforeend", `<div class="msg pending-reply" id="${pendingId}">正在识别药盒名称...</div>`);
  els.chatMessages.scrollTop = els.chatMessages.scrollHeight;

  try {
    sessionId = await ensureAssistantOcrReadyForRecognition();

    const result = await requestMedicineBoxOcr("/recognitions", {
      method: "POST",
      body: JSON.stringify({ sessionId, source: "assistant", page: "assistant" }),
      path: "/recognitions",
      page: "assistant",
      source: "assistant",
    });
    if (token !== assistantOcrToken) return;
    const medicineName = String(result?.medicineName || "").trim();
    if (!medicineName) throw new Error("medicine_name_missing");
    const pending = document.querySelector(`#${pendingId}`);
    pending?.remove();
    appendRecognizedMedicinePrompt(medicineName);
    await sendAssistantTextInner(medicineName, { hideQuestion: true });
  } catch (error) {
    if (token !== assistantOcrToken) return;
    const pending = document.querySelector(`#${pendingId}`);
    if (pending) {
      pending.classList.remove("pending-reply");
      pending.textContent = `药盒识别失败：${error.message}。请检查是否将药盒正确放入识别区。`;
    }
    void showTaskDiagnostic("ocr", error);
  }
}

function captureMedicineBoxFromAssistant() {
  if (assistantOcrBusy) return Promise.resolve();
  assistantOcrBusy = true;
  const token = ++assistantOcrToken;
  setAssistantOcrButtons(true);
  return runK1TaskGate("assistant-ocr", () => captureMedicineBoxFromAssistantInner(token)).finally(() => {
    assistantOcrBusy = false;
    if (token === assistantOcrToken) setAssistantOcrButtons(false);
  });
}

function clearAssistantPage() {
  assistantOcrToken += 1;
  const quickInput = document.querySelector("#assistantQuickInput");
  const chatInput = document.querySelector("#chatInput");
  if (quickInput) quickInput.value = "";
  if (chatInput) chatInput.value = "";
  document.querySelector("#page-assistant .assistant-floating-hint")?.remove();
  closeKeyboard();
  els.assistantMode.hidden = true;
  els.assistantChat.hidden = false;
  els.assistantChat.dataset.active = "1";
  const member = activeMember();
  const today = new Date();
  const dateText = `${today.getMonth() + 1}月${today.getDate()}日`;
  const welcome = assistantMode === "member" ? `已选择 ${escapeHtml(member?.name || "当前成员")}，我会围绕该成员服药方案答疑。` : "可以文字或语音咨询。";
  els.chatMessages.innerHTML = `<div class="chat-date">${dateText}</div><div class="msg">${welcome}</div>`;
  els.chatMessages.scrollTop = 0;
  showToast("已清空当前咨询内容");
}

function sendChat() {
  const input = document.querySelector("#chatInput");
  const text = input.value.trim();
  if (!text) return;
  sendAssistantText(text);
  input.value = "";
  closeKeyboard();
  els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
}

function takeQueueForRow(row) {
  if (!row) return [];
  return row.meds.map((med) => {
    const item = allMedicineItems().find((entry) => entry.member.id === row.member.id && entry.med.name === med.name);
    return { member: row.member, med, drawer: item?.drawer || 1 };
  });
}

function prepareTakeQueue(doseId = "") {
  const next = doseId ? reminderById(doseId) : reminderById(activeDoseId) || nextReminder();
  currentTakeDoseId = next?.id || "";
  if (!next) return [];
  return takeQueueForRow(next);
}
function renderMedicationConfirm() {
  const medicineText = takeQueue.length
    ? takeQueue.map((item) => `${item.med.name}${item.med.amount || "-"}${item.med.unit || ""}`).join("，")
    : "暂无本次待确认药物";
  const voiceText = `请您确认本次药物名称及其数量：${medicineText}。确认无误后点击继续。`;
  const medicineRows = takeQueue.map((item) => `<tr><td>${escapeHtml(item.med.name)}</td><td>${escapeHtml(item.med.amount || "-")}${escapeHtml(item.med.unit || "")}</td></tr>`);
  const emptyRows = takeQueue.length ? [] : [`<tr><td>暂无本次待确认药物</td><td>-</td></tr>`];
  const visibleRows = medicineRows.length ? medicineRows : emptyRows;
  const placeholderRows = Array.from({ length: Math.max(0, 4 - visibleRows.length) }, () => `<tr class="confirm-placeholder-row" aria-hidden="true"><td>&nbsp;</td><td>&nbsp;</td></tr>`)
    .join("");
  const rows = `${visibleRows.join("")}${placeholderRows}`;
  const voiceEl = document.querySelector("#confirmVoiceText");
  const rowsEl = document.querySelector("#confirmMedRows");
  if (voiceEl) voiceEl.textContent = voiceText;
  if (rowsEl) rowsEl.innerHTML = rows;
  if (isWangGrandpaTakeRow(reminderById(currentTakeDoseId || activeDoseId))) {
    preloadWangGrandpaAudio();
    void playWangGrandpaAudio("take_confirm", voiceText);
  } else {
    announcePageOnce("med-confirm", voiceText, true, 1, "pre_take_confirm_example");
  }
}
function setOcrBoxState(text) {
  const visual = document.querySelector("#ocrDetectedVisual");
  const scanText = document.querySelector("#ocrScanText");
  if (visual) visual.textContent = text;
  if (scanText) scanText.textContent = text === "等待识别" ? "请将药物放入识别框，系统将自动识别数量。" : "识别完成，结果已同步到右侧识别区。";
}

function setTakeRecognitionResult(text) {
  const result = document.querySelector("#recognitionResult");
  if (result) result.textContent = String(text || "等待识别");
}



function updateTake() {
  const item = takeQueue[takeIndex];
  if (!item) {
    const row = todaySchedule().find((entry) => entry.id === currentTakeDoseId);
    const completedId = currentTakeDoseId;
    completeDose(row, "取药监督完成");
    currentTakeDoseId = "";
    clearSavedFlowState();
    renderAll();
    if (advanceVoiceSequence(completedId)) return;
    showToast("本轮取药完成，请开始服药");
    setPage("home");
    return;
  }
  takeWorkflowToken += 1;
  takeRecognitionAttemptToken += 1;
  clearTimeout(takeClearanceTimer);
  takeClearanceTimer = 0;
  stopTakeDoorMonitoring();
  takeRecognitionSimulated = false;
  takeItemCompleting = false;
  takeRecognitionBusy = false;
  takeClearanceBusy = false;
  takeClearanceZeroStreak = 0;
  takeDrawerOpening = false;
  takeDrawerOpen = false;
  takeDoorObservedOpen = false;
  takeDoorPollFailures = 0;
  takeDoorState = "announcing";
  takePromptCompleted = false;
  // Start the pill-count session as soon as the take page is rendered.  The
  // backend keeps OV5647 warm for this session, so the recognition button
  // consumes the latest stable frame instead of starting the camera on click.
  if (activePageId === "take") void ensurePillCountReady(item).catch(() => {});
  els.takeDrug.textContent = `${item.med.name} ${item.med.amount}${item.med.unit}`;
  const prompt = `请从${item.drawer}号药柜取出${item.med.name}${item.med.amount}${item.med.unit}，并放到识别区核验。`;
  els.takeHint.textContent = prompt;
  els.takeState.textContent = "等待数量识别与柜门关闭。";
  document.querySelector("#targetCount").textContent = `${item.med.amount}${item.med.unit}`;
  document.querySelector("#detectedCount").textContent = "等待识别";
  setTakeRecognitionResult("请放入识别框");
  setOcrBoxState("等待识别");
  clearTimeout(takeOcrTimer);
  wangGrandpaClearanceMessage = "";
  wangGrandpaClearanceAt = 0;
  pillConfirmed = false;
  doorConfirmed = false;
  updateTakeVisualState();
  renderTakeRecognitionControls();
  clearSavedFlowState("take", item.member?.id || "", currentTakeDoseId || activeDoseId);
  const token = takeWorkflowToken;
  void beginCurrentTakeDrawerFlow(item, prompt, token);
}

function takeItemKey(item) {
  return item ? `${currentTakeDoseId}:${item.member?.id || "member"}:${item.drawer}:${item.med?.name || "medicine"}` : "";
}

function isCurrentTakeItem(item, token) {
  return activePageId === "take" && token === takeWorkflowToken && takeQueue[takeIndex] === item;
}

function stopTakeDoorMonitoring() {
  window.clearTimeout(takeDoorPollTimer);
  takeDoorPollTimer = 0;
  takeDoorPollKey = "";
}

function updateTakeVisualState() {
  const item = takeQueue[takeIndex];
  const total = takeQueue.length;
  const progress = document.querySelector("#takeProgressText");
  const door = document.querySelector("#takeDoorState");
  if (progress) progress.textContent = item && total ? `${takeIndex + 1}/${total}` : "0/0";
  if (door) {
    door.textContent = takeDoorState === "announcing" ? "正在播报"
      : takeDoorState === "opening" ? "正在打开"
        : takeDoorState === "open" ? "已打开"
          : takeDoorState === "closed" ? "已关闭"
            : takeDoorState === "error" ? "检测失败"
              : "等待确认";
  }
}

function canRunTakeRecognition() {
  return takeDoorObservedOpen
    && ["open", "closed"].includes(takeDoorState)
    && !takeRecognitionBusy
    && !takeItemCompleting
    && !pillConfirmed;
}

function takeExpectedDetectionClass(item) {
  const unit = String(item?.med?.unit || "").trim().toLowerCase();
  if (["袋", "包"].includes(unit)) return "sachet";
  if (["片", "粒", "丸", "胶囊", "颗"].includes(unit)) return "pill";
  return "";
}

function takeDetectionClassMismatch(result, item) {
  const expectedClass = takeExpectedDetectionClass(item);
  const detected = Number(result?.count ?? result?.detectedCount);
  const pillCount = Number(result?.pillCount);
  const sachetCount = Number(result?.sachetCount);
  if (!expectedClass || !Number.isFinite(detected) || !Number.isFinite(pillCount) || !Number.isFinite(sachetCount)) return true;
  if (expectedClass === "sachet") return pillCount !== 0 || sachetCount !== detected;
  return sachetCount !== 0 || pillCount !== detected;
}

function renderTakeRecognitionControls() {
  const ready = canRunTakeRecognition();
  const button = document.querySelector("#runPillCountRecognition");
  const simulationButton = document.querySelector("#simulatePillCount");
  if (button) button.disabled = !ready;
  if (simulationButton) {
    simulationButton.hidden = true;
    simulationButton.disabled = true;
  }
}

async function beginCurrentTakeDrawerFlow(item, prompt, token) {
  if (!isCurrentTakeItem(item, token)) return;
  takePromptCompleted = false;
  els.takeState.textContent = "正在播报取药提示，药柜同步打开，可在柜门打开后开始识别。";
  setTakePhase("步骤 1/3：正在播报取药提示并打开药柜");
  const openPromise = ensureCurrentTakeCabinetOpen(item, token);
  const played = await announce(prompt, false, 1, "");
  if (!isCurrentTakeItem(item, token)) return;
  takePromptCompleted = true;
  void played;
  await openPromise;
}

async function ensureCurrentTakeCabinetOpen(item, token) {
  if (!isCurrentTakeItem(item, token) || takeDrawerOpening || takeDrawerOpen) return;
  takeDrawerOpening = true;
  takeDoorState = "opening";
  takeDoorPollFailures = 0;
  updateTakeVisualState();
  renderTakeRecognitionControls();
  try {
    const command = await openCabinetViaGateway(item.drawer, item);
    if (!isCurrentTakeItem(item, token)) return;
    const commandObservedOpen = command?.door?.closed === false || command?.closed === false;
    if (!commandObservedOpen) {
      await delay(250);
      const state = await refreshCabinetStateViaGateway(item.drawer);
      if (!isCurrentTakeItem(item, token)) return;
      if (!cabinetOpenFromPayload(state)) throw new Error("cabinet_open_not_confirmed");
    }
    takeDrawerOpen = true;
    takeDoorObservedOpen = true;
    takeDoorState = "open";
    doorConfirmed = false;
    updateTakeVisualState();
    renderTakeRecognitionControls();
    els.takeState.textContent = `${item.drawer}号药柜已确认打开，请取出${item.med.name}并放到识别区核验。`;
    setTakePhase("步骤 1/3：柜门已打开，请取药并开始识别");
    scheduleTakeDoorMonitoring(item, token);
  } catch (error) {
    if (!isCurrentTakeItem(item, token)) return;
    takeDrawerOpen = false;
    takeDoorState = "error";
    updateTakeVisualState();
    renderTakeRecognitionControls();
    els.takeState.textContent = `开柜失败：${error.message}`;
    setTakePhase("步骤 1/3：药柜未打开");
    showToast(`开柜失败：${error.message}`);
  } finally {
    takeDrawerOpening = false;
  }
}

function scheduleTakeDoorMonitoring(item, token) {
  stopTakeDoorMonitoring();
  const key = takeItemKey(item);
  if (!key || !isCurrentTakeItem(item, token) || !takeDrawerOpen) return;
  takeDoorPollKey = key;
  const poll = async () => {
    if (!isCurrentTakeItem(item, token) || takeDoorPollKey !== key) return;
    try {
      const state = await refreshCabinetStateViaGateway(item.drawer);
      if (!isCurrentTakeItem(item, token) || takeDoorPollKey !== key) return;
      if (!cabinetDoorStateFromGpio(state)) throw new Error("cabinet_state_not_from_gpio");
      const closed = cabinetClosedFromPayload(state);
      takeDoorPollFailures = 0;
      takeDoorState = closed ? "closed" : "open";
      takeDrawerOpen = !closed;
      if (!closed) takeDoorObservedOpen = true;
      doorConfirmed = closed && takeDoorObservedOpen;
      updateTakeVisualState();
      renderTakeRecognitionControls();
      if (doorConfirmed && pillConfirmed) {
        tryNextDrug();
        return;
      }
    } catch (error) {
      if (!isCurrentTakeItem(item, token) || takeDoorPollKey !== key) return;
      takeDoorPollFailures += 1;
      if (takeDoorPollFailures >= 3) {
        takeDrawerOpen = false;
        takeDoorState = "error";
        updateTakeVisualState();
        renderTakeRecognitionControls();
        els.takeState.textContent = "柜门状态连续读取失败，请刷新柜门状态后继续。";
        setTakePhase("步骤 3/3：等待柜门状态");
        showToast(`柜门状态读取失败：${error.message || "未知错误"}`);
        return;
      }
    }
    takeDoorPollTimer = window.setTimeout(poll, 1000);
  };
  void poll();
}


function updateLoadVisualState(totalItems = 0) {
  const total = Math.max(1, totalItems);
  const current = totalItems ? Math.min(loadItemIndex + 1, totalItems) : 0;
  const percent = totalItems ? Math.round((current / total) * 100) : 100;
  const progressText = document.querySelector("#loadProgressText");
  const progressFill = document.querySelector("#loadProgressFill");
  const doorState = document.querySelector("#loadDoorState");
  if (progressText) progressText.textContent = totalItems ? `${current}/${totalItems}` : "0/0";
  if (progressFill) progressFill.style.width = `${percent}%`;
  if (doorState) {
    doorState.textContent = loadDoorState === "open" ? "打开" : loadDoorState === "closed" ? "关闭" : loadDoorState === "error" ? "检测失败" : "检测中";
  }
}

function renderLoadVerificationButton() {
  const button = document.querySelector("#nextLoadStep");
  if (!button) return;
  if (loadGuideFinished) {
    button.hidden = true;
    return;
  }
  button.hidden = false;
  button.textContent = "\u5f00\u59cb\u8bc6\u522b";
  button.disabled = loadMedicineBoxBusy || loadDrawerOpen;
}

function loadMedicineBoxRequestData(item, sessionId) {
  return {
    sessionId,
    page: "load-guide",
    source: "load-guide",
    purpose: "load-verify",
    memberId: item?.member?.id || "",
    cabinetId: item?.drawer || "",
    medicineName: item?.med?.name || "",
    expectedMedicineId: item?.med?.id || `${item?.member?.id || "member"}:${item?.drawer || ""}`,
    expectedMedicineName: item?.med?.name || "",
  };
}

async function ensureLoadMedicineBoxReady(item) {
  if (loadMedicineBoxReady) return true;
  if (loadMedicineBoxPreloadPromise) return loadMedicineBoxPreloadPromise;
  loadMedicineBoxSessionId = `load-medicine-box-${item?.member?.id || "member"}-${Date.now()}`;
  loadMedicineBoxPreloadPromise = (async () => {
    let preloadError = null;
    try {
      const result = await requestMedicineBoxOcr("/preload", {
        method: "POST",
        body: JSON.stringify(loadMedicineBoxRequestData(item, loadMedicineBoxSessionId)),
      });
      if (result?.ready !== true) throw new Error("medicine_box_not_ready");
    } catch (error) {
      if (!isAssistantOcrPreloadRecoveryCandidate(error)) throw annotateOcrError(error, { path: "/preload", page: "load-guide", source: "load-guide", sessionId: loadMedicineBoxSessionId });
      preloadError = error;
    }

    try {
      await waitForMedicineBoxReady(loadMedicineBoxSessionId, { path: "/status", page: "load-guide", source: "load-guide" }, { timeoutMs: 5000, pollMs: 500 });
    } catch (error) {
      if (preloadError) throw annotateOcrError(preloadError, { path: "/preload", page: "load-guide", source: "load-guide", sessionId: loadMedicineBoxSessionId });
      throw error;
    }
    return true;
  })();
  try {
    await loadMedicineBoxPreloadPromise;
    loadMedicineBoxReady = true;
    scheduleLoadMedicineBoxLeaseRenewal(item);
    return true;
  } catch (error) {
    clearLoadMedicineBoxLeaseRenewal();
    loadMedicineBoxSessionId = "";
    loadMedicineBoxReady = false;
    throw error;
  } finally {
    loadMedicineBoxPreloadPromise = null;
    renderLoadVerificationButton();
  }
}

function clearLoadMedicineBoxLeaseRenewal() {
  window.clearTimeout(loadMedicineBoxLeaseTimer);
  loadMedicineBoxLeaseTimer = 0;
}

function scheduleLoadMedicineBoxLeaseRenewal(item) {
  clearLoadMedicineBoxLeaseRenewal();
  const sessionId = loadMedicineBoxSessionId;
  if (!sessionId || !loadMedicineBoxReady || activePageId !== "load-guide") return;
  loadMedicineBoxLeaseTimer = window.setTimeout(async () => {
    if (sessionId !== loadMedicineBoxSessionId || !loadMedicineBoxReady || activePageId !== "load-guide") return;
    try {
      await requestMedicineBoxOcr("/preload", {
        method: "POST",
        body: JSON.stringify(loadMedicineBoxRequestData(currentLoadItem() || item, sessionId)),
        timeoutMs: 5000,
        keepalive: true,
      });
    } catch {
      // The backend lease remains the fallback if the browser loses connectivity.
    }
    if (sessionId === loadMedicineBoxSessionId && loadMedicineBoxReady && activePageId === "load-guide") {
      scheduleLoadMedicineBoxLeaseRenewal(currentLoadItem() || item);
    }
  }, K1_MEDICINE_BOX_LEASE_RENEW_MS);
}

function releaseLoadMedicineBox() {
  const sessionId = loadMedicineBoxSessionId;
  clearLoadMedicineBoxLeaseRenewal();
  loadMedicineBoxSessionId = "";
  loadMedicineBoxPreloadPromise = null;
  loadMedicineBoxReady = false;
  if (!sessionId) return;
  void requestMedicineBoxOcr("/release", {
    method: "POST",
    body: JSON.stringify({ sessionId, page: "load-guide" }),
    timeoutMs: 5000,
    keepalive: true,
  }).catch(() => {});
}

function stopLoadGuideWorkflow(release = false) {
  loadWorkflowToken += 1;
  loadMedicineBoxBusy = false;
  window.clearTimeout(loadCloseAdvanceTimer);
  loadCloseAdvanceTimer = 0;
  stopLoadDoorMonitoring();
  if (release) releaseLoadMedicineBox();
}

function cabinetSpeechNumber(cabinetId) {
  const labels = ["", "\u4e00", "\u4e8c", "\u4e09", "\u56db", "\u4e94", "\u516d", "\u4e03", "\u516b", "\u4e5d", "\u5341", "\u5341\u4e00", "\u5341\u4e8c"];
  return labels[Number(cabinetId)] || String(cabinetId);
}

function stopLoadDoorMonitoring() {
  window.clearTimeout(loadDoorPollTimer);
  loadDoorPollTimer = 0;
  loadDoorPollKey = "";
}

function isCurrentLoadStep(key, token) {
  return activePageId === "load-guide"
    && !loadGuideFinished
    && token === loadWorkflowToken
    && loadItemKey(currentLoadItem()) === key;
}

function isCurrentLoadDoorPoll(key, token) {
  return isCurrentLoadStep(key, token) && loadDoorPollKey === key;
}

function scheduleLoadDoorMonitoring(item) {
  stopLoadDoorMonitoring();
  const key = loadItemKey(item);
  const token = loadWorkflowToken;
  if (!key || loadGuideFinished || !loadDrawerOpen) return;
  loadDoorPollKey = key;
  const poll = async () => {
    if (!isCurrentLoadDoorPoll(key, token)) return;
    try {
      const state = await refreshCabinetStateViaGateway(item.drawer);
      if (!isCurrentLoadDoorPoll(key, token)) return;
      if (!cabinetDoorStateFromGpio(state)) throw new Error("cabinet_state_not_from_gpio");
      const closed = cabinetClosedFromPayload(state);
      loadDoorPollFailures = 0;
      loadDoorState = closed ? "closed" : "open";
      loadDrawerOpen = !closed;
      updateLoadVisualState(loadGuideItems().length);
      if (!closed) loadDoorObservedOpen = true;
      if (closed && loadDoorObservedOpen) {
        stopLoadDoorMonitoring();
        els.loadStepText.textContent = `${item.drawer}号药柜已关闭，正在播报确认信息。`;
        void announce(`${item.drawer}号药柜已关闭。`, false, 1, "").then(() => {
          if (isCurrentLoadStep(key, token)) return completeCurrentLoadStep({ expectedKey: key, expectedToken: token });
          return false;
        });
        return;
      }
    } catch (err) {
      if (!isCurrentLoadDoorPoll(key, token)) return;
      loadDoorPollFailures += 1;
      loadDoorState = loadDoorPollFailures >= 3 ? "error" : "unknown";
      if (loadDoorPollFailures >= 3) {
        loadDrawerOpen = false;
        loadOpenedItemKey = "";
      }
      updateLoadVisualState(loadGuideItems().length);
      if (loadDoorPollFailures >= 3) {
        els.loadStepText.textContent = "柜门状态连续读取失败，请重新点击开始识别并开柜。";
        showToast(`柜门状态读取失败：${(err && err.message) || "未知错误"}`);
        renderLoadVerificationButton();
        return;
      }
    }
    loadDoorPollTimer = window.setTimeout(poll, 1000);
  };
  void poll();
}
function loadGuideItems() {
  return allMedicineItems().filter((item) => !activeLoadMemberId || item.member.id === activeLoadMemberId);
}

function currentLoadItem() {
  return loadGuideItems()[loadItemIndex] || null;
}

function loadItemKey(item) {
  return item ? `${item.member.id}:${item.drawer}:${item.med.name}` : "";
}

function loadRecognitionSucceeded(result) {
  const status = String(result?.status || "").toLowerCase();
  return result?.recognized === true || result?.verified === true || result?.matched === true || result?.success === true
    || ["recognized", "verified", "success", "completed"].includes(status);
}

async function ensureCurrentLoadCabinetOpen(item) {
  const key = loadItemKey(item);
  if (!key || loadGuideFinished || loadOpenedItemKey === key) return;
  loadOpenedItemKey = key;
  loadDrawerOpen = false;
  loadDoorObservedOpen = false;
  loadDoorPollFailures = 0;
  loadDoorState = "unknown";
  updateLoadVisualState(loadGuideItems().length);
  try {
    const command = await openCabinetViaGateway(item.drawer, item);
    if (key !== loadItemKey(currentLoadItem()) || activePageId !== "load-guide" || loadGuideFinished) return;
    const commandObservedOpen = command?.door?.closed === false || command?.closed === false;
    if (!commandObservedOpen) {
      await delay(250);
      const state = await refreshCabinetStateViaGateway(item.drawer);
      if (key !== loadItemKey(currentLoadItem()) || activePageId !== "load-guide" || loadGuideFinished) return;
      if (!cabinetOpenFromPayload(state)) throw new Error("cabinet_open_not_confirmed");
    }
    loadDrawerOpen = true;
    loadDoorObservedOpen = true;
    loadDoorState = "open";
    updateLoadVisualState(loadGuideItems().length);
    scheduleLoadDoorMonitoring(item);
    showToast(`${item.drawer}号柜已发送真实开柜指令，正在检测柜门状态`);
  } catch (error) {
    loadOpenedItemKey = "";
    loadDrawerOpen = false;
    loadDoorState = "error";
    updateLoadVisualState(loadGuideItems().length);
    showToast(`开柜失败：${error.message}`);
  }
}

function loadGuidePrompt(item) {
  return `\u8bf7\u5c06${item.med.name}\u836f\u76d2\u653e\u5728\u8bc6\u522b\u533a\uff0c\u5e76\u70b9\u51fb\u5f00\u59cb\u8bc6\u522b\u6309\u94ae\u3002`;
}

async function completeCurrentLoadStep({ simulated = false, expectedKey = "", expectedToken = null } = {}) {
  const items = loadGuideItems();
  if (!items.length || loadGuideFinished) return;
  if (expectedKey && expectedToken !== null && !isCurrentLoadStep(expectedKey, expectedToken)) return;
  stopLoadDoorMonitoring();
  loadDrawerOpen = false;
  loadDoorState = "closed";
  loadOpenedItemKey = "";
  updateLoadVisualState(items.length);
  if (loadItemIndex < items.length - 1) {
    loadItemIndex += 1;
    updateLoadGuide();
    showToast(simulated ? "\u6a21\u62df\u8bc6\u522b\u901a\u8fc7\uff0c\u5df2\u8fdb\u5165\u4e0b\u4e00\u79cd\u836f\u54c1" : "\u5df2\u8fdb\u5165\u4e0b\u4e00\u79cd\u836f\u54c1\u7684\u653e\u7f6e\u6d41\u7a0b");
    return;
  }
  loadItemIndex = Math.max(0, items.length - 1);
  loadGuideFinished = true;
  items.forEach((item) => recordInventoryOperation("load", item, item.med.stock, `load-guide:${item.drawer}:${item.med.name}`));
  addReport("\u653e\u836f\u5b8c\u6210", `\u5df2\u5b8c\u6210${items.length}\u79cd\u836f\u54c1\u7684\u653e\u7f6e`, activeMember()?.name || "\u7cfb\u7edf");
  saveMembers();
  updateLoadGuide();
  saveCurrentFlowState("load-guide");
  const finishToken = loadWorkflowToken;
  await announce("\u60a8\u672c\u8f6e\u653e\u7f6e\u7684\u836f\u7269\u5df2\u5168\u90e8\u653e\u7f6e\u5b8c\u6210\u3002", false, 1, "");
  showToast(simulated ? "\u6a21\u62df\u8bc6\u522b\u901a\u8fc7\uff0c\u653e\u836f\u5f15\u5bfc\u5df2\u5b8c\u6210" : "\u653e\u836f\u5f15\u5bfc\u5df2\u5b8c\u6210");
  if (finishToken === loadWorkflowToken && activePageId === "load-guide" && loadGuideFinished) setPage("home");
}

async function recognizeCurrentLoadMedicineBox() {
  const item = currentLoadItem();
  if (!item || loadGuideFinished || loadDrawerOpen) return;
  const token = ++loadWorkflowToken;
  loadMedicineBoxBusy = true;
  renderLoadVerificationButton();
  try {
    await ensureLoadMedicineBoxReady(item);
    if (!isCurrentLoadStep(loadItemKey(item), token)) return;
    const recognition = await requestMedicineBoxOcr("/recognitions", {
      method: "POST",
      body: JSON.stringify(loadMedicineBoxRequestData(item, loadMedicineBoxSessionId)),
      path: "/recognitions",
      page: "load-guide",
      source: "load-guide",
    });
    if (!isCurrentLoadStep(loadItemKey(item), token)) return;
    const recognizedName = String(recognition?.medicineName || "").trim();
    if (normalizedMedicineName(recognizedName) !== normalizedMedicineName(item.med.name)) {
      const spokenErrorText = `\u836f\u76d2\u540d\u79f0\u8bc6\u522b\u9519\u8bef\uff0c\u8bf7\u653e\u7f6e${item.med.name}\u836f\u76d2\u5230\u8bc6\u522b\u533a\uff0c\u5e76\u91cd\u65b0\u70b9\u51fb\u201c\u5f00\u59cb\u8bc6\u522b\u201d\u6309\u94ae\u3002`;
      els.loadStepText.textContent = `${spokenErrorText}请检查是否将药盒正确放入识别区。`;
      announce(spokenErrorText, false, 1, recognition?.audioId || "");
      return;
    }
    const successText = `药盒名称识别正确，${item.drawer}号药柜即将弹出。请将药物放入该药柜并手动关闭药柜。`;
    els.loadStepText.textContent = successText;
    const played = await announce(successText, false, 1, recognition?.audioId || "");
    if (played && isCurrentLoadStep(loadItemKey(item), token)) await ensureCurrentLoadCabinetOpen(item);
  } catch (error) {
    if (token === loadWorkflowToken) showToast(`\u836f\u76d2\u8bc6\u522b\u5931\u8d25\uff1a${error.message}`);
    void showTaskDiagnostic("ocr", error);
  } finally {
    loadMedicineBoxBusy = false;
    if (!loadGuideFinished) renderLoadVerificationButton();
  }
}

function updateLoadGuide() {
  const items = loadGuideItems();
  const item = items[loadItemIndex];
  if (!item) {
    stopLoadDoorMonitoring();
    loadDrawerOpen = false;
    loadDoorState = "unknown";
    updateLoadVisualState(items.length);
    els.loadStepTitle.textContent = "\u6682\u65e0\u5f85\u653e\u836f\u54c1";
    els.loadStepText.textContent = "";
    els.drawerNumber.textContent = "-";
    els.drawerStatus.textContent = "";
    const button = document.querySelector("#nextLoadStep");
    if (button) button.hidden = true;
    return;
  }
  selectedCabinetIndex = item.drawer - 1;
  const key = loadItemKey(item);
  const drawer = `${item.drawer}\u53f7\u67dc`;
  els.loadStepTitle.textContent = `${item.member.name} \u00b7 \u7b2c ${loadItemIndex + 1}/${items.length} \u79cd\u836f\u54c1`;
  els.drawerNumber.textContent = drawer;
  els.drawerStatus.textContent = item.med.name;
  if (loadGuideFinished) {
    els.loadStepText.textContent = "\u60a8\u672c\u8f6e\u653e\u7f6e\u7684\u836f\u7269\u5df2\u5168\u90e8\u653e\u7f6e\u5b8c\u6210\u3002";
    renderLoadVerificationButton();
    updateLoadVisualState(items.length);
    renderCabinets();
    return;
  }
  if (!loadDrawerOpen && loadDoorState !== "error") {
    loadDoorState = "unknown";
    updateLoadVisualState(items.length);
  }
  els.loadStepText.textContent = loadGuidePrompt(item);
  renderLoadVerificationButton();
  renderCabinets();
  if (loadPromptedItemKey !== key) {
    loadPromptedItemKey = key;
    announcePageOnce("load-guide", loadGuidePrompt(item), loadItemIndex === 0, 1, "");
  }
}

function flowPageName(pageId = activePageId) {
  if (pageId === "load-guide") return "放药流程";
  if (pageId === "face") return "人脸核验";
  if (pageId === "med-confirm") return "确认药物";
  if (pageId === "take") return "取药流程";
  return "当前流程";
}

function flowPreviousPage(pageId = activePageId) {
  const previous = { alarm: "home", face: "alarm", "med-confirm": "face", take: "med-confirm" };
  return previous[pageId] || previousPageId || "home";
}

function navigateFlowBack() {
  const targetPage = flowPreviousPage();
  if (activePageId === "take" && requestFlowExit(targetPage)) return;
  setPage(targetPage);
  if (targetPage === "med-confirm") renderMedicationConfirm();
}

function exitTakeFlowNow(targetPage = "home") {
  if (activePageId !== "take") return false;
  const drawerMayRemainOpen = takeDrawerOpening
    || takeDrawerOpen
    || ["opening", "open"].includes(takeDoorState)
    || (takeDoorObservedOpen && takeDoorState === "error");
  const savedState = currentFlowState("take");
  const callback = flowExitAfterSave;
  flowExitTargetPage = "";
  flowExitAfterSave = null;
  stopVoiceAnnouncements();
  stopTakeDoorMonitoring();
  if (savedState) clearSavedFlowState("take", savedState.memberId, savedState.doseId);
  releasePillCount();
  pillConfirmed = false;
  doorConfirmed = false;
  takeItemCompleting = false;
  takeRecognitionSimulated = false;
  if (callback) callback();
  setPage(targetPage || "home");
  if (targetPage === "med-confirm") renderMedicationConfirm();
  if (drawerMayRemainOpen) showToast("已退出取药流程；请手动确认药柜已推回");
  return true;
}

function takePhysicalPhaseStarted() {
  return activePageId === "take" && (
    takeDoorObservedOpen
    || takeDrawerOpening
    || takeRecognitionBusy
    || takeClearanceBusy
    || takeItemCompleting
  );
}

function takeFlowExitBlockMessage() {
  if (activePageId !== "take") return "";
  if (takeRecognitionBusy) return "数量识别正在进行，请等待识别结束后再退出";
  if (takeClearanceBusy || takeItemCompleting || pillConfirmed) return "正在确认药物已取走和柜门已关闭，请完成当前步骤后再退出";
  if (takeDrawerOpening || takeDrawerOpen || ["opening", "open"].includes(takeDoorState)) return "请先关闭当前药柜，再退出取药流程";
  if (takeDoorObservedOpen && takeDoorState === "error") return "柜门状态尚未确认，请刷新柜门状态后再退出";
  return "";
}

function shouldAskFlowExit() {
  if (activePageId === "load-guide" && loadGuideFinished) return false;
  return ["face", "med-confirm", "take", "load-guide"].includes(activePageId);
}

function emptyFlowStateStore() {
  return { schemaVersion: FLOW_STATE_SCHEMA_VERSION, take: {}, load: {} };
}

function saveFlowStateStore(store) {
  localStorage.setItem(FLOW_STATE_KEY, JSON.stringify(store));
}

function loadFlowStateStore() {
  try {
    const raw = JSON.parse(localStorage.getItem(FLOW_STATE_KEY) || "null");
    if (raw?.schemaVersion === FLOW_STATE_SCHEMA_VERSION) {
      return {
        schemaVersion: FLOW_STATE_SCHEMA_VERSION,
        take: raw.take && typeof raw.take === "object" ? raw.take : {},
        load: raw.load && typeof raw.load === "object" ? raw.load : {},
      };
    }
    const migrated = emptyFlowStateStore();
    if (raw?.pageId === "load-guide") {
      const memberId = String(raw.activeLoadMemberId || "").trim();
      if (memberId) migrated.load[memberId] = { ...raw, kind: "load", memberId, savedAt: Number(raw.savedAt || Date.now()) };
    } else if (["face", "med-confirm", "take"].includes(raw?.pageId)) {
      const doseId = String(raw.currentTakeDoseId || raw.activeDoseId || "").trim();
      const row = doseId ? reminderById(doseId) : null;
      if (doseId && row?.member?.id) {
        migrated.take[doseId] = { ...raw, kind: "take", doseId, memberId: row.member.id, savedAt: Number(raw.savedAt || Date.now()) };
      }
    }
    if (Object.keys(migrated.take).length || Object.keys(migrated.load).length) saveFlowStateStore(migrated);
    return migrated;
  } catch {
    return emptyFlowStateStore();
  }
}

function loadSavedFlowState(kind = "", memberId = "", doseId = "") {
  const store = loadFlowStateStore();
  if (kind === "load") return store.load[String(memberId || "").trim()] || null;
  if (kind === "take") return store.take[String(doseId || "").trim()] || null;
  return null;
}

function currentFlowState(pageId = activePageId) {
  if (pageId === "load-guide") {
    const memberId = String(activeLoadMemberId || activeMember()?.id || "").trim();
    if (!memberId) return null;
    return { kind: "load", pageId, memberId, activeLoadMemberId: memberId, loadItemIndex, loadDrawerOpen, loadGuideFinished };
  }
  if (["face", "med-confirm", "take"].includes(pageId)) {
    const doseId = currentTakeDoseId || activeDoseId;
    const row = doseId ? reminderById(doseId) : null;
    const memberId = String(row?.member?.id || takeQueue[0]?.member?.id || "").trim();
    if (!doseId || !memberId) return null;
    return { kind: "take", pageId, memberId, doseId, activeDoseId: doseId, currentTakeDoseId: doseId, takeIndex };
  }
  return null;
}

function saveCurrentFlowState(pageId = activePageId) {
  if (pageId === "take" && takePhysicalPhaseStarted()) return false;
  const state = currentFlowState(pageId);
  if (!state) return false;
  const store = loadFlowStateStore();
  const key = state.kind === "load" ? state.memberId : state.doseId;
  store[state.kind][key] = { ...state, savedAt: Date.now() };
  saveFlowStateStore(store);
  return true;
}

function clearSavedFlowState(kind = "", memberId = "", doseId = "") {
  const current = kind ? null : currentFlowState();
  const targetKind = kind || current?.kind || "";
  const key = targetKind === "load" ? String(memberId || current?.memberId || "").trim() : String(doseId || current?.doseId || "").trim();
  if (!targetKind || !key) return false;
  const store = loadFlowStateStore();
  if (!store[targetKind]?.[key]) return false;
  delete store[targetKind][key];
  saveFlowStateStore(store);
  return true;
}

function restoreSavedFlowState(expectedKind = "", expectedMemberId = "", expectedDoseId = "") {
  const targetDoseId = expectedDoseId || (expectedKind === "take" ? activeDoseId : "");
  const state = loadSavedFlowState(expectedKind, expectedMemberId, targetDoseId);
  if (!state) return false;
  const isLoad = state.kind === "load";
  const isTake = state.kind === "take";
  if (expectedKind === "load" && !isLoad) return false;
  if (expectedKind === "take" && !isTake) return false;

  if (isLoad) {
    if (expectedMemberId && state.memberId !== expectedMemberId) return false;
    activeLoadMemberId = state.memberId || expectedMemberId || activeMember()?.id || "";
    const items = allMedicineItems().filter((item) => !activeLoadMemberId || item.member.id === activeLoadMemberId);
    loadItemIndex = Math.min(Math.max(0, Number(state.loadItemIndex || 0)), Math.max(0, items.length - 1));
    loadGuideFinished = state.loadGuideFinished === true;
    loadDrawerOpen = false;
    loadDoorObservedOpen = false;
    loadOpenedItemKey = "";
    loadDoorPollFailures = 0;
    setPage("load-guide");
    updateLoadGuide();
    showToast(loadGuideFinished ? "已恢复放药完成页面" : "已恢复上次保存的放药页面");
    return true;
  }

  const doseId = String(state.doseId || state.currentTakeDoseId || state.activeDoseId || "").trim();
  const row = reminderById(doseId);
  if (!row || (expectedMemberId && row.member.id !== expectedMemberId) || (expectedDoseId && row.id !== expectedDoseId)) {
    clearSavedFlowState("take", expectedMemberId, doseId);
    return false;
  }
  activeDoseId = row.id;
  currentTakeDoseId = row.id;
  takeQueue = prepareTakeQueue(row.id);
  if (!takeQueue.length) {
    clearSavedFlowState("take", row.member.id, row.id);
    return false;
  }
  takeIndex = Math.min(Math.max(0, Number(state.takeIndex || 0)), Math.max(0, takeQueue.length - 1));
  if (state.pageId === "face") {
    setPage("face");
    if (isWangGrandpaTakeRow(row)) {
      preloadWangGrandpaAudio();
      void playWangGrandpaAudio("take_face", "请进行人脸核验，核验通过后确认药物。");
    } else {
      announcePageOnce("face", "请进行人脸核验，核验通过后确认药物。", true, 1, "face_verify");
    }
  } else if (state.pageId === "med-confirm") {
    setPage("med-confirm");
    renderMedicationConfirm();
  } else {
    setPage("take");
    updateTake();
  }
  showToast("已恢复当前患者上次保存的取药页面");
  return true;
}

function ensureFlowExitDialog() {
  let dialog = document.querySelector("#flowExitDialog");
  if (dialog) return dialog;
  dialog = document.createElement("section");
  dialog.id = "flowExitDialog";
  dialog.className = "flow-exit-dialog";
  dialog.hidden = true;
  dialog.innerHTML = `
    <article>
      <h2>是否保存当前页面？</h2>
      <p id="flowExitText">保存后下次打开将从此页面继续。</p>
      <div class="flow-exit-actions">
        <button class="primary" data-flow-exit="save" type="button">保存并退出</button>
        <button class="secondary" data-flow-exit="discard" type="button">不保存退出</button>
        <button class="ghost" data-flow-exit="cancel" type="button">取消</button>
      </div>
    </article>`;
  document.querySelector(".device")?.appendChild(dialog);
  return dialog;
}

function requestFlowExit(targetPage = "home", afterSave = null) {
  if (activePageId === "take") {
    flowExitAfterSave = afterSave;
    return exitTakeFlowNow(targetPage);
  }
  if (!shouldAskFlowExit()) return false;
  const blockedMessage = takeFlowExitBlockMessage();
  if (blockedMessage) {
    showToast(blockedMessage);
    return true;
  }
  flowExitTargetPage = targetPage || "home";
  flowExitAfterSave = afterSave;
  const dialog = ensureFlowExitDialog();
  const text = dialog.querySelector("#flowExitText");
  const physicalTakePhase = takePhysicalPhaseStarted();
  const saveButton = dialog.querySelector("[data-flow-exit='save']");
  if (saveButton) saveButton.hidden = physicalTakePhase;
  if (text) {
    text.textContent = physicalTakePhase
      ? "当前药柜已安全关闭，但本次实体取药阶段不能保存恢复。可选择不保存退出，稍后从该药品重新开始。"
      : `${flowPageName()}尚未结束，是否保存当前页面进度？保存后下次打开将从此页面继续。`;
  }
  dialog.hidden = false;
  return true;
}

function closeFlowExitDialog() {
  const dialog = document.querySelector("#flowExitDialog");
  if (dialog) dialog.hidden = true;
}

function ensureDeleteConfirmDialog() {
  let dialog = document.querySelector("#deleteConfirmDialog");
  if (dialog) return dialog;
  dialog = document.createElement("section");
  dialog.id = "deleteConfirmDialog";
  dialog.className = "flow-exit-dialog delete-confirm-dialog";
  dialog.hidden = true;
  dialog.innerHTML = `
    <article>
      <h2>确认删除？</h2>
      <p id="deleteConfirmText">删除后将无法直接恢复，请确认是否继续。</p>
      <div class="delete-confirm-actions">
        <button class="danger" data-delete-confirm="confirm" type="button">确认删除</button>
        <button class="ghost" data-delete-confirm="cancel" type="button">取消</button>
      </div>
    </article>`;
  document.querySelector(".device")?.appendChild(dialog);
  return dialog;
}

function requestDeleteConfirmation(message, onConfirm) {
  pendingDeleteConfirmAction = typeof onConfirm === "function" ? onConfirm : null;
  const dialog = ensureDeleteConfirmDialog();
  const text = dialog.querySelector("#deleteConfirmText");
  if (text) text.textContent = message || "删除后将无法直接恢复，请确认是否继续。";
  dialog.hidden = false;
  dialog.querySelector("[data-delete-confirm='cancel']")?.focus();
  return true;
}

function closeDeleteConfirmDialog() {
  const dialog = document.querySelector("#deleteConfirmDialog");
  if (dialog) dialog.hidden = true;
}

function finishDeleteConfirmation(shouldDelete) {
  const callback = pendingDeleteConfirmAction;
  pendingDeleteConfirmAction = null;
  closeDeleteConfirmDialog();
  if (shouldDelete && callback) callback();
}

function finishFlowExit(shouldSave) {
  const blockedMessage = takeFlowExitBlockMessage();
  if (blockedMessage) {
    closeFlowExitDialog();
    showToast(blockedMessage);
    return;
  }
  if (shouldSave && takePhysicalPhaseStarted()) {
    showToast("实体取药阶段不能保存恢复，请完成当前药品或选择不保存退出");
    return;
  }
  const target = flowExitTargetPage || "home";
  const from = activePageId;
  const flowState = currentFlowState(from);
  const flowMember = flowState?.memberId ? members.find((member) => member.id === flowState.memberId) : activeMember();
  const callback = flowExitAfterSave;
  closeFlowExitDialog();
  flowExitTargetPage = "";
  flowExitAfterSave = null;
  if (shouldSave) {
    const saved = saveCurrentFlowState(from);
    addReport("流程保存", saved ? `${flowPageName(from)}页面进度已保存，下次打开会从此页面继续` : `${flowPageName(from)}暂无可保存进度`, flowMember?.name || "系统");
  } else {
    clearSavedFlowState(flowState?.kind, flowState?.memberId, flowState?.doseId);
  }
  if (callback) callback();
  setPage(target);
}

function beginDoseFromAlarm() {
  stopVoiceAnnouncements({ clearPageKey: true });
  markReminderOperated();
  const row = reminderById(activeDoseId);
  if (!row) {
    currentTakeDoseId = "";
    takeQueue = [];
    takeIndex = 0;
    showToast("未找到当前服药提醒，请返回首页重新选择");
    return;
  }
  currentTakeDoseId = row.id;
  takeQueue = [];
  takeIndex = 0;
  if (restoreSavedFlowState("take", row.member.id, row.id)) return;
  setPage("face");
  if (isWangGrandpaTakeRow(row)) {
    preloadWangGrandpaAudio();
    void playWangGrandpaAudio("take_face", "请进行人脸核验，核验通过后确认药物。");
  } else {
    announcePageOnce("face", "请进行人脸核验，核验通过后确认药物。", true, 1, "face_verify");
  }
}

function touchScrollTargetForRail(rail) {
  const selector = rail.querySelector("[data-scroll-target]")?.dataset.scrollTarget;
  return selector ? rail.closest(".page")?.querySelector(selector) || null : null;
}

function syncTouchScrollRail(rail) {
  const target = touchScrollTargetForRail(rail);
  const track = rail.querySelector(".touch-scroll-track");
  const thumb = track?.querySelector("i");
  if (!target || !track || !thumb || track.clientHeight < 1) return;
  const maximum = Math.max(0, target.scrollHeight - target.clientHeight);
  const trackHeight = track.clientHeight;
  const thumbHeight = maximum
    ? Math.max(34, Math.min(trackHeight, Math.round(trackHeight * (target.clientHeight / target.scrollHeight))))
    : trackHeight;
  const travel = Math.max(0, trackHeight - thumbHeight);
  const top = maximum ? Math.round((target.scrollTop / maximum) * travel) : 0;
  thumb.style.setProperty("--touch-scroll-thumb-height", `${thumbHeight}px`);
  thumb.style.setProperty("--touch-scroll-thumb-y", `${top}px`);
  rail.classList.toggle("is-scrollable", maximum > 0);
}

function syncAllTouchScrollRails() {
  document.querySelectorAll(".touch-scroll-rail").forEach(syncTouchScrollRail);
}

function scrollTouchTarget(target, direction) {
  const maximum = Math.max(0, target.scrollHeight - target.clientHeight);
  const start = target.scrollTop;
  const distance = Math.max(120, Math.round(target.clientHeight * 0.72));
  const end = Math.max(0, Math.min(maximum, start + direction * distance));
  if (end === start) return;
  const startedAt = performance.now();
  const duration = 280;
  const step = (now) => {
    const progress = Math.min(1, (now - startedAt) / duration);
    const eased = 1 - Math.pow(1 - progress, 3);
    target.scrollTop = start + (end - start) * eased;
    if (progress < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function bindEvents() {
  ensureKeyboardModes();
  document.querySelectorAll(".touch-scroll-rail").forEach((rail) => {
    const target = touchScrollTargetForRail(rail);
    const track = rail.querySelector(".touch-scroll-track");
    if (target) target.addEventListener("scroll", () => syncTouchScrollRail(rail), { passive: true });
    if (track && target) {
      track.addEventListener("pointerdown", (event) => {
        const maximum = Math.max(0, target.scrollHeight - target.clientHeight);
        if (!maximum) return;
        event.preventDefault();
        const thumb = track.querySelector("i");
        const startY = event.clientY;
        const startScroll = target.scrollTop;
        const travel = Math.max(1, track.clientHeight - (thumb?.clientHeight || 0));
        const move = (moveEvent) => {
          target.scrollTop = Math.max(0, Math.min(maximum, startScroll + ((moveEvent.clientY - startY) / travel) * maximum));
        };
        const stop = () => {
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", stop);
          window.removeEventListener("pointercancel", stop);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", stop, { once: true });
        window.addEventListener("pointercancel", stop, { once: true });
      });
    }
  });
  window.addEventListener("resize", syncAllTouchScrollRails);
  requestAnimationFrame(syncAllTouchScrollRails);
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-scroll-step]");
    if (!button) return;
    const page = button.closest(".page");
    const targets = page ? [...page.querySelectorAll(button.dataset.scrollTarget || "")] : [];
    const target = targets.find((item) => item.scrollHeight > item.clientHeight + 1) || targets[0];
    if (!target) return;
    scrollTouchTarget(target, Number(button.dataset.scrollStep || 0));
  });
  els.nav.addEventListener("click", (event) => {
    const button = event.target.closest("[data-page]");
    if (button) {
      if (requestFlowExit(button.dataset.page)) return;
      setPage(button.dataset.page);
    }
  });
  document.addEventListener("change", (event) => {
    const select = event.target.closest(".voice-tone-select");
    if (!select) return;
    selectedReminderVoiceName = select.value;
    localStorage.setItem(VOICE_TONE_KEY, selectedReminderVoiceName);
    syncVoiceToneControls();
    warmReminderAudio();
    const label = select.selectedOptions[0]?.textContent || "默认音色";
    showToast(`已切换音色：${label}`);
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-voice-tone-control]")) return;
    const deleteConfirm = event.target.closest("[data-delete-confirm]");
    if (deleteConfirm) {
      finishDeleteConfirmation(deleteConfirm.dataset.deleteConfirm === "confirm");
      return;
    }
    const reportDelete = event.target.closest("[data-report-delete]");
    if (reportDelete) {
      const targetReport = reports.find((report) => report.id === reportDelete.dataset.reportDelete);
      requestDeleteConfirmation("确认删除这条记录报告吗？删除后将无法直接恢复。", () => {
        reports = reports.filter((report) => report.id !== reportDelete.dataset.reportDelete);
        saveReports();
        renderReports();
        renderAdherence();
        showToast(targetReport ? "记录已删除" : "记录已删除");
      });
      return;
    }
    const flowExit = event.target.closest("[data-flow-exit]");
    if (flowExit) {
      const action = flowExit.dataset.flowExit;
      if (action === "cancel") closeFlowExitDialog();
      else finishFlowExit(action === "save");
      return;
    }
    const back = event.target.closest("[data-page-back]");
    if (back) {
      navigateFlowBack();
      return;
    }
    const jump = event.target.closest("[data-page-jump]");
    if (jump) {
      if (jump.dataset.pageJump === "today-status") syncHomeReminderPageToCurrent();
      if (requestFlowExit(jump.dataset.pageJump)) return;
      setPage(jump.dataset.pageJump);
      return;
    }
    const toggleHomeUser = event.target.closest("[data-toggle-home-user]");
    if (toggleHomeUser) {
      event.stopPropagation();
      toggleHomeUser.closest(".home-user-switch")?.classList.toggle("open");
    } else if (!event.target.closest(".home-user-switch")) {
      document.querySelector(".home-user-switch.open")?.classList.remove("open");
    }
    const homePageButton = event.target.closest("[data-home-page]");
    if (homePageButton) {
      const pageCount = Math.max(1, members.length);
      homeReminderPage = (homeReminderPage + Number(homePageButton.dataset.homePage) + pageCount) % pageCount;
      renderHome();
      renderTodayStatus();
    }
    const planPageButton = event.target.closest("[data-plan-page]");
    if (planPageButton) {
      const count = Math.max(1, members.length);
      planPage = (planPage + Number(planPageButton.dataset.planPage) + count) % count;
      renderPlan();
      renderTodayStatus();
    }
    const edit = event.target.closest("[data-edit]");
    if (edit) openForm(edit.dataset.edit);
    const del = event.target.closest("[data-delete]");
    if (del) {
      const removed = members.find((member) => member.id === del.dataset.delete);
      if (!removed) return;
      requestDeleteConfirmation(`确认删除 ${removed.name} 的成员档案吗？该成员的药品信息也会一并删除。`, () => {
        void (async () => {
          try {
            await requestFace("/remove", { method: "POST", body: JSON.stringify({ memberId: removed.id }), timeoutMs: 10000 });
          } catch (_err) {
            // 人脸库可能没有该成员的数据，忽略错误继续删除
          }
          members = normalizeMembers(members.filter((member) => member.id !== del.dataset.delete));
          saveMembers();
          addReport("删除成员", `${removed.name} 的成员档案已删除`, removed.name);
          renderAll();
          showToast("已删除成员");
        })();
      });
      return;
    }
    const current = event.target.closest("[data-current]");
    if (current) {
      const selected = members.find((member) => member.id === current.dataset.current);
      members = members.map((member) => ({ ...member, current: member.id === current.dataset.current }));
      const selectedIndex = members.findIndex((member) => member.id === current.dataset.current);
      if (selectedIndex >= 0) homeReminderPage = selectedIndex;
      saveMembers();
      if (selected) addReport("切换成员", `当前操作成员切换为 ${selected.name}`, selected.name);
      renderAll();
      showToast("已切换当前成员");
    }
    const plan = event.target.closest("[data-plan]");
    if (plan) {
      const index = members.findIndex((member) => member.id === plan.dataset.plan);
      planPage = Math.max(0, index);
      members = members.map((member) => ({ ...member, current: member.id === plan.dataset.plan }));
      saveMembers();
      setPage("plan");
    }
    const loadMember = event.target.closest("[data-load-member]");
    if (loadMember) {
      const member = members.find((item) => item.id === loadMember.dataset.loadMember);
      if (!member?.medicines?.length) return showToast("该成员还没有录入药品");
      if (isMemberFullyLoaded(member)) return showToast(`${member.name} 的药品已全部放入药柜`);
      members = members.map((item) => ({ ...item, current: item.id === member.id }));
      saveMembers();
      beginLoadGuide("member", member.id);
    }
    const cabinet = event.target.closest("[data-cabinet]");
    if (cabinet) {
      selectedCabinetIndex = Number(cabinet.dataset.cabinet || 0);
      renderCabinets();
      saveCabinetState("select-cabinet");
    }
    const assistant = event.target.closest("[data-assistant]");
    if (assistant) startAssistant(assistant.dataset.assistant);
    const togglePatientMenu = event.target.closest("[data-toggle-patient-menu]");
    if (togglePatientMenu) {
      togglePatientMenu.closest(".assistant-patient-menu")?.classList.toggle("open");
    }
    const patient = event.target.closest("[data-patient]");
    if (patient) {
      const selected = members.find((member) => member.id === patient.dataset.patient);
      members = members.map((member) => ({ ...member, current: member.id === patient.dataset.patient }));
      saveMembers();
      if (selected) addReport("用药答疑", `切换为 ${selected.name} 的精准答疑`, selected.name);
      renderAll();
      startAssistant("member");
    }
    const completeButton = event.target.closest("[data-complete-dose]");
    if (completeButton) {
      const row = todaySchedule().find((item) => item.id === completeButton.dataset.completeDose);
      if (completeDose(row, "手动完成")) {
        renderAll();
        showToast("今日进度已更新");
      }
    }
    const removeMed = event.target.closest("[data-remove-med]");
    if (removeMed) {
      const card = removeMed.closest(".medicine-card");
      const medName = card?.querySelector("[data-med='name']")?.value?.trim() || "这条药品信息";
      requestDeleteConfirmation(`确认删除 ${medName} 吗？`, () => {
        if (card?.isConnected) card.remove();
        renumberMedicineCards();
      });
      return;
    }
    if (event.target.closest("#saveCabinetParam")) saveCabinetParam();
    if (event.target.closest("#ocrStartLoadGuide")) beginLoadGuide("ocr");
    if (event.target.closest("#unlockDrawer")) {
      void openSelectedCabinetFromDetail();
    }
    if (event.target.closest("#closeDrawer")) {
      void refreshSelectedCabinetDoorState();
    }
  });
  document.querySelector("#addMember").addEventListener("click", () => openForm());
  document.querySelector("#cancelForm").addEventListener("click", () => setPage("members"));
  document.querySelector("#fillDemoMember").addEventListener("click", fillDemoMemberForm);
  document.querySelector("#saveMember").addEventListener("click", saveForm);
  document.querySelector("#addMed").addEventListener("click", () => addMedicineCard());
  els.prescriptionOcrStart?.addEventListener("click", () => void startPrescriptionOcrWorkflow());
  els.prescriptionOcrReview?.addEventListener("click", (event) => {
    if (event.target.closest("[data-rx-discard]")) clearPrescriptionOcrDraft("识别草稿已放弃");
    if (event.target.closest("[data-rx-confirm]")) confirmPrescriptionOcrSelection();
  });
  els.prescriptionOcrReview?.addEventListener("change", (event) => {
    if (event.target.matches("[data-rx-select]")) updatePrescriptionDraftSelectionState();
  });
  els.prescriptionOcrReview?.addEventListener("input", (event) => {
    if (!event.target.matches("[data-rx-field='stock']")) return;
    const hint = event.target.closest("label")?.querySelector("[data-rx-stock-hint]");
    if (hint) hint.textContent = "已手动填写，请保存前核对";
  });
  document.querySelector("#faceEnrollBackend")?.addEventListener("click", () => void enrollFaceForEditingMember());
  document.addEventListener("click", (event) => {
    if (event.target.closest("#openAlarm")) {
      const { member } = currentHomeMemberProgress();
      const row = memberVoiceBroadcastRow(member);
      if (row) beginVoiceSequence(member, row, { memberOnly: true });
      else showToast("暂无待播报用药");
    }
    const adherenceMember = event.target.closest("[data-adherence-member]");
    if (adherenceMember) {
      activeAdherenceMemberId = adherenceMember.dataset.adherenceMember;
      renderAdherence();
    }
    const playHomeVoice = event.target.closest("#playHomeVoice");
    if (playHomeVoice) {
      const { member } = currentHomeMemberProgress();
      let row = memberVoiceBroadcastRow(member);
      // 演示用：王爷爷始终从早餐后开始取药，忽略当前时间
      if (isWangGrandpaMember(member) && row) {
        const breakfastRow = todaySchedule().find((r) =>
          r.member.id === member.id && r.slot === "早餐后" && !r.done
        );
        if (breakfastRow) row = breakfastRow;
      }
      if (row) beginVoiceSequence(member, row, { memberOnly: true });
      else showToast("暂无待播报用药");
    }
  });
  document.querySelector("#startDose").addEventListener("click", beginDoseFromAlarm);
  document.querySelector("#faceBackend")?.addEventListener("click", () => void verifyActiveMemberFace());
  document.querySelector("#faceSimulateVerify")?.addEventListener("click", simulateActiveMemberFace);
  document.querySelector("#confirmMedication")?.addEventListener("click", () => {
    setPage("take");
    updateTake();
  });
  document.querySelector("#confirmMedication")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setPage("take");
      updateTake();
    }
  });
  document.querySelector("#runPillCountRecognition")?.addEventListener("click", () => void runPillCountRecognition());
  document.querySelector("#simulatePillCount")?.addEventListener("click", () => void simulatePillCountRecognition());
  document.querySelector("#refreshDoorState")?.addEventListener("click", () => void refreshCurrentTakeDoorState());
  document.querySelector("#startLoadGuide").addEventListener("click", () => {
    beginLoadGuide("manual");
  });
  document.querySelector("#nextLoadStep").addEventListener("click", () => void recognizeCurrentLoadMedicineBox());
  document.querySelector("#backAssistant")?.addEventListener("click", () => {
    closeKeyboard();
    els.assistantMode.hidden = false;
    els.assistantChat.hidden = true;
    els.assistantMode.classList.remove("chatting");
    delete els.assistantChat.dataset.active;
  });
  document.querySelector("#sendChat").addEventListener("click", sendChat);
  document.querySelector("#chatInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") sendChat();
  });
  document.querySelector("#assistantVoice")?.addEventListener("click", () => toggleK1VoiceInput("assistantQuickInput"));
  document.querySelectorAll("#assistantPhoto, #chatPhoto").forEach((button) => {
    button.addEventListener("click", () => void captureMedicineBoxFromAssistant());
  });
  document.querySelector("#assistantClear")?.addEventListener("click", clearAssistantPage);
  document.querySelector("#assistantMore")?.addEventListener("click", () => {
    const quick = document.querySelector("#assistantQuickInput");
    const text = quick?.value.trim();
    if (!text) return showToast("请输入想咨询的问题");
    startAssistant("quick");
    sendAssistantText(text);
    quick.value = "";
  });
  document.querySelector("#assistantQuickInput")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.currentTarget.value.trim()) {
      event.preventDefault();
      const text = event.currentTarget.value.trim();
      event.currentTarget.value = "";
      startAssistant("quick");
      sendAssistantText(text);
    }
  });
  document.querySelector("#assistantQuickInput")?.addEventListener("focus", (event) => openKeyboard(event.currentTarget));
  document.querySelector("#chatInput")?.addEventListener("focus", (event) => openKeyboard(event.currentTarget));
  document.querySelector("#voiceBtn").addEventListener("click", () => toggleK1VoiceInput("chatInput"));
  document.addEventListener("focusin", (event) => {
    const input = event.target;
    if (!input.matches("input, textarea")) return;
    openKeyboard(input);
  });
  document.addEventListener("click", (event) => {
    const input = event.target.closest("input, textarea");
    if (!input) return;
    openKeyboard(input);
  });
  document.addEventListener(
    "pointerdown",
    (event) => {
      if (!els.keyboard.classList.contains("show")) return;
      if (event.target.closest("#touchKeyboard, input, textarea, select, #sendChat, #voiceBtn, .chat-input button")) return;
      closeKeyboard();
    },
    true,
  );
  els.keyboardQuick.addEventListener("pointerdown", (event) => {
    const button = event.target.closest("[data-word]");
    if (!button) return;
    event.preventDefault();
    pinyinBuffer = "";
    writeKeyboardText(button.dataset.word);
  });
  els.keyboardModes.addEventListener("pointerdown", (event) => {
    const button = event.target.closest("[data-mode]");
    if (!button || !activeKeyboardInput) return;
    event.preventDefault();
    if (pinyinBuffer) flushPinyinBuffer();
    keyboardMode = button.dataset.mode;
    pinyinBuffer = "";
    renderKeyboard(activeKeyboardInput);
  });
  els.keyboardKeys.addEventListener("pointerdown", (event) => {
    const wordButton = event.target.closest("[data-word]");
    if (wordButton) {
      event.preventDefault();
      pinyinBuffer = "";
      writeKeyboardText(wordButton.dataset.word);
      return;
    }
    const button = event.target.closest("[data-key]");
    if (!button) return;
    event.preventDefault();
    const key = button.dataset.key;
    if (key === "完成") return closeKeyboard();
    if (key === "退格") return backspaceKeyboard();
    if (key === "清空") return clearActiveInput();
    if (key === "语音") {
      toggleK1VoiceInput(activeKeyboardInput?.id || "assistantQuickInput");
      return;
    }
    if (key === "shift") {
      showToast("已切换大写/小写演示");
      return;
    }
    if (key === "拼音") {
      keyboardMode = "pinyin";
      pinyinBuffer = "";
      return renderKeyboard(activeKeyboardInput);
    }
    if (key === "中/英") {
      if (pinyinBuffer) flushPinyinBuffer();
      keyboardMode = "abc";
      return renderKeyboard(activeKeyboardInput);
    }
    if (key === "123") {
      if (pinyinBuffer) flushPinyinBuffer();
      keyboardMode = "number";
      return renderKeyboard(activeKeyboardInput);
    }
    if (key === "符") {
      if (pinyinBuffer) flushPinyinBuffer();
      keyboardMode = "symbol";
      return renderKeyboard(activeKeyboardInput);
    }
    if (key === "空格") {
      if (pinyinBuffer) return flushPinyinBuffer();
      return writeKeyboardText(" ");
    }
    if (keyboardMode === "pinyin" && /^[a-z]$/.test(key)) {
      pinyinBuffer += key;
      return renderKeyboard(activeKeyboardInput);
    }
    writeKeyboardText(key);
  });
  document.querySelector("#keyboardClose").addEventListener("click", closeKeyboard);
  els.keyboardSize.addEventListener("click", () => {
    keyboardLarge = !keyboardLarge;
    els.keyboard.classList.toggle("large", keyboardLarge);
    els.keyboardSize.textContent = keyboardLarge ? "缩小" : "放大";
  });
  els.keyboardZoomOut?.addEventListener("click", () => {
    keyboardScale = Math.max(0.82, Number((keyboardScale - 0.04).toFixed(2)));
    if (activeKeyboardInput) renderKeyboard(activeKeyboardInput);
  });
  els.keyboardZoomIn?.addEventListener("click", () => {
    keyboardScale = Math.min(1.12, Number((keyboardScale + 0.04).toFixed(2)));
    if (activeKeyboardInput) renderKeyboard(activeKeyboardInput);
  });
}

function renderAlarm() {
  const next = reminderById(activeDoseId) || nextReminder();
  const card = document.querySelector(".alarm-card");
  if (!card || !next) return;
  const voice = alarmVoiceTextForRow(next);
  const queueIndex = voiceQueueIds.indexOf(next.id);
  const queueText = queueIndex >= 0 && voiceQueueIds.length > 1 ? `同时间段第 ${queueIndex + 1}/${voiceQueueIds.length} 位患者` : "当前患者";
  card.innerHTML = `
    <span>服药闹钟</span>
    <h2>${escapeHtml(next.member.name)}，该服药了</h2>
    <p>${escapeHtml(queueText)} · ${escapeHtml(voice)}</p>
    <div class="pulse"></div>
    <button class="primary huge" id="startDose" type="button">开始服药</button>`;
  document.querySelector("#startDose").addEventListener("click", beginDoseFromAlarm);
}

function updateClock() {
  const now = new Date();
  const week = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][now.getDay()];
  const clock = document.querySelector("#clock");
  const dateText = document.querySelector("#dateText");
  if (clock) clock.textContent = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  if (dateText) dateText.textContent = `${now.getMonth() + 1}月${now.getDate()}日 ${week}`;
}

function checkDueReminder() {
  resetDailyReminderState();
  const now = new Date();
  const due = todaySchedule().find((row) => !row.done && !announcedDoseIds.has(row.id) && isReminderDueNow(row, now));
  if (!due) return;
  beginVoiceSequence(due.member, due, { markAnnounced: true });
}

function bindCabinetPersistenceGuards() {
  window.addEventListener("beforeunload", (event) => {
    const saved = persistAllLocalState("beforeunload");
    if (!saved) {
      event.preventDefault();
      event.returnValue = "";
      return event.returnValue;
    }
    return undefined;
  });
  window.addEventListener("pagehide", () => {
    releaseAssistantResources();
    releasePillCount();
    releaseLoadMedicineBox();
    resetPrescriptionOcrWorkflow({ clearDraft: true });
    void releaseFacePreview();
    stopCpuMetricsPolling();
    persistAllLocalState("pagehide");
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      stopCpuMetricsPolling();
      persistAllLocalState("visibility-hidden");
    } else {
      startCpuMetricsPolling();
    }
  });
}

let takePillSessionId = "";
let takePillPreloadPromise = null;
let takeClearanceTimer = 0;
let takeOpenTimer = 0;
let takeFinishTimer = 0;
let takeWorkflowToken = 0;
let takeRecognitionAttemptToken = 0;
let takeRecognitionSimulated = false;
let takeItemCompleting = false;
let takeRecognitionBusy = false;
let takeClearanceBusy = false;
let takeClearanceZeroStreak = 0;
let takePromptCompleted = false;
let takeDrawerOpen = false;
let takeDrawerOpening = false;
let takeDoorObservedOpen = false;
let takeDoorState = "unknown";
let takeDoorPollTimer = 0;
let takeDoorPollKey = "";
let takeDoorPollFailures = 0;
let facePreviewStream = null;
let facePreviewSessionId = "";
let facePreviewSessionPage = "";
let facePreviewPromise = null;

async function requestPillCount(path, options = {}) {
  try {
    const pillCount = (await getK1GatewayClient()).pillCount;
    const input = clientRequestInput(options);
    const requestOptions = clientRequestOptions(options, 50000);
    if (path === "/preload") return await pillCount.preload(input, requestOptions);
    if (path === "/recognitions") return await pillCount.recognize(input, requestOptions);
    if (path === "/release") return await pillCount.release(input, requestOptions);
    throw new Error(`unsupported_pill_count_operation:${path}`);
  } catch (error) {
    translateClientTimeout(error, "pill_count_timeout");
  }
}

async function requestFace(path, options = {}) {
  try {
    const face = (await getK1GatewayClient()).face;
    const input = clientRequestInput(options);
    const requestOptions = clientRequestOptions(options, 50000);
    if (path === "/preload") return await face.preload(input, requestOptions);
    if (path === "/register") return await face.register(input, requestOptions);
    if (path === "/verify") return await face.verify(input, requestOptions);
    if (path === "/remove") return await face.remove(input, requestOptions);
    if (path === "/reset") return await face.reset(input, requestOptions);
    if (path === "/release") return await face.release(input, requestOptions);
    throw new Error(`unsupported_face_operation:${path}`);
  } catch (error) {
    translateClientTimeout(error, "face_timeout");
  }
}

function updateFacePreviewState(text) { const state = document.querySelector("#facePreviewState"); if (state) state.textContent = text; }
async function ensureFacePreview(page = "face") {
  if (facePreviewPromise) return facePreviewPromise;
  if (facePreviewSessionId && facePreviewSessionPage === page) {
    return { ready: true, sessionId: facePreviewSessionId, previewRunning: true };
  }
  if (facePreviewSessionId && facePreviewSessionPage && facePreviewSessionPage !== page) {
    await releaseFacePreview();
  }
  const sessionId = `${page}-${Date.now()}`;
  facePreviewPromise = requestFace("/preload", { method: "POST", body: JSON.stringify({ sessionId, page }) })
    .then((result) => {
      if (result?.ready !== true) throw new Error("face_not_ready");
      facePreviewSessionId = sessionId;
      facePreviewSessionPage = page;
      return result;
    })
    .finally(() => {
      facePreviewPromise = null;
    });
  return facePreviewPromise;
}
async function releaseFacePreview() {
  const sessionId = facePreviewSessionId;
  facePreviewSessionId = "";
  facePreviewSessionPage = "";
  facePreviewPromise = null;
  if (!sessionId) return;
  try {
    await requestFace("/release", { method: "POST", body: JSON.stringify({ sessionId }) });
  } catch (_) {}
}
function setTakePhase(text) { const element = document.querySelector("#takePhase"); if (element) element.textContent = text; }
function pillRequest(item, extra = {}) { return { sessionId: takePillSessionId, source: "take-flow", memberId: item?.member?.id || "", cabinetId: item?.drawer || "", medicineName: item?.med?.name || "", expectedCount: Number(item?.med?.amount || 0), unit: item?.med?.unit || "", ...extra }; }
async function ensurePillCountReady(item) {
  if (takePillPreloadPromise) return takePillPreloadPromise;
  if (!takePillSessionId) takePillSessionId = `take-pill-count-${currentTakeDoseId || activeDoseId || "dose"}-${Date.now()}`;
  takePillPreloadPromise = requestPillCount("/preload", { method: "POST", body: JSON.stringify(pillRequest(item)) });
  try { const result = await takePillPreloadPromise; if (result?.ready !== true) throw new Error("pill_count_not_ready"); return result; }
  catch (error) { takePillSessionId = ""; throw error; }
  finally { takePillPreloadPromise = null; }
}
function releasePillCount() {
  clearTimeout(takeClearanceTimer); clearTimeout(takeOpenTimer); clearTimeout(takeFinishTimer); stopTakeDoorMonitoring(); takeWorkflowToken += 1; takeRecognitionAttemptToken += 1;
  takeDrawerOpen = false;
  takeDrawerOpening = false;
  takeDoorObservedOpen = false;
  takeDoorState = "unknown";
  takePromptCompleted = false;
  takeRecognitionBusy = false;
  takeClearanceBusy = false;
  takeClearanceZeroStreak = 0;
  const sessionId = takePillSessionId;
  const pending = takePillPreloadPromise;
  takePillSessionId = "";
  takePillPreloadPromise = null;
  if (!sessionId) return;
  // If the user leaves while preload is still starting the camera, release
  // only after that request settles; otherwise the backend could create a
  // session after receiving an early release and keep OV5647 open.
  const release = () => requestPillCount("/release", { method: "POST", body: JSON.stringify({ sessionId }) }).catch(() => {});
  if (pending) void Promise.resolve(pending).catch(() => {}).then(release);
  else void release();
}
function takeSpeechDelay(text) { return Math.max(1200, Math.min(9000, String(text || "").length * 190)); }
function scheduleClearance(delayMs = 3000) { clearTimeout(takeClearanceTimer); const token = takeWorkflowToken; takeClearanceTimer = window.setTimeout(() => { takeClearanceTimer = 0; void checkTakeClearance(token); }, delayMs); }
function scheduleSimulatedDoorCheck(delayMs = 1000) { clearTimeout(takeClearanceTimer); const token = takeWorkflowToken; takeClearanceTimer = window.setTimeout(() => { takeClearanceTimer = 0; void checkSimulatedDoorCheck(token); }, delayMs); }
function wangGrandpaCountMismatchAudioKey(diff) {
  const count = Math.abs(Number(diff));
  if (!Number.isInteger(count) || count < 1 || count > 4) return "";
  return diff < 0 ? `take_short_${count}` : `take_extra_${count}`;
}
function wangGrandpaClearanceAudioKey(message) {
  if (message.startsWith("药物已取出")) return "take_clear_taken";
  if (message.startsWith("药柜已推回")) return "take_clear_closed";
  return "take_clear_both";
}
async function checkTakeClearance(token) {
  const item = takeQueue[takeIndex]; if (!item || token !== takeWorkflowToken || activePageId !== "take" || takeClearanceBusy) return;
  let retryDelay = 0;
  takeClearanceBusy = true;
  renderTakeRecognitionControls();
  setTakePhase("步骤 3/3：正在复检药柜和识别区");
  try {
    const result = await requestPillCount("/recognitions", { method: "POST", body: JSON.stringify(pillRequest(item, { expectedCount: 0, phase: "clearance" })) });
    if (token !== takeWorkflowToken) return;
    const door = await refreshCabinetStateViaGateway(item.drawer);
    if (token !== takeWorkflowToken) return;
    if (!cabinetDoorStateFromGpio(door)) throw new Error("cabinet_state_not_from_gpio");
    doorConfirmed = cabinetClosedFromPayload(door); const remaining = Number(result?.count ?? result?.detectedCount);
    if (!Number.isFinite(remaining) || remaining < 0) throw new Error("invalid_clearance_count");
    if (doorConfirmed && remaining === 0) {
      takeClearanceZeroStreak += 1;
      if (takeClearanceZeroStreak >= 2) return completeCurrentTakeItem();
      els.takeState.textContent = "第一次复检识别区为空，正在再次确认。";
      setTakePhase("步骤 3/3：正在再次确认识别区");
      retryDelay = 300;
      return;
    }
    takeClearanceZeroStreak = 0;
    const message = !doorConfirmed && remaining === 0 ? "药物已取出，药柜还未推回，请您将药柜推回。" : doorConfirmed ? "药柜已推回，药物还未取走，请您将药物取走。" : "药柜未推回，药物未取走，请您将药柜推回，并将药物取走。";
    document.querySelector("#detectedCount").textContent = `${remaining}${item.med.unit || ""}`; setTakeRecognitionResult("正在复检");
    els.takeState.textContent = message; setTakePhase("步骤 3/3：请按提示完成复检");
    await announce(message);
    if (isCurrentTakeItem(item, token)) retryDelay = 3000;
  } catch (error) {
    if (token === takeWorkflowToken) {
      takeClearanceZeroStreak = 0;
      els.takeState.textContent = `复检失败：${error.message}，将继续检测。`;
      retryDelay = 3000;
    }
  }
  finally {
    if (token === takeWorkflowToken) {
      takeClearanceBusy = false;
      renderTakeRecognitionControls();
      if (retryDelay) scheduleClearance(retryDelay);
    }
  }
}
async function checkSimulatedDoorCheck(token) {
  const item = takeQueue[takeIndex];
  if (!item || token !== takeWorkflowToken || activePageId !== "take" || !pillConfirmed) return;
  setTakePhase("步骤 3/3：正在确认柜门关闭");
  try {
    const door = await refreshCabinetStateViaGateway(item.drawer);
    if (token !== takeWorkflowToken) return;
    doorConfirmed = cabinetClosedFromPayload(door);
    if (doorConfirmed) return completeCurrentTakeItem();
    els.takeState.textContent = "数量正确，请取走药物并关闭药柜。";
    setTakePhase("步骤 3/3：请关闭药柜");
    scheduleSimulatedDoorCheck(1200);
  } catch (error) {
    if (token !== takeWorkflowToken) return;
    els.takeState.textContent = `柜门状态读取失败：${error.message}`;
    setTakePhase("步骤 3/3：等待柜门状态");
    scheduleSimulatedDoorCheck(2000);
  }
}
async function completeCurrentTakeItem() {
  const item = takeQueue[takeIndex]; if (!item || takeItemCompleting) return;
  takeItemCompleting = true;
  const token = takeWorkflowToken;
  stopTakeDoorMonitoring();
  takeDrawerOpen = false;
  takeDoorState = "closed";
  updateTakeVisualState();
  renderTakeRecognitionControls();
  els.takeState.textContent = `${item.drawer}号药柜已关闭，正在播报确认信息。`;
  setTakePhase("步骤 3/3：柜门已关闭");
  await announce(`${item.drawer}号药柜已关闭。`, false, 1, "");
  if (!isCurrentTakeItem(item, token)) return;
  const sourceLabel = takeRecognitionSimulated ? "（模拟数量识别）" : "";
  recordInventoryOperation("take", item, item.med.amount, `${currentTakeDoseId}:${item.med.name}`); addReport("取药确认", `${item.med.name} 已完成数量确认、药物取走和柜门关闭${sourceLabel}`, item.member.name);
  const last = takeIndex >= takeQueue.length - 1; const message = last ? "本轮药物已全部服用完毕，祝您早日康复！" : "现在进入下一种药物的服药环节。";
  els.takeState.textContent = message; setTakePhase(last ? "本轮取药完成" : "当前药品完成，准备下一种药物");
  await announce(message);
  if (!isCurrentTakeItem(item, token)) return;
  takeIndex += 1;
  updateTake();
}
function isCurrentTakeRecognitionAttempt(item, workflowToken, attemptToken) {
  return attemptToken === takeRecognitionAttemptToken && isCurrentTakeItem(item, workflowToken);
}

async function continuePillCountMismatch(item, text, workflowToken, attemptToken) {
  const announcement = announce(text);
  let shouldReopen = false;
  takeDoorState = "checking";
  updateTakeVisualState();
  renderTakeRecognitionControls();
  try {
    const state = await refreshCabinetStateViaGateway(item.drawer);
    if (!isCurrentTakeRecognitionAttempt(item, workflowToken, attemptToken)) return;
    if (!cabinetDoorStateFromGpio(state)) throw new Error("cabinet_state_not_from_gpio");
    shouldReopen = cabinetClosedFromPayload(state);
    takeDrawerOpen = !shouldReopen;
    if (!shouldReopen) takeDoorObservedOpen = true;
    takeDoorState = shouldReopen ? "closed" : "open";
    doorConfirmed = shouldReopen && takeDoorObservedOpen;
    updateTakeVisualState();
    if (shouldReopen && isCurrentTakeRecognitionAttempt(item, workflowToken, attemptToken)) {
      els.takeState.textContent = "数量不符，正在重新打开药柜，请按提示调整药物后重试。";
      const reopen = ensureCurrentTakeCabinetOpen(item, workflowToken);
      await Promise.allSettled([announcement, reopen]);
      if (isCurrentTakeRecognitionAttempt(item, workflowToken, attemptToken) && takeDoorState === "open") {
        els.takeState.textContent = text;
        setTakePhase("数量不符：药柜已重新打开，调整药物后重新识别");
      }
      return;
    }
    renderTakeRecognitionControls();
  } catch (error) {
    if (isCurrentTakeRecognitionAttempt(item, workflowToken, attemptToken)) {
      takeDoorState = "error";
      updateTakeVisualState();
      renderTakeRecognitionControls();
      els.takeState.textContent = `数量不符，请调整药物后重新识别。柜门状态读取失败：${error.message}`;
    }
  }
  await announcement;
}

async function applyPillCountResult(result, { simulated = false, attemptToken = takeRecognitionAttemptToken } = {}) {
  const item = takeQueue[takeIndex]; if (!item) return;
  const detected = Number(result?.count ?? result?.detectedCount), target = Number(item.med.amount || 0), diff = detected - target, unit = item.med.unit || "";
  if (!Number.isFinite(detected) || detected < 0) throw new Error("invalid_pill_count_result");
  const classMismatch = takeDetectionClassMismatch(result, item);
  const expectedClass = takeExpectedDetectionClass(item);
  takeRecognitionSimulated = Boolean(simulated);
  const quantityText = diff === 0
    ? "与目标数量一致，数量正确。"
    : diff < 0
      ? `您少拿了${Math.abs(diff)}个药品，请补放${Math.abs(diff)}个药品到识别区，并重新点击开始识别。`
      : `您多拿了${Math.abs(diff)}个药品，请将多拿的${Math.abs(diff)}个药品放回药柜，并重新点击开始识别。`;
  const typeText = !expectedClass
    ? "当前剂型不支持 YOLO 数量核验，请人工核对后再继续。"
    : `识别到的药物类型与目标不符，请按目标药物调整后重新识别。`;
  const text = diff !== 0 ? `${quantityText}${classMismatch ? ` ${typeText}` : ""}` : classMismatch ? typeText : quantityText;
  const resultText = !expectedClass
    ? "当前剂型不支持"
    : classMismatch && diff === 0
      ? "识别类型不符"
      : diff === 0
        ? (simulated ? "模拟数量正确" : "数量正确")
        : `${diff < 0 ? "少拿" : "多拿"}${Math.abs(diff)}${unit}${classMismatch ? "，类型不符" : ""}`;
  document.querySelector("#detectedCount").textContent = `${detected}${unit}`; setTakeRecognitionResult(resultText); setOcrBoxState(`${simulated ? "模拟识别到" : "识别到"} ${detected}${unit}`); pillConfirmed = diff === 0 && !classMismatch;
  if (diff === 0 && !classMismatch) {
    const cabinetAlreadyClosed = doorConfirmed && takeDoorObservedOpen && takeDoorState === "closed";
    doorConfirmed = cabinetAlreadyClosed;
    const next = cabinetAlreadyClosed
      ? `${simulated ? "模拟数量正确" : "数量识别正确"}，药柜已关闭，请将药物从识别区取走。`
      : `${simulated ? "模拟数量正确" : "数量识别正确"}，请将药物从识别区取走，并关闭药柜。`;
    els.takeState.textContent = next;
    setTakePhase(cabinetAlreadyClosed ? "步骤 2/3：请取走识别区药物" : "步骤 2/3：请取走药物并关闭药柜");
    renderTakeRecognitionControls();
    if (simulated) {
      await announce(next);
      scheduleSimulatedDoorCheck(500);
      return;
    }
    const token = takeWorkflowToken;
    await announce(next);
    if (isCurrentTakeItem(item, token)) scheduleClearance(0);
    return;
  }
  els.takeState.textContent = text; setTakePhase("数量不符：调整药物后重新识别");
  takeRecognitionBusy = false;
  renderTakeRecognitionControls();
  void continuePillCountMismatch(item, text, takeWorkflowToken, attemptToken);
}
async function runPillCountRecognition() {
  const item = takeQueue[takeIndex], button = document.querySelector("#runPillCountRecognition"), simulationButton = document.querySelector("#simulatePillCount"); if (!item) return;
  if (takeRecognitionBusy) return;
  if (!canRunTakeRecognition()) return showToast(takeDoorState === "error" ? "请先刷新柜门状态" : "请等待药柜至少确认打开一次后再开始识别");
  const workflowToken = takeWorkflowToken;
  const attemptToken = ++takeRecognitionAttemptToken;
  stopVoiceAnnouncements();
  takeRecognitionBusy = true;
  if (button) button.disabled = true; if (simulationButton) simulationButton.disabled = true;
  takeRecognitionSimulated = false;
  setOcrBoxState("正在进行真实数量识别"); setTakeRecognitionResult("正在识别");
  try {
    await ensurePillCountReady(item);
    if (!isCurrentTakeRecognitionAttempt(item, workflowToken, attemptToken)) return;
    const result = await requestPillCount("/recognitions", { method: "POST", body: JSON.stringify(pillRequest(item)) });
    if (!isCurrentTakeRecognitionAttempt(item, workflowToken, attemptToken)) return;
    await applyPillCountResult(result, { attemptToken });
  } catch (error) {
    if (!isCurrentTakeRecognitionAttempt(item, workflowToken, attemptToken)) return;
    setOcrBoxState("识别失败"); setTakeRecognitionResult("识别失败，请重试"); els.takeState.textContent = `请检查识别区和 K1 摄像头后重试：${error.message}`; void showTaskDiagnostic("yolo", error);
  } finally {
    if (isCurrentTakeRecognitionAttempt(item, workflowToken, attemptToken)) {
      takeRecognitionBusy = false;
      renderTakeRecognitionControls();
    }
  }
}
async function simulatePillCountRecognition() {
  const item = takeQueue[takeIndex];
  const button = document.querySelector("#simulatePillCount");
  const realButton = document.querySelector("#runPillCountRecognition");
  if (!item || button?.disabled || !canRunTakeRecognition()) return;
  const workflowToken = takeWorkflowToken;
  const attemptToken = ++takeRecognitionAttemptToken;
  stopVoiceAnnouncements();
  if (button) button.disabled = true;
  if (realButton) realButton.disabled = true;
  takeRecognitionBusy = true;
  setOcrBoxState("正在模拟数量识别");
  setTakeRecognitionResult("正在模拟识别");
  try {
    await delay(250);
    if (!isCurrentTakeRecognitionAttempt(item, workflowToken, attemptToken)) return;
    await applyPillCountResult({ count: Number(item.med.amount || 0), source: "simulation" }, { simulated: true, attemptToken });
  } catch (error) {
    if (!isCurrentTakeRecognitionAttempt(item, workflowToken, attemptToken)) return;
    setOcrBoxState("模拟识别失败");
    setTakeRecognitionResult("模拟识别失败");
  } finally {
    if (isCurrentTakeRecognitionAttempt(item, workflowToken, attemptToken)) {
      takeRecognitionBusy = false;
      renderTakeRecognitionControls();
    }
  }
}
function tryNextDrug() {
  if (!pillConfirmed) return;
  if (takeRecognitionSimulated) {
    if (doorConfirmed) completeCurrentTakeItem();
    else scheduleSimulatedDoorCheck(0);
    return;
  }
  scheduleClearance(0);
}
async function startFacePreview() { await ensureFacePreview("take-face"); const state = document.querySelector("#facePreviewState"); if (state) state.textContent = "K1 IMX219 摄像头正在进行人脸核验"; return true; }
async function verifyActiveMemberFace() {
  const member = memberForActiveDose();
  if (!member?.faceEnrolled) return showToast(`${member?.name || "当前成员"} 未录入人脸，请先到成员管理录入`);
  const expectedIdentity = {
    memberId: String(member.id || "").trim(),
    name: String(member.name || "").trim(),
  };
  if (!expectedIdentity.memberId || !expectedIdentity.name) return showToast("当前服药成员身份不完整，请返回成员管理检查资料");
  const button = document.querySelector("#faceBackend");
  const sessionId = facePreviewSessionPage === "take-face" && facePreviewSessionId
    ? facePreviewSessionId
    : `take-face-${expectedIdentity.memberId}-${Date.now()}`;
  if (button) button.disabled = true;
  setFaceRecognitionStatus("recognizing");
  try {
    await startFacePreview();
    const ready = await requestFace("/preload", { method: "POST", body: JSON.stringify({ sessionId, page: "take-face" }) });
    if (ready?.ready !== true) throw new Error("face_not_ready");
    const result = await requestFace("/verify", {
      method: "POST",
      body: JSON.stringify({ sessionId, memberId: expectedIdentity.memberId, expectedName: expectedIdentity.name, timeoutSeconds: 30, threshold: 0.6, stableFrames: 3 }),
      timeoutMs: 55000,
    });
    const scoreText = Number.isFinite(Number(result?.score)) ? `，相似度 ${Number(result.score).toFixed(2)}` : "";
    if (!(result?.verified === true || ["verified", "recognized", "success"].includes(result?.status))) {
      throw new Error(`face_verification_failed${scoreText}`);
    }
    if (
      String(result?.memberId || "").trim() !== expectedIdentity.memberId
      || String(result?.expectedName || "").trim() !== expectedIdentity.name
      || String(result?.name || "").trim() !== expectedIdentity.name
    ) {
      throw new Error("face_identity_mismatch");
    }
    setFaceRecognitionStatus("success");
    await delay(650);
    clearSavedFlowState();
    takeQueue = prepareTakeQueue();
    takeIndex = 0;
    setPage("med-confirm");
    renderMedicationConfirm();
    addReport("人脸核验", `${expectedIdentity.name} 人脸核验通过${scoreText}`, expectedIdentity.name);
  } catch (error) {
    setFaceRecognitionStatus("failure");
    showToast(`识别失败，请重新开始核验：${error.message}`);
    void showTaskDiagnostic("face", error);
  } finally {
    if (button) button.disabled = false;
  }
}

function simulateActiveMemberFace() {
  const member = memberForActiveDose();
  if (!member) return showToast("未找到当前服药成员，无法模拟核验");
  stopVoiceAnnouncements({ clearPageKey: true });
  setFaceRecognitionStatus("success");
  clearSavedFlowState();
  takeQueue = prepareTakeQueue();
  takeIndex = 0;
  setPage("med-confirm");
  renderMedicationConfirm();
  addReport("人脸核验", `${member.name} 模拟核验通过`, member.name);
  showToast("模拟核验通过");
}

function createDraftMemberId() {
  return "draft-member-" + Date.now() + "-" + Math.random().toString(16).slice(2);
}

function stripLegacyPin(member) {
  const { pin, ...withoutPin } = member || {};
  return withoutPin;
}

function currentMemberFormIdentity() {
  return {
    memberId: String(editingId || draftMemberId || "").trim(),
    name: String(document.querySelector("#memberName")?.value || "").trim(),
  };
}

function updateMemberFormFaceStatus() {
  const { name } = currentMemberFormIdentity();
  const existing = editingId ? members.find((member) => member.id === editingId) : null;
  const draftReady = Boolean(draftFaceEnrollment && !draftFaceEnrollment.stale && draftFaceEnrollment.name === name);
  const existingFaceStillValid = Boolean(existing?.faceEnrolled && existing.name === name);
  const renamedExistingFace = Boolean(editingId && existing?.faceEnrolled && existing.name !== name && !draftReady);
  const faceReady = Boolean(existingFaceStillValid || draftReady);
  const text = document.querySelector(".security-grid p");
  const button = document.querySelector("#faceEnrollBackend");
  if (text) {
    text.textContent = faceReady
      ? editingId ? "人脸已录入。可直接保存成员资料，或重新录入更新人脸。" : "人脸已录入，待保存成员资料。保存与人脸录入相互独立。"
      : renamedExistingFace || draftFaceEnrollment?.stale ? "姓名已变更，请重新录入人脸后再保存成员。" : "填写姓名后即可直接录入人脸，无需保存成员资料。";
  }
  if (button) button.textContent = faceReady ? "重新录入" : "开始录入";
}

function fixedWangGrandpaPlan(member) {
  if (!isWangGrandpaMember(member)) return member;
  const profile = cloneDemoProfile(wangGrandpaDemoProfile);
  const stockByMedicine = new Map((member.medicines || []).map((medicine) => [normalizedMedicineName(medicine.name), medicine.stock || ""]));
  return {
    ...member,
    demoProfile: profile.demoProfile,
    schedule: { ...profile.schedule, breakfast: "08:00" },
    medicines: profile.medicines.map((medicine) => ({
      ...medicine,
      stock: stockByMedicine.get(normalizedMedicineName(medicine.name))
        || (normalizedMedicineName(medicine.name) === "复合维生素b" ? stockByMedicine.get("复合维生素b片") : "")
        || "",
    })),
    planOverrides: profile.planOverrides,
    planExplanation: profile.planExplanation,
    planExplanationGeneratedAt: "preset",
  };
}

function wangGrandpaExpectedDrawer(item) {
  if (!isWangGrandpaMember(item?.member)) return 0;
  const medicine = normalizedMedicineName(item?.med?.name);
  if (medicine === "小柴胡颗粒") return 7;
  if (medicine === "复合维生素b" || medicine === "复合维生素b片") return 8;
  if (medicine === "盐酸西替利嗪片") return 9;
  return 0;
}

function cabinetAssignmentKey(member, med) {
  return `${member?.id || "unknown"}::${normalizedMedicineName(med?.name)}`;
}

function loadCabinetAssignments() {
  try {
    const stored = JSON.parse(localStorage.getItem(CABINET_ASSIGNMENT_KEY) || "{}");
    return stored && typeof stored === "object" && !Array.isArray(stored) ? stored : {};
  } catch {
    return {};
  }
}

function cabinetPlanError(candidateMembers) {
  const reservedDrawers = new Set([7, 8, 9]);
  let ordinaryCount = 0;
  let totalCount = 0;
  for (const member of candidateMembers) {
    const names = new Set();
    for (const med of member.medicines || []) {
      if (!String(med?.name || "").trim()) continue;
      totalCount += 1;
      const normalized = normalizedMedicineName(med.name);
      if (names.has(normalized)) return `${member.name}存在重复药品：${med.name}`;
      names.add(normalized);
      if (!wangGrandpaExpectedDrawer({ member, med })) ordinaryCount += 1;
    }
  }
  if (totalCount > 12) return "12 个药柜已全部占用，请先删除不再使用的药品";
  if (ordinaryCount > 12 - reservedDrawers.size) return "普通药品可用柜位不足；7、8、9 号柜为王爷爷固定药位";
  return "";
}

function isWangGrandpaTakeRow(row) {
  if (!isWangGrandpaMember(row?.member) || String(row?.slot || "") !== "早餐后") return false;
  const medicines = new Map((row?.meds || []).map((medicine) => [normalizedMedicineName(medicine.name), Number(medicine.amount)]));
  const vitaminAmount = medicines.get("复合维生素b") ?? medicines.get("复合维生素b片");
  return medicines.size === 2 && medicines.get("小柴胡颗粒") === 1 && vitaminAmount === 2;
}

function allMedicineItems() {
  const items = [];
  members.forEach((member) => {
    (member.medicines || []).forEach((med) => {
      if (med.name) items.push({ member, med });
    });
  });
  const previous = loadCabinetAssignments();
  const assignments = {};
  const used = new Set();
  const reserved = new Set([7, 8, 9]);

  // Fixed competition drawers are authoritative and are claimed first.
  items.forEach((item) => {
    const fixed = wangGrandpaExpectedDrawer(item);
    if (!fixed) return;
    assignments[cabinetAssignmentKey(item.member, item.med)] = fixed;
    used.add(fixed);
  });

  // Keep every valid historic assignment.  Reordering members or medicines
  // must never move a physical medicine to another drawer in software.
  items.forEach((item) => {
    if (wangGrandpaExpectedDrawer(item)) return;
    const key = cabinetAssignmentKey(item.member, item.med);
    const drawer = Number(previous[key]);
    if (Number.isInteger(drawer) && drawer >= 1 && drawer <= 12 && !reserved.has(drawer) && !used.has(drawer)) {
      assignments[key] = drawer;
      used.add(drawer);
    }
  });

  items.forEach((item) => {
    if (wangGrandpaExpectedDrawer(item)) return;
    const key = cabinetAssignmentKey(item.member, item.med);
    if (assignments[key]) return;
    const drawer = Array.from({ length: 12 }, (_, index) => index + 1)
      .find((candidate) => !reserved.has(candidate) && !used.has(candidate));
    assignments[key] = drawer || 0;
    if (drawer) used.add(drawer);
  });

  if (JSON.stringify(assignments) !== JSON.stringify(previous)) {
    localStorage.setItem(CABINET_ASSIGNMENT_KEY, JSON.stringify(assignments));
  }
  return items.map((item) => ({
    ...item,
    drawer: assignments[cabinetAssignmentKey(item.member, item.med)] || 0,
  }));
}

function saveForm() {
  let next = collectForm();
  if (!next) return;
  if (!editingId && draftFaceEnrollment?.stale) {
    showToast("姓名已变更，请重新录入人脸后再保存成员");
    return;
  }
  const previous = editingId ? members.find((item) => item.id === editingId) : null;
  if (previous?.faceEnrolled && previous.name !== next.name && !(draftFaceEnrollment && !draftFaceEnrollment.stale && draftFaceEnrollment.name === next.name)) {
    showToast("姓名已变更，请重新录入人脸后再保存成员");
    return;
  }
  // The visible form is authoritative. Fixed demo data is applied only when
  // the user explicitly taps the simulation button in fillDemoMemberForm().
  next = stripLegacyPin(next);
  const candidateMembers = editingId
    ? members.map((item) => item.id === editingId ? next : item)
    : [...members, next];
  const cabinetError = cabinetPlanError(candidateMembers);
  if (cabinetError) {
    showToast(cabinetError);
    return;
  }
  const action = editingId ? "修改成员" : "新增成员";
  const currentBefore = members.find((item) => item.id === next.id)?.current;
  next.current = Boolean(currentBefore || members.length === 0);
  if (editingId) members = members.map((item) => item.id === editingId ? next : item);
  else members.push(next);
  members = normalizeMembers(members).map(stripLegacyPin);
  saveMembers();
  saveCabinetState("member-form");
  addReport(action, next.name + " 的成员档案与 " + next.medicines.length + " 种药品已保存", next.name);
  draftFaceEnrollment = null;
  draftMemberId = "";
  showToast("成员资料已保存");
  setPage("members");
}

async function enrollFaceForEditingMember() {
  const identity = currentMemberFormIdentity();
  if (!identity.name) return showToast("请先填写姓名，再录入人脸");
  if (!identity.memberId) return showToast("成员身份尚未建立，请退出后重新打开成员表单");
  const button = document.querySelector("#faceEnrollBackend");
  const nameInput = document.querySelector("#memberName");
  const saveButton = document.querySelector("#saveMember");
  const previousNameReadOnly = Boolean(nameInput?.readOnly);
  const previousSaveDisabled = Boolean(saveButton?.disabled);
  const sessionId = "member-face-" + identity.memberId + "-" + Date.now();
  if (button) button.disabled = true;
  if (nameInput) nameInput.readOnly = true;
  if (saveButton) saveButton.disabled = true;
  try {
    const ready = await requestFace("/preload", { method: "POST", body: JSON.stringify({ sessionId, page: "member-form" }) });
    if (ready?.ready !== true) throw new Error("face_not_ready");
    const result = await requestFace("/register", {
      method: "POST",
      body: JSON.stringify({ sessionId, memberId: identity.memberId, name: identity.name, samples: 8, timeoutSeconds: 60 }),
      timeoutMs: 90000,
    });
    if (!["registered", "completed", "success"].includes(result?.status)) throw new Error("face_registration_failed");
    if (
      String(result?.memberId || "").trim() !== identity.memberId
      || String(result?.name || "").trim() !== identity.name
    ) {
      throw new Error("face_identity_mismatch");
    }
    const updatedAt = formatDateTime();
    if (editingId) {
      updateMemberSecurity(identity.memberId, { faceEnrolled: true, faceUpdatedAt: updatedAt });
      addReport("人脸录入", identity.name + " 的真实人脸录入已完成", identity.name);
      draftFaceEnrollment = { memberId: identity.memberId, name: identity.name, updatedAt, stale: false };
      localStorage.setItem(FACE_DRAFT_KEY, JSON.stringify(draftFaceEnrollment));
    } else {
      draftFaceEnrollment = { memberId: identity.memberId, name: identity.name, updatedAt, stale: false };
      localStorage.setItem(FACE_DRAFT_KEY, JSON.stringify(draftFaceEnrollment));
    }
    updateMemberFormFaceStatus();
    showToast(identity.name + " 的人脸录入完成");
  } catch (error) {
    showToast("人脸录入失败：" + error.message);
    void showTaskDiagnostic("face", error);
  } finally {
    // Release only the page session; the worker and both face models stay resident.
    void requestFace("/release", {
      method: "POST",
      body: JSON.stringify({ sessionId }),
      timeoutMs: 5000,
      keepalive: true,
    }).catch(() => {});
    if (button) button.disabled = false;
    if (nameInput) nameInput.readOnly = previousNameReadOnly;
    if (saveButton) saveButton.disabled = previousSaveDisabled;
  }
}

async function discardMemberForm(targetPage = "members") {
  const draft = !editingId ? draftFaceEnrollment : null;
  if (draft) {
    try {
      await requestFace("/remove", { method: "POST", body: JSON.stringify({ memberId: draft.memberId }), timeoutMs: 10000 });
    } catch (error) {
      showToast("未能清理未保存的人脸资料：" + error.message);
      return;
    }
  }
  draftFaceEnrollment = null;
  draftMemberId = "";
  localStorage.removeItem(FACE_DRAFT_KEY);
  setPage(targetPage);
}

function requestMemberFormCancel(targetPage = "members") {
  if (!editingId && draftFaceEnrollment) {
    requestDeleteConfirmation("未保存成员的人脸资料将一并删除，确认取消吗？", () => { void discardMemberForm(targetPage); });
    return;
  }
  void discardMemberForm(targetPage);
}

function ensureCompetitionResetDialog() {
  let dialog = document.querySelector("#competitionResetDialog");
  if (dialog) return dialog;
  dialog = document.createElement("section");
  dialog.id = "competitionResetDialog";
  dialog.className = "flow-exit-dialog competition-reset-dialog";
  dialog.hidden = true;
  dialog.innerHTML = '<article><h2>重置全部测试状态？</h2><p>将删除王爷爷的成员档案与服药方案，并清除全部成员的取药、放药、药柜管理、人脸录入和相关记录。</p><label class="reset-confirm-check"><input type="checkbox" id="competitionResetAcknowledged" />我已确认识别区已清空，所有药柜已推回。</label><div class="delete-confirm-actions"><button class="danger" id="competitionResetConfirm" type="button" disabled>确认重置</button><button class="ghost" id="competitionResetCancel" type="button">取消</button></div></article>';
  document.querySelector(".device")?.appendChild(dialog);
  dialog.querySelector("#competitionResetAcknowledged")?.addEventListener("change", (event) => {
    dialog.querySelector("#competitionResetConfirm").disabled = !event.currentTarget.checked;
  });
  dialog.querySelector("#competitionResetCancel")?.addEventListener("click", () => { dialog.hidden = true; });
  dialog.querySelector("#competitionResetConfirm")?.addEventListener("click", () => { void resetCompetitionState(); });
  return dialog;
}

function openCompetitionResetDialog() {
  const dialog = ensureCompetitionResetDialog();
  dialog.querySelector("#competitionResetAcknowledged").checked = false;
  dialog.querySelector("#competitionResetConfirm").disabled = true;
  dialog.hidden = false;
  dialog.querySelector("#competitionResetCancel")?.focus();
}

async function resetCompetitionState() {
  const dialog = ensureCompetitionResetDialog();
  const confirmButton = dialog.querySelector("#competitionResetConfirm");
  if (confirmButton?.disabled) return;
  confirmButton.disabled = true;
  try {
    const faceReset = await requestFace("/reset", { method: "POST", body: JSON.stringify({ scope: "all" }), timeoutMs: 15000 });
    if (faceReset?.reset !== true) throw new Error("face_reset_failed");
    stopWangGrandpaAudio();
    stopLoadGuideWorkflow(true);
    releasePillCount();
    window.K1StaticVoice?.stop();
    members = cloneDefaultMembers().map(stripLegacyPin);
    completedDoseIds = new Set();
    reports = [];
    inventoryLedger = [];
    activeDoseId = "";
    currentTakeDoseId = "";
    takeQueue = [];
    takeIndex = 0;
    pillConfirmed = false;
    doorConfirmed = false;
    activeLoadMemberId = "";
    loadItemIndex = 0;
    loadDrawerOpen = false;
    loadGuideFinished = false;
    selectedCabinetIndex = 0;
    announcedDoseIds.clear();
    voiceQueueIds = [];
    draftMemberId = "";
    draftFaceEnrollment = null;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(members));
    localStorage.setItem(PROGRESS_KEY, JSON.stringify({ date: todayKey(), ids: [], history: {} }));
    localStorage.setItem(REPORT_KEY, JSON.stringify([]));
    localStorage.setItem(INVENTORY_LEDGER_KEY, JSON.stringify([]));
    localStorage.removeItem(CABINET_STATE_KEY);
    localStorage.removeItem(CABINET_ASSIGNMENT_KEY);
    localStorage.removeItem(FLOW_STATE_KEY);
    localStorage.removeItem(FACE_DRAFT_KEY);
    localStorage.setItem(TWO_MEMBER_ROSTER_MIGRATION_KEY, "done");
    seedCompetitionBaselineLoad(members.map((member) => member.id));
    addReport("比赛状态重置", "已恢复王叔叔、何爷爷的新版档案，并确认两人的默认药品已完成入柜", "系统");
    dialog.hidden = true;
    setPage("home");
    showToast("全部测试状态已重置");
  } catch (error) {
    showToast("重置失败：" + error.message);
  } finally {
    confirmButton.disabled = false;
  }
}

function syncCabinetVoiceVolumeControl(value = window.K1StaticVoice?.getVolume?.()) {
  const normalized = Math.min(1, Math.max(0, Number.isFinite(Number(value)) ? Number(value) : 1));
  const percent = Math.round(normalized * 100);
  if (els.cabinetVoiceVolume) {
    els.cabinetVoiceVolume.value = String(percent);
    els.cabinetVoiceVolume.style.setProperty("--volume-fill", `${percent}%`);
    els.cabinetVoiceVolume.setAttribute("aria-valuetext", `${percent}%`);
  }
  if (els.cabinetVoiceVolumeValue) els.cabinetVoiceVolumeValue.textContent = `${percent}%`;
}

function bindCabinetVoiceVolumeControl() {
  const input = els.cabinetVoiceVolume;
  if (!input) return;
  syncCabinetVoiceVolumeControl();
  input.addEventListener("input", () => {
    const value = Math.min(1, Math.max(0, Number(input.value) / 100));
    const applied = window.K1StaticVoice?.setVolume?.(value);
    syncCabinetVoiceVolumeControl(applied ?? value);
  });
}

function bindFeatureUpdateControls() {
  bindCabinetVoiceVolumeControl();
  document.querySelector("#resetAllMemberProgress")?.addEventListener("click", openCompetitionResetDialog);
  document.querySelector("#cancelForm")?.addEventListener("click", (event) => {
    event.stopImmediatePropagation();
    requestMemberFormCancel();
  }, true);
  document.querySelector("#memberName")?.addEventListener("input", () => {
    if (draftFaceEnrollment) {
      const name = String(document.querySelector("#memberName")?.value || "").trim();
      draftFaceEnrollment.stale = draftFaceEnrollment.name !== name;
    }
    updateMemberFormFaceStatus();
  });
}

// Preserve the restored non-blocking load workflow before the optional
// Wang Grandpa simulation layer wraps its own take-medicine behavior.
window.__loadGuideRollbackBaseline = {
  beginLoadGuide,
  completeCurrentLoadStep,
  ensureCurrentLoadCabinetOpen,
  recognizeCurrentLoadMedicineBox,
  restoreSavedFlowState,
  scheduleLoadDoorMonitoring,
  updateLoadGuide,
};

bindEvents();
bindFeatureUpdateControls();
bindCabinetPersistenceGuards();
refreshSpeechVoices();
document.addEventListener("pointerdown", warmReminderAudio, { once: true });
document.addEventListener("keydown", warmReminderAudio, { once: true });
members = normalizeMembers(members).map(stripLegacyPin);
const startupPage = new URLSearchParams(location.search).get("page");
if (startupPage && pages[startupPage]) setPage(startupPage);
restoreCabinetState();
const twoMemberRosterMigrated = migrateToTwoMemberRoster();
const demoPrescriptionMigrated = migrateBuiltInDemoPrescriptions();
saveMembers();
activePageId = "home";
document.querySelector(".device")?.setAttribute("data-page", "home");
renderAll();
if (twoMemberRosterMigrated) showToast("成员资料已更新为王叔叔和何爷爷");
if (demoPrescriptionMigrated) showToast("已更新王叔叔、何爷爷的最新处方和药柜状态");
updateClock();
startCpuMetricsPolling();
checkDueReminder();
setInterval(() => {
  updateClock();
  checkDueReminder();
}, 5000);
