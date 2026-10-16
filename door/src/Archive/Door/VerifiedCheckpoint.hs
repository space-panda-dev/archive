{-# LANGUAGE ViewPatterns #-}
{-# LANGUAGE OverloadedStrings #-}

-- | Door's verification checkpoint (ADR 0052).
--
-- A checkpoint says that this Door build fully verified one commit: its
-- records, contracts, and immutable path history, and the reachable objects
-- beneath it. It is operational state. It lives in the Git directory, outside
-- history, is written only under the archive lock, and can be lost at any
-- moment. Losing it, or any doubt about it, costs one full verification.
--
-- A checkpoint lets Door skip work, never checks: it is used only when HEAD is
-- the checkpointed commit, the checksum holds, the running Door build wrote
-- it, and the object store's pack set is unchanged.
module Archive.Door.VerifiedCheckpoint
  ( VerifiedCheckpoint (..)
  , RecordSummary (..)
  , summarizeEnvelope
  , summarizeStoredBytes
  , checkpointFileName
  , readCheckpoint
  , readCheckpointAt
  , writeCheckpoint
  , appendCheckpoint
  , removeCheckpoint
  , extendCheckpoint
  , checkpointCompactAfter
  ) where

import Archive.Door.Json (decodeUnique)
import Archive.Door.Types
  ( DraftEnvelope (..)
  , RecordBody (..)
  , Reference (referenceRevision)
  , StoredEnvelope (..)
  , objectIdText
  , parseStoredEnvelope
  , revisionIdText
  )
import Control.Exception (IOException, bracket, evaluate, try)
import qualified Crypto.Hash.SHA256 as SHA256
import Data.Aeson (Value (..), (.=))
import qualified Data.Aeson as Aeson
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString as ByteString
import qualified Data.ByteString.Lazy as LazyByteString
import Data.IORef (IORef, newIORef, readIORef, writeIORef)
import Data.List (isPrefixOf, isSuffixOf, sort)
import Data.Map.Strict (Map)
import qualified Data.Map.Strict as Map
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Vector as Vector
import Numeric (showHex)
import System.Directory (listDirectory)
import System.Environment (getExecutablePath)
import System.FilePath ((</>))
import System.Posix.Files (deviceID, fileID, fileSize, getFileStatus, modificationTimeHiRes, statusChangeTimeHiRes)
import qualified Data.Text.Encoding as TextEncoding
import System.IO.Unsafe (unsafePerformIO)
import System.Posix.IO
  ( OpenFileFlags (cloexec, creat, nofollow, nonBlock, trunc)
  , OpenMode (WriteOnly)
  , closeFd
  , defaultFileFlags
  , fdWriteBuf
  , openFdAt
  )
import System.Posix.Types (Fd (..))
import Foreign.C.Error (throwErrnoIfMinus1_)
import Foreign.C.String (CString, withCString)
import Foreign.C.Types (CInt (..))
import qualified Data.ByteString.Unsafe as UnsafeByteString
import Foreign.Ptr (castPtr)

-- | What a checkpoint asserts about one verified commit.
data VerifiedCheckpoint = VerifiedCheckpoint
  { checkpointCommit :: Text
  -- ^ The commit Door verified.
  , checkpointAdditions :: Map FilePath Text
  -- ^ Each immutable archive path and the commit that added it.
  , checkpointHistory :: [Text]
  -- ^ The linear history from the root commit to 'checkpointCommit'.
  , checkpointRecords :: Map FilePath RecordSummary
  -- ^ What planning needs from each committed record without parsing it.
  , checkpointChain :: Maybe Int
  -- ^ How many per-commit additions follow the base on disk, when this
  -- checkpoint is the tip of the chain on disk (ADR 0054); 'Nothing' when it
  -- exists only in memory.
  }
  deriving (Eq, Show)

-- | A committed record's object, shape, and the revisions it supersedes.
data RecordSummary = RecordSummary
  { summaryObject :: Text
  , summaryShape :: Text
  , summarySupersedes :: [Text]
  }
  deriving (Eq, Show)

summarizeEnvelope :: StoredEnvelope -> RecordSummary
summarizeEnvelope envelope = RecordSummary
  { summaryObject = objectIdText (envelopeObject draft)
  , summaryShape = case envelopeBody draft of
      ArtifactRecord _ -> "artifact"
      EntityRecord _ -> "entity"
      ClaimRecord _ -> "claim"
      AssessmentRecord _ -> "assessment"
  , summarySupersedes =
      [revisionIdText revision | reference <- envelopeSupersedes draft, Just revision <- [referenceRevision reference]]
  }
  where
    draft = storedDraft envelope

summarizeStoredBytes :: LazyByteString.ByteString -> Either Text RecordSummary
summarizeStoredBytes bytes = do
  value <- either (Left . Text.pack) Right (decodeUnique bytes)
  summarizeEnvelope <$> parseStoredEnvelope value

checkpointFileName :: FilePath
checkpointFileName = "archive-door-verified.json"

checkpointFormat :: Int
checkpointFormat = 3

-- | The checkpoint for this archive, if one is present and trustworthy for the
-- running Door build. Every doubt reads as no checkpoint.
readCheckpoint :: FilePath -> IO (Maybe VerifiedCheckpoint)
readCheckpoint root = readCheckpointAt (root </> ".git")

readCheckpointAt :: FilePath -> IO (Maybe VerifiedCheckpoint)
readCheckpointAt gitDirectory = do
  bytesResult <- readMaybeFile (gitDirectory </> checkpointFileName)
  case bytesResult of
    Nothing -> pure Nothing
    Just bytes -> do
      build <- doorBuildIdentity
      packs <- packFingerprint gitDirectory
      case (build, packs) of
        (Just buildId, Just packNames) ->
          case parseCheckpoint buildId packNames bytes of
            Left _ -> pure Nothing
            Right base -> Just <$> follow buildId base
        _ -> pure Nothing
  where
    -- Apply each addition whose parent is the current tip. A missing,
    -- damaged, or foreign addition ends the chain: the checkpoint is then
    -- the last good tip, which Door uses only if HEAD is that commit.
    follow buildId tip = do
      next <- readMaybeFile (gitDirectory </> additionFileName (checkpointCommit tip))
      case next >>= either (const Nothing) Just . parseAddition buildId (checkpointCommit tip) of
        Nothing -> pure tip
        Just (commit, records) -> follow buildId (extendCheckpoint tip commit records)
          { checkpointChain = (+ 1) <$> checkpointChain tip }

readMaybeFile :: FilePath -> IO (Maybe ByteString.ByteString)
readMaybeFile path = do
  result <- try (ByteString.readFile path) :: IO (Either IOException ByteString.ByteString)
  pure (either (const Nothing) Just result)

-- | Where the addition for the commit after @parent@ is written.
additionFileName :: Text -> FilePath
additionFileName parent = additionPrefix <> Text.unpack parent <> ".json"

additionPrefix :: FilePath
additionPrefix = "archive-door-verified-after-"

-- | Compact the chain into a new base once it holds this many additions.
checkpointCompactAfter :: Int
checkpointCompactAfter = 64

-- | Split a checksummed file into its verified JSON object.
checkedObject :: ByteString.ByteString -> Either Text (KeyMap.KeyMap Value)
checkedObject bytes = do
  let (checksumLine, rest) = ByteString.break (== 10) bytes
      body = ByteString.drop 1 rest
  if TextEncoding.decodeLatin1 checksumLine /= digestText body || ByteString.null rest
    then Left "checkpoint checksum does not match"
    else Right ()
  value <- either (Left . Text.pack) Right (decodeUnique (LazyByteString.fromStrict body))
  case value of
    Object object -> Right object
    _ -> Left "checkpoint is not an object"

checkedBytes :: Value -> ByteString.ByteString
checkedBytes body =
  let encoded = LazyByteString.toStrict (encodeCanonical body)
   in TextEncoding.encodeUtf8 (digestText encoded) <> "\n" <> encoded

parseAddition :: Text -> Text -> ByteString.ByteString -> Either Text (Text, [(FilePath, RecordSummary)])
parseAddition buildId parent bytes = do
  fields <- checkedObject bytes
  case KeyMap.lookup "format" fields of
    Just (Number number) | number == fromIntegral checkpointFormat -> Right ()
    _ -> Left "checkpoint addition format is unsupported"
  case (KeyMap.lookup "door" fields, KeyMap.lookup "parent" fields, KeyMap.lookup "commit" fields) of
    (Just (String door), Just (String recordedParent), Just (String commit))
      | door == buildId && recordedParent == parent -> do
          records <- case KeyMap.lookup "records" fields of
            Just (Object object) -> mapM summaryEntry (KeyMap.toList object)
            _ -> Left "checkpoint addition records are missing"
          Right (commit, records)
    _ -> Left "checkpoint addition does not continue this chain"

summaryEntry :: (Key.Key, Value) -> Either Text (FilePath, RecordSummary)
summaryEntry (key, Array (Vector.toList -> [String object, String shape, Array supersedes])) = do
  revisions <- mapM revisionText (Vector.toList supersedes)
  Right (Text.unpack (Key.toText key), RecordSummary object shape revisions)
  where
    revisionText (String text) = Right text
    revisionText _ = Left "checkpoint supersession is not a revision"
summaryEntry _ = Left "checkpoint record summary is malformed"

summariesValue :: [(FilePath, RecordSummary)] -> Value
summariesValue records = Aeson.object
  [ Key.fromText (Text.pack path) .= (summaryObject summary, summaryShape summary, summarySupersedes summary)
  | (path, summary) <- records
  ]

parseCheckpoint :: Text -> [Text] -> ByteString.ByteString -> Either Text VerifiedCheckpoint
parseCheckpoint buildId packNames bytes = do
  -- The file is a checksum line, then the body bytes the checksum covers.
  fields <- checkedObject bytes
  case KeyMap.lookup "format" fields of
    Just (Number number) | number == fromIntegral checkpointFormat -> Right ()
    _ -> Left "checkpoint format is unsupported"
  door <- textField "door" fields
  if door == buildId then Right () else Left "checkpoint was written by another Door build"
  packs <- textList "packs" fields
  if packs == packNames then Right () else Left "object store packs changed"
  commit <- textField "commit" fields
  history <- textList "history" fields
  additions <- case KeyMap.lookup "additions" fields of
    Just (Object object) -> Map.fromList <$> mapM addition (KeyMap.toList object)
    _ -> Left "checkpoint additions are missing"
  records <- case KeyMap.lookup "records" fields of
    Just (Object object) -> Map.fromList <$> mapM summaryEntry (KeyMap.toList object)
    _ -> Left "checkpoint records are missing"
  case reverse history of
    tip : _ | tip == commit -> Right ()
    _ -> Left "checkpoint history does not end at its commit"
  Right VerifiedCheckpoint
    { checkpointCommit = commit
    , checkpointAdditions = additions
    , checkpointHistory = history
    , checkpointRecords = records
    , checkpointChain = Just 0
    }
  where
    addition (key, String introducing) = Right (Text.unpack (Key.toText key), introducing)
    addition _ = Left "checkpoint addition is not a commit"
    textField name fields = case KeyMap.lookup name fields of
      Just (String text) -> Right text
      _ -> Left ("checkpoint field is missing: " <> Key.toText name)
    textList name fields = case KeyMap.lookup name fields of
      Just (Array values) -> mapM asText (Vector.toList values)
      _ -> Left ("checkpoint field is missing: " <> Key.toText name)
    asText (String text) = Right text
    asText _ = Left "checkpoint list holds a non-string"

-- | Write a checkpoint into the pinned Git directory. The caller holds the
-- archive lock. The file is replaced by rename, so a reader sees the old or
-- the new checkpoint, and a torn write fails its checksum. It is not
-- synchronized: losing it costs only a full verification.
writeCheckpoint :: Fd -> FilePath -> VerifiedCheckpoint -> IO (Either Text ())
writeCheckpoint gitFd gitDirectory checkpoint = do
  build <- doorBuildIdentity
  packs <- packFingerprint gitDirectory
  case (build, packs) of
    (Nothing, _) -> pure (Left "could not identify the running Door build")
    (_, Nothing) -> pure (Left "could not list the object store packs")
    (Just buildId, Just packNames) -> do
      let body = Aeson.object
            [ "format" .= checkpointFormat
            , "door" .= buildId
            , "packs" .= packNames
            , "commit" .= checkpointCommit checkpoint
            , "history" .= checkpointHistory checkpoint
            , "additions" .= Aeson.object
                [ Key.fromText (Text.pack path) .= introducing
                | (path, introducing) <- Map.toList (checkpointAdditions checkpoint)
                ]
            , "records" .= summariesValue (Map.toList (checkpointRecords checkpoint))
            ]
      written <- replaceFile gitFd checkpointFileName (checkedBytes body)
      case written of
        Left problem -> pure (Left problem)
        Right () -> removeAdditions gitFd gitDirectory

-- | Record a commit Door made on top of a checkpoint: as one small addition
-- when that checkpoint is the tip on disk, otherwise (or once the chain is
-- long) as a new base (ADR 0054).
appendCheckpoint :: Fd -> FilePath -> VerifiedCheckpoint -> Text -> [(FilePath, RecordSummary)]
                 -> IO (Either Text Int)
appendCheckpoint gitFd gitDirectory parent commit records = case checkpointChain parent of
  Just length' | length' + 1 < checkpointCompactAfter -> do
    build <- doorBuildIdentity
    case build of
      Nothing -> pure (Left "could not identify the running Door build")
      Just buildId -> fmap (const (length' + 1)) <$> replaceFile gitFd (additionFileName (checkpointCommit parent)) (checkedBytes
        (Aeson.object
          [ "format" .= checkpointFormat
          , "door" .= buildId
          , "parent" .= checkpointCommit parent
          , "commit" .= commit
          , "records" .= summariesValue records
          ]))
  _ -> fmap (const 0) <$> writeCheckpoint gitFd gitDirectory (extendCheckpoint parent commit records)

replaceFile :: Fd -> FilePath -> ByteString.ByteString -> IO (Either Text ())
replaceFile gitFd name bytes = do
  let temporary = name <> ".tmp"
  written <- try $ do
    bracket
      (openFdAt (Just gitFd) temporary WriteOnly defaultFileFlags
        { creat = Just 0o644, trunc = True, nofollow = True, nonBlock = True, cloexec = True })
      closeFd
      (\fd -> writeAll fd bytes)
    renameIn gitFd temporary name
    :: IO (Either IOException ())
  pure (either (Left . Text.pack . show) Right written)

removeAdditions :: Fd -> FilePath -> IO (Either Text ())
removeAdditions gitFd gitDirectory = do
  listed <- try (listDirectory gitDirectory) :: IO (Either IOException [FilePath])
  case listed of
    Left problem -> pure (Left (Text.pack (show problem)))
    Right names -> do
      mapM_ (unlinkIn gitFd) [name | name <- names, additionPrefix `isPrefixOf` name]
      pure (Right ())

-- | Remove the checkpoint, for example before coordinated maintenance. An
-- absent checkpoint is already removed.
removeCheckpoint :: Fd -> FilePath -> IO (Either Text ())
removeCheckpoint gitFd gitDirectory = do
  removed <- try (unlinkIn gitFd checkpointFileName) :: IO (Either IOException CInt)
  case removed of
    Left problem -> pure (Left (Text.pack (show problem)))
    Right _ -> removeAdditions gitFd gitDirectory

-- | The checkpoint for a commit Door made on top of a checkpointed commit.
extendCheckpoint :: VerifiedCheckpoint -> Text -> [(FilePath, RecordSummary)] -> VerifiedCheckpoint
extendCheckpoint base commit records = VerifiedCheckpoint
  { checkpointCommit = commit
  , checkpointAdditions = foldr (\(path, _) -> Map.insert path commit) (checkpointAdditions base) records
  , checkpointHistory = checkpointHistory base <> [commit]
  , checkpointRecords = Map.union (Map.fromList records) (checkpointRecords base)
  , checkpointChain = Nothing
  }

digestText :: ByteString.ByteString -> Text
digestText bytes = "sha256:" <> Text.pack (concatMap byteHex (ByteString.unpack (SHA256.hash bytes)))
  where
    byteHex byte = let value = showHex byte "" in if length value == 1 then '0' : value else value

-- | The sorted names of the object store's pack files. Coordinated maintenance
-- that replaces packs changes this list, so an old checkpoint no longer applies.
packFingerprint :: FilePath -> IO (Maybe [Text])
packFingerprint gitDirectory = do
  listed <- try (listDirectory (gitDirectory </> "objects" </> "pack"))
    :: IO (Either IOException [FilePath])
  pure $ case listed of
    Left _ -> Just []
    Right names -> Just (map Text.pack (sort (filter (".pack" `isSuffixOf`) names)))

-- | The running executable's file identity: path, device, inode, size, and
-- modification and change times, read once per process. Replacing or
-- rebuilding Door changes it, so a new build verifies from scratch once.
doorBuildIdentity :: IO (Maybe Text)
doorBuildIdentity = do
  cached <- readIORef buildIdentityCache
  case cached of
    Just value -> pure value
    Nothing -> do
      result <- try $ do
        path <- getExecutablePath
        status <- getFileStatus path
        evaluate (digestText (TextEncoding.encodeUtf8 (Text.pack (unwords
          [ path
          , show (deviceID status)
          , show (fileID status)
          , show (fileSize status)
          , show (modificationTimeHiRes status)
          , show (statusChangeTimeHiRes status)
          ]))))
        :: IO (Either IOException Text)
      let value = either (const Nothing) Just result
      writeIORef buildIdentityCache (Just value)
      pure value

buildIdentityCache :: IORef (Maybe (Maybe Text))
buildIdentityCache = unsafePerformIO (newIORef Nothing)
{-# NOINLINE buildIdentityCache #-}

writeAll :: Fd -> ByteString.ByteString -> IO ()
writeAll fd bytes
  | ByteString.null bytes = pure ()
  | otherwise = do
      written <- UnsafeByteString.unsafeUseAsCStringLen bytes $ \(pointer, size) ->
        fdWriteBuf fd (castPtr pointer) (fromIntegral size)
      writeAll fd (ByteString.drop (fromIntegral written) bytes)

foreign import ccall unsafe "renameat"
  c_renameat :: CInt -> CString -> CInt -> CString -> IO CInt

foreign import ccall unsafe "unlinkat"
  c_unlinkat :: CInt -> CString -> CInt -> IO CInt

renameIn :: Fd -> FilePath -> FilePath -> IO ()
renameIn (Fd directory) from to = withCString from $ \fromName -> withCString to $ \toName ->
  throwErrnoIfMinus1_ "rename Door checkpoint" (c_renameat directory fromName directory toName)

unlinkIn :: Fd -> FilePath -> IO CInt
unlinkIn (Fd directory) name = withCString name $ \cName -> c_unlinkat directory cName 0
