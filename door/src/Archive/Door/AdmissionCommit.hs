{-# LANGUAGE OverloadedStrings #-}

-- | Persistent admission above the pure admission planner.
--
-- An identical complete retry returns the existing plan without touching Git
-- or git-annex. A new batch obtains each declared key from its configured
-- staging remote through git-annex, verifies the local object's SHA-256 and
-- size independently of the key name, and commits the records and content
-- locations together through the shared lock and record commit seam
-- (ADR 0023). Verification asks git-annex to hash the object (`calckey`)
-- rather than `fsck`, which writes the git-annex branch on every run and
-- would break INV-5; Door itself never reads content bytes (D-4).
module Archive.Door.AdmissionCommit
  ( commitAdmission
  , refreshVerificationCheckpoint
  ) where

import qualified Archive.GitSnapshot as Git
import Archive.Door.AdmissionDryRun
  ( AdmissionPlanningError (..)
  , dryRunAdmissionVerified
  , recheckAdmissionForCommit
  , forgetSnapshots
  , loadSnapshot
  , rememberCommitted
  , snapshotVerification
  )
import Archive.Door.AdmissionPlan (AdmissionPlan (..))
import Archive.Door.Canonical (finalizeStored)
import Archive.Door.Layout (archiveRoot, contentPath, parseSha256Digest, recordPath)
import Archive.Door.MutationError (MutationError (..))
import Archive.Door.Proposal
  ( ContentDeclaration (..)
  , Proposal (..)
  , ProposalManifest (..)
  )
import Archive.Door.RecordCommit
  ( ArchiveWriteLock
  , ContentLink (..)
  , archiveWriteLockGitExecutableIdentity
  , commitRecordBatchWithContent
  , CommitVerification (..)
  , proveObjectBaseline
  , writeBaseCheckpoint
  , writeCommitCheckpoint
  , configuredRemoteNames
  , runAnnexLocked
  , withArchiveWriteLock
  )
import Archive.Executables (Executable, resolveAnnexExecutable)
import qualified Control.Exception as Exception
import Control.Monad (unless, when)
import Control.Monad.Trans.Class (lift)
import Control.Monad.Trans.Except (ExceptT (..), except, runExceptT, throwE, withExceptT)
import Data.Aeson (Value (Object))
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString.Lazy as LazyByteString
import Data.Text (Text)
import qualified Data.Text as Text
import Data.Time (defaultTimeLocale, formatTime, getCurrentTime)
import System.Exit (ExitCode (..))
import System.FilePath (makeRelative, splitDirectories, (</>))
import System.Posix.Files (FileStatus, fileSize, getFdStatus, isRegularFile)
import System.Posix.IO
  ( OpenFileFlags (cloexec, nofollow, nonBlock)
  , OpenMode (ReadOnly)
  , closeFd
  , defaultFileFlags
  , openFd
  )
import Text.Read (readMaybe)

-- | Verify HEAD in full under the archive lock (its records, contracts,
-- history, and every reachable object, which are also synchronized) and
-- record a new base checkpoint for it, so the next admission does not pay for
-- a full verification.
refreshVerificationCheckpoint :: FilePath -> IO (Either MutationError ())
refreshVerificationCheckpoint root = withArchiveWriteLock root $ \archiveLock -> runExceptT $ do
  lift forgetSnapshots
  snapshot <- withExceptT MutationSnapshotFailure (ExceptT (Git.resolveSnapshot root))
  data' <- withExceptT (MutationIntegrityFailure . Text.pack . show) (ExceptT (loadSnapshot snapshot))
  withExceptT MutationIntegrityFailure
    (ExceptT (proveObjectBaseline archiveLock (Git.snapshotCommit snapshot)))
  withExceptT MutationOperationFailure
    (ExceptT (writeBaseCheckpoint archiveLock (fst (snapshotVerification data'))))

commitAdmission :: FilePath -> Proposal -> IO (Either MutationError AdmissionPlan)
commitAdmission root proposal = withArchiveWriteLock root $ \archiveLock -> runExceptT $ do
  (plan, snapshot) <- withExceptT admissionMutationError
    (ExceptT (dryRunAdmissionVerified root proposal))
  when (plannedGitExecutableIdentity plan /= archiveWriteLockGitExecutableIdentity archiveLock)
    (throwE (MutationConfigurationFailure "configured Git executable changed before commit"))
  unless (planIsRetry plan) $ do
    let verification = verificationOf snapshot
    (links, transferred) <- withExceptT (\(problem, transferred) -> unadmitted transferred problem)
      (ExceptT (obtainContents root archiveLock (manifestContents (proposalManifest proposal))))
    timestamp <- lift currentTimestamp
    records <- withExceptT (unadmitted transferred . MutationIntegrityFailure)
      (except (finalizePlan root timestamp plan))
    commit <- withExceptT (unadmitted transferred) $ ExceptT $
      commitRecordBatchWithContent archiveLock
        (plannedAdmissionSnapshot plan)
        (if null links then "Admit archive metadata" else "Admit archive records and content")
        records
        links
        (Just verification)
        (finalCheck root proposal plan)
    chain <- lift (writeCommitCheckpoint archiveLock verification commit records)
    lift (rememberCommitted root snapshot commit chain records)
  pure plan
  where
    verificationOf snapshot =
      let (base, proven) = snapshotVerification snapshot
       in CommitVerification { verifiedBase = base, verifiedObjectsProven = proven }

-- | Keys this operation obtained stay reported when the batch fails later.
unadmitted :: [Text] -> MutationError -> MutationError
unadmitted [] problem = problem
unadmitted keys problem = MutationUnadmittedContent problem keys

-- | Obtain and verify every declared content item, in proposal order.
--
-- Every staging remote is checked before git-annex runs, so a proposal naming
-- an unconfigured remote is refused without transfer. On success the result
-- carries one content location per declaration and the keys this operation
-- transferred (keys already present locally are not reported as transferred).
obtainContents
  :: FilePath
  -> ArchiveWriteLock
  -> [ContentDeclaration]
  -> IO (Either (MutationError, [Text]) ([ContentLink], [Text]))
obtainContents _ _ [] = pure (Right ([], []))
obtainContents root archiveLock declarations = do
  annexResult <- resolveAnnexExecutable
  remotesResult <- configuredRemoteNames archiveLock
  case (annexResult, remotesResult) of
    (Left problem, _) -> pure (Left (MutationConfigurationFailure problem, []))
    (_, Left problem) -> pure (Left (MutationOperationFailure problem, []))
    (Right annex, Right remotes) ->
      case [remote | remote <- map contentStagingRemote declarations, remote `notElem` remotes] of
        missing : _ -> pure (Left (MutationRejected
          ("staging remote is not a configured Git remote: " <> missing), []))
        [] -> go annex [] [] declarations
  where
    go _ links transferred [] = pure (Right (reverse links, reverse transferred))
    go annex links transferred (declaration : rest) = do
      obtained <- obtainOne root archiveLock annex declaration
      case obtained of
        Left (problem, newlyTransferred) ->
          pure (Left (problem, reverse transferred <> newlyTransferred))
        Right (link, newlyTransferred) ->
          go annex (link : links) (reverse newlyTransferred <> transferred) rest

obtainOne
  :: FilePath
  -> ArchiveWriteLock
  -> Executable
  -> ContentDeclaration
  -> IO (Either (MutationError, [Text]) (ContentLink, [Text]))
obtainOne root archiveLock annex declaration = runExceptT $ do
  presentBefore <- withExceptT (withKeys []) (ExceptT (contentLocation archiveLock annex key))
  fetched <- withExceptT (withKeys []) $ ExceptT $ runAnnexLocked archiveLock annex
    ["get", "--quiet", "--key", Text.unpack key, "--from", Text.unpack remote]
  let transferred = [key | presentBefore == Nothing]
  case fetched of
    (ExitFailure code, _, errors) -> throwE (MutationSubprocessFailure
      ("git-annex get failed (" <> Text.pack (show code) <> ") for " <> key
        <> ": " <> Text.strip errors), [])
    (ExitSuccess, _, _) -> pure ()
  -- git-annex get exits 0 when the remote lacks the key, so only local
  -- presence after the call counts as obtaining it.
  after <- withExceptT (withKeys transferred) (ExceptT (contentLocation archiveLock annex key))
  location <- maybe
    (throwE (MutationRejected
      ("staging remote " <> remote <> " did not supply annex key " <> key), transferred))
    pure after
  withExceptT (withKeys transferred)
    (ExceptT (verifyContent root archiveLock annex declaration location))
  link <- withExceptT (withKeys transferred . MutationIntegrityFailure)
    (except (contentLinkFor root declaration location))
  pure (link, transferred)
  where
    key = contentAnnexKey declaration
    remote = contentStagingRemote declaration
    withKeys keys problem = (problem, keys)

-- | The local object path git-annex reports for a key, relative to the
-- archive root, or Nothing when the content is not present here.
contentLocation :: ArchiveWriteLock -> Executable -> Text -> IO (Either MutationError (Maybe FilePath))
contentLocation archiveLock annex key = do
  located <- runAnnexLocked archiveLock annex ["contentlocation", Text.unpack key]
  pure $ case located of
    Left problem -> Left problem
    Right (ExitFailure 1, _, _) -> Right Nothing
    Right (ExitFailure code, _, errors) -> Left (MutationSubprocessFailure
      ("git-annex contentlocation failed (" <> Text.pack (show code) <> "): " <> Text.strip errors))
    Right (ExitSuccess, output, _) ->
      let location = Text.unpack (Text.strip output)
      in case splitDirectories location of
        ".git" : "annex" : "objects" : rest
          | final : _ <- reverse rest
          , final == Text.unpack key
          , ".." `notElem` rest
          , "." `notElem` rest -> Right (Just location)
        _ -> Left (MutationIntegrityFailure
          ("git-annex reported a content location outside the annex object store for " <> key))

-- | Check the local object independently of its key name: a regular file of
-- the declared size whose SHA-256, computed by git-annex from the bytes,
-- equals the declared digest.
verifyContent
  :: FilePath -> ArchiveWriteLock -> Executable -> ContentDeclaration -> FilePath
  -> IO (Either MutationError ())
verifyContent root archiveLock annex declaration location = do
  statted <- Exception.try (Exception.bracket
    (openFd (root </> location) ReadOnly defaultFileFlags
      { nofollow = True, nonBlock = True, cloexec = True })
    closeFd getFdStatus) :: IO (Either Exception.IOException FileStatus)
  case statted of
    Left problem -> pure (Left (MutationIoFailure
      ("could not inspect local content for " <> key <> ": " <> Text.pack (show problem))))
    Right status
      | not (isRegularFile status) -> pure (Left (MutationIntegrityFailure
          ("local content for " <> key <> " is not a regular file")))
      | toInteger (fileSize status) /= toInteger (contentSize declaration) ->
          pure (Left (mismatch "byte size"))
      | otherwise -> do
          hashed <- runAnnexLocked archiveLock annex
            ["calckey", "--backend=SHA256", location]
          pure $ case hashed of
            Left problem -> Left problem
            Right (ExitFailure code, _, errors) -> Left (MutationSubprocessFailure
              ("git-annex calckey failed (" <> Text.pack (show code) <> "): " <> Text.strip errors))
            Right (ExitSuccess, output, _) -> case parseSha256Key (Text.strip output) of
              Nothing -> Left (MutationIntegrityFailure
                ("git-annex calckey returned an unexpected key for " <> key))
              Just (size, digest)
                | size /= toInteger (contentSize declaration) -> Left (mismatch "byte size")
                | "sha256:" <> digest /= contentDigest declaration -> Left (mismatch "SHA-256 digest")
                | otherwise -> Right ()
  where
    key = contentAnnexKey declaration
    mismatch what = MutationRejected
      ("content obtained for annex key " <> key <> " does not match the declared " <> what)

-- | `SHA256-s<size>--<hex>`, the key git-annex computes for the SHA256
-- backend. Only the size and digest are used.
parseSha256Key :: Text -> Maybe (Integer, Text)
parseSha256Key value = do
  rest <- Text.stripPrefix "SHA256-s" value
  let (sizeText, afterSize) = Text.breakOn "--" rest
  digest <- Text.stripPrefix "--" afterSize
  size <- readMaybe (Text.unpack sizeText)
  if Text.length digest == 64 && Text.all isLowerHex digest && Text.all (`elem` ['0' .. '9']) sizeText
    then Just (size, digest)
    else Nothing
  where
    isLowerHex character = ('0' <= character && character <= '9')
      || ('a' <= character && character <= 'f')

-- | The D-1 content path for the declared digest and the symlink target
-- git-annex itself uses from that depth.
contentLinkFor :: FilePath -> ContentDeclaration -> FilePath -> Either Text ContentLink
contentLinkFor root declaration location = do
  archive <- archiveRoot root
  digest <- parseSha256Digest (contentDigest declaration)
  pure ContentLink
    { contentLinkPath = makeRelative root (contentPath archive digest)
    , contentLinkTarget = "../../../../" <> location
    }

finalCheck :: FilePath -> Proposal -> AdmissionPlan -> IO (Either MutationError ())
finalCheck root proposal expected = do
  current <- recheckAdmissionForCommit root proposal expected
  pure $ case current of
    Left problem -> Left (admissionMutationError problem)
    Right value
      | value == expected -> Right ()
      | otherwise -> Left (MutationRejected "admission plan changed before commit")

admissionMutationError :: AdmissionPlanningError -> MutationError
admissionMutationError problem = case problem of
  AdmissionSnapshotFailure issue -> MutationSnapshotFailure issue
  AdmissionSnapshotIntegrityFailure message -> MutationIntegrityFailure message
  AdmissionProposalRejected message -> MutationRejected message
  AdmissionProposalStale message -> MutationStale message

finalizePlan
  :: FilePath
  -> Text
  -> AdmissionPlan
  -> Either Text [(FilePath, LazyByteString.ByteString)]
finalizePlan root timestamp plan = do
  archive <- archiveRoot root
  mapM (finalizeOne archive) (plannedRecords plan)
  where
    finalizeOne archive (plannedRevision, draft) = do
      draftOnly <- case draft of
        Object fields -> Right (Object
          (KeyMap.delete (Key.fromText "at")
            (KeyMap.delete (Key.fromText "revision_id") fields)))
        _ -> Left "planned admission record is not an object"
      (actualRevision, stored) <- finalizeStored timestamp draftOnly
      if actualRevision == plannedRevision
        then pure ()
        else Left "finalized admission revision differs from its plan"
      digest <- parseSha256Digest plannedRevision
      let relative = makeRelative root (recordPath archive digest)
      pure (relative, encodeCanonical stored)

currentTimestamp :: IO Text
currentTimestamp = Text.pack
  . formatTime defaultTimeLocale "%Y-%m-%dT%H:%M:%S%QZ"
  <$> getCurrentTime
