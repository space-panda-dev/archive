{-# LANGUAGE OverloadedStrings #-}

-- | Known-answer vectors for portable identifiers. Allocated object ids are
-- written into records, so a change to the UUIDv5 digest or its formatting
-- would silently fork identities; these pin RFC 9562's own example and the
-- archive's namespace-NUL-name spelling (ADR 0021), each checked against
-- Python's @uuid.uuid5@ and the hand-rolled SHA-1 Door used before.
--
-- Each check is a (label, passed, detail) row; Main asserts them.
module IdentityVectors (identityVectorChecks) where

import Archive.Door.Identity (parseIdentityName, parseIdentityNamespace, uuidV5, uuidV5Name)
import qualified Data.Text as Text
import Data.Text (Text)

type Check = (String, Bool, String)

identityVectorChecks :: [Check]
identityVectorChecks =
  check "UUIDv5 matches RFC 9562's DNS example"
    (uuidV5Name "6ba7b810-9dad-11d1-80b4-00c04fd430c8" "www.example.com")
    "urn:uuid:2ed6657d-e927-568b-95e1-2665a8aea6a2"
  : [ check ("UUIDv5 allocates the pinned id for " <> Text.unpack namespace <> " " <> Text.unpack name)
        (allocate archive namespace name) expected
    | (archive, namespace, name, expected) <- allocations ]
  where
    check label actual expected =
      (label, actual == Right expected, "got " <> show actual <> ", want " <> Text.unpack expected)
    allocate archive namespace name = do
      identityNamespace <- parseIdentityNamespace namespace
      identityName <- parseIdentityName name
      uuidV5 archive identityNamespace identityName

-- | The last name makes the hashed message longer than one 64-byte SHA-1
-- block, so padding across blocks is pinned too.
allocations :: [(Text, Text, Text, Text)]
allocations =
  [ ("6ba7b810-9dad-11d1-80b4-00c04fd430c8", "archive.core/entity", "person"
    , "urn:uuid:eb4abe4d-1568-5ef9-88cf-b729464e0f15")
  , ("00000000-0000-4000-8000-000000000001", "archive.core/entity", "index-identity-target"
    , "urn:uuid:b1509bdc-e02f-538c-a2b8-168ce67f48ce")
  , ("2c1a8f3e-7b4d-4e6a-9f0c-1d2e3f405162", "a", "B"
    , "urn:uuid:da002a06-edfd-5cce-a642-b87fb8538a42")
  , ("00000000-0000-4000-8000-000000000001", "archive.core/entity"
    , "x-very-long-name-that-pushes-the-message-past-one-sha1-block-of-sixty-four-bytes_ok"
    , "urn:uuid:068bf0cf-6817-5637-82fa-c951f40fdff9")
  ]
