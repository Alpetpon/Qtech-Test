import type { components } from "../api/schema";
import "./StatusBadge.css";

export type SupplierStatus = components["schemas"]["ReportSupplier"]["status"];

const LABELS: Record<SupplierStatus, string> = {
  queued: "в очереди",
  sent: "отправлено",
  unknown: "неизвестно, ушло ли",
  silent: "молчит",
  replied: "ответил",
  offer: "предложение",
  refusal: "отказ",
  other: "другое",
  excluded: "исключён",
};

export function StatusBadge({ status }: { status: SupplierStatus }) {
  return <span className={`status-badge status-badge-${status}`}>{LABELS[status]}</span>;
}
