export interface E2EFixtureIdentity {
  fixture_id: string;
  reader: string;
  neso_network_policy: string;
  neso_transport_attempts: number;
}

export const E2E_API_IDENTITY_URL: string;
export const EXPECTED_FIXTURE_IDENTITY: Readonly<E2EFixtureIdentity>;

export function assertFixtureIdentity(payload: unknown): E2EFixtureIdentity;
export function verifyE2EFixtureIdentity(fetchImpl?: typeof fetch, timeoutMs?: number): Promise<E2EFixtureIdentity>;
export function assertPortAvailable(port: number, host?: string): Promise<void>;
