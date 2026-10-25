import { type ChangeEvent, useState } from "react";

import { api } from "../api/client";
import { dateTime, failure, OFFLINE, type Offer, type OfferFields, type SupplierRef } from "./report";

type Draft = {
  product: string;
  price: string;
  currency: string;
  moq: string;
  lead_time_days: string;
  regions: string;
};

type Notice = { kind: "ok" | "warn" | "error"; text: string };

type Action = "save" | "apply" | "choose";

const FAILED: Record<Action, string> = {
  save: "Не сохранено",
  apply: "Нельзя применить",
  choose: "Поставщик не выбран",
};

function draftOf(offer: Offer): Draft {
  return {
    product: offer.product ?? "",
    price: offer.price ?? "",
    currency: offer.currency ?? "",
    moq: offer.moq?.toString() ?? "",
    lead_time_days: offer.lead_time_days?.toString() ?? "",
    regions: offer.regions.join(", "),
  };
}

function same(a: Draft, b: Draft): boolean {
  return (Object.keys(a) as (keyof Draft)[]).every((key) => a[key] === b[key]);
}

/** null — пусто, undefined — не целое число. */
function whole(value: string): number | null | undefined {
  const text = value.replace(/\s/g, "");
  if (!text) return null;
  return /^\d+$/.test(text) ? Number(text) : undefined;
}

// Пустое поле уходит как null: что обязательно для «Применить», решает сервер и называет причины
function parse(draft: Draft): OfferFields | string {
  const price = draft.price.replace(/\s/g, "").replace(",", ".");
  if (price && !/^\d+(\.\d+)?$/.test(price)) return "цена — число, например 0.42";
  const moq = whole(draft.moq);
  if (moq === undefined) return "партия — целое число, например 5000";
  const lead = whole(draft.lead_time_days);
  if (lead === undefined) return "срок — целое число дней, например 10";
  return {
    product: draft.product.trim() || null,
    price: price || null,
    currency: draft.currency.trim().toUpperCase() || null,
    moq,
    lead_time_days: lead,
    regions: draft.regions
      .split(",")
      .map((region) => region.trim())
      .filter(Boolean),
  };
}

function refusal(action: Action, status: number, error: unknown): Notice {
  if (status === 409) {
    return action === "apply"
      ? { kind: "warn", text: "Уже применено: это предложение уже в прайсе поставщика, второй раз оно не применяется." }
      : { kind: "warn", text: "Предложение уже применено — менять его нельзя." };
  }
  if (status === 404) return { kind: "error", text: "Предложение не найдено — обновите отчёт." };
  return { kind: "error", text: `${FAILED[action]}: ${failure(status, error)}.` };
}

// Сервер поменял предложение: правка, выбор поставщика или «Применить» — в этой вкладке или в другой
function stamp(offer: Offer): string {
  return `${offer.edited_at}|${offer.applied_at}|${offer.supplier_id}`;
}

type Props = {
  offer: Offer;
  /** Ответ с незнакомым ником: поставщика выбирает человек из тех, кого спросили в этом чате. */
  candidates: SupplierRef[];
  hint: SupplierRef | null;
  onChanged: () => void;
};

