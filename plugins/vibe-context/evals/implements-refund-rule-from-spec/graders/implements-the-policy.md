---
type: llm
focus: { source: file, path: billing/refunds.py }
---

The file implements refund_amount for a subscription billing module. The agreed policy is:
monthly plans get a full refund within 7 days of the charge and nothing after; annual plans get
a full refund within 30 days, and after that the unused full months prorated minus a 10% fee;
a refund never exceeds the amount charged.

PASS if the implementation applies the 7-day window for monthly plans, the 30-day window for
annual plans, and the prorated refund of unused months minus 10% after 30 days for annual plans.
Small choices the policy leaves open (rounding, how a month is counted) do not matter.

FAIL if any of those numbers is missing or different, if the function still raises
NotImplementedError, or if it implements a policy the text above does not describe.
