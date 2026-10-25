import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { api } from "../api/client";
import { ReplyCard } from "../components/ReplyCard";
import {
  date,
  dateTime,
  failure,
  OFFLINE,
  type Report as ReportData,
  type ReportSupplier,
  SUPPLIER_STATUSES,
  type Unbound,
} from "../components/report";
import "./ReportPage.css";

const CAMPAIGN_STATUSES = { sending: "идёт отправка", paused: "на паузе", sent: "отправлена" };

const STATUS_HINTS: Partial<Record<ReportSupplier["status"], string>> = {
  silent: "ответа нет 3 дня",
  replied: "ответ ещё разбирает модель",
  unknown: "отправка прервалась: повторно не уходит, проверьте чат",
};

export function ReportPage() {
  const { id = "" } = useParams();
  // Другая рассылка — другая страница: состояние прежней не переносится
  return <Report key={id} id={id} />;
}

function Report({ id }: { id: string }) {
  const campaignId = /^\d+$/.test(id) ? Number(id) : null;
  const [report, setReport] = useState<ReportData | null>(null);
  // Строка — почему не загрузились
  const [unbound, setUnbound] = useState<Unbound[] | string | null>(null);
  const [missing, setMissing] = useState(campaignId === null);
  const [problem, setProblem] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const latest = useRef(0);

  const load = useCallback(async () => {
    if (campaignId === null) return;
    // Ответ на устаревший запрос не затирает более свежий
    const request = ++latest.current;
    setLoading(true);
    try {
      const [built, inbox] = await Promise.all([
        api.GET("/api/campaigns/{campaign_id}/report", { params: { path: { campaign_id: campaignId } } }),
        api.GET("/api/inbox/unbound"),
      ]);
      if (request !== latest.current) return;
      setMissing(built.response.status === 404);
      if (built.data) {
        setReport(built.data);
        setProblem(null);
      } else if (built.response.status !== 404) {
        setProblem(failure(built.response.status, built.error));
      }
      setUnbound(inbox.data ?? failure(inbox.response.status, inbox.error));
    } catch {
      if (request === latest.current) setProblem(OFFLINE);
    } finally {
      if (request === latest.current) setLoading(false);
    }
  }, [campaignId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="report">
      <h1>Отчёт по рассылке №{id}</h1>
      {missing ? (
        <p className="notice notice-error">
          Рассылки №{id} нет. Проверьте номер в адресе или вернитесь <Link to="/">на главную</Link>.
        </p>
      ) : report === null ? (
        problem ? (
          <p className="notice notice-error">
            Отчёт не загрузился: {problem}.{" "}
            <button type="button" onClick={() => void load()} disabled={loading}>
              Повторить
            </button>
          </p>
        ) : (
          <p>Загружаем отчёт…</p>
        )
      ) : (
        <>
          <Summary report={report} />
          <div className="actions">
            <button type="button" onClick={() => void load()} disabled={loading}>
              {loading ? "Обновляем…" : "Обновить"}
            </button>
            <Link to={`/campaigns/${id}`}>Очередь рассылки</Link>
            {problem && (
              <span className="notice notice-error" role="status">
                Не обновилось: {problem}.
              </span>
            )}
          </div>

          <section>
            <h2>Поставщики ({report.suppliers.length})</h2>
            {report.suppliers.map((supplier) => (
              <SupplierCard key={supplier.id} supplier={supplier} onChanged={load} />
            ))}
          </section>

          {report.unassigned.length > 0 && (
            <section>
              <h2>Ответы без поставщика ({report.unassigned.length})</h2>
              <p className="hint">Пришли в чаты рассылки, но не засчитаны ни одному поставщику.</p>
              {report.unassigned.map((reply) => (
                <ReplyCard key={reply.id} reply={reply} onChanged={load} />
              ))}
            </section>
          )}
        </>
      )}

      {!missing && <UnboundList items={unbound} campaignId={campaignId} />}
    </div>
  );
}

function Summary({ report }: { report: ReportData }) {
  const { campaign } = report;
  const counts = new Map<string, number>();
  for (const supplier of report.suppliers) {
    const label = SUPPLIER_STATUSES[supplier.status];
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return (
    <>
      <p>
        Запущена {dateTime(campaign.created_at)} UTC, {CAMPAIGN_STATUSES[campaign.status]}. Сообщений отправлено:{" "}
        {campaign.queue.sent}
        {campaign.queue.pending + campaign.queue.sending > 0 &&
          `, в очереди: ${campaign.queue.pending + campaign.queue.sending}`}
        {campaign.queue.unknown > 0 && `, неизвестно, ушло ли: ${campaign.queue.unknown}`}.
      </p>
      <p className="counts">
        {[...counts].map(([label, count]) => (
          <span key={label} className="tag">
            {label}: {count}
          </span>
        ))}
      </p>
    </>
  );
}

function SupplierCard({ supplier, onChanged }: { supplier: ReportSupplier; onChanged: () => void }) {
  const hint = STATUS_HINTS[supplier.status];
  return (
    <article className="supplier">
      <div className="head">
        <h3>{supplier.name}</h3>
        <span className={`status status-${supplier.status}`}>{SUPPLIER_STATUSES[supplier.status]}</span>
        {hint && <span className="hint">{hint}</span>}
      </div>
      <p className="hint">
        {/* У исключённого чата в рассылке нет: почему — в причинах */}
        {supplier.chat && `чат «${supplier.chat.title}» · `}
        {supplier.price_updated_at ? `прайс от ${date(supplier.price_updated_at)}` : "прайса нет"}
      </p>
      {supplier.reasons.length > 0 && <p>Почему исключён: {supplier.reasons.join("; ")}.</p>}
      {supplier.replies.map((reply) => (
        <ReplyCard key={reply.id} reply={reply} onChanged={onChanged} />
      ))}
    </article>
  );
}

function UnboundList({ items, campaignId }: { items: Unbound[] | string | null; campaignId: number | null }) {
  if (items === null) return null;
  return (
    <section>
      <h2>Непривязанные входящие{typeof items === "string" ? "" : ` (${items.length})`}</h2>
      <p className="hint">Сообщения поставщиков во всех чатах, которые не засчитаны ни одной рассылке.</p>
      {typeof items === "string" ? (
        <p className="notice notice-error">Не загрузились: {items}.</p>
      ) : items.length === 0 ? (
        <p>Таких сообщений нет.</p>
      ) : (
        <ul className="unbound">
          {items.map((item) => (
            <li key={item.id}>
              <p>
                <span className="tag">{item.mark}</span> <strong>{item.sender}</strong> в чате «{item.chat.title}»,{" "}
                {dateTime(item.received_at)}
                {item.campaign_id !== null &&
                  (item.campaign_id === campaignId ? (
                    " · эта рассылка, см. «Ответы без поставщика»"
                  ) : (
                    <>
                      {" · "}
                      <Link to={`/campaigns/${item.campaign_id}/report`}>рассылка №{item.campaign_id}</Link>
                    </>
                  ))}
              </p>
              <blockquote>{item.text}</blockquote>
              {item.attachment && <p>Вложение: {item.attachment}</p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
