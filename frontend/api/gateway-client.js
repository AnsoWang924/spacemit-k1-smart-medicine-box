import { requestGateway, resolveGatewayOrigin } from "./core/http.js";
import { createAsrApi } from "./modules/asr.js";
import { createAssistantApi } from "./modules/assistant.js";
import { createCabinetApi } from "./modules/cabinet.js";
import { createDiagnosticsApi } from "./modules/diagnostics.js";
import { createFaceApi } from "./modules/face.js";
import { createHealthApi } from "./modules/health.js";
import { createMedicineBoxApi } from "./modules/medicine-box.js";
import { createMedicationScheduleApi } from "./modules/medication-schedule.js";
import { createPillCountApi } from "./modules/pill-count.js";
import { createPrescriptionOcrApi } from "./modules/prescription-ocr.js";
import { createQaApi } from "./modules/qa.js";
import { createSystemApi } from "./modules/system.js";
import { createTtsApi } from "./modules/tts.js";

export function createGatewayClient({ origin = resolveGatewayOrigin() } = {}) {
  const call = (version, path, options = {}) => requestGateway({ origin, version, path, ...options });

  return Object.freeze({
    asr: createAsrApi(call),
    assistant: createAssistantApi(call),
    cabinet: createCabinetApi(call),
    diagnostics: createDiagnosticsApi(call),
    face: createFaceApi(call),
    health: createHealthApi(call),
    medicineBox: createMedicineBoxApi(call),
    medicationSchedule: createMedicationScheduleApi(call),
    pillCount: createPillCountApi(call),
    prescriptionOcr: createPrescriptionOcrApi(call),
    qa: createQaApi(call),
    system: createSystemApi(call),
    tts: createTtsApi(call),
  });
}

export const gateway = createGatewayClient();
