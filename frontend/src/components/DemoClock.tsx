import { useState } from "react";

import { api } from "../api/client";
import { failure, OFFLINE } from "./failure";
import { dateTime } from "./time";
import "./DemoClock.css";

const STEPS = [
  { by: "PT1H", label: "+1 час" },
  { by: "P1D", label: "+1 день" },
];

/** Текущее время по часам задания и перемотка вперёд: время общее для сервиса и воркера, назад его не вернуть. */
export function DemoClock({ now, onAdvanced }: { now: string; onAdvanced: (now: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  async function advance(by: string) {
    setBusy(true);
    setError(undefined);
    try {
      const { data, error, response } = await api.POST("/api/clock/advance", { body: { by } });
      if (data) {
        onAdvanced(data.now);
      } else {
        setError(failure(response.status, error));
      }
    } catch {
      setError(OFFLINE);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="demo-clock" aria-labelledby="demo-clock-title">
      <h2 id="demo-clock-title">Демо-часы</h2>
      <p className="demo-clock-now">
        Сейчас: <time dateTime={now}>{dateTime(now)}</time>
      </p>
      <div className="demo-clock-steps">
        {STEPS.map((step) => (
          <button key={step.by} type="button" disabled={busy} onClick={() => advance(step.by)}>
            {step.label}
          </button>
        ))}
      </div>
      {error && (
        <p className="demo-clock-error" role="alert">
          Не удалось перемотать часы. {error}
        </p>
      )}
    </section>
  );
}
