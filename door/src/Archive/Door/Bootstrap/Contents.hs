{-# LANGUAGE OverloadedStrings #-}

-- | What a new archive holds: Door's release files and the bootstrap records.
--
-- The specification, its context, and its catalog's bundles come from
-- Door's own data files, checked against the catalog before anything is
-- written. The appointed person and the operator initialization claim are
-- drafted here and canonicalized by the caller.
module Archive.Door.Bootstrap.Contents
  ( InitResult (..)
  , archiveVersion
  , operatorNamespace
  , initializationNamespace
  , Resources (..)
  , InstalledContract (..)
  , loadResources
  , findInitializationContract
  , personDraftFor
  , initializationDraftFor
  , contractInstance
  , specDestinationFiles
  , contractDestinationFiles
  , recordPathFor
  , currentTimestamp
  , decodeResource
  , objectFields
  ) where

import Archive.Door.Bootstrap.Abort (failIO, ioEither)
import Archive.Door.Catalog (loadContractCatalog)
import Archive.Door.Contracts
  ( ContractBundle (contractBundleSchema)
  , validateContractBundle
  , validateCoreContextForSpecification
  )
import Archive.Door.Hex (hexText)
import Archive.Door.Json (decodeUnique)
import Archive.Door.Json.Access (requiredText)
import Archive.Door.Layout
  ( ArchiveRoot
  , Sha256Digest
  , contractPath
  , digestText
  , parseSha256Digest
  , recordPath
  , specContextPath
  , specDocumentPath
  , specVersionFilePath
  )
import Archive.Door.Types (ObjectId, RevisionId, objectIdText, revisionIdText)
import qualified Crypto.Hash.SHA256 as SHA256
import Data.Aeson (Value (..), object, (.=))
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString.Lazy as LazyByteString
import Data.List (sortOn)
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text
import Data.Time (UTCTime, defaultTimeLocale, formatTime, getCurrentTime)
import Paths_archive (getDataFileName)
import System.FilePath ((</>))

data InitResult = InitResult
  { initArchiveId :: Text
  , initOperatorId :: Text
  } deriving (Eq, Show)

archiveVersion :: Text
archiveVersion = "archive/0.0.11"

archiveProgram :: Text
archiveProgram = "pkg:hackage/archive@0.0.1.0"

operatorNamespace :: Text
operatorNamespace = "archive.core/entity"

initializationNamespace :: Text
initializationNamespace = "archive.transition/operator_initialization"

initializationContractId :: Text
initializationContractId = "archive.transition/operator_initialization"

initializationContractVersion :: Text
initializationContractVersion = "1"

data Resources = Resources
  { resourceVersion :: LazyByteString.ByteString
  , resourceSpecification :: LazyByteString.ByteString
  , resourceContext :: LazyByteString.ByteString
  , resourceContracts :: [InstalledContract]
  }

data InstalledContract = InstalledContract
  { installedContractId :: Text
  , installedContractVersion :: Text
  , installedContractDigest :: Sha256Digest
  , installedContractBytes :: LazyByteString.ByteString
  , installedContractSchema :: Value
  }

loadResources :: IO Resources
loadResources = do
  versionPath <- getDataFileName "spec/VERSION"
  specificationPath <- getDataFileName "spec/archive-spec.md"
  contextPath <- getDataFileName "spec/0.0.11/context.jsonld"
  contractDirectory <- getDataFileName "spec/contracts"
  versionBytes <- LazyByteString.readFile versionPath
  specificationBytes <- LazyByteString.readFile specificationPath
  contextBytes <- LazyByteString.readFile contextPath
  if versionBytes /= "0.0.11\n"
    then failIO "installed VERSION does not select archive/0.0.11"
    else pure ()
  context <- decodeResource contextBytes
  _ <- ioEither (validateCoreContextForSpecification archiveVersion context)
  -- The specification's own catalog says which bundles it installs
  -- (ADR 0057); no copy of it lives in this source. Each listed bundle is
  -- loaded by its digest path and must carry the listed identifier and
  -- version.
  catalog <- loadContractCatalog archiveVersion >>= ioEither
  contracts <- mapM (loadContractResource contractDirectory)
    [ "sha256-" <> Text.unpack (Text.drop 7 digest) <> ".json" | (_, _, digest) <- Set.toList catalog ]
  let installed = Set.fromList
        [ (installedContractId contract, installedContractVersion contract
          , digestText (installedContractDigest contract))
        | contract <- contracts
        ]
  if installed /= catalog
    then failIO "installed contract bundles are not the specification's catalog"
    else pure Resources
      { resourceVersion = versionBytes
      , resourceSpecification = specificationBytes
      , resourceContext = contextBytes
      , resourceContracts = sortOn installedContractId contracts
      }

loadContractResource :: FilePath -> FilePath -> IO InstalledContract
loadContractResource directory fileName = do
  bytes <- LazyByteString.readFile (directory </> fileName)
  value <- decodeResource bytes
  fields <- ioEither (objectFields "contract bundle" value)
  identifier <- ioEither (requiredText "id" fields)
  version <- ioEither (requiredText "version" fields)
  let digest = "sha256:" <> hexText (SHA256.hashlazy bytes)
  parsedDigest <- ioEither (parseSha256Digest digest)
  if encodeCanonical value /= bytes
    then failIO "contract bundle is not RFC 8785 canonical"
    else do
      bundle <- ioEither (validateContractBundle identifier version digest value)
      let expectedName = "sha256-" <> Text.unpack (Text.drop 7 digest) <> ".json"
      if fileName /= expectedName
        then failIO "contract bundle filename does not match its digest"
        else pure InstalledContract
          { installedContractId = identifier
          , installedContractVersion = version
          , installedContractDigest = parsedDigest
          , installedContractBytes = bytes
          , installedContractSchema = contractBundleSchema bundle
          }

findInitializationContract :: Resources -> IO InstalledContract
findInitializationContract resources = case filter matches (resourceContracts resources) of
  [contract] -> pure contract
  _ -> failIO "operator initialization contract is not installed exactly once"
  where
    matches contract = installedContractId contract == initializationContractId
      && installedContractVersion contract == initializationContractVersion

personDraftFor :: ObjectId -> IO Value
personDraftFor operatorId = pure (object
  [ "spec" .= archiveVersion
  , "object_id" .= objectIdText operatorId
  , "shape" .= ("entity" :: Text)
  , "by" .= archiveProgram
  , "agent" .= objectIdText operatorId
  , "payload" .= object ["kind" .= ("person" :: Text)]
  , "refs" .= ([] :: [Value])
  , "supersedes" .= ([] :: [Value])
  ])

initializationDraftFor :: ObjectId -> ObjectId -> RevisionId -> InstalledContract -> IO Value
initializationDraftFor claimId operatorId personRevision contract = pure (object
  [ "spec" .= archiveVersion
  , "object_id" .= objectIdText claimId
  , "shape" .= ("claim" :: Text)
  , "by" .= archiveProgram
  , "agent" .= objectIdText operatorId
  , "payload" .= object []
  , "refs" .= [ reference "author", reference "authorizer", reference "appoints" ]
  , "supersedes" .= ([] :: [Value])
  , "contracts" .= [ object
      [ "id" .= installedContractId contract
      , "version" .= installedContractVersion contract
      , "digest" .= digestText (installedContractDigest contract)
      ]
    ]
  ])
  where
    reference role = object
      [ "role" .= (role :: Text)
      , "id" .= objectIdText operatorId
      , "revision_id" .= revisionIdText personRevision
      ]

contractInstance :: Value -> Either Text Value
contractInstance (Object fields) = do
  payload <- maybe (Left "claim payload is required") Right (KeyMap.lookup "payload" fields)
  refs <- maybe (Left "claim refs are required") Right (KeyMap.lookup "refs" fields)
  pure (object ["payload" .= payload, "refs" .= refs])
contractInstance _ = Left "claim draft must be an object"

specDestinationFiles :: ArchiveRoot -> Resources -> IO [(FilePath, LazyByteString.ByteString)]
specDestinationFiles root resources = do
  specification <- ioEither (specDocumentPath root archiveVersion)
  version <- ioEither (specVersionFilePath root archiveVersion)
  context <- ioEither (specContextPath root archiveVersion)
  pure [ (version, resourceVersion resources)
       , (specification, resourceSpecification resources)
       , (context, resourceContext resources)
       ]

contractDestinationFiles :: ArchiveRoot -> Resources -> IO [(FilePath, LazyByteString.ByteString)]
contractDestinationFiles root resources = pure
  [ (contractPath root (installedContractDigest contract), installedContractBytes contract)
  | contract <- resourceContracts resources
  ]

-- | A revision is a SHA-256 digest of the canonical record, and its record
-- lives at the path that digest fans out to.
recordPathFor :: ArchiveRoot -> RevisionId -> Either Text FilePath
recordPathFor root revision = case parseSha256Digest (revisionIdText revision) of
  Right digest -> Right (recordPath root digest)
  Left problem -> Left problem

currentTimestamp :: IO Text
currentTimestamp = Text.pack . formatTime defaultTimeLocale "%Y-%m-%dT%H:%M:%S%QZ" <$> getCurrentTimeUTC

getCurrentTimeUTC :: IO UTCTime
getCurrentTimeUTC = getCurrentTime

objectFields :: Text -> Value -> Either Text (KeyMap.KeyMap Value)
objectFields _label (Object fields) = Right fields
objectFields label _ = Left (label <> " must be an object")

decodeResource :: LazyByteString.ByteString -> IO Value
decodeResource bytes = case decodeUnique bytes of
  Left problem -> failIO (Text.pack problem)
  Right value -> pure value
