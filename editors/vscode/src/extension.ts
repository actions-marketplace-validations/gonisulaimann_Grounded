/* VS Code thin client for the grounded LSP server.
 *
 * The extension never bundles or downloads grounded on its own. Server
 * resolution is explicit and visible:
 *
 * 1. `grounded.serverPath` setting (exact binary, e.g. a venv path).
 * 2. `grounded` on PATH.
 * 3. `uvx --from grounded-lint grounded lsp`, only with the user's
 *    consent (per-session button or persistent `grounded.allowUvx`).
 *    uvx downloads the package on first use; nothing is fetched silently.
 * 4. Otherwise an error pointing at the install guide. No writes, no
 *    installs, no network calls from this extension itself.
 */

import * as vscode from 'vscode';
import * as cp from 'child_process';
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
} from 'vscode-languageclient/node';

const DOCUMENT_SELECTOR = [
  'python',
  'javascript',
  'typescript',
  'javascriptreact',
  'typescriptreact',
  'go',
  'c',
];

const INSTALL_GUIDE = 'https://grounded-lint.readthedocs.io/en/latest/installation/';

let client: LanguageClient | undefined;

function commandExists(cmd: string): Promise<boolean> {
  return new Promise((resolve) => {
    const probe =
      process.platform === 'win32' ? `where ${cmd} 2>NUL` : `command -v ${cmd}`;
    cp.exec(probe, (err) => resolve(!err));
  });
}

interface ServerCommand {
  command: string;
  args: string[];
}

async function resolveServer(
  context: vscode.ExtensionContext
): Promise<ServerCommand | undefined> {
  const cfg = vscode.workspace.getConfiguration('grounded');
  const explicit = (cfg.get<string>('serverPath') || '').trim();
  if (explicit) {
    return { command: explicit, args: ['lsp'] };
  }
  if (await commandExists('grounded')) {
    return { command: 'grounded', args: ['lsp'] };
  }
  if (await commandExists('uvx')) {
    if (cfg.get<boolean>('allowUvx') === true) {
      return { command: 'uvx', args: ['--from', 'grounded-lint', 'grounded', 'lsp'] };
    }
    const pick = await vscode.window.showInformationMessage(
      'Grounded is not on PATH. Run the language server through uvx ' +
        '(downloads grounded-lint on first use, cached afterwards)?',
      'Just this session',
      'Always use uvx',
      'Open install guide'
    );
    if (pick === 'Just this session') {
      return { command: 'uvx', args: ['--from', 'grounded-lint', 'grounded', 'lsp'] };
    }
    if (pick === 'Always use uvx') {
      await cfg.update('allowUvx', true, vscode.ConfigurationTarget.Global);
      return { command: 'uvx', args: ['--from', 'grounded-lint', 'grounded', 'lsp'] };
    }
    if (pick === 'Open install guide') {
      await vscode.env.openExternal(vscode.Uri.parse(INSTALL_GUIDE));
    }
    return undefined;
  }
  await vscode.window.showErrorMessage(
    'Grounded server not found. Install it (`pip install grounded-lint`, ' +
      'Python 3.10+) or set `grounded.serverPath` to the binary, then run ' +
      '`Grounded: Restart Server`.',
    'Open install guide'
  ).then((pick) => {
    if (pick === 'Open install guide') {
      void vscode.env.openExternal(vscode.Uri.parse(INSTALL_GUIDE));
    }
  });
  return undefined;
}

async function startClient(context: vscode.ExtensionContext): Promise<void> {
  await stopClient();
  const server = await resolveServer(context);
  if (!server) {
    return;
  }
  const folder = vscode.workspace.workspaceFolders?.[0];
  const serverOptions: ServerOptions = {
    command: server.command,
    args: server.args,
    options: folder ? { cwd: folder.uri.fsPath } : undefined,
  };
  const clientOptions: LanguageClientOptions = {
    documentSelector: DOCUMENT_SELECTOR.map((language) => ({
      scheme: 'file',
      language,
    })),
    outputChannelName: 'Grounded',
  };
  client = new LanguageClient(
    'grounded',
    'Grounded Integrity Firewall',
    serverOptions,
    clientOptions
  );
  context.subscriptions.push(client);
  await client.start();
}

async function stopClient(): Promise<void> {
  const running = client;
  client = undefined;
  if (running) {
    await running.stop();
  }
}

export async function activate(
  context: vscode.ExtensionContext
): Promise<void> {
  context.subscriptions.push(
    vscode.commands.registerCommand('grounded.restartServer', () => startClient(context))
  );
  await startClient(context);
}

export function deactivate(): Thenable<void> | undefined {
  // The client is disposed through subscriptions on deactivate.
  return undefined;
}
