"""Клієнт API Нової Пошти: міста, вантажні відділення, створення ТТН."""
import re

import aiohttp

import config
import perf

API_URL = "https://api.novaposhta.ua/v2.0/json/"

# типи відділень НП
TYPE_CARGO = "9a68df70-0267-42a8-bb5c-37f427e36ee4"      # вантажне відділення
TYPE_POSTOMAT = "f9316480-5f2d-425d-bc2c-ac7cd29decf0"   # поштомат


class NPError(Exception):
    pass


async def _call(model, method, props, api_key: str | None = None):
    import np_store
    key = api_key or np_store.get("api_key")
    if not key:
        raise NPError("не задано API-ключ Нової Пошти — адмін: команда /np_setup")
    payload = {
        "apiKey": key,
        "modelName": model,
        "calledMethod": method,
        "methodProperties": props,
    }
    async with perf.timed(f"НП {model}.{method}"):
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
    def _f(v):
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    result = []
    for w in data:
        if w.get("TypeOfWarehouse") == TYPE_POSTOMAT:
            continue
        # беремо найбільший із двох лімітів НП: на одне місце і на відправлення
        max_w = max(_f(w.get("PlaceMaxWeightAllowed")),
                    _f(w.get("TotalMaxWeightAllowed")))
        is_cargo = w.get("TypeOfWarehouse") == TYPE_CARGO
        # 0 у НП означає «без обмежень»; підходить: вантажне, без обмежень,
        # або з лімітом понад min_w кг
        if is_cargo or max_w == 0 or max_w > min_w:
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


async def get_sender_info(api_key: str):
    """Для /np_setup: контрагент-відправник і контактна особа за ключем.

    Повертає {sender_ref, sender_name, contact_ref, contact_name, phone}.
    """
    senders = await _call("Counterparty", "getCounterparties",
                          {"CounterpartyProperty": "Sender", "Page": "1"},
                          api_key=api_key)
    if not senders:
        raise NPError("у цьому кабінеті немає контрагента-відправника")
    sender = senders[0]
    contacts = await _call("Counterparty", "getCounterpartyContactPersons",
                           {"Ref": sender["Ref"], "Page": "1"}, api_key=api_key)
    if not contacts:
        raise NPError("у відправника немає контактної особи")
    contact = contacts[0]
    return {
        "sender_ref": sender["Ref"],
        "sender_name": sender.get("Description", ""),
        "contact_ref": contact["Ref"],
        "contact_name": contact.get("Description", ""),
        "phone": contact.get("Phones", "") or "",
    }


async def all_warehouses(city_ref: str, api_key: str | None = None):
    """Всі відділення міста (для вибору звідки відправляєте)."""
    data = await _call("Address", "getWarehouses",
                       {"CityRef": city_ref, "Limit": "500"}, api_key=api_key)
    return [{"ref": w["Ref"], "name": w.get("Description", ""),
             "number": w.get("Number", "")}
            for w in data if w.get("TypeOfWarehouse") != TYPE_POSTOMAT]


async def search_streets(city_ref: str, query: str, limit: int = 10):
    """Пошук вулиць у місті. Повертає [{ref, name, type}]."""
    data = await _call("Address", "getStreet",
                       {"CityRef": city_ref, "FindByString": query.strip(),
                        "Limit": str(limit)})
    return [{"ref": s["Ref"], "name": s.get("Description", ""),
             "type": s.get("StreetsTypeDescription", "")} for s in data]


async def create_address(counterparty_ref: str, street_ref: str,
                         building: str, flat: str = ""):
    """Створити адресу отримувача. Повертає Ref адреси."""
    props = {"CounterpartyRef": counterparty_ref, "StreetRef": street_ref,
             "BuildingNumber": building}
    if flat:
        props["Flat"] = flat
    data = await _call("Address", "save", props)
    return data[0]["Ref"]


async def create_ttn(*, recipient_city_ref: str, recipient_warehouse_ref: str,
                     fio: str, phone: str, description: str, cost: float,
                     weight: float, volume: float | None, seats: int = 1,
                     cod_amount: float = 0, to_door: bool = False,
                     street_ref: str = "", building: str = "", flat: str = ""):
    """Створити ТТН. cod_amount > 0 додає «Контроль оплати» на цю суму.

    Повертає {ttn, ref, cost_delivery, estimated_date}.
    """
    import np_store
    if not np_store.is_ready():
        raise NPError("відправника не налаштовано — адмін: команда /np_setup")
    s = np_store.all_values()

    recipient_ref, recipient_contact = await create_recipient(fio, phone)

    if to_door:
        address_ref = await create_address(recipient_ref, street_ref, building, flat)
        service_type = "WarehouseDoors"
    else:
        address_ref = recipient_warehouse_ref
        service_type = "WarehouseWarehouse"

    props = {
        "PayerType": config.NP_PAYER_TYPE,
        "PaymentMethod": "Cash",
        # дата відправлення — у поясі таблиці: під UTC контейнер після
        # опівночі поставив би вчорашню дату, і НП таку ТТН не прийняла б
        "DateTime": config.now().strftime("%d.%m.%Y"),
        "CargoType": "Cargo",
        "Weight": str(round(max(weight, 0.5), 1)),
        "SeatsAmount": str(seats),
        "ServiceType": service_type,
        "Description": description[:100],
        "Cost": str(int(cost)),
        "CitySender": s["city_ref"],
        "Sender": s["sender_ref"],
        "SenderAddress": s["warehouse_ref"],
        "ContactSender": s["contact_ref"],
        "SendersPhone": s["phone"],
        "CityRecipient": recipient_city_ref,
        "Recipient": recipient_ref,
        "RecipientAddress": address_ref,
        "ContactRecipient": recipient_contact,
        "RecipientsPhone": phone,
    }
    if volume:
        props["VolumeGeneral"] = str(round(volume, 3))
    # Контроль оплати: клієнт платить суму у відділенні, НП переказує на IBAN
    if cod_amount and config.NP_PAYMENT_CONTROL:
        props["AfterpaymentOnGoodsCost"] = str(int(cod_amount))
    data = await _call("InternetDocument", "save", props)
    doc = data[0]
    return {
        "ttn": doc.get("IntDocNumber"),
        "ref": doc.get("Ref"),
        "cost_delivery": doc.get("CostOnSite"),
        "estimated_date": doc.get("EstimatedDeliveryDate"),
    }
