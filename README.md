# Angel One Copy Trading Worker

Run one instance on each Angel One account's registered-IP server. It has no market-data WebSocket. The Flattrade master sends canonical contracts and order commands; this worker resolves Angel symbols locally, places orders, and reports order/fill status.

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
venv/bin/uvicorn main:app --host 0.0.0.0 --port 7100
```

Put it behind HTTPS, restrict inbound access to the master server IP, and use a different long random `WORKER_API_TOKEN` for each account.

Angel One validates the source IP of place/modify/cancel requests for static-IP API keys. Because SmartAPI is called only by this worker, the observed source IP is the public IPv4 of this worker's server. Deploy the same repository separately for every Angel account and never share `.env` files.

API operations are idempotent by `command_id`. Entry and exit responses include the exact broker request, raw response, order ID and current order status. Protection synchronization creates the exact master SL/TSL and target prices; the local monitor cancels the sibling order when either one fills.

Entry and exit use Angel One `MARKET` orders. A rejected market order is replaced up to three times after the original attempt. Every attempt is returned to the master and written to the audit trail. SL/TSL uses `STOPLOSS_LIMIT` with a 5% execution buffer; target uses `LIMIT`. The worker checks protective orders every 30 seconds, while a master SL/TSL or target event triggers an immediate on-demand broker-status refresh.

Order submission responses include `timing`: placement start/acknowledgement timestamps and duration, followed by fill-confirmation timestamps, duration and `fill_confirmed`. Placement timing includes contract resolution and the broker placement call; acknowledgement does not mean the order filled. Market-order fill polling and retry behavior are unchanged. The final HTTP response may arrive seconds after the placement acknowledgement while the worker checks order status.

Official references:

MCX option contracts are supported alongside NFO/BFO. The master can send CRUDEOILM,
GOLDM, SILVERM and NATGASMINI contracts using exchange MCX. The worker resolves
the exact expiry/strike/CE-or-PE from Angel's daily instrument master, including
MCX strikes quoted in paise, and validates whole contract lots before submission.
GOLDM/SILVERM order and trigger prices are snapped to their 0.50 tick; CRUDEOILM
and NATGASMINI use 0.05. The account must have the MCX segment enabled.

- https://smartapi.angelone.in/docs/Instruments
- https://github.com/angel-one/smartapi-python
