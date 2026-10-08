import { ChatCircleDotsIcon, RobotIcon } from "@phosphor-icons/react";
import { productMode, switchProductMode } from "./ModeNavigationStore.js";
import "./product-mode-switcher.css";

const SHOW_PRODUCT_MODE_SWITCHER = false;

export function ProductModeSwitcher() {
  const mode = productMode();
  // Keep both modes available in code while this release exposes Copilot only.
  if (!SHOW_PRODUCT_MODE_SWITCHER) return null;

  return (
    <div className="product-mode-switcher" role="group" aria-label="工作模式">
      <button
        type="button"
        className={mode === "copilot" ? "active" : ""}
        aria-pressed={mode === "copilot"}
        onClick={() => switchProductMode("copilot")}
      >
        <ChatCircleDotsIcon />
        Copilot
      </button>
      <button
        type="button"
        className={mode === "employee" ? "active" : ""}
        aria-pressed={mode === "employee"}
        onClick={() => switchProductMode("employee")}
      >
        <RobotIcon />
        AI 员工
      </button>
    </div>
  );
}
