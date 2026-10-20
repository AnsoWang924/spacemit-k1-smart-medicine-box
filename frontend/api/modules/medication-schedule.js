export function createMedicationScheduleApi(call) {
  return {
    generate: (input, requestOptions = {}) => call(
      "v1",
      "/medication-schedule/generate",
      { timeoutMs: 8_000, ...requestOptions, method: "POST", body: input },
    ),
  };
}
