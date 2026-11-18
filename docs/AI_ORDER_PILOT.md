# AI Telegram order intake — pilot

This feature is a child router of handlers_order.py. It does not modify main.py,
which is SHA-checked by install_solution.py during Docker builds.

## Railway variables

- OPENAI_API_KEY: secret OpenAI key. Do not commit it.
- AI_ORDER_ENABLED=1: enables the AI menu button and /ai.
- AI_ORDER_PILOT_IDS=260287389: explicitly allowed approved Telegram IDs.
- ADMIN_IDS: all configured bot administrators also get AI pilot access automatically
  while AI_ORDER_ENABLED=1; they do not need to be added to AI_ORDER_PILOT_IDS.
- AI_ORDER_DRY_RUN=1: DEFAULT. The final confirmation creates no real
  Nova Poshta TTN, no Sheets row, no stock update, and no order.
- AI_ORDER_MODEL=gpt-6-luna: OpenAI GPT-6 Luna supports Responses API Structured Outputs.

AI_ORDER_ENABLED defaults to 0. An active Telegram account must also be
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
7. In dry-run the confirmation ends without any write. In explicitly
   approved real mode, normal confirmation/payment-proof/TTN steps run.

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

A live Railway dry-run with the pilot Telegram account is still required
before considering AI_ORDER_DRY_RUN=0.
