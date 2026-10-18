import type { KeyboardEvent } from "react";

// Pestanas accesibles (patron WAI-ARIA "tabs"): role tablist/tab, aria-selected, aria-controls y
// navegacion con las flechas, Inicio y Fin. El panel lo dibuja quien las usa con `tabPanelId`.
export interface TabDef {
  id: string;
  label: string;
}

export const tabId = (id: string) => `tab-${id}`;
export const tabPanelId = (id: string) => `panel-${id}`;

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: TabDef[];
  active: string;
  onChange: (id: string) => void;
}) {
  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault();
    onChange(tabs[next].id);
    document.getElementById(tabId(tabs[next].id))?.focus();
  }

  return (
    <div
      role="tablist"
      aria-label="Secciones del dashboard"
      className="flex flex-wrap gap-1 border-b border-gray-200 dark:border-gray-700"
    >
      {tabs.map((tab, index) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            id={tabId(tab.id)}
            role="tab"
            type="button"
            aria-selected={selected}
            aria-controls={tabPanelId(tab.id)}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.id)}
            onKeyDown={(event) => handleKeyDown(event, index)}
            className={
              "-mb-px rounded-t border border-b-0 px-3 py-1.5 text-sm " +
              (selected
                ? "border-gray-200 bg-white font-semibold text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
                : "border-transparent text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200")
            }
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
