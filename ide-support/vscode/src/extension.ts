import * as readline from 'readline';
import * as vscode from 'vscode';
import {
    LanguageClient,
    LanguageClientOptions,
    ServerOptions,
    TransportKind,
} from 'vscode-languageclient/node';

/**
 * The runtime half of the extension (plan §5.7, §5.10): a thin LSP client.
 *
 * Everything language-specific lives in the server; this file only decides how
 * to start it, which documents to send, and where its logs go. The server's
 * stderr is forwarded to the output channel, so a crash or a stack trace is
 * visible without a debugger.
 */
let client: LanguageClient | undefined;

/** Python logging levels, mapped onto the output channel's levels. */
const LOG_LEVELS: Record<string, 'trace' | 'debug' | 'info' | 'warn' | 'error'> = {
    TRACE: 'trace',
    DEBUG: 'debug',
    INFO: 'info',
    WARNING: 'warn',
    ERROR: 'error',
    CRITICAL: 'error',
};

const LEVEL_PREFIX = /^\S+ \S+ (TRACE|DEBUG|INFO|WARNING|ERROR|CRITICAL) /;

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
 * mismatch means the interpreter that started the server has an older (or a
 * different) `yian` install — the usual cause is a second Python environment.
 * That is a warning with a fix, not an error: the features that do exist still
 * work (plan §6.1, §7 P8).
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
                `Update the server with: pip install -e '.[lsp]'`,
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
            `Update the server in the interpreter that runs it: pip install -e '.[lsp]'`,
    );
}

/**
 * Forward the server's stderr to *outputChannel* at the level it logged.
 *
 * `vscode-languageclient`'s default handler reports every stderr line as an
 * error, which turns ordinary progress lines into `[error]` noise and buries the
 * real failures (the server writes all of its logging to stderr, because stdout
 * is the JSON-RPC channel). The server prefixes each record with its level, so
 * the level is read back from the line; continuation lines — a Python traceback,
 * for example — keep the level of the record they belong to.
 */
function pipeServerLog(input: NodeJS.ReadableStream, outputChannel: vscode.LogOutputChannel): void {
    // Until the server logs a level of its own, stderr is not progress: it is a
    // crash dump (a failed import, an argument error) and deserves attention.
    let level = 'WARN';
    readline
        .createInterface({ input, crlfDelay: Infinity, terminal: false, historySize: 0 })
        .on('line', (line: string) => {
            if (line === '') {
                return;
            }
            const match = LEVEL_PREFIX.exec(line);
            if (match !== null && match[1] in LOG_LEVELS) {
                level = match[1];
            } else if (line.startsWith('Traceback (most recent call last)')) {
                // An uncaught exception: its frames and message follow and have
                // no prefix of their own.
                level = 'ERROR';
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

    // stdio transport (plan §5.10): the extension owns the process, and the
    // server exits when the client closes its stdin, so closing the window does
    // not leave an orphan behind.
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
                    `(pip install '.[lsp]') or set "yian.languageServer.command" to the ` +
                    `interpreter that has it, with "yian.languageServer.args": ["-m", "lsp"].`,
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
