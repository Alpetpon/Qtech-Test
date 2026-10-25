import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { api } from "../api/client";
import type { components } from "../api/schema";
import { DemoClock } from "../components/DemoClock";
import { failure, OFFLINE } from "../components/failure";
import { StatusBadge } from "../components/StatusBadge";
import { dateTime } from "../components/time";
import "./CampaignPage.css";

type CampaignStatus = components["schemas"]["CampaignStatus"];
type Campaign = components["schemas"]["Campaign"];
type Report = components["schemas"]["Report"];
type Snapshot = { status: CampaignStatus; report: Report; now: string };

const POLL_MS = 3000;

const CAMPAIGN_STATUSES: Record<Campaign["status"], string> = {
  sending: "идёт отправка",
  paused: "на паузе",
  sent: "отправка завершена",
};

const QUEUE: { key: keyof Campaign["queue"]; label: string }[] = [
  { key: "pending", label: "Ждёт" },
  { key: "sending", label: "Отправляется" },
  { key: "sent", label: "Отправлено" },
  { key: "unknown", label: "Неизвестно, ушло ли" },
];

const PAUSE_REASONS: Record<string, string> = {
  RATE_LIMIT: "RATE_LIMIT — мессенджер ограничил частоту отправки",
};

export function CampaignPage() {
  const { id = "" } = useParams();
  // Не число — API ответил бы ошибкой проверки, а не «рассылки нет»
  if (!/^[1-9]\d*$/.test(id)) {
    return <Missing text={`В адресе нет номера рассылки: «${id}».`} />;
  }
  // key: на другой рассылке состояние начинается заново, без данных предыдущей
  return <CampaignView key={id} id={Number(id)} />;
}

function Missing({ text }: { text: string }) {
  return (
    <div className="campaign">
      <h1>Рассылка не найдена</h1>
      <p>
        {text} <Link to="/">Новая рассылка</Link>
      </p>
    </div>
  );
}

