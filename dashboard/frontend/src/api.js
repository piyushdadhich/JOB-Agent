// Tiny fetch wrapper — the Vite dev server proxies /api to FastAPI
// on :8000, and in prod FastAPI serves both the static bundle and
// the API on :8000 itself. Either way, relative URLs work.

async function request(path, { method = "GET", body } = {}) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail;
    try {
      detail = (await res.json()).detail;
    } catch {
      detail = await res.text();
    }
    throw new Error(`${res.status} ${path}: ${detail || res.statusText}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  health: () => request("/api/health"),

  // --- Onboarding wizard (Spec 1) -------------------------------
  setupStatus: () => request("/api/setup/status"),
  setupHardware: () => request("/api/setup/hardware"),
  setupSaveStep: (stepN, body) =>
    request(`/api/setup/step/${stepN}`, { method: "POST", body: body || {} }),
  setupConfig: () => request("/api/setup/config"),
  setupComplete: () =>
    request("/api/setup/complete", { method: "POST", body: {} }),
  setupTestApiKey: (provider, apiKey) =>
    request("/api/setup/test-api-key", {
      method: "POST",
      body: { provider, api_key: apiKey },
    }),
  setupCheckOllama: () =>
    request("/api/setup/check-ollama", { method: "POST", body: {} }),
  setupSaveProfile: (body) =>
    request("/api/setup/profile", { method: "POST", body }),
  setupInventoryPrompt: () => request("/api/setup/inventory-prompt"),
  setupParseResume: async (file) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/setup/parse-resume", {
      method: "POST", body: form,
    });
    if (!res.ok) {
      let detail;
      try { detail = (await res.json()).detail; }
      catch { detail = await res.text(); }
      throw new Error(`${res.status}: ${detail || res.statusText}`);
    }
    return res.json();
  },
  setupParseInventoryResponse: (markdown) =>
    request("/api/setup/parse-inventory-response", {
      method: "POST", body: { markdown },
    }),
  setupSaveInventory: (markdown) =>
    request("/api/setup/save-inventory", {
      method: "POST", body: { markdown },
    }),
  setupInventoryQuality: () =>
    request("/api/setup/inventory-quality"),
  setupSaveTargetMarket: (body) =>
    request("/api/setup/target-market", { method: "POST", body }),
  setupSaveSources: (body) =>
    request("/api/setup/sources", { method: "POST", body }),
  setupGmailUpload: async (file) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/setup/gmail/upload", {
      method: "POST", body: form,
    });
    if (!res.ok) {
      let detail;
      try { detail = (await res.json()).detail; }
      catch { detail = await res.text(); }
      throw new Error(`${res.status}: ${detail || res.statusText}`);
    }
    return res.json();
  },
  setupGmailFinalize: () =>
    request("/api/setup/gmail/finalize", { method: "POST", body: {} }),
  setupInstallService: (scheduleTime) =>
    request("/api/setup/install-service", {
      method: "POST", body: { schedule_time: scheduleTime },
    }),
  setupFirstRunStart: () =>
    request("/api/setup/first-run", { method: "POST", body: {} }),
  setupFirstRunProgress: () =>
    request("/api/setup/first-run/progress"),

  // --- Calibration (Spec 6) -----------------------------------
  calibrateAuto: () =>
    request("/api/calibrate/auto", { method: "POST", body: {} }),
  calibrateSample: () => request("/api/calibrate/sample"),
  calibrateConfirm: (body) =>
    request("/api/calibrate/confirm", { method: "POST", body }),
  calibrateApply: (thresholds) =>
    request("/api/calibrate/apply", {
      method: "POST", body: { thresholds },
    }),

  // --- Daily digest (Spec 13 TASK 2) ---------------------------
  digestToday: () => request("/api/digest/today"),

  // --- Score explainer (Spec 13 TASK 3) ------------------------
  explainScore: (postingId) =>
    request(`/api/shortlist/${postingId}/explain`),

  // --- Skill gap hint (Spec 13 TASK 4) -------------------------
  gapsTop: (limit = 5) => request(`/api/gaps?limit=${limit}`),

  // --- CSV export (Spec 13 TASK 6) -----------------------------
  shortlistExportUrl: (includeAllTiers = false) =>
    `/api/shortlist/export${includeAllTiers ? "?include_all_tiers=true" : ""}`,

  // --- Resume cost estimate (Spec 8 TASK 4) --------------------
  resumeCostEstimate: (appsPerWeek = 5) =>
    request(`/api/resume/cost-estimate?apps_per_week=${appsPerWeek}`),

  // --- Settings page -------------------------------------------
  settingsGetProfile:    () => request("/api/settings/profile"),
  settingsSaveProfile:   (body) =>
    request("/api/settings/profile", { method: "POST", body }),
  settingsGetApplicant:  () => request("/api/settings/applicant"),
  settingsSaveApplicant: (body) =>
    request("/api/settings/applicant", { method: "POST", body }),
  settingsGetLLM:        () => request("/api/settings/llm"),
  settingsSaveLLM:       (body) =>
    request("/api/settings/llm", { method: "POST", body }),
  settingsGetSources:    () => request("/api/settings/sources"),
  settingsSaveSources:   (body) =>
    request("/api/settings/sources", { method: "POST", body }),
  settingsListExclusions: () => request("/api/settings/exclusions"),
  settingsAddExclusion:   (body) =>
    request("/api/settings/exclusions", { method: "POST", body }),
  settingsDeleteExclusion: (name) =>
    request(`/api/settings/exclusions/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  settingsGetSchedule:   () => request("/api/settings/schedule"),
  settingsSaveSchedule:  (schedule_time) =>
    request("/api/settings/schedule", {
      method: "POST", body: { schedule_time },
    }),
  settingsGetGmail:      () => request("/api/settings/gmail"),
  settingsResetWizard:   () =>
    request("/api/settings/reset-wizard", { method: "POST", body: {} }),
  settingsExportDataUrl: () => "/api/settings/export-data",

  // Shared param builder so listShortlist + shortlistCounts (FIX-4)
  // produce the same query against the backend.
  _shortlistParams: (filters = {}) => {
    const params = new URLSearchParams();
    for (const key of ["function", "industry", "city", "ai_subtype"]) {
      const v = filters[key];
      if (Array.isArray(v) && v.length > 0) {
        params.set(key, v.join(","));
      } else if (typeof v === "string" && v) {
        // back-compat: legacy single-slug like "gta"
        params.set(key, v);
      }
    }
    if (filters.ai_role) params.set("ai_role", filters.ai_role);
    if (filters.mode === "hide") params.set("mode", "hide");
    if (filters.date_range && filters.date_range !== "all") {
      params.set("date_range", filters.date_range);
    }
    return params;
  },
  listShortlist: (filters = {}) => {
    const params = api._shortlistParams(filters);
    if (filters.tier && filters.tier !== "all") {
      params.set("tier", filters.tier);
    }
    if (filters.page) params.set("page", String(filters.page));
    if (filters.per_page) {
      params.set("per_page", String(filters.per_page));
    }
    const qs = params.toString();
    return request(`/api/shortlist${qs ? `?${qs}` : ""}`);
  },
  shortlistCounts: (filters = {}) => {
    const params = api._shortlistParams(filters);
    const qs = params.toString();
    return request(`/api/shortlist/counts${qs ? `?${qs}` : ""}`);
  },
  listShortlistFilterOptions: () =>
    request("/api/shortlist/filter-options"),
  selectPosting: (id) => request(`/api/shortlist/${id}/select`, { method: "POST" }),
  unselectPosting: (id) => request(`/api/shortlist/${id}/unselect`, { method: "POST" }),
  skipPosting: (id) => request(`/api/shortlist/${id}/skip`, { method: "POST" }),
  unskipPosting: (id) => request(`/api/shortlist/${id}/unskip`, { method: "POST" }),
  listApplications: ({ stage, flaggedOnly } = {}) => {
    const params = new URLSearchParams();
    if (stage) params.set("stage", stage);
    if (flaggedOnly) params.set("flagged_only", "true");
    const qs = params.toString();
    return request(`/api/applications${qs ? `?${qs}` : ""}`);
  },
  getApplication: (id) => request(`/api/applications/${id}`),
  updateStatus: (id, status, reason) =>
    request(`/api/applications/${id}/status`, {
      method: "PUT",
      body: { status, reason },
    }),
  getResumePrompt: (postingId) =>
    request(`/api/prompts/${postingId}/resume`),
  getCoverLetterPrompt: (postingId) =>
    request(`/api/prompts/${postingId}/cover-letter`),
  saveResumeText: (postingId, content) =>
    request(`/api/prompts/${postingId}/resume`, {
      method: "POST",
      body: { content },
    }),
  saveCoverLetterText: (postingId, content) =>
    request(`/api/prompts/${postingId}/cover-letter`, {
      method: "POST",
      body: { content },
    }),
  getBatchPrompts: () => request("/api/prompts/batch"),
  saveBatchResponses: ({ response, ids }) =>
    request("/api/prompts/batch", {
      method: "POST",
      body: { response, ids },
    }),
  applyStart: (postingId, { dryRun = false, smart = false } = {}) => {
    const params = new URLSearchParams();
    if (dryRun) params.set("dry_run", "true");
    if (smart) params.set("smart", "true");
    const qs = params.toString();
    return request(
      `/api/apply/${postingId}/start${qs ? `?${qs}` : ""}`,
      { method: "POST" },
    );
  },
  applySubmit: (postingId) =>
    request(`/api/apply/${postingId}/submit`, { method: "POST" }),
  applyAbort: (postingId) =>
    request(`/api/apply/${postingId}/abort`, { method: "POST" }),
  applyApprovePlan: (postingId) =>
    request(`/api/apply/${postingId}/approve-plan`, { method: "POST" }),
  applySkipPlan: (postingId) =>
    request(`/api/apply/${postingId}/skip-plan`, { method: "POST" }),
  applyMarkApplied: (postingId, notes) =>
    request(`/api/apply/${postingId}/mark-applied`, {
      method: "POST",
      body: { notes: notes || null },
    }),
  applyStatus: () => request("/api/apply/status"),
  applyConfirmAnswer: (question, answer) =>
    request("/api/apply/answer", {
      method: "POST",
      body: { question, answer },
    }),
  batchPromptsPreview: (opportunityIds) =>
    request("/api/prompts/batch/preview", {
      method: "POST",
      body: { opportunity_ids: opportunityIds },
    }),
  batchApplyStart: (opportunityIds) =>
    request("/api/apply/batch", {
      method: "POST",
      body: { opportunity_ids: opportunityIds },
    }),
  batchApplyNext: (batchId) =>
    request(`/api/apply/batch/${encodeURIComponent(batchId)}/next`),
  batchApplyAdvance: (batchId, action) =>
    request(`/api/apply/batch/${encodeURIComponent(batchId)}/advance`, {
      method: "POST",
      body: { action },
    }),
  flagPosting: (postingId, { createRule = false, rule = null } = {}) =>
    request(`/api/shortlist/${postingId}/flag`, {
      method: "POST",
      body: { create_rule: createRule, rule },
    }),
  listFlagRules: () => request("/api/flags/rules"),
  disableFlagRule: (ruleId) =>
    request(`/api/flags/rules/${ruleId}/disable`, { method: "POST" }),
  enableFlagRule: (ruleId) =>
    request(`/api/flags/rules/${ruleId}/enable`, { method: "POST" }),
  getPipeline: (range = "today") =>
    request(`/api/pipeline?range=${encodeURIComponent(range)}`),
  statsToday: () => request("/api/stats/today"),
  statsSources: () => request("/api/stats/sources"),
  statsEvaluator: () => request("/api/stats/evaluator"),
  statsApplications: () => request("/api/stats/applications"),
  expansionSummary: () => request("/api/expansion/summary"),
  expansionTitleClusters: () => request("/api/expansion/title-clusters"),
  expansionDeepTargets: () => request("/api/expansion/deep-targets"),
  expansionSkillGaps: () => request("/api/expansion/skill-gaps"),
  expansionConfirmCluster: (id) =>
    request(
      `/api/expansion/title-clusters/${encodeURIComponent(id)}/confirm`,
      { method: "POST" },
    ),
  expansionRejectCluster: (id) =>
    request(
      `/api/expansion/title-clusters/${encodeURIComponent(id)}/reject`,
      { method: "POST" },
    ),
  expansionWatchTarget: (companyId) =>
    request(`/api/expansion/deep-targets/${companyId}/watch`, {
      method: "POST",
    }),
  expansionUnwatchTarget: (companyId) =>
    request(`/api/expansion/deep-targets/${companyId}/unwatch`, {
      method: "POST",
    }),
  expansionGapAction: (skillId, action) =>
    request(
      `/api/expansion/skill-gaps/${encodeURIComponent(skillId)}/action`,
      { method: "POST", body: { action } },
    ),
  expansionRun: () => request("/api/expansion/run", { method: "POST" }),
  emailMonitorStatus: () => request("/api/email-monitor/status"),
  emailMonitorRecent: () => request("/api/email-monitor/recent"),
  emailMonitorCheckNow: () =>
    request("/api/email-monitor/check-now", { method: "POST" }),
  emailMonitorSetupStatus: () =>
    request("/api/email-monitor/setup-status"),
  emailMonitorSetupGuide: () =>
    request("/api/email-monitor/setup-guide"),
  pipelineBoard: () => request("/api/pipeline/board"),
  pipelineStats: () => request("/api/pipeline/stats"),
  pipelineUpdateStatus: (id, status) =>
    request(`/api/pipeline/${id}/status`, {
      method: "PUT",
      body: { status },
    }),
  pipelineUpdateNotes: (id, notes) =>
    request(`/api/pipeline/${id}/notes`, {
      method: "PUT",
      body: { notes },
    }),

  // --- Spec FIX-2 / FIX-3 / FIX-5 / FIX-6 additions ---
  // Apply flow extras for the Smart-filler review path.
  applyApprovePlan: (postingId) =>
    request(`/api/apply/${postingId}/approve-plan`, { method: "POST" }),
  applySkipPlan: (postingId) =>
    request(`/api/apply/${postingId}/skip-plan`, { method: "POST" }),
  applyConfirmAnswer: (question, answer) =>
    request("/api/apply/answer", {
      method: "POST",
      body: { question, answer },
    }),

  // Unified Applications board (FIX-2) — alternative view to the
  // Prompts/Apply/Pipeline trio. Both UIs coexist.
  applicationsBoard: () => request("/api/applications/board"),
  applicationsUpdateNotesByOpp: (oppId, notes) =>
    request(`/api/applications/by-opp/${oppId}/notes`, {
      method: "POST",
      body: { notes },
    }),
  applicationsUpdateStatusByOpp: (oppId, status) =>
    request(`/api/applications/by-opp/${oppId}/status`, {
      method: "POST",
      body: { status },
    }),
  applicationsAddJob: (payload) =>
    request("/api/applications/add", {
      method: "POST",
      body: payload,
    }),

  // Cloud generation (FIX-3): on-demand resume / cover-letter docs
  // for one posting or the whole "Selected" pool.
  applicationsGenerateDoc: (oppId, type) =>
    request(`/api/applications/by-opp/${oppId}/generate`, {
      method: "POST",
      body: { type },
    }),
  applicationsGenerateAll: (types, force = false) =>
    request("/api/applications/generate-all", {
      method: "POST",
      body: { types, force },
    }),
  applicationsGenerateAllProgress: (taskId) =>
    request(
      `/api/applications/generate-all/${encodeURIComponent(taskId)}`,
    ),
  cloudUsageToday: () => request("/api/cloud-usage/today"),

  // Activity log + rich health (FIX-6).
  activityLog: ({ category, limit = 50, offset = 0 } = {}) => {
    const p = new URLSearchParams();
    if (category && category !== "all") p.set("category", category);
    p.set("limit", String(limit));
    p.set("offset", String(offset));
    return request(`/api/activity-log?${p.toString()}`);
  },
  activityLogSummary: () => request("/api/activity-log/summary"),

  // FIX-5 Settings (applicant + api key + exclusions). These
  // PUT-style endpoints coexist with the older Spec-10 settings*
  // methods above so both Settings UIs keep working.
  settingsUpdateApplicant: (fields) =>
    request("/api/settings/applicant", { method: "PUT", body: fields }),
  settingsGetApiKey: () => request("/api/settings/api-key"),
  settingsUpdateApiKey: (key) =>
    request("/api/settings/api-key", { method: "PUT", body: { key } }),
  settingsGetExclusions: () => request("/api/settings/exclusions"),
  settingsRemoveExclusion: (company) =>
    request(`/api/settings/exclusions/${encodeURIComponent(company)}`, {
      method: "DELETE",
    }),
};
