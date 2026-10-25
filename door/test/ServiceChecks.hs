{-# LANGUAGE OverloadedStrings #-}

-- | Checks of Door's service and command line: the socket service, its
-- published refusal codes, and the command-line refusals.
module ServiceChecks
  ( tests
  ) where

import Archive.Door.Json (decodeUnique)
import Archive.Door.Refusal (refusalCodeDescription, refusalCodeText)
import Control.Concurrent (threadDelay)
import Data.Aeson ((.=))
import qualified Data.Aeson as Aeson
import qualified Data.Aeson.KeyMap as KeyMap
import qualified Data.ByteString as ByteString
import qualified Data.ByteString.Lazy.Char8 as LazyByteString
import Data.Foldable (toList)
import qualified Data.Text as Text
import Fixtures
import qualified Network.Socket as Socket
import qualified Network.Socket.ByteString as SocketBytes
import Support
import System.Directory
  ( createDirectoryIfMissing
  , doesPathExist
  , findExecutable
  , removeFile
  )
import System.Environment (getEnvironment)
import System.Exit (ExitCode (ExitSuccess))
import System.Posix.Files (fileMode, getFileStatus, setFileMode)
import System.Process
  ( CreateProcess (cwd, env, std_err, std_out)
  , StdStream (CreatePipe, NoStream)
  , proc
  , readCreateProcessWithExitCode
  , readProcessWithExitCode
  , terminateProcess
  , waitForProcess
  , withCreateProcess
  )
import Test.Tasty (TestTree, testGroup)
import Test.Tasty.HUnit (testCase)

-- | Door's command line refuses stdin proposals and a bare admit.
commandLineChecks :: TestTree
commandLineChecks = testGroup "commandLineChecks"
  [ commandRefusesCase "stdin admission is refused" ["admit", "--dry-run"] "admission requires --proposal DIRECTORY"
  , commandRefusesCase "transition stdin is refused" ["transition", "--dry-run"] "transition requires --proposal DIRECTORY"
  , commandRefusesCase "persistent admission requires a proposal" ["admit"] "admit requires --proposal DIRECTORY"
  ]

commandRefusesCase :: String -> [String] -> String -> TestTree
commandRefusesCase label arguments expectedError =
  testCase label (commandRefuses label arguments expectedError)

commandRefuses :: String -> [String] -> String -> IO ()
commandRefuses label arguments expectedError = do
  (code, output, errors) <- doorCommand arguments ""
  assert label (code /= ExitSuccess && null output && expectedError `contains` errors) code output errors

-- | `door serve` answers exactly as the command line does, keeps planning
-- from memory, notices a HEAD it did not make, and stays private (ADR 0054).
serviceChecks :: FilePath -> IO ()
serviceChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "service"
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "service fixture cannot find git")) pure
  let socketDirectory = parent </> "service-socket"
      socketPath = socketDirectory </> "door.sock"
      wrapperDirectory = parent </> "service-git"
      wrapper = wrapperDirectory </> "git"
      armed = parent </> "service-git-armed"
      proposalNamed name = do
        objectId <- allocatedObjectId archiveId "archive.core/entity" name
        writeAdmissionProposal root name (manifestFor
          ["records/entity.json"] [(objectId, "archive.core/entity", name)]
          [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
      request command proposal = do
        connection <- Socket.socket Socket.AF_UNIX Socket.Stream Socket.defaultProtocol
        Socket.connect connection (Socket.SockAddrUnix socketPath)
        SocketBytes.sendAll connection (LazyByteString.toStrict (Aeson.encode (Aeson.object
          ["command" .= (command :: Text.Text), "proposal" .= proposal])) <> "\n")
        answer <- receiveAll connection ByteString.empty
        Socket.close connection
        pure (decodeUnique (LazyByteString.fromStrict answer))
      receiveAll connection collected = do
        chunk <- SocketBytes.recv connection 65536
        if ByteString.null chunk then pure collected else receiveAll connection (collected <> chunk)
      rawRequest bytes = do
        connection <- Socket.socket Socket.AF_UNIX Socket.Stream Socket.defaultProtocol
        Socket.connect connection (Socket.SockAddrUnix socketPath)
        SocketBytes.sendAll connection bytes
        Socket.shutdown connection Socket.ShutdownSend
        answer <- receiveAll connection ByteString.empty
        Socket.close connection
        pure (decodeUnique (LazyByteString.fromStrict answer))
      errorCode answer = case answer of
        Right (Aeson.Object fields)
          | KeyMap.lookup "ok" fields == Just (Aeson.Bool False)
          , Just (Aeson.Object problem) <- KeyMap.lookup "error" fields
          , Just (Aeson.String code) <- KeyMap.lookup "code" problem
          , Just (Aeson.String _) <- KeyMap.lookup "message" problem -> Just code
        _ -> Nothing :: Maybe Text.Text
      okResult answer = case answer of
        Right (Aeson.Object fields) | KeyMap.lookup "ok" fields == Just (Aeson.Bool True) ->
          KeyMap.lookup "result" fields
        _ -> Nothing
      waitForSocket tries = do
        present <- doesPathExist socketPath
        if present || tries <= (0 :: Int) then pure present
          else threadDelay 50000 >> waitForSocket (tries - 1)
  createDirectoryIfMissing True socketDirectory
  setFileMode socketDirectory 0o700
  createDirectoryIfMissing True wrapperDirectory
  -- Once armed, the wrapper refuses the work a remembered or checkpointed
  -- snapshot must not repeat: the history walk of a full read and the
  -- whole-store fsck.
  writeFile wrapper ("#!/bin/sh\nif [ -e " <> quote armed <> " ]; then for argument in \"$@\"; do case \"$argument\" in log|fsck) exit 97;; esac; done; fi\nexec "
    <> quote realGit <> " \"$@\"\n")
  setFileMode wrapper 0o755
  environment <- getEnvironment
  let serviceEnvironment = ("ARCHIVE_GIT_EXECUTABLE", wrapper)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
  -- withCreateProcess stops the service when an assertion exits early, so a
  -- failure cannot leave it running, holding the test's output open.
  withCreateProcess (proc "door" ["serve", "--socket", socketPath])
    { cwd = Just root, env = Just serviceEnvironment, std_out = NoStream, std_err = CreatePipe } $ \_ _ _ service -> do
    started <- waitForSocket 100
    socketStatus <- getFileStatus socketPath
    assert "door serve listens on an owner-only socket"
      (started && fileMode socketStatus `mod` 0o1000 == 0o600) ExitSuccess socketPath ""

    first <- proposalNamed "service-first"
    (_, cliOutput, _) <- readCreateProcessWithExitCode
      ((proc "door" ["admit", "--proposal", first, "--dry-run"]) { cwd = Just root }) ""
    servedDryRun <- request "admit-dry-run" first
    assert "the service answers a dry run exactly as the command line prints it"
      (okResult servedDryRun == either (const Nothing) Just (decodeUnique (LazyByteString.pack cliOutput)))
      ExitSuccess (show servedDryRun <> "\n" <> cliOutput) ""
    servedFirst <- request "admit" first
    firstHead <- gitHead root
    writeFile armed ""
    servedSecond <- request "admit" =<< proposalNamed "service-second"
    secondHead <- gitHead root
    assert "the service commits from memory without re-reading history or the whole store"
      (okResult servedFirst /= Nothing && okResult servedSecond /= Nothing && secondHead /= firstHead)
      ExitSuccess (show servedFirst <> "\n" <> show servedSecond) ""

    outside <- proposalNamed "service-outside"
    (outsideCode, outsideOutput, outsideErrors) <- readCreateProcessWithExitCode
      ((proc "door" ["admit", "--proposal", outside]) { cwd = Just root }) ""
    assert "the command line writes while the service runs" (outsideCode == ExitSuccess)
      outsideCode outsideOutput outsideErrors
    outsideHead <- gitHead root
    servedAfter <- request "admit" =<< proposalNamed "service-after"
    afterHead <- gitHead root
    (_, parentOfAfter, _) <- readProcessWithExitCode "git" ["-C", root, "rev-parse", "HEAD^"] ""
    assert "the service plans against a HEAD it did not make"
      (okResult servedAfter /= Nothing && afterHead /= outsideHead
        && takeWhile (/= '\n') parentOfAfter == outsideHead)
      ExitSuccess (show servedAfter) ""

    unknown <- request "remove-everything" first
    assert "the service refuses an unknown command as a malformed request"
      (errorCode unknown == Just "request_malformed") ExitSuccess (show unknown) ""

    -- Refusal codes (ADR 0058): each fixture differs from an accepted twin
    -- only in what the code names.
    firstId <- allocatedObjectId archiveId "archive.core/entity" "service-first"
    firstRevision <- revisionOf (entityDraft firstId)
    let revisionProposal name expected = writeAdmissionProposal root name (manifestFor
          ["records/entity.json"] [] [(firstId, Just expected)] [])
          ["records/entity.json", replace "\"label\":\"test\"" "\"label\":\"renamed\""
            (replace "\"supersedes\":[]" ("\"supersedes\":[" <> transitionReference "supersedes" firstId firstRevision <> "]")
              (entityDraft firstId))]
    stale <- request "admit-dry-run" =<< revisionProposal "service-stale" zeroDigest
    current <- request "admit-dry-run" =<< revisionProposal "service-current" firstRevision
    assert "a stale expected head answers expected_head_stale, and the current head is accepted (anchor)"
      (errorCode stale == Just "expected_head_stale" && okResult current /= Nothing)
      ExitSuccess (show stale <> "\n" <> show current) ""
    misallocatedId <- allocatedObjectId archiveId "archive.core/entity" "service-misallocated"
    misallocated <- request "admit-dry-run" =<< writeAdmissionProposal root "service-misallocated" (manifestFor
      ["records/entity.json"] [(misallocatedId, "archive.core/entity", "service-other-name")]
      [(misallocatedId, Nothing)] []) ["records/entity.json", entityDraft misallocatedId]
    allocated <- request "admit-dry-run" =<< proposalNamed "service-misallocated"
    assert "a proposal Door refuses answers proposal_refused, and its correctly allocated twin is accepted (anchor)"
      (errorCode misallocated == Just "proposal_refused" && okResult allocated /= Nothing)
      ExitSuccess (show misallocated <> "\n" <> show allocated) ""
    missingDirectory <- request "admit-dry-run" (root <> "-proposal-does-not-exist")
    assert "a proposal Door cannot read answers proposal_malformed"
      (errorCode missingDirectory == Just "proposal_malformed") ExitSuccess (show missingDirectory) ""
    oversized <- rawRequest (ByteString.replicate 70000 32 <> "\n")
    unterminated <- rawRequest "{\"command\":\"admit-dry-run\""
    -- Clients that connect and hang up at once, as a health probe does,
    -- must not stop the service: its answer then has nowhere to go.
    -- One at a time, so the listen backlog never fills.
    mapM_ (\_ -> do
      connection <- Socket.socket Socket.AF_UNIX Socket.Stream Socket.defaultProtocol
      Socket.connect connection (Socket.SockAddrUnix socketPath)
      Socket.close connection
      threadDelay 50000) [1 .. 10 :: Int]
    afterHangups <- request "admit-dry-run" =<< proposalNamed "service-after-hangups"
    assert "clients that hang up do not stop the service"
      (okResult afterHangups /= Nothing) ExitSuccess (show afterHangups) ""
    assert "an oversized or unterminated request line is refused, not parsed"
      (errorCode oversized == Just "request_malformed" && errorCode unterminated == Just "request_malformed"
        && contains "exceeds" (show oversized) && contains "closed before" (show unterminated))
      ExitSuccess (show oversized <> "\n" <> show unterminated) ""
    (secondCode, _, secondErrors) <- readCreateProcessWithExitCode
      ((proc "door" ["serve", "--socket", socketPath]) { cwd = Just root }) ""
    assert "a second service refuses an existing socket path"
      (secondCode /= ExitSuccess && contains "already exists" secondErrors) secondCode "" secondErrors
    let openDirectory = parent </> "service-open"
    createDirectoryIfMissing True openDirectory
    setFileMode openDirectory 0o755
    (openCode, _, openErrors) <- readCreateProcessWithExitCode
      ((proc "door" ["serve", "--socket", openDirectory </> "door.sock"]) { cwd = Just root }) ""
    assert "the service refuses a socket directory open to other users"
      (openCode /= ExitSuccess && contains "open to other users" openErrors) openCode "" openErrors

    terminateProcess service
    _ <- waitForProcess service
    remaining <- doesPathExist socketPath
    assert "a stopped service removes its socket" (not remaining) ExitSuccess socketPath ""

  -- Anchor: a new service with no checkpoint must re-read history, which the
  -- armed wrapper refuses, so the earlier successes did come from memory.
  removeFile (root </> ".git" </> "archive-door-verified.json")
  coldAnswer <- withCreateProcess (proc "door" ["serve", "--socket", socketPath])
    { cwd = Just root, env = Just serviceEnvironment, std_out = NoStream, std_err = CreatePipe } $ \_ _ _ fresh -> do
    _ <- waitForSocket 100
    answer <- request "admit" =<< proposalNamed "service-cold"
    terminateProcess fresh
    _ <- waitForProcess fresh
    pure answer
  assert "without memory or a checkpoint the armed wrapper stops the service's full read (anchor)"
    (okResult coldAnswer == Nothing) ExitSuccess (show coldAnswer) ""

-- Publication transitions (ADR 0051), through the real `door transition`.

-- | The dispatch table and the installed transition contracts name the same
-- (identifier, version) set, so no transition contract ships without a
-- handler and no handler outlives its contract.
-- | Every specification version's catalog loads, lists only bundles whose
-- bytes carry the listed identity, and a missing or malformed catalog is a
-- refusal, never an empty set (ADR 0057).
-- | The published service schema lists exactly Door's refusal codes, with
-- the same descriptions (ADR 0058).
refusalCodeSchemaChecks :: IO ()
refusalCodeSchemaChecks = do
  bytes <- LazyByteString.readFile "door/service.schema.json"
  let published = case decodeUnique bytes of
        Right (Aeson.Object schema)
          | Just (Aeson.Object definitions) <- KeyMap.lookup "$defs" schema
          , Just (Aeson.Object codes) <- KeyMap.lookup "refusal_code" definitions
          , Just (Aeson.Array options) <- KeyMap.lookup "oneOf" codes ->
              [ (code, description)
              | Aeson.Object option <- toList options
              , Just (Aeson.String code) <- [KeyMap.lookup "const" option]
              , Just (Aeson.String description) <- [KeyMap.lookup "description" option]
              ]
        _ -> []
      expected = [ (refusalCodeText code, refusalCodeDescription code) | code <- [minBound .. maxBound] ]
  assert "door/service.schema.json lists exactly Door's refusal codes and descriptions"
    (not (null published) && published == expected) ExitSuccess (show published) (show expected)

-- | The service checks, one case per check or row.
tests :: IO TestTree
tests = fmap (testGroup "service") $ sequence
  [ caseOf "refusalCodeSchemaChecks" refusalCodeSchemaChecks
  , caseOf "serviceChecks" (withProposalTemporaryRoot serviceChecks)
  , treeOf commandLineChecks
  ]
