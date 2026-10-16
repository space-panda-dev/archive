# 0038. Proposed placement configuration and protocol

Status: proposed; depends on ADR 0032 and proposed ADR 0037

**Blocked for the seedbox pending service-placement contracts.** The protocol below
uses an ordinary Git remote and target-side plaintext hashing. ADR 0016
classifies a hosted plaintext transfer as service disclosure, beyond the
private initial placement limited by ADR 0032. The operator selected
plaintext service placement with authentication at every served route.
[Proposed ADR 0039](0039-proposed-hosted-plaintext-placement.md) records that
direction. Do not implement or deploy this protocol for the seedbox before the
cross-custody decision, pre-transfer disclosure contract, and authentication
conformance tests are accepted.

ADR 0032 settles the location identity and additive placement intent, but leaves
the remote protocol and configuration format unimplemented. Proposed ADR 0037
sets the first intended scope: a managed copy of the whole Archive on
the seedbox, with the music publication folder kept separate. This proposal chooses
concrete defaults so implementation can begin after acceptance. It does not
authorize remote setup, a real transfer, or an update to the current Archive
specification.

## Configuration

The first implementation MUST read one operator-owned UTF-8 JSON file named by
the absolute `ARCHIVE_LOCATIONS_FILE` environment variable. It MUST fail if the
variable is missing, relative, unreadable, or names a file with group or other
read/write permission. The file is operational configuration; losing it must
not lose evidence. The operator MUST keep a recoverable copy separately.

The top-level object has exactly `version` and `locations`. `version` is the
integer `1`. Each location object has exactly the fields shown below. Unknown
fields, duplicate object keys, duplicate location ids, malformed values, and
non-canonical absolute paths MUST be refused. The JSON bytes MUST be RFC 8785
canonicalized before their SHA-256 configuration fingerprint is reported.

```json
{
  "version": 1,
  "locations": [
    {
      "id": "urn:uuid:00000000-0000-4000-8000-000000000000",
      "endpoint": {
        "host": "seedbox.example.invalid",
        "port": 22,
        "user": "archive-copy"
      },
      "host_key": {
        "type": "ssh-ed25519",
        "key": "BASE64_PUBLIC_KEY",
        "fingerprint": "SHA256:OPERATOR_VERIFIED_FINGERPRINT"
      },
      "control_identity_file": "/absolute/path/to/control-key",
      "sync_identity_file": "/absolute/path/to/git-annex-sync-key",
      "annex_uuid": "00000000-0000-4000-8000-000000000001",
      "managed_root": "/absolute/path/to/archive",
      "failure_domain": "operator-assigned-stable-domain-id"
    }
  ]
}
```

The example values are placeholders and MUST NOT be used. The `id` is an
operator-allocated UUID URN and remains stable across endpoint changes. The
host key is the complete public key material; the fingerprint MUST match that
key. The operator MUST obtain and verify the key and fingerprint through an
authenticated channel independent of the SSH connection being configured.
The two identity files identify distinct public-key credentials: the control
key is forced to the read-only placement helper and the sync key is forced to
the restricted Git/git-annex capability. Archive MUST NOT read, copy, or store
private-key bytes. If either credential is unavailable without an interactive
prompt, placement fails. The target MUST install separate authorized-key
entries so one key cannot select the other's capability.

The endpoint host MUST be a DNS name or IP literal, the port an integer in
1–65535, and the user a non-empty restricted ASCII account name. The annex
UUID MUST match the target repository's existing git-annex identity. The
managed root MUST be an absolute path with no `.` or `..` components. A
location binding is valid only when its SSH host key, annex UUID, and root
match the observed target. A changed endpoint may retain the same location id
only if those identities still match. A changed host key or annex UUID blocks
placement pending explicit operator reconciliation and a new bound plan.

The failure-domain id is a stable operator assertion, not discovered from SSH.
The operator MUST establish whether the seedbox, its provider, and people with
server-side access can read transferred plaintext. This custody and disclosure
classification is required before any content transfer. If the destination is
outside operator custody, the grant and disclosure record required by ADR 0016
must already exist.

## SSH trust and command boundary

The local SSH executable MUST be selected by the absolute
`ARCHIVE_SSH_EXECUTABLE` environment variable and resolved before use. The
implementation MUST construct an argv vector and spawn it directly; it MUST
NOT invoke a local shell. It MUST ignore ambient SSH config, proxy commands,
agent forwarding, port forwarding, local commands, and password fallback.

