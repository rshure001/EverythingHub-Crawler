// Search must not depend on PostgreSQL availability. The verified, SHA-256
// checked public crawler index is the default source of truth. Database search
// is an explicit opt-in for controlled rollout or diagnostics only.
export function shouldUseDatabaseSearch(env = {}) {
  const value = typeof env.get === "function"
    ? env.get("EH_DATABASE_SEARCH_ENABLED")
    : env.EH_DATABASE_SEARCH_ENABLED;
  return String(value || "").trim().toLowerCase() === "true";
}
