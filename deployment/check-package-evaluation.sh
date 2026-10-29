#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Archive contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
set -eu
# Force real target derivations on the evaluator host. Any Cabal IFD fails
# even when a cached result happens to exist locally.
${NIX:-/nix/var/nix/profiles/default/bin/nix} eval --impure --option allow-import-from-derivation false --json --expr '
  let
    source = builtins.path {
      path = ./.;
      name = "archive-package-evaluation-source";
      filter = path: type: !(builtins.elem (builtins.baseNameOf path)
        [ ".git" ".wf" ".claude" ".direnv" "result" "dist-newstyle"
          "__pycache__" ".venv" ".store" "cabal.project.local" ]);
    };
    f = builtins.getFlake (builtins.unsafeDiscardStringContext ("path:" + toString source));
  in builtins.mapAttrs (system: p: {
    archive = p.archive.drvPath;
    deployment = p.deployment.drvPath;
    upgrade = p.upgrade.drvPath;
  }) f.packages
'
