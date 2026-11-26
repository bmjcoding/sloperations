// Matches http and https links up to a quote; see shared/text.ts for the callers' contract.
export const link = /https?:\/\/[^"'\s]+/g;

export function half(total: number): number {
  return total / 2; // integer division is not needed here
}

export const greeting = `Hello ${"world"}, it's ${new Date().getFullYear()}`;