For the read-only control channel, the invocation MUST use a temporary
mode-0600 `known_hosts` file containing only the configured key under the
stable alias `archive-<location UUID without punctuation>`. It MUST set
`-F /dev/null`, `-T`, `BatchMode=yes`, `StrictHostKeyChecking=yes`, that
`UserKnownHostsFile`, `GlobalKnownHostsFile=/dev/null`,
`UpdateHostKeys=no`, `VerifyHostKeyDNS=no`, `HostKeyAlias=<stable alias>`,
`PreferredAuthentications=publickey`, `PasswordAuthentication=no`,
`KbdInteractiveAuthentication=no`, `IdentitiesOnly=yes`,
`ClearAllForwardings=yes`, `ForwardAgent=no`, `PermitLocalCommand=no`,
`ProxyCommand=none`, and `ProxyJump=none`. The configured identity file for the
selected capability, endpoint host, port, and user are passed as separate argv
values. The
destination argument is the stable alias, with `HostName`, `Port`, and `User`
set by explicit command-line options. No remote command argument is supplied.

The target account MUST install a forced command for the control key that
starts one fixed `archive-placement-control-v1` executable, with PTY and all
forwarding disabled. The helper MUST ignore `SSH_ORIGINAL_COMMAND`, accept no
command-line arguments, and read only the protocol below from standard input.
The sync credential used by Git and git-annex MUST be restricted to the
target's managed repository and the approved Git/git-annex SSH capability;
it MUST NOT allow a general interactive shell. The exact Git-annex remote
commands used by the selected git-annex version MUST be established by the
synthetic probe and pinned in the implementation. If the host cannot confine
the sync credential to this repository and capability, it is unsupported.

The local sync path MUST set `GIT_SSH` to the absolute path of the packaged
`archive-ssh-proxy` and set `GIT_ANNEX_USE_GIT_SSH=1` for the git-annex child.
The proxy receives the SSH argv requested by Git or git-annex, accepts only the
destination alias derived from the configured location and the tested Git /
git-annex remote-command grammar for the pinned executable versions, then
spawns the configured SSH executable with the same host-key and forwarding
restrictions as the control channel. It MUST reject an unrecognized option,
destination, command, or repository path. It MUST NOT use `GIT_SSH_COMMAND`,
which is interpreted as a command string. The local remote name is the fixed
value `archive-` followed by the location UUID's 32 lowercase hex digits; its
configured URL, annex UUID, user, endpoint, and managed root MUST match the
location file before transfer.

