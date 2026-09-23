from .security import redact

def redact_payload(value): return redact(value)
def verified_receipt(registry, idempotency_key):
    row=registry.receipt(idempotency_key)
    if not row or not row['verified'] or not row['external_id'] or not row['url']:
        return None
    return dict(row)
def receipt_proves_external_response(row):
    return bool(row and row['verified'] and row['external_id'] and row['url'] and row['payload_json'])
