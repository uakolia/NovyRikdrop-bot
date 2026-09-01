"""Клієнт API Нової Пошти: міста, вантажні відділення, створення ТТН."""
import datetime as dt
import re

import aiohttp

import config

API_URL = "https://api.novaposhta.ua/v2.0/json/"

# типи відділень НП
TYPE_CARGO = "9a68df70-0267-42a8-bb5c-37f427e36ee4"      # вантажне відділення
TYPE_POSTOMAT = "f9316480-5f2d-425d-bc2c-ac7cd29decf0"   # поштомат


class NPError(Exception):
    pass


async def _call(model, method, props):
    payload = {
        "apiKey": config.NP_API_KEY,
        "modelName": model,
        "calledMethod": method,
        "methodProperties": props,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(API_URL, json=payload,
                          timeout=aiohttp.ClientTimeout(total=30)) as r:
            data = await r.json(content_type=None)
    if not data.get("success"):
        errs = data.get("errors") or data.get("warnings") or ["невідома помилка НП"]
        raise NPError("; ".join(str(e) for e in errs))
    return data["data"]


async def search_cities(query: str, limit: int = 8):
    """Пошук міст за назвою. Повертає [{Ref, Description, AreaDescription}]"""
    data = await _call("Address", "getCities",
                       {"FindByString": query.strip(), "Limit": str(limit)})
    return [{"ref": c["Ref"], "name": c["Description"],
             "area": c.get("AreaDescription", "")} for c in data]


async def cargo_warehouses(city_ref: str, min_weight: float | None = None):
    """Вантажні відділення міста (приймають > min_weight кг)."""
    min_w = min_weight if min_weight is not None else config.NP_MIN_WAREHOUSE_WEIGHT
    data = await _call("Address", "getWarehouses",
                       {"CityRef": city_ref, "Limit": "500"})
    result = []
    for w in data:
        if w.get("TypeOfWarehouse") == TYPE_POSTOMAT:
            continue
        try:
            max_w = float(w.get("PlaceMaxWeightAllowed") or 0)
        except (TypeError, ValueError):
            max_w = 0
        # 0 у НП означає «без обмежень» лише для вантажних; надійніше:
        # беремо вантажні відділення АБО відділення з лімітом більше min_w
        is_cargo = w.get("TypeOfWarehouse") == TYPE_CARGO
        if is_cargo or max_w > min_w:
            result.append({
                "ref": w["Ref"],
                "number": w.get("Number", ""),
                "name": w.get("Description", ""),
                "max_weight": max_w,
                "cargo": is_cargo,
            })
    result.sort(key=lambda x: (not x["cargo"], _num_key(x["number"])))
    return result


def _num_key(n):
    try:
        return int(re.sub(r"\D", "", str(n)) or 0)
    except ValueError:
        return 0


def split_fio(fio: str):
    """'Прізвище Ім'я По-батькові' -> (last, first, middle)"""
    parts = fio.split()
    last = parts[0] if parts else ""
    first = parts[1] if len(parts) > 1 else ""
    middle = " ".join(parts[2:]) if len(parts) > 2 else ""
    return last, first, middle


async def create_recipient(fio: str, phone: str):
    """Створити контрагента-отримувача (приватна особа). Повертає (ref, contact_ref)."""
    last, first, middle = split_fio(fio)
    data = await _call("Counterparty", "save", {
        "FirstName": first, "MiddleName": middle, "LastName": last,
        "Phone": phone,
        "CounterpartyType": "PrivatePerson",
        "CounterpartyProperty": "Recipient",
    })
    cp = data[0]
    contact_ref = cp["ContactPerson"]["data"][0]["Ref"]
    return cp["Ref"], contact_ref


async def create_ttn(*, recipient_city_ref: str, recipient_warehouse_ref: str,
                     fio: str, phone: str, description: str, cost: float,
                     weight: float, volume: float | None, seats: int = 1):
    """Створити ТТН (без зворотної доставки/накладеного платежу).

    Повертає {ttn, ref, cost_delivery, estimated_date}.
    """
    for v in ("NP_SENDER_REF", "NP_SENDER_CONTACT_REF", "NP_SENDER_CITY_REF",
              "NP_SENDER_WAREHOUSE_REF", "NP_SENDER_PHONE"):
        if not getattr(config, v):
            raise NPError(f"не налаштовано {v} у .env (запустіть setup_np.py)")

    recipient_ref, recipient_contact = await create_recipient(fio, phone)

    props = {
        "PayerType": config.NP_PAYER_TYPE,
        "PaymentMethod": "Cash",
        "DateTime": dt.datetime.now().strftime("%d.%m.%Y"),
        "CargoType": "Cargo",
        "Weight": str(round(max(weight, 0.5), 1)),
        "SeatsAmount": str(seats),
        "ServiceType": "WarehouseWarehouse",
        "Description": description[:100],
        "Cost": str(int(cost)),
        "CitySender": config.NP_SENDER_CITY_REF,
        "Sender": config.NP_SENDER_REF,
        "SenderAddress": config.NP_SENDER_WAREHOUSE_REF,
        "ContactSender": config.NP_SENDER_CONTACT_REF,
        "SendersPhone": config.NP_SENDER_PHONE,
        "CityRecipient": recipient_city_ref,
        "Recipient": recipient_ref,
        "RecipientAddress": recipient_warehouse_ref,
        "ContactRecipient": recipient_contact,
        "RecipientsPhone": phone,
    }
    if volume:
        props["VolumeGeneral"] = str(round(volume, 3))
    data = await _call("InternetDocument", "save", props)
    doc = data[0]
    return {
        "ttn": doc.get("IntDocNumber"),
        "ref": doc.get("Ref"),
        "cost_delivery": doc.get("CostOnSite"),
        "estimated_date": doc.get("EstimatedDeliveryDate"),
    }