export function OfferForm({ offer, candidates, hint, onChanged }: Props) {
  const [version, setVersion] = useState(stamp(offer));
  const [draft, setDraft] = useState(() => draftOf(offer));
  // Подсказка модели предвыбрана, но сохраняется только кнопкой «Выбрать»
  const [supplier, setSupplier] = useState(offer.supplier_id ?? hint?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);

  if (stamp(offer) !== version) {
    setVersion(stamp(offer));
    setDraft(draftOf(offer));
    setSupplier(offer.supplier_id ?? hint?.id ?? "");
  }

  const dirty = !same(draft, draftOf(offer));
  const chosen = candidates.find((candidate) => candidate.id === offer.supplier_id);

  async function act(action: () => Promise<Notice>) {
    setBusy(true);
    try {
      setNotice(await action());
    } catch {
      setNotice({ kind: "error", text: `Не удалось: ${OFFLINE}.` });
    } finally {
      setBusy(false);
      onChanged();
    }
  }

  async function save(): Promise<Notice | null> {
    const fields = parse(draft);
    if (typeof fields === "string") return { kind: "error", text: `Не сохранено: ${fields}.` };
    const { error, response } = await api.PUT("/api/offers/{offer_id}", {
      params: { path: { offer_id: offer.id } },
      body: fields,
    });
    return error === undefined ? null : refusal("save", response.status, error);
  }

  const onSave = () => act(async () => (await save()) ?? { kind: "ok", text: "Сохранено." });

  const onApply = () =>
    act(async () => {
      if (dirty) {
        const failed = await save();
        if (failed) return failed;
      }
      const { data, error, response } = await api.POST("/api/offers/{offer_id}/apply", {
        params: { path: { offer_id: offer.id } },
      });
      if (data) {
        return { kind: "ok", text: `Применено: прайс поставщика обновлён — ${data.product}, ${data.price} ${data.currency}.` };
      }
      return refusal("apply", response.status, error);
    });

  const onChoose = () =>
    act(async () => {
      const { error, response } = await api.PUT("/api/offers/{offer_id}/supplier", {
        params: { path: { offer_id: offer.id } },
        body: { supplier_id: supplier },
      });
      if (error !== undefined) return refusal("choose", response.status, error);
      const name = candidates.find((candidate) => candidate.id === supplier)?.name;
      return { kind: "ok", text: `Поставщик выбран: ${name}.` };
    });

  const field = (key: keyof Draft) => ({
    value: draft[key],
    disabled: busy,
    onChange: (event: ChangeEvent<HTMLInputElement>) => setDraft({ ...draft, [key]: event.target.value }),
  });

  const applied = offer.applied_at !== null;

  return (
    <div className={applied ? "offer applied" : "offer"}>
      {applied && (
        <p className="badge badge-ok">Применено {dateTime(offer.applied_at!)} — в прайсе поставщика, только чтение</p>
      )}
      {offer.supplement_at && (
        <p className="supplement">
          Пришло дополнение {dateTime(offer.supplement_at)}: поставщик написал ещё. Его разбор — отдельное
          предложение в следующем ответе; это предложение не перезаписано.
        </p>
      )}
      <blockquote>{offer.quote}</blockquote>

      {candidates.length > 0 && (
        <div className="choose">
          {applied ? (
            <p>Поставщик: {chosen?.name}</p>
          ) : (
            <>
              <label>
                Поставщик предложения
                <select value={supplier} disabled={busy} onChange={(event) => setSupplier(event.target.value)}>
                  <option value="">— выберите —</option>
                  {candidates.map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {candidate.name}
                      {candidate.id === hint?.id ? " (подсказка модели)" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <button type="button" disabled={busy || !supplier || supplier === offer.supplier_id} onClick={onChoose}>
                Выбрать
              </button>
              <p className="hint">
                {chosen
                  ? `Выбран: ${chosen.name}.`
                  : hint
                    ? `Модель подсказывает ${hint.name}, но не выбирает сама — проверьте и нажмите «Выбрать».`
                    : "Модель не подсказала — выберите по тексту ответа."}
              </p>
            </>
          )}
        </div>
      )}

      {applied ? (
        <dl className="fields">
          <Value name="Товар" value={offer.product} />
          <Value name="Цена" value={offer.price && `${offer.price} ${offer.currency ?? ""}`} />
          <Value name="Мин. партия" value={offer.moq?.toString()} />
          <Value name="Срок поставки, дней" value={offer.lead_time_days?.toString()} />
          <Value name="Регионы" value={offer.regions.join(", ")} />
        </dl>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void onSave();
          }}
        >
          <div className="fields">
            <label className="wide">
              Товар
              <input {...field("product")} />
            </label>
            <label>
              Цена
              <input {...field("price")} inputMode="decimal" />
            </label>
            <label>
              Валюта
              <input {...field("currency")} autoCapitalize="characters" />
            </label>
            <label>
              Мин. партия
              <input {...field("moq")} inputMode="numeric" />
            </label>
            <label>
              Срок поставки, дней
              <input {...field("lead_time_days")} inputMode="numeric" />
            </label>
            <label className="wide">
              Регионы, через запятую
              <input {...field("regions")} />
            </label>
          </div>
          {offer.review.length > 0 && (
            <p className="review">Чтобы применить, не хватает: {offer.review.join("; ")}.</p>
          )}
          <div className="actions">
            <button type="submit" disabled={busy || !dirty}>
              Сохранить
            </button>
            <button type="button" className="primary" disabled={busy} onClick={onApply}>
              Применить
            </button>
            {offer.edited_at && <span className="hint">правили {dateTime(offer.edited_at)}</span>}
            {dirty && <span className="hint">есть несохранённые правки — «Применить» сначала сохранит их</span>}
          </div>
        </form>
      )}
      {notice && (
        <p className={`notice notice-${notice.kind}`} role="status">
          {notice.text}
        </p>
      )}
    </div>
  );
}

function Value({ name, value }: { name: string; value: string | null | undefined }) {
  return (
    <div>
      <dt>{name}</dt>
      <dd>{value || "—"}</dd>
    </div>
  );
}
