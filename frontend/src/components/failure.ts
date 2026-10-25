export const OFFLINE = "Нет связи с сервером. Проверьте, что сервис запущен.";

// У отказа API тело {"detail": [строки для человека]}; у 500 или 502 от прокси его может не быть
export function failure(status: number, error: unknown): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  return Array.isArray(detail) && detail.length ? detail.join("; ") : `Сервер ответил ошибкой ${status}.`;
}
