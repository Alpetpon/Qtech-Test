import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "../api/client";
import type { components } from "../api/schema";
import "./LaunchPage.css";

type Options = components["schemas"]["Options"];
type Launch = components["schemas"]["Launch"];
type Preview = components["schemas"]["Preview"];

type Choice = { key: string; label: string; select: Omit<Launch, "template"> };
type Failure = { title: string; reasons: string[] };

const OFFLINE = "Нет связи с сервером. Проверьте, что сервис запущен, и попробуйте ещё раз.";

function choicesOf(options: Options): Choice[] {
  return [
    ...options.presets.map((preset) => ({
      key: `preset:${preset.id}`,
      label: preset.title,
      select: { preset: preset.id },
    })),
    ...options.statuses.map((status) => ({
      key: `status:${status}`,
      label: `Все со статусом ${status}`,
      select: { supplier_status: status },
    })),
  ];
}

// Тело ошибки бывает не JSON (502 от прокси, 500): тогда показываем только код
function reasonsOf(error: unknown, status: number): string[] {
  if (typeof error === "object" && error !== null && "detail" in error && Array.isArray(error.detail)) {
    return error.detail.map(String);
  }
  return [`Сервер ответил ошибкой ${status}. Попробуйте ещё раз.`];
}

export function LaunchPage() {
  const navigate = useNavigate();
  const [options, setOptions] = useState<Options>();
  const [optionsError, setOptionsError] = useState<string>();
  const [choiceKey, setChoiceKey] = useState("");
  const [template, setTemplate] = useState("");
  // Превью помнит запрос, по которому построено: изменили выбор или шаблон — запускать по нему нельзя
  const [preview, setPreview] = useState<{ request: string; data: Preview }>();
  const [busy, setBusy] = useState<"preview" | "launch">();
  const [failure, setFailure] = useState<Failure>();

  async function loadOptions() {
    setOptionsError(undefined);
    try {
      const { data, error, response } = await api.GET("/api/options");
      if (!data) {
        setOptionsError(reasonsOf(error, response.status).join(" "));
        return;
      }
      setOptions(data);
      setChoiceKey(choicesOf(data)[0].key);
      setTemplate(data.default_template);
    } catch {
      setOptionsError(OFFLINE);
    }
  }

  useEffect(() => {
    loadOptions();
  }, []);

  if (!options) {
    return (
      <>
        <h1>Новая рассылка</h1>
        {optionsError ? (
          <div className="launch-alert" role="alert">
            <p>Не удалось загрузить варианты рассылки. {optionsError}</p>
            <button type="button" onClick={loadOptions}>
              Повторить
            </button>
          </div>
        ) : (
          <p>Загружаем…</p>
        )}
      </>
    );
  }

  const choices = choicesOf(options);
  const choice = choices.find((item) => item.key === choiceKey) ?? choices[0];
  const launch: Launch = { ...choice.select, template };
  const request = JSON.stringify(launch);
  const current = preview?.request === request ? preview.data : undefined;
  const blocked = current !== undefined && current.problems.length > 0;

  async function showPreview() {
    setBusy("preview");
    setFailure(undefined);
    try {
      const { data, error, response } = await api.POST("/api/campaigns/preview", { body: launch });
      if (data) setPreview({ request, data });
      else setFailure({ title: "Превью не построено", reasons: reasonsOf(error, response.status) });
    } catch {
      setFailure({ title: "Превью не построено", reasons: [OFFLINE] });
    } finally {
      setBusy(undefined);
    }
  }

  async function start() {
    setBusy("launch");
    setFailure(undefined);
    try {
      const { data, error, response } = await api.POST("/api/campaigns", { body: launch });
      if (data) {
        navigate(`/campaigns/${data.id}`);
        return;
      }
      const reasons = reasonsOf(error, response.status);
      if (response.status === 422) {
        reasons.push("Данные могли измениться после превью — покажите превью заново.");
        // Сервер отказал по свежим данным: показанное превью больше ничему не соответствует
        setPreview((shown) => shown && { ...shown, request: "" });
      }
      setFailure({ title: "Рассылка не запущена", reasons });
    } catch {
      setFailure({ title: "Рассылка не запущена", reasons: [OFFLINE] });
    } finally {
      setBusy(undefined);
    }
  }

  let launchHint: string;
  if (!preview) launchHint = "Сначала покажите превью.";
  else if (!current) launchHint = "Превью устарело — покажите его заново.";
  else if (blocked) launchHint = "Запуск заблокирован — причины в превью.";
  else launchHint = `Уйдёт сообщений: ${current.totals.chats}.`;

  return (
    <div className="launch">
      <h1>Новая рассылка</h1>

      <fieldset>
        <legend>Кому писать</legend>
        {choices.map((item) => (
          <label key={item.key} className="launch-choice">
            <input
              type="radio"
              name="choice"
              value={item.key}
              checked={item.key === choice.key}
              onChange={() => setChoiceKey(item.key)}
            />
            {item.label}
          </label>
        ))}
      </fieldset>

      <label className="launch-template">
        <span>Шаблон сообщения</span>
        <textarea value={template} rows={5} onChange={(event) => setTemplate(event.target.value)} />
        <small>
          {"{supplier}"} — названия поставщиков чата, {"{manager}"} — их менеджер. Пустое значение блокирует запуск.
        </small>
      </label>

      <div className="launch-actions">
        <button type="button" onClick={showPreview} disabled={busy !== undefined}>
          {busy === "preview" ? "Считаем…" : "Показать превью"}
        </button>
        <button type="button" onClick={start} disabled={busy !== undefined || !current || blocked}>
          {busy === "launch" ? "Запускаем…" : "Запустить"}
        </button>
        <span className="launch-hint">{launchHint}</span>
      </div>

      {failure && (
        <div className="launch-alert" role="alert">
          <strong>{failure.title}:</strong>
          <ul>
            {failure.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}

      {preview && <PreviewView preview={preview.data} stale={!current} />}
    </div>
  );
}

function PreviewView({ preview, stale }: { preview: Preview; stale: boolean }) {
  const { totals, outgoing, excluded, problems } = preview;
  return (
    <section className={stale ? "launch-preview launch-stale" : "launch-preview"}>
      <h2>Превью{stale && " — устарело"}</h2>
      <p>
        Чатов в рассылке: <strong>{totals.chats}</strong>, в каждый уходит одно сообщение. Выбрано поставщиков:{" "}
        {totals.selected}, исключено: {totals.excluded}.
      </p>

      {problems.length > 0 && (
        <div className="launch-alert">
          <strong>Запуск заблокирован:</strong>
          <ul>
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </div>
      )}

      <h3>Уйдёт ({outgoing.length})</h3>
      {outgoing.length === 0 ? (
        <p>Ни одного чата.</p>
      ) : (
        <ul className="launch-list">
          {outgoing.map((item) => (
            <li key={item.chat.id}>
              <div>
                Чат «{item.chat.title}» <small>{item.chat.id}</small>
              </div>
              <div>Кому: {item.suppliers.map((supplier) => supplier.name).join(", ")}</div>
              {item.text === null ? (
                <p className="launch-muted">Текст не собрать — см. причины блокировки.</p>
              ) : (
                <pre>{item.text}</pre>
              )}
            </li>
          ))}
        </ul>
      )}

      <h3>Исключены ({excluded.length})</h3>
      {excluded.length === 0 ? (
        <p>Никого.</p>
      ) : (
        <ul className="launch-list">
          {excluded.map((item) => (
            <li key={item.supplier.id}>
              {item.supplier.name} <small>{item.supplier.id}</small> — {item.reasons.join("; ")}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
