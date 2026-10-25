"""HTTP API: запрос — в вызов сервиса, результат — в ответ. Решения принимают сервисы.

Пишущий роут коммитит сам. Отказы сервисов становятся ответами 404, 409 и 422 в errors.py.
"""

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api import schemas
from app.api.deps import ClockDep, SessionDep
from app.domain.offers import Fields
from app.domain.preview import Outgoing
from app.domain.template import check, render
from app.seed import STATUSES
from app.services import campaigns, offers, report, sending

router = APIRouter(prefix="/api")

TITLES = {"stale_price": "Активные, прайс устарел", "inactive_new": "Новые без движения"}
TEMPLATE = (
    "Здравствуйте, {supplier}! Пришлите, пожалуйста, актуальный прайс: цену, минимальную партию, "
    "срок поставки и регионы доставки.\nС уважением, {manager}"
)


def refusals(*codes: int) -> dict[int | str, dict[str, Any]]:
    """Отказы роута для OpenAPI: тело у всех одно — причины."""
    return {code: {"model": schemas.Problem} for code in codes}


@router.get("/options", tags=["рассылки"])
def options() -> schemas.Options:
    presets = [schemas.Preset(id=preset, title=TITLES[preset]) for preset in campaigns.PRESETS]
    return schemas.Options(presets=presets, statuses=list(STATUSES), default_template=TEMPLATE)


@router.post("/campaigns/preview", tags=["рассылки"], responses=refusals(422))
def preview(launch: schemas.Launch, session: SessionDep, clock: ClockDep) -> schemas.Preview:
    """Что уйдёт, если запустить сейчас. Ничего не создаёт; причины блокировки — в problems."""
    draft = campaigns.preview(session, clock, launch.template, preset=launch.preset, status=launch.supplier_status)
    return schemas.Preview(
        totals=draft.preview.totals,
        outgoing=[
            schemas.Outgoing(chat=item.chat, suppliers=item.suppliers, text=text(launch.template, item))
            for item in draft.preview.outgoing
        ],
        excluded=draft.preview.excluded,
        problems=draft.problems,
    )


def text(template: str, item: Outgoing) -> str | None:
    return None if check(template, item.suppliers) else render(template, item.suppliers)


@router.post("/campaigns", status_code=201, tags=["рассылки"], responses=refusals(422))
def create(launch: schemas.Launch, session: SessionDep, clock: ClockDep) -> schemas.Campaign:
    """Запуск: сообщения встают в очередь. Запуск заблокирован — 422 с причинами, рассылка не создаётся."""
    campaign = campaigns.create(session, clock, launch.template, preset=launch.preset, status=launch.supplier_status)
    session.commit()
    return schemas.Campaign.model_validate(report.campaign(session, clock.now(), campaign.id))


@router.get("/campaigns", tags=["рассылки"])
def campaign_list(session: SessionDep, clock: ClockDep) -> list[schemas.Campaign]:
    return [schemas.Campaign.model_validate(item) for item in report.campaigns(session, clock.now())]


@router.get("/campaigns/{campaign_id}", tags=["рассылки"], responses=refusals(404, 422))
def campaign_status(campaign_id: int, session: SessionDep, clock: ClockDep) -> schemas.CampaignStatus:
    """Очередь рассылки, пауза общего аккаунта и сколько он отправил за сутки."""
    now = clock.now()
    campaign = report.campaign(session, now, campaign_id)
    if campaign is None:
        raise not_found(campaign_id)
    return schemas.CampaignStatus(campaign=campaign, sender=report.sender(session, now))


@router.get("/campaigns/{campaign_id}/report", tags=["рассылки"], responses=refusals(404, 422))
def campaign_report(campaign_id: int, session: SessionDep, clock: ClockDep) -> schemas.Report:
    """По каждому поставщику — статус и причины исключения, по каждому ответу — разбор и предложения."""
    built = report.build(session, clock.now(), campaign_id)
    if built is None:
        raise not_found(campaign_id)
    return schemas.Report.model_validate(built)


def not_found(campaign_id: int) -> HTTPException:
    return HTTPException(404, [f"рассылки №{campaign_id} нет"])


@router.post("/sender/resume", tags=["рассылки"])
def resume(session: SessionDep, clock: ClockDep) -> schemas.Sender:
    """«Продолжить» после RATE_LIMIT: пауза снимается сразу, очередь идёт со следующего шага воркера."""
    sending.resume(session, clock.now())
    session.commit()
    return schemas.Sender.model_validate(report.sender(session, clock.now()))


@router.put("/offers/{offer_id}", tags=["предложения"], responses=refusals(404, 409, 422))
def edit_offer(offer_id: int, changed: schemas.OfferFields, session: SessionDep, clock: ClockDep) -> schemas.Offer:
    """Поля целиком, как их поправил человек. Применённое не правится — 409."""
    fields = Fields(
        changed.product, changed.price, changed.currency, changed.moq, changed.lead_time_days, tuple(changed.regions)
    )
    offer = offers.edit(session, clock, offer_id, fields)
    session.commit()
    return schemas.Offer.model_validate(offer)


@router.put("/offers/{offer_id}/supplier", tags=["предложения"], responses=refusals(404, 409, 422))
def choose_supplier(
    offer_id: int, choice: schemas.SupplierChoice, session: SessionDep, clock: ClockDep
) -> schemas.Offer:
    """Поставщик предложения, если по нику он непонятен: из тех, кого в этом чате спросили в рассылке."""
    offer = offers.choose_supplier(session, clock, offer_id, choice.supplier_id)
    session.commit()
    return schemas.Offer.model_validate(offer)


@router.post("/offers/{offer_id}/apply", tags=["предложения"], responses=refusals(404, 409, 422))
def apply_offer(offer_id: int, session: SessionDep, clock: ClockDep) -> schemas.Price:
    """«Применить»: запись в прайсе поставщика. Второй раз — 409, не хватает полей — 422 с причинами."""
    price = offers.apply(session, clock, offer_id)
    session.commit()
    return schemas.Price.model_validate(price)


@router.get("/inbox/unbound", tags=["входящие"])
def unbound(session: SessionDep) -> list[schemas.Unbound]:
    """Входящие поставщиков, не засчитанные ни одной рассылке."""
    return [schemas.Unbound.model_validate(item) for item in report.unbound(session)]


@router.get("/clock", tags=["часы"])
def now(clock: ClockDep) -> schemas.Clock:
    """Текущее время по часам задания — общее для API и воркера."""
    return schemas.Clock(now=clock.now())


@router.post("/clock/advance", tags=["часы"], responses=refusals(422))
def advance(step: schemas.Advance, clock: ClockDep) -> schemas.Clock:
    """Перемотка вперёд: воркер увидит новое время на следующем шаге, через секунду. Назад нельзя — 422."""
    clock.advance(step.by)
    return schemas.Clock(now=clock.now())
