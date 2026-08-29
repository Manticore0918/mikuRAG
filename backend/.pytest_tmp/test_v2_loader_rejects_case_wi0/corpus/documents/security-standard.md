# Security standard

## Session controls

An inactive administrative session locks after twenty minutes. A continuously active administrator must reauthenticate after twelve hours.

## Incident code SEC-417

Incident code SEC-417 requires the incident commander to revoke active sessions immediately and rotate affected service credentials within thirty minutes. Both actions must be recorded in the incident ticket.

## Backup encryption

Backup archives use AES-256 encryption with keys held in the managed key service. The backup encryption key must rotate every quarter.

## Break-glass access

Emergency access request BG-9 requires manager approval, Security approval, and a linked incident ticket. The access expires after four hours.

## Security log retention

Authentication and authorization logs are retained for four hundred days. Access to the archive is limited to the Security Analytics role.

## Phishing reports

Suspected phishing must be reported with the mail client's Report Phishing action within fifteen minutes. Forwarding the message to another mailbox is prohibited.
