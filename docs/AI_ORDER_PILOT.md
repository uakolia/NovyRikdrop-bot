# AI Telegram order intake — pilot

This feature is a child router of handlers_order.py. It does not modify main.py,
which is SHA-checked by install_solution.py during Docker builds.

## Railway variables

- OPENAI_API_KEY: secret OpenAI key. Do not commit it.
- AI_ORDER_ENABLED=1: enables the AI menu button and /ai.
- AI_ORDER_PILOT_IDS=260287389: explicitly allowed approved Telegram IDs when global access is disabled.
- AI_ORDER_ALL_APPROVED=1: offer AI ordering to every approved dropshipper (and admins).
  Keep AI_ORDER_DRY_RUN=1 while rolling out the interface; no real TTNs/orders.
- ADMIN_IDS: all configured bot administrators also get AI pilot access automatically
  while AI_ORDER_ENABLED=1; they do not need to be added to AI_ORDER_PILOT_IDS.
- AI_ORDER_DRY_RUN=1: DEFAULT. The final confirmation creates no real
  Nova Poshta TTN, no Sheets row, no stock update, and no order.
- AI_ORDER_MODEL=gpt-6-luna: OpenAI GPT-6 Luna supports Responses API Structured Outputs.

AI_ORDER_ENABLED and AI_ORDER_ALL_APPROVED both default to 0. An active Telegram account must also be
approved in the original dropshipper access system. Setting
AI_ORDER_DRY_RUN=0 is a separate production-release decision: the user then
confirms a REAL order through the original existing order logic.

## Flow

1. Pilot user starts via /ai or the AI button in /start.
2. User writes an order in Ukrainian, potentially in several messages.
3. OpenAI extracts only fields into a structured draft. Unknown values
   remain empty; it cannot submit orders or create tracking numbers.
4. Python validates product SKU against existing catalog, personal product
   names, height, quantities, prices and payment. Ambiguity asks for clarification.
5. Python checks stock and resolves actual city/warehouse references via
   the existing Nova Poshta search API.
6. Existing order confirmation appears, with an AI edit button.
7. In dry-run the confirmation ends without any write. In live mode
   (`AI_ORDER_DRY_RUN=0`), the existing order handler checks stock and payment.
   If no additional payment is due, confirmation calls the normal Nova Poshta
   TTN creation flow. If payment is due, the bot requests proof; choosing
   'Надішлю чек пізніше' saves an unpaid order without creating a TTN.
   A failed Nova Poshta response is reported to an administrator.

## Constraints

- Up to 6 distinct positions, 20 units each, and 1600 characters per message.
- A sale price per position is mandatory; never use wholesale price by default.
- This pilot supports warehouse delivery, not courier delivery.
- No voice/image transcription and no unattended order submission.
- Do not log raw prompts, recipient names or telephone numbers.
- The API request includes customer order data and sets store=false.
- Manual ordering remains available.

## Tests

Run locally after pip install -r requirements.txt:

python -m unittest -v test_ai_order_core.py test_ai_order_extract.py
python -m compileall -q .

A Railway dry-run with several approved dropshipper accounts is recommended
before considering AI_ORDER_DRY_RUN=0.

## Ukrainian interface style

- All AI prompts address customers using formal `Ви`.
- Both `/ai` and the AI menu button show the same instructions.
- Example model: `Українська Преміум`.
- Customer-facing note: TTNs are created only after `Вашого підтвердження`
  and completion of required payment steps.
