# Operations catalog

## Service registry

The Atlas gateway has service identifier SVC-1042, listens on port 8443, and is owned by the Platform Operations team. Its production alias is atlas-gw.prod.example.invalid.

## Queue poison error

Error code QPX-731 means a poison message was detected. The operator must quarantine the message, capture its SHA-256 digest in the incident record, and must not replay it until the payload owner approves.

## Release windows

Routine production releases may start only on Tuesday or Thursday between 02:00 and 04:00 UTC. A release freeze must be announced five business days before a quarter close.

## Priority-one escalation

A priority-one incident requires acknowledgement within ten minutes. An incident commander must be named within fifteen minutes, after which the commander owns stakeholder updates.

## Batch ledger

The finance reconciliation job has identifier FIN-882. It may retry at most three times with a fixed ninety-second delay before the run is moved to manual review.

## Status probes

The `/livez` endpoint proves only that the process is running. The `/readyz` endpoint succeeds only when both PostgreSQL and Redis dependency checks pass.
