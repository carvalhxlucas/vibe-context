---
expect:
  query: string
---

{
  "results": [
    {
      "chunk_id": "7d0c1a52-4f1e-4a3b-9d0e-2b8f6c1e9a01",
      "document_id": "b6f3e1c0a9d84f2e8c7b5a4d3e2f1a0b",
      "filename": "product-spec-billing.md",
      "scope": "global",
      "kind": "markdown",
      "location": {"heading": "Billing > Refund policy (v2, agreed 2026-08-14)"},
      "score": 0.91,
      "text": "## Refund policy (v2, agreed 2026-08-14)\n\n- Monthly plans: full refund when requested within 7 days of the charge. After 7 days, no refund.\n- Annual plans: full refund when requested within 30 days of the charge. After 30 days, refund the unused full months, prorated, minus a 10% processing fee.\n- A refund never exceeds the amount charged.\n- Requests are measured from the charge timestamp, in UTC."
    },
    {
      "chunk_id": "0e9b8a77-6c5d-4e3f-8a1b-3c2d1e0f9a88",
      "document_id": "c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6",
      "filename": "meeting-2026-08-14.md",
      "scope": "session",
      "kind": "markdown",
      "location": {"heading": "Decisions"},
      "score": 0.42,
      "text": "Decisions: finance approved the v2 refund policy. The 10% fee applies only to prorated annual refunds, never to full refunds."
    }
  ],
  "reranked": true,
  "notes": []
}
