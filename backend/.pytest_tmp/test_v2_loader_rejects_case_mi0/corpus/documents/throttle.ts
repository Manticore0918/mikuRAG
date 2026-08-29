export function normalizeKey(value: string): string {
  return value.trim().toLowerCase().replaceAll("/", ":");
}

export class TokenBucket {
  constructor(
    private tokens: number,
    private readonly capacity: number,
    private readonly refillPerSecond: number,
  ) {}

  consume(elapsedSeconds: number): boolean {
    this.tokens = Math.min(this.capacity, this.tokens + elapsedSeconds * this.refillPerSecond);
    if (this.tokens < 1) return false;
    this.tokens -= 1;
    return true;
  }
}

export function retryAfterMs(missingTokens: number, refillPerSecond: number): number {
  if (refillPerSecond <= 0) throw new Error("refill rate must be positive");
  return Math.ceil((missingTokens / refillPerSecond) * 1000);
}

export class CircuitBreaker {
  private failures = 0;
  private openUntil = 0;

  recordFailure(nowMs: number): void {
    this.failures += 1;
    if (this.failures >= 5) this.openUntil = nowMs + 30_000;
  }

  isOpen(nowMs: number): boolean {
    return nowMs < this.openUntil;
  }
}

export function partitionFor(key: string, shardCount: number): number {
  if (shardCount < 1) throw new Error("shard count must be positive");
  const total = [...key].reduce((sum, character) => sum + character.charCodeAt(0), 0);
  return total % shardCount;
}
