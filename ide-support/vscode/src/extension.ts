import * as readline from 'readline';
import * as vscode from 'vscode';
import {
    LanguageClient,
    LanguageClientOptions,
    ServerOptions,
    TransportKind,
} from 'vscode-languageclient/node';

/**
 * The runtime half of the extension: a thin LSP client.
 *
 * Everything language-specific lives in the server; this file only decides how
 * to start it, which documents to send, and where its logs go. The server's
 * stderr is forwarded to the output channel, so a crash or a stack trace is
 * visible without a debugger.
 */
let client: LanguageClient | undefined;

/** Native server log levels, mapped onto the output channel's levels. */
const LOG_LEVELS: Record<string, 'trace' | 'debug' | 'info' | 'warn' | 'error'> = {
    DEBUG: 'debug',
    INFO: 'info',
    WARNING: 'warn',
    ERROR: 'error',
};

const LEVEL_PREFIX = /^\[yian-lsp (DEBUG|INFO|WARNING|ERROR)\] /;

function serverConfig(): { command: string; args: string[] } {
    const config = vscode.workspace.getConfiguration('yian.languageServer');
    const args = [...config.get<string[]>('args', [])];
    const level = config.get<string>('logLevel', '');
    if (level !== '') {
        args.push('--log-level', level);
    }
    const logFile = config.get<string>('logFile', '');
    if (logFile !== '') {
        // Settings are plain strings, so the editor does not expand variables
        // for us; the workspace folder is the only one worth having, because a
        // log written next to the project is easy to find and easy to delete.
        args.push('--log-file', expandWorkspaceFolder(logFile));
    }
    return {
        command: config.get<string>('command', 'yian-lsp'),
        args,
    };
}

function expandWorkspaceFolder(value: string): string {
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (folder === undefined) {
        return value;
    }
    return value.replaceAll('${workspaceFolder}', folder.uri.fsPath);
}

/**
 * Report which server answered, and whether it belongs to this extension.
 *
 * The extension and the server are released together from one repository, so a
 * mismatch means PATH selected an older (or different) native installation.
 * That is a warning with a fix, not an error: features supported by the running
 * server remain available.
 */
function checkServerVersion(
    context: vscode.ExtensionContext,
    outputChannel: vscode.LogOutputChannel,
    started: LanguageClient,
): void {
    const expected = context.extension.packageJSON.version as string;
    const info = started.initializeResult?.serverInfo;
    if (info === undefined) {
        outputChannel.warn(
            `the language server did not report its version; expected ${expected}. ` +
                `Check that the native yian-lsp installed by scripts/install_lsp.sh is on PATH.`,
        );
        return;
    }
    if (info.version === expected) {
        outputChannel.info(`connected to ${info.name} ${info.version}`);
        return;
    }
    outputChannel.warn(
        `the language server reported ${info.name} ${info.version}, but this ` +
            `extension is ${expected}; the two are released together`,
    );
    void vscode.window.showWarningMessage(
        `YIAN: language server ${info.version} does not match extension ${expected}. ` +
        `Rebuild and install yian-lsp with scripts/install_lsp.sh, then restart VS Code.`,
    );
}

/**
 * Forward the server's stderr to *outputChannel* at the level it logged.
 *
 * `vscode-languageclient`'s default handler reports every stderr line as an
 * error, which turns ordinary progress lines into `[error]` noise and buries the
 * real failures (the server writes all of its logging to stderr, because stdout
 * is the JSON-RPC channel). The server prefixes each record with its level, so
 * the level is read back from the line; continuation lines keep the level of
 * the record they belong to.
 */
function pipeServerLog(input: NodeJS.ReadableStream, outputChannel: vscode.LogOutputChannel): void {
    // Unprefixed stderr output signals an error; normal server messages carry a level.
    let level = 'ERROR';
    readline
        .createInterface({ input, crlfDelay: Infinity, terminal: false, historySize: 0 })
        .on('line', (line: string) => {
            if (line === '') {
                return;
            }
            const match = LEVEL_PREFIX.exec(line);
            if (match !== null && match[1] in LOG_LEVELS) {
                level = match[1];
            }
            switch (LOG_LEVELS[level]) {
                case 'trace':
                    outputChannel.trace(line);
                    break;
                case 'debug':
                    outputChannel.debug(line);
                    break;
                case 'warn':
                    outputChannel.warn(line);
                    break;
                case 'error':
                    outputChannel.error(line);
                    break;
                default:
                    outputChannel.info(line);
            }
        });
}

export function activate(context: vscode.ExtensionContext): void {
    const { command, args } = serverConfig();
    const outputChannel = vscode.window.createOutputChannel('YIAN Language Server', {
        log: true,
    });
    context.subscriptions.push(outputChannel);

    // The extension owns the stdio process; closing the client closes its
    // stdin and lets the server exit.
    const serverOptions: ServerOptions = {
        command,
        args,
        transport: TransportKind.stdio,
    };

    const clientOptions: LanguageClientOptions = {
        documentSelector: [{ scheme: 'file', language: 'yian' }],
        outputChannel,
        // With stdio the server's stdout carries JSON-RPC frames and is read by
        // the client's message reader, so only stderr is ever piped here.
        stdioOptions: {
            stdout: (input, channel) => pipeServerLog(input, channel),
            stderr: (input, channel) => pipeServerLog(input, channel),
        },
    };

    const languageClient = new LanguageClient(
        'yian',
        'YIAN Language Server',
        serverOptions,
        clientOptions,
    );
    client = languageClient;

    // A missing `yian-lsp` (not installed, or not on PATH) must be an actionable
    // message rather than a silently dead extension.
    void languageClient
        .start()
        .then(() => {
            checkServerVersion(context, outputChannel, languageClient);
        })
        .catch((error: unknown) => {
            const reason = error instanceof Error ? error.message : String(error);
            outputChannel.error(`could not start '${command}': ${reason}`);
            void vscode.window.showErrorMessage(
                `YIAN: could not start '${command}'. Install the language server ` +
                    `with scripts/install_lsp.sh and ensure its bin directory is on PATH, or set ` +
                    `"yian.languageServer.command" to the installed binary's absolute path.`,
            );
        });
}

export async function deactivate(): Promise<void> {
    if (client === undefined) {
        return;
    }
    const stopped = client;
    client = undefined;
    try {
        await stopped.stop();
    } catch {
        // The server never started (or is already gone): there is nothing left
        // to stop, and a rejected deactivate would only add noise at shutdown.
    }
}
