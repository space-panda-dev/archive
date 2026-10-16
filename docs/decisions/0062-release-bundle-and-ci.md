# 0062. Releases carry an image and a bundle, and CI checks both

Status: accepted

A client of Door's service, the relay, and World (ADRs 0058–0060) needs to
test against a running Archive in its own CI. That must not need the tailnet
or anyone's archive, and the client must learn from its own CI, not from a
person, that a release broke it. Archive itself had no CI: `make test` ran
only the Haskell suites, and the World, relay, shadow, and migration suites
ran by hand.

The operator decided on 2026-10-15 that each Archive release publishes a
container image by digest with the contract bundles, the projection shapes,
and the fixture builder beside it, and that CI on GitHub runs every suite, the
conformance check, and the image build.

## Decision

**Each release has an image, published by digest.** `nix build .#image`
builds, from `flake.nix`, a container image holding Door, the archive command,
World, the relay, the fixture builder, git, and git-annex. The release
workflow pushes it to the GitHub container registry and records its digest in
the release. Clients pull it by digest, never by tag.

By default the image builds the fixture archive in `/work/fixture` and serves
it with `fixture/serve.py`:

- `door serve`;
- a World store;
- a relay on `127.0.0.1:8450` with role `scratch` and commits on, which
  accepts the login `ci@example.invalid` as the fixture's operator.

The relay listens on loopback only (ADR 0056). A client therefore runs the
container in the host's network namespace (`docker run --network host`) and
sends the `Tailscale-User-Login` header itself.

**Each release has a bundle beside the image.** `nix build .#bundle` makes
`archive-release-bundle.tar.gz`, which holds:

- the specification with its contract catalogs and bundles (ADR 0057);
- World's projection descriptions (ADR 0059);
- Door's service schema (ADR 0058) and the relay's OpenAPI description
  (ADR 0060);
- the fixture plan, its content, and the builder;
- the licences.

It is attached to the release with its SHA-256 and the image digest. A client
generates code and checks its answers against the bundle from the same
release as the image it tests against.

**CI runs everything on every push.** `.github/workflows/ci.yml` runs:

- `make check`;
- `nix flake check`, whose checks are:
  - the Haskell package, whose build runs `cabal test all`;
  - every Python suite (World with its conformance tests, relay, shadow,
    migration, and fixture) against that build, failing rather than skipping
    when Door is missing;
- the image build;
- a start of the image the way a client's CI would, which must answer
  `/health` with role `scratch`.

`make test` runs the same Python suites against the cabal build.

**Releases are tags.** Pushing a `v*` tag runs `.github/workflows/release.yml`,
which repeats the checks before publishing.

## Consequences

- A client's CI needs Docker on Linux and the release's digest; nothing else.
- The image's version is the release's. A client testing a newer image
  against older generated code sees the failure in its own CI, which is the
  point.
- CI cannot run until the repository has a GitHub remote. The image can be
  built only on Linux, or with a Linux builder.

## Alternatives considered

- **Bind the relay to all interfaces in the image.** It would weaken ADR
  0056's loopback rule for every deployment that copies the image's
  configuration.
- **Publish only source and let clients build.** A client would need GHC,
  Nix, or both, and might build something other than the release.
- **Separate images for Door, World, and the relay.** They are released and
  tested together, and Door and the relay must match (ADR 0058).
