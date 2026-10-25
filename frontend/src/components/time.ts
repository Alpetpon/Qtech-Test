// Время сервиса — UTC: время, указанное в задании, и даты в данных тоже в UTC
const DATE_TIME = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
});

export function dateTime(value: string): string {
  return `${DATE_TIME.format(new Date(value))} UTC`;
}
