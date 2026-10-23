import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { SSEClientTransport } from '@modelcontextprotocol/sdk/client/sse.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const probe = process.argv.includes('--probe');
const diagnosticNoAuth = process.argv.includes('--diagnostic-no-auth');
let token = process.env.BAIDU_NETDISK_ACCESS_TOKEN?.trim();
const encryptedTokenPath = fileURLToPath(new URL('./token.dpapi', import.meta.url));
if (!token && existsSync(encryptedTokenPath) && !diagnosticNoAuth) {
  const decrypt = spawnSync('C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe', [
    '-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
    "$ErrorActionPreference = 'Stop'; Import-Module ($PSHOME + '\\Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1'); $s = [Console]::In.ReadToEnd().Trim() | ConvertTo-SecureString; $c = New-Object System.Management.Automation.PSCredential('baidu-mcp', $s); [Console]::Out.Write($c.GetNetworkCredential().Password)"
  ], { input: readFileSync(encryptedTokenPath, 'ascii').trim(), encoding: 'utf8', windowsHide: true, timeout: 15000 });
  if (decrypt.status !== 0 || !decrypt.stdout.trim()) {
    console.error('Could not decrypt Baidu credential for the current Windows account.');
    process.exit(2);
  }
  token = decrypt.stdout.trim();
}
if (!token && !probe && !diagnosticNoAuth) {
  console.error('Missing Baidu Access Token. Run Save-Token.ps1 first.');
  process.exit(2);
}

const url = new URL('https://mcp-pan.baidu.com/sse');
if (token) url.searchParams.set('access_token', token);
const remote = new SSEClientTransport(url);

try {
  if (probe) {
    const client = new Client({ name: 'baidu-netdisk-connection-check', version: '1.0.0' });
    try {
      await client.connect(remote, { timeout: 20000 });
      const result = await client.listTools({}, { timeout: 20000 });
      console.log(JSON.stringify({
        authenticatedTokenProvided: Boolean(token),
        server: client.getServerVersion(),
        toolsCount: result.tools.length,
        tools: result.tools.map(({ name }) => name)
      }, null, 2));
      if (token && result.tools.length === 0) process.exitCode = 3;
    } finally {
      await client.close();
    }
  } else {
    const local = new StdioServerTransport();
    let closing = false;
    const close = async () => {
      if (closing) return;
      closing = true;
      await Promise.allSettled([remote.close(), local.close()]);
    };
    const fail = () => {
      // SDK errors can contain a URL with the token. Never print the raw error.
      console.error('Baidu MCP transport failed. Check network and token, then restart the server.');
      process.exitCode = 1;
      void close();
    };
    remote.onmessage = message => { void local.send(message).catch(fail); };
    local.onmessage = message => { void remote.send(message).catch(fail); };
    remote.onerror = fail;
    local.onerror = fail;
    remote.onclose = () => { void close(); };
    local.onclose = () => { void close(); };
    process.once('SIGINT', () => { void close(); });
    process.once('SIGTERM', () => { void close(); });
    // Obtain the SSE endpoint before accepting local requests.
    await remote.start();
    await local.start();
  }
} catch {
  console.error('Baidu MCP connection failed. Check network and token. No credential was printed.');
  process.exitCode = 1;
  await remote.close().catch(() => {});
}
