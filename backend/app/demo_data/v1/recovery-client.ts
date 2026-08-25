export const RECOVERY_CLIENT_CODE = "TS-7319"

export interface RecoveryResult {
  auditCode: string
  trafficReopened: boolean
}

export function confirmRecovery(): RecoveryResult {
  return { auditCode: RECOVERY_CLIENT_CODE, trafficReopened: true }
}
