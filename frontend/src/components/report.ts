import type { components } from "../api/schema";

type Schemas = components["schemas"];
export type Report = Schemas["Report"];
export type ReportSupplier = Schemas["ReportSupplier"];
export type Reply = Schemas["Reply"];
export type Offer = Schemas["Offer"];
export type OfferFields = Schemas["OfferFields"];
export type SupplierRef = Schemas["SupplierRef"];
export type Unbound = Schemas["Unbound"];

export const SUPPLIER_STATUSES: Record<ReportSupplier["status"], string> = {
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

export const REPLY_TYPES: Record<NonNullable<Reply["type"]>, string> = {
  offer: "предложение",
  refusal: "отказ",
  other: "другое",
};

export const OFFLINE = "нет связи с сервером. Проверьте, что сервис запущен, и повторите";

// Время сервиса — UTC: время, указанное в задании, и даты в данных тоже в UTC
const DATE_TIME = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
});
const DATE = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "UTC" });

export function dateTime(value: string): string {
  return DATE_TIME.format(new Date(value));
}

export function date(value: string): string {
  return DATE.format(new Date(value));
}

/** Причины отказа из тела ответа API: у любого отказа оно {"detail": [строки для человека]}. */
export function reasons(error: unknown): string[] {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  return Array.isArray(detail) ? detail.map(String) : [];
}

export function failure(status: number, error: unknown): string {
  const list = reasons(error);
  return list.length ? list.join("; ") : `сервер ответил ошибкой ${status}`;
}
