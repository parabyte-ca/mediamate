import { Star } from "lucide-react";

/** 1-5 stars; picking the current value clears it. */
export default function Stars({ value, onChange, label, big }: { value?: number | null; onChange: (n: number) => void; label: string; big?: boolean }) {
  const v = value ?? 0;
  return (
    <span className="inline-flex items-center" role="group" aria-label={`Rating for ${label}`}>
      {[1, 2, 3, 4, 5].map((n) => (
        <button key={n} type="button" onClick={() => onChange(v === n ? 0 : n)} aria-pressed={v >= n}
                aria-label={v === n ? `Clear rating (${n} star${n > 1 ? "s" : ""})` : `${n} star${n > 1 ? "s" : ""}`}
                title={v === n ? "Clear rating" : `${n} star${n > 1 ? "s" : ""}`}
                className={`rounded ${big ? "p-1" : "p-1 sm:p-0.5"} hover:bg-amber-50 dark:hover:bg-amber-900/20`}>
          <Star className={`${big ? "h-6 w-6" : "h-3.5 w-3.5"} ${v >= n ? "fill-amber-400 text-amber-400" : "text-gray-300 dark:text-gray-600"}`} />
        </button>
      ))}
    </span>
  );
}