function CampaignView({ id }: { id: number }) {
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [presets, setPresets] = useState<Record<string, string>>();
  const [missing, setMissing] = useState(false);
  const [problem, setProblem] = useState<string>();
  // Меняется после «Продолжить» и перемотки: опрос перезапускается сразу, не дожидаясь своего круга
  const [refresh, setRefresh] = useState(0);
  const [resuming, setResuming] = useState(false);
  const [resumeError, setResumeError] = useState<string>();

  useEffect(() => {
    // Без названий пресетов страница всё равно нужна: покажем идентификатор пресета
    api.GET("/api/options").then(
      ({ data }) => setPresets(Object.fromEntries((data?.presets ?? []).map((preset) => [preset.id, preset.title]))),
      () => setPresets({}),
    );
  }, []);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const path = { params: { path: { campaign_id: id } } };

    async function load() {
      try {
        const [status, report, clock] = await Promise.all([
          api.GET("/api/campaigns/{campaign_id}", path),
          api.GET("/api/campaigns/{campaign_id}/report", path),
          api.GET("/api/clock"),
        ]);
        if (!alive) return;
        if (status.response.status === 404) {
          setMissing(true);
          return;
        }
        if (status.data && report.data && clock.data) {
          setSnapshot({ status: status.data, report: report.data, now: clock.data.now });
          setProblem(undefined);
        } else {
          const failed = !status.data ? status : !report.data ? report : clock;
          setProblem(failure(failed.response.status, failed.error));
        }
      } catch {
        if (!alive) return;
        setProblem(OFFLINE);
      }
      timer = window.setTimeout(load, POLL_MS);
    }

    load();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [id, refresh]);

  async function resume() {
    setResuming(true);
    setResumeError(undefined);
    try {
      const { data, error, response } = await api.POST("/api/sender/resume");
      // Блок паузы и статус рассылки уходят вместе со следующим снимком, а не по отдельности
      if (data) {
        setRefresh((n) => n + 1);
      } else {
        setResumeError(failure(response.status, error));
      }
    } catch {
      setResumeError(OFFLINE);
    } finally {
      setResuming(false);
    }
  }

  function advanced(now: string) {
    setSnapshot((current) => current && { ...current, now });
    setRefresh((n) => n + 1);
  }

  if (missing) {
    return <Missing text={`Рассылки №${id} нет.`} />;
  }

  if (!snapshot || !presets) {
    return (
      <div className="campaign">
        <h1>Рассылка №{id}</h1>
        {problem ? (
          <p className="campaign-alert" role="alert">
            Не удалось загрузить рассылку. {problem} Пробуем ещё раз каждые {POLL_MS / 1000} с.
          </p>
        ) : (
          <p>Загружаем…</p>
        )}
      </div>
    );
  }

  const { campaign, sender } = snapshot.status;
  const { suppliers } = snapshot.report;
  const total = QUEUE.reduce((sum, { key }) => sum + (campaign.queue[key] ?? 0), 0);
  const selection = campaign.preset
    ? `пресет «${presets[campaign.preset] ?? campaign.preset}»`
    : `все поставщики со статусом ${campaign.supplier_status}`;

  return (
    <div className="campaign">
      <div className="campaign-head">
        <h1>Рассылка №{campaign.id}</h1>
        <Link to={`/campaigns/${campaign.id}/report`}>Отчёт →</Link>
      </div>
      <p>
        Выбор: {selection}. Создана {dateTime(campaign.created_at)}. Статус:{" "}
        <strong>{CAMPAIGN_STATUSES[campaign.status]}</strong>.
      </p>

      <DemoClock now={snapshot.now} onAdvanced={advanced} />

      {problem && (
        <p className="campaign-alert" role="alert">
          Не удалось обновить страницу. {problem} Показаны данные на {dateTime(snapshot.now)}, пробуем ещё раз.
        </p>
      )}

      {sender.pause && (
        <section className="campaign-pause" aria-labelledby="campaign-pause-title">
          <h2 id="campaign-pause-title">Отправка на паузе до {dateTime(sender.pause.until)}</h2>
          <p>
            Причина: {PAUSE_REASONS[sender.pause.reason] ?? sender.pause.reason}. Пауза с{" "}
            {dateTime(sender.pause.since)}; после неё очередь продолжится сама, раньше — кнопкой «Продолжить».
          </p>
          <p>Стоят все рассылки: они уходят с одного аккаунта {sender.account}.</p>
          <button type="button" onClick={resume} disabled={resuming}>
            Продолжить
          </button>
          {resumeError && <p role="alert">Не удалось снять паузу. {resumeError}</p>}
        </section>
      )}

      <section className="campaign-section" aria-labelledby="campaign-queue-title">
        <h2 id="campaign-queue-title">Очередь</h2>
        <p>Сообщений в рассылке: {total} — по одному на чат.</p>
        <dl className="campaign-queue">
          {QUEUE.map(({ key, label }) => (
            <div key={key}>
              <dt>{label}</dt>
              <dd>{campaign.queue[key] ?? 0}</dd>
            </div>
          ))}
        </dl>
        {(campaign.queue.unknown ?? 0) > 0 && (
          <p>
            «Неизвестно, ушло ли» — отправка прервалась на полпути. Повторно такие сообщения не уходят, чтобы не
            было дублей: проверьте чат вручную.
          </p>
        )}
        <p>
          Отправлено за последние сутки: {sender.sent_last_day} из {sender.daily_limit}. Лимит у аккаунта{" "}
          {sender.account}, общий для всех рассылок.
        </p>
      </section>

      <section className="campaign-section" aria-labelledby="campaign-suppliers-title">
        <h2 id="campaign-suppliers-title">Поставщики</h2>
        <table className="campaign-suppliers">
          <thead>
            <tr>
              <th>Поставщик</th>
              <th>Чат</th>
              <th>Статус</th>
            </tr>
          </thead>
          <tbody>
            {suppliers.map((supplier) => (
              <tr key={supplier.id}>
                <td>{supplier.name}</td>
                <td>{supplier.chat?.title ?? "—"}</td>
                <td>
                  <StatusBadge status={supplier.status} />
                  {supplier.reasons.length > 0 && (
                    <ul className="campaign-reasons">
                      {supplier.reasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
