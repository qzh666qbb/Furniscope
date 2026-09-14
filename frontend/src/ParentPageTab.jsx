import { ArrowLeft, CaretRight } from "@phosphor-icons/react";

export function ParentPageTab({ label, current, to, onBack, className = "" }) {
  const goBack = () => {
    if (onBack) onBack();
    else location.hash = to;
  };

  return (
    <nav className={`parent-page-tab ${className}`.trim()} aria-label="返回上一级页面">
      <button type="button" onClick={goBack} title={`返回${label}`}>
        <ArrowLeft />
        <span>返回</span>
        <strong>{label}</strong>
      </button>
      {current && (
        <>
          <CaretRight className="parent-page-divider" />
          <span className="parent-page-current">{current}</span>
        </>
      )}
    </nav>
  );
}
