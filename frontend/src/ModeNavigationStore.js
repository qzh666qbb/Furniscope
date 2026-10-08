const LAST_COPILOT_ROUTE = "furniscope-last-copilot-route";

const routeName = (hash = location.hash) =>
  hash.replace(/^#\/?/, "").split("?")[0] || "workspace";

export function productMode(hash = location.hash) {
  return routeName(hash) === "employee" ? "employee" : "copilot";
}

export function rememberCopilotRoute(hash = location.hash) {
  const route = routeName(hash);
  if (
    route === "employee" ||
    ["login", "landing", "register", "forgot-password", "admin", "admin-login"].includes(route)
  ) {
    return;
  }
  localStorage.setItem(LAST_COPILOT_ROUTE, hash.replace(/^#\/?/, "") || "workspace");
}

export function switchProductMode(mode) {
  if (mode === "employee") {
    rememberCopilotRoute();
    location.hash = "employee";
    return;
  }
  location.hash = localStorage.getItem(LAST_COPILOT_ROUTE) || "workspace";
}
