{-# LANGUAGE OverloadedStrings #-}

module Main (main) where

import Archive.Door.AdmissionDryRun (AdmissionPlanningError (..), dryRunAdmissionForCommit)
import Archive.Door.AdmissionCommit (commitAdmission, refreshVerificationCheckpoint)
import Archive.Door.AdmissionPlan (AdmissionPlan (..))
import Archive.Door.Proposal (readAdmissionProposal, readTransitionProposal)
import Archive.Door.Refusal (Refusal (..), RefusalCode (..), mutationRefusal, refusalValue)
import Archive.GitSnapshot (gitSnapshotErrorMessage)
import Archive.Door.TransitionDryRun (TransitionPlan (..), TransitionPlanningError (..), dryRunTransitionForCommit)
import Archive.Door.TransitionCommit (commitTransition)
import Control.Concurrent (myThreadId, throwTo)
import Control.Exception (AsyncException (UserInterrupt), SomeException, bracket, displayException, finally, fromException, throwIO, try)
import Control.Monad (forever, unless, when)
import qualified Data.Aeson as Aeson
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Aeson ((.=))
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString as ByteString
import qualified Data.ByteString.Lazy.Char8 as LazyByteString
import Data.Bits ((.&.))
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Network.Socket as Socket
import qualified Network.Socket.ByteString as SocketBytes
import System.Directory (doesPathExist, removeFile)
import System.Environment (getArgs)
import System.Exit (die)
import System.FilePath (takeDirectory)
import System.IO (hPutStrLn, stderr)
import System.Posix.Files
  ( fileMode
  , fileOwner
  , getFileStatus
  , isDirectory
  , setFileCreationMask
  )
import System.Posix.Signals (Handler (Catch), installHandler, sigTERM)
import System.Posix.User (getEffectiveUserID)
import System.Timeout (timeout)

main :: IO ()
main = do
  arguments <- getArgs
  case arguments of
    ["admit", "--proposal", directory, "--dry-run"] -> cli (proposalDryRun directory)
    ["admit", "--dry-run"] ->
      die "door: admission requires --proposal DIRECTORY"
    ["admit", "--proposal", directory] -> cli (admissionCommit directory)
    ["admit"] -> die "door: admit requires --proposal DIRECTORY"
    ["transition", "--proposal", directory, "--dry-run"] ->
      cli (transitionDryRun directory)
    ["transition", "--dry-run"] ->
      die "door: transition requires --proposal DIRECTORY"
    ["transition", "--proposal", directory] -> cli (transitionCommit directory)
    ["transition"] -> die "door: transition requires --proposal DIRECTORY"
    ["serve", "--socket", path] -> serve path
    ["verify"] -> cli verifyArchive
    _ -> die "usage: door (admit --proposal DIRECTORY --dry-run | admit | transition --proposal DIRECTORY [--dry-run] | transition | serve --socket PATH | verify)"

-- | Verify HEAD in full and record a new base checkpoint (ADR 0052). Run it
-- after `archive maintenance repack`, which removes the checkpoint; the
-- checkpoint belongs to the Door build that verified it.
verifyArchive :: IO Answer
verifyArchive = do
  verified <- refreshVerificationCheckpoint "."
  pure $ case verified of
    Left problem -> Left (mutationRefusal problem)
    Right () -> Right (Aeson.object ["status" .= ("verified" :: Text)])

-- | One request's answer: the JSON the command prints, or the refusal whose
-- message it reports before exiting non-zero (ADR 0058).
type Answer = Either Refusal Aeson.Value

cli :: IO Answer -> IO ()
cli action = action >>= either (die . ("door: " <>) . Text.unpack . refusalMessage)
  (LazyByteString.putStrLn . encodeCanonical)

refusal :: RefusalCode -> Text -> Refusal
refusal code message = Refusal code message []

-- | A proposal Door cannot read as a proposal.
proposalRefusal :: Either Text a -> Either Refusal a
proposalRefusal = either (Left . refusal ProposalMalformed) Right

admissionRefusal :: AdmissionPlanningError -> Refusal
admissionRefusal problem = case problem of
  AdmissionSnapshotFailure issue -> refusal ArchiveUnreadable (gitSnapshotErrorMessage issue)
  AdmissionSnapshotIntegrityFailure message -> refusal IntegrityFailure message
  AdmissionProposalRejected message -> refusal ProposalRefused message
  AdmissionProposalStale message -> refusal ExpectedHeadStale message

transitionRefusal :: TransitionPlanningError -> Refusal
transitionRefusal problem = case problem of
  TransitionSnapshotFailure issue -> refusal ArchiveUnreadable (gitSnapshotErrorMessage issue)
  TransitionSnapshotIntegrityFailure message -> refusal IntegrityFailure message
  TransitionProposalRejected message -> refusal ProposalRefused message
  TransitionProposalStale message -> refusal ExpectedHeadStale message

proposalDryRun :: FilePath -> IO Answer
proposalDryRun directory = do
  proposal <- proposalRefusal <$> readAdmissionProposal directory
  planned <- either (pure . Left) (fmap (either (Left . admissionRefusal) Right) . dryRunAdmissionForCommit ".") proposal
  pure $ do
    plan <- planned
    pure (Aeson.object
      [ "status" .= ("predicted" :: Text)
      , "revision_ids" .= plannedRevisions plan
      , "records" .= map snd (plannedRecords plan)
      , "content_verified" .= False
      , "retry" .= planIsRetry plan
      ])

admissionCommit :: FilePath -> IO Answer
admissionCommit directory = do
  proposal <- proposalRefusal <$> readAdmissionProposal directory
  committed <- either (pure . Left) (fmap (either (Left . mutationRefusal) Right) . commitAdmission ".") proposal
  pure $ do
    plan <- committed
    pure (Aeson.object
      [ "status" .= ("committed" :: Text)
      , "revision_ids" .= plannedRevisions plan
      , "retry" .= planIsRetry plan
      ])

transitionDryRun :: FilePath -> IO Answer
transitionDryRun directory = do
  proposal <- proposalRefusal <$> readTransitionProposal directory
  planned <- either (pure . Left) (fmap (either (Left . transitionRefusal) Right) . dryRunTransitionForCommit ".") proposal
  pure $ do
    plan <- planned
    pure (Aeson.object
      [ "status" .= ("predicted" :: Text)
      , "revision_ids" .= plannedTransitionRevisions plan
      , "retired_objects" .= resultingRetiredObjects plan
      , "destroyed_digests" .= resultingDestroyedDigests plan
      , "authorization_recheck_required" .= True
      , "state_recheck_required" .= True
      , "retry" .= transitionRetry plan
      ])

transitionCommit :: FilePath -> IO Answer
transitionCommit directory = do
  proposal <- proposalRefusal <$> readTransitionProposal directory
  committed <- either (pure . Left) (fmap (either (Left . mutationRefusal) Right) . commitTransition ".") proposal
  pure $ do
    plan <- committed
    pure (Aeson.object
      [ "status" .= ("committed" :: Text)
      , "revision_ids" .= plannedTransitionRevisions plan
      , "retired_objects" .= resultingRetiredObjects plan
      , "destroyed_digests" .= resultingDestroyedDigests plan
      , "retry" .= transitionRetry plan
      ])

-- | Serve one archive (the working directory) on a private Unix socket
-- (ADR 0054). Each connection carries one request line,
-- @{"command": ..., "proposal": ...}@, and gets one answer line:
-- @{"ok": true, "result": ...}@ with exactly the JSON the command prints, or
-- @{"ok": false, "error": {"code": ..., "message": ...}}@ with a stable code
-- (ADR 0058). Requests are handled one at a time; every write still takes
-- the archive lock, so the command line and maintenance stay coordinated
-- with the service. The answers are described in @door/service.schema.json@.
serve :: FilePath -> IO ()
serve path = do
  privateDirectory (takeDirectory path)
  exists <- doesPathExist path
  when exists (die ("door: socket path already exists: " <> path))
  -- A SIGTERM stops the service like an interrupt, so the socket is removed.
  mainThread <- myThreadId
  _ <- installHandler sigTERM (Catch (throwTo mainThread UserInterrupt)) Nothing
  bracket open close $ \listener -> do
    hPutStrLn stderr ("door: serving on " <> path)
    forever $ bracket (fst <$> Socket.accept listener) Socket.close $ \connection -> do
      -- A client that disconnects early, or any failure on one connection,
      -- ends that connection only; an interrupt still stops the service.
      outcome <- try (handle connection) :: IO (Either SomeException ())
      case outcome of
        Left problem | Just interrupt <- fromException problem -> throwIO (interrupt :: AsyncException)
        Left problem -> hPutStrLn stderr ("door: connection failed: " <> displayException problem)
        Right () -> pure ()
  where
    open = do
      listener <- Socket.socket Socket.AF_UNIX Socket.Stream Socket.defaultProtocol
      -- The socket is created owner-only; no window exists with wider access.
      previous <- setFileCreationMask 0o177
      Socket.bind listener (Socket.SockAddrUnix path) `finally` setFileCreationMask previous
      Socket.listen listener 16
      pure listener
    close listener = Socket.close listener `finally` removeIfPresent
    removeIfPresent = do
      present <- doesPathExist path
      when present (removeFile path)
    handle connection = do
      -- One slow or silent client must not hold the serial service.
      received <- timeout (requestSeconds * 1000000) (readRequestLine connection ByteString.empty)
      answer <- case received of
        Nothing -> pure (Right (Left (refusal RequestMalformed
          ("no complete request line within " <> Text.pack (show requestSeconds) <> " seconds"))))
        Just (Left problem) -> pure (Right (Left (refusal RequestMalformed problem)))
        Just (Right request) -> try (respond request) :: IO (Either SomeException Answer)
      case answer of
        Left problem | Just interrupt <- fromException problem -> throwIO (interrupt :: AsyncException)
        _ -> pure ()
      let reply = case answer of
            Left problem -> failure (refusal InternalError (Text.pack (displayException problem)))
            Right (Left problem) -> failure problem
            Right (Right value) -> Aeson.object ["ok" .= True, "result" .= value]
      SocketBytes.sendAll connection (LazyByteString.toStrict (encodeCanonical reply) <> "\n")
      -- Closing with unread input makes Linux reset the connection, which
      -- discards the answer before the client reads it (an oversized line
      -- is refused unread). So end the answer, then read what the client
      -- still sends, within bounds, before closing.
      Socket.shutdown connection Socket.ShutdownSend
      _ <- timeout (drainSeconds * 1000000) (drain connection 0)
      pure ()
    drain connection seen
      | seen > drainBytes = pure ()
      | otherwise = do
          chunk <- SocketBytes.recv connection 65536
          unless (ByteString.null chunk) (drain connection (seen + ByteString.length chunk))
    failure problem = Aeson.object ["ok" .= False, "error" .= refusalValue problem]
    respond request = case Aeson.eitherDecodeStrict request of
      Left problem -> pure (Left (refusal RequestMalformed ("request is not JSON: " <> Text.pack problem)))
      Right (Aeson.Object fields) -> case (KeyMap.lookup "command" fields, KeyMap.lookup "proposal" fields) of
        (Just (Aeson.String command), Just (Aeson.String proposal))
          | KeyMap.size fields == 2 -> dispatch command (Text.unpack proposal)
        _ -> pure (Left (refusal RequestMalformed "request must be exactly {command, proposal}"))
      Right _ -> pure (Left (refusal RequestMalformed "request must be a JSON object"))
    dispatch command proposal = case command of
      "admit" -> admissionCommit proposal
      "admit-dry-run" -> proposalDryRun proposal
      "transition" -> transitionCommit proposal
      "transition-dry-run" -> transitionDryRun proposal
      _ -> pure (Left (refusal RequestMalformed ("unknown command: " <> command)))

requestSeconds :: Int
requestSeconds = 30

maxRequestBytes :: Int
maxRequestBytes = 65536

-- | How long, and how much, the service reads after its answer so the
-- connection closes without a reset.
drainSeconds, drainBytes :: Int
drainSeconds = 1
drainBytes = 1048576

-- | Read exactly one newline-terminated request line of at most
-- 'maxRequestBytes' bytes. A longer line, or a connection closed before its
-- newline, is refused rather than parsed as whatever arrived.
readRequestLine :: Socket.Socket -> ByteString.ByteString -> IO (Either Text ByteString.ByteString)
readRequestLine connection collected = case ByteString.elemIndex 10 collected of
  Just index
    | index > maxRequestBytes -> pure (Left tooLong)
    | otherwise -> pure (Right (ByteString.take index collected))
  Nothing
    | ByteString.length collected > maxRequestBytes -> pure (Left tooLong)
    | otherwise -> do
        chunk <- SocketBytes.recv connection 4096
        if ByteString.null chunk
          then pure (Left "the connection closed before the request line ended")
          else readRequestLine connection (collected <> chunk)
  where
    tooLong = "the request line exceeds " <> Text.pack (show maxRequestBytes) <> " bytes"

-- | The socket's directory must belong to this user and be closed to others,
-- so file permissions alone keep the service private.
privateDirectory :: FilePath -> IO ()
privateDirectory directory = do
  status <- getFileStatus directory
  user <- getEffectiveUserID
  unless (isDirectory status) (die ("door: socket directory is not a directory: " <> directory))
  unless (fileOwner status == user) (die ("door: socket directory is not owned by this user: " <> directory))
  unless (fileMode status .&. 0o077 == 0)
    (die ("door: socket directory is open to other users: " <> directory))
