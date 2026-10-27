/**
 * BidCraft Signer: the app on this computer that signs with a DSC token (signer/Program.cs). It answers only on this
 * computer and only this site. Called with fetch, not HttpClient: the app must never receive the BidCraft login
 * token that the HttpClient interceptor adds to every request.
 */
const SIGNER = 'http://localhost:47821';
export const SIGNER_DOWNLOAD = '/downloads/BidCraftSigner.exe';

export interface SignerCert {
  thumbprint: string; subject: string; issuer: string; validTo: string;
  certificate: string; chain: string[];          // base64 DER, for the signature to carry
}

async function call<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(SIGNER + path, body === undefined ? { cache: 'no-store' }
    : { method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { message?: string }).message || `BidCraft Signer refused (${res.status})`);
  return data as T;
}

/** The signing certificates of the tokens plugged in. Rejects when BidCraft Signer is not running here. */
export const signerCertificates = () => call<SignerCert[]>('/certificates');

/** The token's RSA signature of a SHA-256 hash, after the person confirms on this computer and enters the PIN. */
export async function signerSign(thumbprint: string, hash: string, document: string): Promise<string> {
  return (await call<{ signature: string }>('/sign', { thumbprint, hash, document })).signature;
}
