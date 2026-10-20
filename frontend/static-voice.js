(() => {
  "use strict";

  const BASE_URL = new URL("assets/audio/tts-modular/", document.baseURI);
  const FOLDERS = Object.freeze({
    frame: "01_框架",
    slot: "02_用药时段",
    name: "03_姓名模块",
    hour: "04_小时模块",
    minute: "05_分钟模块",
    drawer: "06_柜号模块",
    dose: "07_剂量数字模块",
    count: "08_多拿少拿数量模块",
    unit: "09_剂量单位模块",
    medicine: "10_药名模块",
  });
  const SLOT_ALIASES = Object.freeze({
    午饭后: "午餐后",
    晚饭后: "晚餐后",
    睡前: "睡觉前",
  });
  const VOLUME_STORAGE_KEY = "smart-medicine-ui-v2-cabinet-voice-volume";
  const DEFAULT_VOLUME = 1;

  let catalog = null;
  let activeAudio = null;
  let activeResolve = null;
  let generation = 0;
  let broadcastVolume = readStoredVolume();

  function clampVolume(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return DEFAULT_VOLUME;
    return Math.min(1, Math.max(0, parsed));
  }

  function readStoredVolume() {
    try {
      const stored = localStorage.getItem(VOLUME_STORAGE_KEY);
      return stored === null ? DEFAULT_VOLUME : clampVolume(stored);
    } catch {
      return DEFAULT_VOLUME;
    }
  }

  function setVolume(value) {
    broadcastVolume = clampVolume(value);
    try {
      localStorage.setItem(VOLUME_STORAGE_KEY, String(broadcastVolume));
    } catch {
      // Continue playback at the selected level when browser storage is unavailable.
    }
    if (activeAudio) activeAudio.volume = broadcastVolume;
    return broadcastVolume;
  }

  function getVolume() {
    return broadcastVolume;
  }

  const normalize = (value) =>
    String(value || "")
      .replace(/\s+/g, "")
      .replace(/，/g, "，")
      .replace(/[！!]/g, "。")
      .replace(/[“”]/g, "")
      .replace(/[（]/g, "(")
      .replace(/[）]/g, ")")
      .replace(/。+$/g, "");

  const loadCatalog = fetch(new URL("voice_cache_index.json", BASE_URL), { cache: "no-store" })
    .then((response) => {
      if (!response.ok) throw new Error(`voice_manifest_${response.status}`);
      return response.json();
    })
    .then((manifest) => {
      const byFolderText = new Map();
      const byId = new Map();
      const byExactText = new Map();
      for (const item of manifest.items || []) {
        const record = { ...item, url: new URL(encodeURI(String(item.wav || "").replace(/\\/g, "/")), BASE_URL).href };
        byFolderText.set(`${record.folder}\u0000${record.text}`, record);
        byId.set(record.id, record);
        const key = normalize(record.text);
        if (key) byExactText.set(key, record);
      }
      catalog = { byFolderText, byId, byExactText };
      return catalog;
    });

  function item(folder, text) {
    return catalog?.byFolderText.get(`${folder}\u0000${String(text)}`) || null;
  }

  function id(value) {
    return catalog?.byId.get(value) || null;
  }

  function fixed(value) {
    return catalog?.byExactText.get(normalize(value)) || null;
  }

  function integer(folder, value, min, max) {
    const parsed = Number(value);
    if (!Number.isInteger(parsed) || parsed < min || parsed > max) return null;
    return item(folder, String(parsed));
  }

  function medicineParts(value) {
    const source = String(value || "").replace(/[。！!]+$/g, "").trim();
    if (!source) return null;
    const candidates = [...catalog.byFolderText.values()]
      .filter((record) => record.folder === FOLDERS.medicine)
      .map((record) => record.text)
      .sort((a, b) => b.length - a.length);
    const units = [...catalog.byFolderText.values()]
      .filter((record) => record.folder === FOLDERS.unit)
      .map((record) => record.text)
      .sort((a, b) => b.length - a.length);
    const groups = source.split(/[，,、；;]/).map((part) => part.trim()).filter(Boolean);
    if (!groups.length) return null;
    const result = [];
    for (const group of groups) {
      const name = candidates.find((candidate) => group.startsWith(candidate));
      if (!name) return null;
      const suffix = group.slice(name.length);
      const unit = units.find((candidate) => suffix.endsWith(candidate));
      if (!unit) return null;
      const amount = suffix.slice(0, -unit.length);
      const dose = integer(FOLDERS.dose, amount, 1, 10);
      const medicine = item(FOLDERS.medicine, name);
      const unitItem = item(FOLDERS.unit, unit);
      if (!dose || !medicine || !unitItem) return null;
      result.push([medicine, dose, unitItem]);
    }
    return result;
  }

  function withSeparators(groups) {
    const separator = id("服药提醒_多药分隔_逗号");
    if (!separator) return null;
    const parts = [];
    groups.forEach((group, index) => {
      if (index) parts.push(separator);
      parts.push(...group);
    });
    return parts;
  }

  function alarmParts(message) {
    const match = String(message).match(/^(.+?)，该服药了。(?:([0-9]{1,2}):([0-9]{1,2})|([0-9]{1,2})(?:点|时)([0-9]{1,2})分)(.+?)，请来到药盒前。本次药物：(.+?)[。！!]*$/);
    if (!match) return null;
    const [, name, colonHour, colonMinute, spokenHour, spokenMinute, slot, medicineList] = match;
    const hour = colonHour ?? spokenHour;
    const minute = colonMinute ?? spokenMinute;
    const medicines = medicineParts(medicineList);
    const parts = [
      item(FOLDERS.name, name),
      id("服药提醒_姓名后_该服药了"),
      integer(FOLDERS.hour, hour, 0, 23),
      id("服药提醒_小时后_时"),
      integer(FOLDERS.minute, minute, 0, 59),
      id("服药提醒_分钟后_分"),
      item(FOLDERS.slot, SLOT_ALIASES[slot] || slot),
      id("服药提醒_时段后_来到药盒前_本次药物"),
    ];
    const renderedList = medicines && withSeparators(medicines);
    return !parts.some((part) => !part) && renderedList ? [...parts, ...renderedList] : null;
  }

  function confirmationParts(message) {
    const match = String(message).match(/^请您确认本次药物名称及其数量：(.+?)[。！!]*确认无误后点击继续。?$/);
    if (!match) return null;
    const medicines = medicineParts(match[1]);
    const parts = [id("确认药物_开头"), ...(medicines ? withSeparators(medicines) || [] : []), id("确认药物_结尾")];
    return medicines && !parts.some((part) => !part) ? parts : null;
  }

  function takeParts(message) {
    const match = String(message).match(/^请从([0-9]{1,2})号药柜取出(.+?)[，,]并放到识别区核验。?$/);
    if (!match) return null;
    const medicines = medicineParts(match[2]);
    if (!medicines || medicines.length !== 1) return null;
    const parts = [id("取药_请从"), integer(FOLDERS.drawer, match[1], 1, 12), id("取药_号药柜取出"), ...medicines[0], id("取药_并放到识别区核验")];
    return parts.some((part) => !part) ? null : parts;
  }

  function countParts(message, kind) {
    const match = String(message).match(kind === "short" ? /您少拿了([0-9]+)个药品/ : /您多拿了([0-9]+)个药品/);
    if (!match) return null;
    const amount = integer(FOLDERS.count, match[1], 1, 10);
    const prefix = id(kind === "short" ? "少拿_开头" : "多拿_开头");
    const middle = id(kind === "short" ? "少拿_中间" : "多拿_中间");
    const suffix = id(kind === "short" ? "少拿_结尾" : "多拿_结尾");
    const parts = [prefix, amount, middle, amount, suffix];
    return parts.some((part) => !part) ? null : parts;
  }

  function loadParts(message) {
    let match = String(message).match(/^请将(.+?)药盒放在识别区，并点击开始识别按钮。?$/);
    if (match) {
      const medicine = item(FOLDERS.medicine, match[1]);
      const parts = [id("放药识别_请将"), medicine, id("放药识别_药盒放在识别区")];
      return parts.some((part) => !part) ? null : parts;
    }
    match = String(message).match(/^药盒名称识别正确，([0-9]{1,2})号药柜即将弹出。?(请将药物放入该药柜并手动关闭药柜。?)?$/);
    if (match) {
      const parts = [id("药盒名称识别正确_开头"), integer(FOLDERS.drawer, match[1], 1, 12), id("药盒名称识别正确_结尾")];
      if (match[2]) parts.push(id("放药识别_请将药物放入该药柜并手动关闭药柜"));
      return parts.some((part) => !part) ? null : parts;
    }
    match = String(message).match(/^请将[“"]?(.+?)药盒[”"]?放入([0-9]{1,2})号药柜。?$/);
    if (match) {
      const parts = [id("放入药柜_请将药盒放入前"), item(FOLDERS.medicine, match[1]), id("放入药柜_药盒放入中"), integer(FOLDERS.drawer, match[2], 1, 12), id("放入药柜_号药柜")];
      return parts.some((part) => !part) ? null : parts;
    }
    match = String(message).match(/^药盒名称识别错误，请放置(.+?)药盒到识别区，并重新点击[“"]?开始识别[”"]?按钮。?$/);
    if (match) {
      const parts = [id("药盒名称识别错误_开头"), item(FOLDERS.medicine, match[1]), id("药盒名称识别错误_结尾")];
      return parts.some((part) => !part) ? null : parts;
    }
    match = String(message).match(/^([0-9]{1,2})号药柜已关闭。?$/);
    if (match) {
      const parts = [integer(FOLDERS.drawer, match[1], 1, 12), id("柜号_号药柜已关闭")];
      return parts.some((part) => !part) ? null : parts;
    }
    return null;
  }

  function render(message) {
    if (!catalog) return null;
    const text = String(message || "").trim();
    if (!text) return null;
    const direct = fixed(text);
    if (direct) return [direct];
    return alarmParts(text) || confirmationParts(text) || takeParts(text) || countParts(text, "short") || countParts(text, "extra") || loadParts(text);
  }

  function stop() {
    generation += 1;
    const audio = activeAudio;
    const resolve = activeResolve;
    activeAudio = null;
    activeResolve = null;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.currentTime = 0;
    }
    if (resolve) resolve(false);
  }

  function playClip(record, token) {
    return new Promise((resolve) => {
      if (token !== generation) return resolve(false);
      const audio = new Audio(record.url);
      activeAudio = audio;
      activeResolve = resolve;
      const finish = (played) => {
        if (activeAudio === audio) {
          activeAudio = null;
          activeResolve = null;
        }
        audio.onended = null;
        audio.onerror = null;
        resolve(played && token === generation);
      };
      audio.preload = "auto";
      audio.volume = broadcastVolume;
      audio.onended = () => finish(true);
      audio.onerror = () => finish(false);
      audio.play().catch(() => finish(false));
    });
  }

  async function playText(message) {
    stop();
    const token = generation;
    await loadCatalog.catch(() => null);
    if (token !== generation) return false;
    const sequence = render(message);
    if (!sequence?.length) {
      console.warn("[K1StaticVoice] unsupported text", String(message || ""));
      return false;
    }
    for (const record of sequence) {
      if (!(await playClip(record, token))) {
        console.warn("[K1StaticVoice] clip playback failed", record?.id || record?.wav || "unknown");
        return false;
      }
    }
    return token === generation;
  }

  async function supportsText(message) {
    await loadCatalog.catch(() => null);
    return Boolean(render(message)?.length);
  }

  window.K1StaticVoice = Object.freeze({ playText, supportsText, stop, getVolume, setVolume, ready: loadCatalog });
})();
