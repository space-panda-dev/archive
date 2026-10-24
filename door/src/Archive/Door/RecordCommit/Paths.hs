{-# LANGUAGE OverloadedStrings #-}

-- | The archive paths Door's writer may create, and nothing else.
--
-- Each grammar is checked at the last boundary before any filesystem access:
-- digest-addressed records, D-1 content locations, and the specification
-- files an upgrade writes (ADR 0053, ADR 0068).
module Archive.Door.RecordCommit.Paths
  ( ArchiveFile (..)
  , ContentLink (..)
  , RecordLocation (..)
  , manifestFilePath
  , uniqueSorted
  , validateArchiveFilePath
  , validateContentLink
  , validateContentPath
  , validateRecordPath
  ) where

import Archive.Door.Layout
  ( contentRelativePath
  , digestText
  , specVersion
  , parseContentRelativePath
  , parseRecordRelativePath
  , parseSha256Digest
  , recordDirectoryChain
  , recordRelativePath
  )
import qualified Data.ByteString.Lazy as LazyByteString
import Data.List (sort)
import Data.Text (Text)
import qualified Data.Text as Text
import System.FilePath (isAbsolute, splitDirectories)

-- | A git-annex location for content already present and verified locally.
-- The commit stores it as the symlink git-annex itself would create; Door
-- never reads the bytes it names.
data ContentLink = ContentLink
  { contentLinkPath :: FilePath
  , contentLinkTarget :: FilePath
  } deriving (Eq, Show)

-- | A file outside @records/@ that a specification upgrade writes in the same
-- commit as its record (ADR 0068): the replaced manifest, or a document or
-- bundle of the new release. Nothing else may be written this way.
data ArchiveFile = ArchiveFile
  { archiveFilePath :: FilePath
  , archiveFileBytes :: LazyByteString.ByteString
  } deriving (Eq, Show)

manifestFilePath :: FilePath
manifestFilePath = "archive.json"

-- | The manifest, one of a release's three documents under
-- @spec/<version>/@, or a bundle under its digest path.
validateArchiveFilePath :: FilePath -> Either Text ()
validateArchiveFilePath path = case splitDirectories path of
  ["archive.json"] | path == manifestFilePath -> Right ()
  ["spec", version, name]
    | name `elem` ["VERSION", "archive-spec.md", "context.jsonld"]
    , path == "spec/" <> version <> "/" <> name -> () <$ specVersion ("archive/" <> Text.pack version)
  ["contracts", name]
    | path == "contracts/" <> name
    , Just hex <- Text.stripSuffix ".json" =<< Text.stripPrefix "sha256-" (Text.pack name)
    , Right _ <- parseSha256Digest ("sha256:" <> hex) -> Right ()
  _ -> Left ("not a specification file path: " <> Text.pack path)

-- | Content paths are the D-1 layout's `content/sha256/<h0h1>/<h2h3>/<digest>`
-- (ADR 0053) and nothing else; their link targets stay inside this
-- repository's annex object store. The result is the fan-out directories and
-- the link's name.
validateContentPath :: FilePath -> Either Text ([FilePath], FilePath)
validateContentPath path = do
  digest <- parseContentRelativePath path
  let hex = Text.unpack (Text.drop 7 (digestText digest))
  if contentRelativePath digest == path
    then Right ([take 2 hex, take 2 (drop 2 hex)], hex)
    else Left "content path must be content/sha256/<h0h1>/<h2h3>/<digest>"

validateContentLink :: ContentLink -> Either Text ()
validateContentLink link = do
  _ <- validateContentPath (contentLinkPath link)
  let target = contentLinkTarget link
  case splitDirectories target of
    ".." : ".." : ".." : ".." : ".git" : "annex" : "objects" : rest
      | not (null rest)
      , ".." `notElem` rest
      , "." `notElem` rest
      , '\0' `notElem` target -> Right ()
    _ -> Left "content link must point into this archive's git-annex object store"

-- | Where a record file lives below @records/@: its fan-out directories and
-- its filename (ADR 0053).
data RecordLocation = RecordLocation
  { recordLocationDirectories :: [FilePath]
  , recordLocationFile :: FilePath
  } deriving (Eq, Show)


-- | Validate the exact record path grammar at the last boundary before any
-- filesystem access. Writers only install digest-addressed immutable records.
validateRecordPath :: FilePath -> Either Text RecordLocation
validateRecordPath path
  | null path = Left "record path must not be empty"
  | '\0' `elem` path = Left "record path must not contain NUL"
  | isAbsolute path = Left "record path must be relative"
  | otherwise = do
      digest <- parseRecordRelativePath path
      if recordRelativePath digest /= path
        then Left "record path must be records/<h0h1>/<h2h3>/sha256-<digest>.json"
        else Right RecordLocation
          { recordLocationDirectories = recordDirectoryChain digest
          , recordLocationFile = "sha256-" <> Text.unpack (Text.drop 7 (digestText digest)) <> ".json"
          }

uniqueSorted :: Ord a => [a] -> [a]
uniqueSorted = foldr keep [] . sort
  where
    keep value [] = [value]
    keep value values@(next : _)
      | value == next = values
      | otherwise = value : values
