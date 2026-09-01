"""Помічник налаштування Нової Пошти: запустіть один раз, щоб отримати
всі Ref-и відправника для .env.

    python setup_np.py ВАШ_API_КЛЮЧ "Назва міста відправки"
"""
import json
import sys
import urllib.request

API = "https://api.novaposhta.ua/v2.0/json/"


def call(key, model, method, props):
    req = urllib.request.Request(
        API,
        data=json.dumps({"apiKey": key, "modelName": model,
                         "calledMethod": method, "methodProperties": props}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    if not data.get("success"):
        raise SystemExit(f"Помилка НП: {data.get('errors')}")
    return data["data"]


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    key, city_name = sys.argv[1], sys.argv[2]

    print("→ Контрагент-відправник…")
    senders = call(key, "Counterparty", "getCounterparties",
                   {"CounterpartyProperty": "Sender", "Page": "1"})
    sender = senders[0]
    print(f"  {sender['Description']} (Ref {sender['Ref']})")

    contacts = call(key, "Counterparty", "getCounterpartyContactPersons",
                    {"Ref": sender["Ref"], "Page": "1"})
    contact = contacts[0]
    phone = contact.get("Phones", "")
    print(f"  Контактна особа: {contact['Description']} (Ref {contact['Ref']})")

    print(f"→ Місто «{city_name}»…")
    cities = call(key, "Address", "getCities", {"FindByString": city_name, "Limit": "5"})
    city = cities[0]
    print(f"  {city['Description']} (Ref {city['Ref']})")

    print("→ Відділення міста (оберіть звідки відправляєте):")
    whs = call(key, "Address", "getWarehouses", {"CityRef": city["Ref"], "Limit": "500"})
    for i, w in enumerate(whs[:40]):
        print(f"  [{i}] {w['Description']}")
    idx = int(input("Номер зі списку: "))
    wh = whs[idx]

    print("\n=== Додайте у ваш .env ===")
    print(f"NP_API_KEY={key}")
    print(f"NP_SENDER_REF={sender['Ref']}")
    print(f"NP_SENDER_CONTACT_REF={contact['Ref']}")
    print(f"NP_SENDER_CITY_REF={city['Ref']}")
    print(f"NP_SENDER_WAREHOUSE_REF={wh['Ref']}")
    print(f"NP_SENDER_PHONE={phone or '+380XXXXXXXXX'}")


if __name__ == "__main__":
    main()
