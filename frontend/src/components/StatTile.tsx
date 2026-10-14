export function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-xs text-gray-500 dark:text-gray-400">{label}</span>
      <span className="tabular-nums text-base font-medium text-gray-900 dark:text-gray-100">
        {value}
      </span>
    </div>
  );
}
