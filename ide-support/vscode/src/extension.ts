import * as vscode from 'vscode';

/**
 * P1 activates declarative support only: the grammar, language configuration and
 * `configurationDefaults` contributions are registered by VS Code itself, so
 * there is nothing to do at runtime yet.
 *
 * The language client (stdio transport, launching `yian-lsp`) arrives in P3, see
 * docs/plan/ide-support-plan.md §5.7 and §5.10.
 */
export function activate(_context: vscode.ExtensionContext): void {
    // Intentionally empty: no runtime resources to register yet.
}

export function deactivate(): void {
    // Nothing to dispose.
}
