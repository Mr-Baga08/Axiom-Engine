export type EmployeeUID = string;

export interface MockUser {
  passwordHash: string;
  registeredAt: string;
  uid: EmployeeUID;
  username: string;
}

declare global {
  // eslint-disable-next-line no-var
  var __mockUsers: MockUser[] | undefined;
  // eslint-disable-next-line no-var
  var __mockEmployeeCounter: number | undefined;
}

if (!globalThis.__mockUsers) {
  globalThis.__mockUsers = [];
}
export const mockUsers: MockUser[] = globalThis.__mockUsers;

export function getCounter(): number {
  if (globalThis.__mockEmployeeCounter == null) {
    globalThis.__mockEmployeeCounter = 1;
  }
  return globalThis.__mockEmployeeCounter;
}

export function incrementCounter(): void {
  globalThis.__mockEmployeeCounter = getCounter() + 1;
}

export function generateUID(username: string): EmployeeUID {
  const yy = String(new Date().getFullYear()).slice(-2);
  const parts = username.trim().split(/\s+/);
  const toPascal = (value: string) =>
    value.charAt(0).toUpperCase() + value.slice(1).toLowerCase();
  const nameSegment =
    parts.length >= 2
      ? `${toPascal(parts[0])}${toPascal(parts[1])}`
      : toPascal(parts[0] ?? username.trim());
  const seq = String(getCounter()).padStart(4, "0");
  return `${yy}-${nameSegment}-${seq}`;
}
