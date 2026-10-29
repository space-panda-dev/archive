# SPDX-FileCopyrightText: 2026 Archive contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# Generated from archive.cabal with cabal2nix; regenerate when it changes.
# src is supplied by flake.nix to retain the filtered Haskell source.
{ mkDerivation, aeson, base, bytestring, containers
, cryptohash-sha1, cryptohash-sha256, directory, filepath, lib
, network, process, QuickCheck, scientific, tasty, tasty-hunit
, tasty-quickcheck, text, time, transformers, unix, vector, zlib
, src
}:
mkDerivation {
  pname = "archive";
  version = "0.0.1.0";
  inherit src;
  isLibrary = true;
  isExecutable = true;
  enableSeparateDataOutput = true;
  libraryHaskellDepends = [
    aeson base bytestring containers cryptohash-sha1 cryptohash-sha256
    directory filepath process scientific text time transformers unix
    vector zlib
  ];
  executableHaskellDepends = [
    aeson base bytestring containers directory filepath network text
    unix
  ];
  testHaskellDepends = [
    aeson base bytestring containers cryptohash-sha256 directory
    filepath network process QuickCheck tasty tasty-hunit
    tasty-quickcheck text time unix vector
  ];
  license = lib.meta.getLicenseFromSpdxId "AGPL-3.0-or-later";
}
