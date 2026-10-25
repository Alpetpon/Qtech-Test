import { OfferForm } from "./OfferForm";
import { dateTime, REPLY_TYPES, type Reply } from "./report";

type Props = {
  reply: Reply;
  onChanged: () => void;
};

export function ReplyCard({ reply, onChanged }: Props) {
  const last = reply.messages.at(-1);
  const attachments = reply.messages.flatMap((message) => (message.attachment ? [message.attachment] : []));

  return (
    <article className="reply">
      <div className="head">
        <span className={`tag type-${reply.type ?? "pending"}`}>
          {reply.type ? REPLY_TYPES[reply.type] : "ещё не разобран моделью"}
        </span>
        <span>
          <strong>{reply.sender}</strong> в чате «{reply.chat.title}»
          {last && `, ${dateTime(last.received_at)}`}
          {reply.messages.length > 1 && ` (сообщений: ${reply.messages.length})`}
        </span>
      </div>
      {reply.mark && <p className="mark">{reply.mark} — ответ не засчитан ни одному поставщику рассылки.</p>}
      {/* У предложения своя цитата; без предложений цитата — сам ответ */}
      {reply.offers.length === 0 && <blockquote>{reply.messages.map((message) => message.text).join("\n")}</blockquote>}
      {attachments.length > 0 && <p>Вложение: {attachments.join(", ")}</p>}
      {reply.notes.length > 0 && <p>Пометки модели: {reply.notes.join("; ")}.</p>}
      {reply.review.length > 0 && <p className="review">Проверить вручную: {reply.review.join("; ")}.</p>}
      {reply.offers.length === 0 && reply.candidates.length > 0 && (
        <p className="hint">
          Предложений в ответе нет — выбирать поставщика не для чего.
          {reply.supplier_hint && ` Модель подсказывает: ${reply.supplier_hint.name}.`}
        </p>
      )}
      {reply.offers.map((offer) => (
        <OfferForm
          key={offer.id}
          offer={offer}
          candidates={reply.candidates}
          hint={reply.supplier_hint}
          onChanged={onChanged}
        />
      ))}
    </article>
  );
}