The forced-command pattern and strict host-key controls follow OpenSSH's
`authorized_keys(5)` and `ssh_config(5)` contracts. The sync capability uses
git-annex's documented restricted `git-annex-shell`; it does not grant a
general shell. See [OpenSSH authorized_keys](https://man.openbsd.org/sshd.8#AUTHORIZED_KEYS_FILE_FORMAT),
[OpenSSH ssh_config](https://man.openbsd.org/ssh_config.5), and
[git-annex-shell](https://git-annex.branchable.com/git-annex-shell/).

## Control protocol

The control channel carries one UTF-8 JSON object per line over SSH stdin and
one UTF-8 JSON reply per line over stdout. Each request and reply is a closed
object with no duplicate keys, no unknown fields, no non-finite numbers, and
no trailing bytes. A request is limited to 1 MiB. Standard output contains
only the reply; diagnostics go to standard error. Each SSH process handles one
request and exits. A transport failure, malformed reply, wrong nonce, or
wrong location identity is an incomplete result.

Every request has exactly `version`, `nonce`, `operation`, and
`location_id`. `version` is `1`; `nonce` is a fresh 256-bit random value
encoded as lowercase hexadecimal; and `location_id` is the configured UUID
URN. Operations are:

- `probe`, with no other fields. The reply reports `status`, `nonce`,
  `location_id`, `archive_id` or null, `annex_uuid`, `managed_root`, and the
  target record-ref heads.
- `verify`, with `archive_id`, `snapshot_id`, `manifest_sha256`, and one
  `content` object containing exactly `annex_key`, `sha256`, and `size`. The
  reply reports those bindings plus `status` equal to `present`, `missing`, or
  `mismatch`. `present` means the helper independently read and hashed the
  bytes inside the managed root and matched both digest and size. It does not
  mean only that git-annex reports a location entry.

The reply MUST echo the request nonce and all identity bindings. A successful
`probe` MUST report the configured annex UUID and root and MUST NOT mutate Git,
git-annex, or the managed root. A `verify` MUST be read-only. It MUST refuse
symlinked or escaping paths and MUST derive the content path from the validated
annex key using git-annex's location mechanism, never from a request path.
The helper MUST NOT accept a request that asks it to execute a command, write
content, remove content, change refs, or publish a reference.

The separate sync path performs Git metadata and content transport using
Git and git-annex against the configured target repository. The control
protocol only proves target identity and content fixity. The use of
git-annex's SSH transport and location accounting preserves ADR 0032; the
synthetic probe remains a prerequisite because some git-annex verification
commands write operational metadata.

For content, apply MUST invoke the configured git-annex executable once per
missing manifest key with the fixed argument vector `copy`,
`--key=<annex_key>`, `--to=archive-<location UUID hex>`, `--json`, and
`--json-error-messages`. It MUST NOT use `--all`, since that can select keys
from Git-annex history outside the pinned, non-destroyed manifest. Git record
refs are updated through the configured Git executable with explicit refspecs
from the bound source snapshot; force updates and ref deletion are refused.
The remote's identity and current ref values are checked before any write.
The exact Git-annex shell argv remains version-pinned by the required synthetic
probe, which checks the `archive-ssh-proxy` boundary end to end.

The git-annex SSH remote is the documented restricted `git-annex-shell`
transport. Its command must remain within the configured repository, and the
remote must report the configured annex UUID. The direct `--key` operation is
documented by [git-annex copy](https://git-annex.branchable.com/git-annex-copy/);
the probe pins the actual command behavior and branch effects for the chosen
version. git-annex's use of `GIT_SSH` requires `GIT_ANNEX_USE_GIT_SSH=1`;
the proxy must accept only the optional leading `-n` documented for this
interface, in addition to the pinned invocation grammar. See
[git-annex environment variables](https://git-annex.branchable.com/git-annex/).

## Whole-Archive inventory and completion

The source snapshot MUST bind the Archive ID, specification version, active
record ref and commit, and the digest of a deterministic copy manifest. The
manifest contains every distinct content identity required by artifact
records in the complete committed history from the proven archive root to the
pinned commit, including retired artifacts whose content has not been
destroyed. It excludes content whose destruction is authoritative. The source
history must be complete under the current specification; an incomplete read,
unresolved artifact content identity, missing size, or missing annex key
blocks the copy.

Manifest entries are sorted by annex key and contain exactly `annex_key`,
`sha256`, and `size`. The manifest digest is SHA-256 over RFC 8785 canonical
JSON. Duplicate keys with conflicting digest or size are an error. The source
must hash each available source object and check its recorded size before it
can be used. Target content is verified independently using the control
`verify` operation after transfer.

The target MUST retain the source Archive ID and the source's complete
record-bearing Git ancestry. It has its own git-annex UUID, which MUST equal
the configured target UUID and MUST differ from the source repository UUID.
The target's annex location log may therefore differ from the source. The
copy is complete only when the selected record ref resolves to the pinned
source commit, the full root-to-commit history is present, and every manifest
entry verifies at the target. The annex branch is checked for the expected
source and target location identities; byte verification does not depend on
that branch alone.

The copy includes Archive records, installed contracts and contexts reachable
from the committed Archive state, and all required annex content. The music
publication folder outside `content/` is not part of the managed root, is not
copied by this operation, and earns no managed-copy credit. Publication into
that folder remains a distinct approved transition.

## Retry, interruption, and non-effects

The implementation MUST pin and recheck one committed source snapshot for the
full operation. Before sending a key, it MUST call `verify` at the target. A
verified key is skipped. A missing key may be copied using git-annex and is
verified again afterward. A mismatched key, changed target identity, changed
source snapshot, stale plan, or incomplete verification blocks the operation;
the helper MUST NOT overwrite or delete mismatched target bytes.

After interruption, the operation remains incomplete. Retry repeats the
read-only identity probe and per-key verification, then transfers only keys
still missing. A matching Git ref and complete verified manifest are a
successful no-op. The no-op path MUST write no record commit and no
git-annex location entry. Any git-annex writes on a new successful transfer
must be limited to the expected location bookkeeping. No path in this
protocol drops a source or destination copy, publishes content, creates a
public reference, or removes data.

Before implementation is enabled, a synthetic disposable-repository probe
MUST exercise the exact Git, git-annex, SSH client, remote helper, and server
versions used. It MUST verify argv boundaries, host-key substitution refusal,
repository identity substitution refusal, option and shell injection refusal,
path confinement, complete manifest accounting, source and destination hash
mismatch, interrupted transfer, exact retry, and no-op quietness. The probe
MUST also confirm every selected git-annex command's git-annex branch effects.
No probe or conformance test may use real Archive bytes or change the seedbox.

## Decisions reserved for the operator

This proposal chooses the wire and configuration formats, but it cannot supply
these values or facts:

- the seedbox endpoint, remote user, absolute managed root, annex UUID, and
  independently verified SSH host key and fingerprint;
- the stable location UUID and failure-domain assignment;
- the separate control and sync authentication keys and their availability for
  unattended apply;
- whether the seedbox and its provider can read plaintext, and the resulting custody
  classification and disclosure authorization; and
- whether the operator is willing to install the restricted control helper and
  sync-key restrictions in the seedbox account.

Until those facts are established, configuration for the seedbox is incomplete and
the operation must fail closed. No remote write, setup, publication, or content
transfer follows from acceptance of this proposed record alone.
