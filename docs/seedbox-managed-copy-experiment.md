# Seedbox managed-copy experiment

Status: investigation contract. This document answers no open question and
authorizes no change to an archive or seedbox.

## Question

[ADR 0009](decisions/0009-copies-and-custody.md) settles that a seedbox is a
full managed git-annex copy and describes the intended acquisition flow:
finished content moves into the repository and is projected back to the
download client so seeding can continue. The remaining question is whether a
real torrent client tolerates that projection across force-recheck and restart.

This experiment supplies the evidence needed to answer that question. It uses
synthetic bytes and an isolated client profile. It does not use personal
archival data, tracker credentials, a production torrent, or an actual archive.

## Settled boundaries

- A managed seedbox copy is controlled by git-annex and belongs to one failure
  domain. A second pathname on the same storage does not add a copy
  ([INV-13](invariants.md#deletion-and-copies)).
- Before admission, the completed download is a source. After admission, the
  downloader-facing and media-server-facing names are projections.
- Moving content changes no object or record identity ([INV-6](invariants.md#records-and-claims)).
- No test step may change archival state. Only Door can do that
  ([INV-2](invariants.md#custody)). Because content-bearing Door admission is
  not implemented, the test repository is explicitly a disposable mechanism
  fixture, not an archive.
- The torrent client's resume database, application configuration, credentials,
  logs, and caches are outside archival state. Losing information found only in
  one of them is a defect to classify, not a reason to treat the live database
  as an archival record ([state model](state-model.md#the-ownership-test)).
- Host provisioning, package installation, service supervision, paths, ports,
  and secret delivery are program configuration outside this repository.

## Fixture

Use an isolated directory on one filesystem with:

```text
fixture/
  source/             initial downloader destination
  managed/            disposable git-annex repository
  torrent-view/       downloader-facing projection
  media-view/         read-only consumer projection
  peer/               local peer used to prove seeding
  client-profile/     isolated torrent-client state
  evidence/           command output and hashes; never an archive
```

Generate deterministic synthetic content large enough to exercise piece
boundaries. Create the torrent locally and use only loopback or an isolated
network namespace. Record the filesystem type and mount options. The client
profile must not be able to see production configuration or credentials.

Pin and record:

- git-annex, Git, torrent-client, and libtorrent versions;
- operating system and filesystem;
- torrent piece size, content size, and content digest;
- the exact client save path and projection target; and
- every command and exit status.

## Procedure

1. Download the complete fixture into `source/` through the isolated client.
   Force-recheck it, seed it to `peer/`, and record the baseline state and
   digest.
2. Stop the client cleanly. Preserve a rollback copy of its isolated profile.
3. Move the completed file into `managed/`, add it to git-annex in the ordinary
   locked form, and run `git annex fsck` for that key.
4. Put a symlink at the exact old downloader path that resolves to the managed
   annex content. Put a second read-only projection in `media-view/`.
5. Start the client. Force-recheck. Record whether it accepts the path, whether
   it tries to write through the symlink, its completion state, and the content
   digest.
6. Seed the whole object to an empty local peer. Verify the peer's bytes
   independently.
7. Stop and restart the client. Force-recheck again and repeat the local-peer
   transfer.
8. Exercise rename and location-change operations that the real client and
   organizer would use. No operation may mutate annex content in place.
9. Exercise the intended removal boundary: removing a projection must leave
   the managed key intact; attempting to drop the only managed copy must be
   refused by the declared copy policy.
10. Roll back by restoring the original source pathname and isolated client
    profile, then force-recheck once more. A failed experiment must not require
    reconstructing bytes from the torrent swarm.

Change one variable at a time. Test an ordinary hardlink and a copy as control
conditions, but neither control is evidence that the annex symlink works.

## Pass criteria

The annex-symlink condition passes only if all of these hold:

- initial force-recheck, restart, second force-recheck, and both peer transfers
  succeed without redownloading;
- content digest and annex key remain unchanged;
- the client performs no in-place mutation of annex content;
- deleting or renaming a projection does not delete or alter the managed copy;
- the client retains its category, tags, ratio state, save path, and completion
  state across restart;
- git-annex still verifies the key and reports the expected location; and
- the rollback procedure succeeds from local bytes alone.

Any write-through mutation, unexplained re-download, resume-state loss,
unverifiable key, or dependence on a production credential is a failure.

## Evidence to retain

The experiment report should contain:

- the pinned versions and environment facts above;
- a timestamped step/result table;
- before/after path, inode, link, digest, annex-key, and client-state evidence;
- local-peer transfer results;
- the rollback result; and
- a conclusion limited to the exact tested client/version/filesystem tuple.

Synthetic fixture bytes and isolated runtime state are disposable. The report
is not automatically evidence in an archive; admitting it as a deliberately
kept derived result would be a separate Door action.

## What this does not settle

This experiment does not decide:

- the staging-host proposal protocol;
- how Door verifies a remote key;
- policy grammar or failure-domain configuration;
- whether a media organizer may rename a projection;
- how pre-existing downloads are admitted in bulk; or
- which program realizes the seedbox on any provider.

If the symlink condition passes, implementation can proceed without a new
architectural noun or subsystem. If it fails, the mechanism stated in ADR 0009
needs a proposed superseding decision; code must not substitute a different
custody model silently.
