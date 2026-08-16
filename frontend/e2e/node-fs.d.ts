// Ambient surface for node:fs (the repo has no @types/node; the e2e specs
// only write artifact files).
declare module 'node:fs' {
  export function mkdirSync(path: string, options?: { recursive?: boolean }): string | undefined;
  export function writeFileSync(path: string, data: string): void;
}
