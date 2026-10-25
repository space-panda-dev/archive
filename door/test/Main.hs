{-# LANGUAGE OverloadedStrings #-}

-- | door-test: the tasty tree of every check module. Each concept module
-- holds its checks and exports one group; Support and Fixtures hold what
-- they share.
module Main (main) where

import qualified AdmissionChecks
import qualified BootstrapChecks
import CanonicalProperties (canonicalProperties)
import qualified CheckpointChecks
import qualified ContractAdmissionChecks
import qualified ContractChecks
import qualified EnvelopeChecks
import qualified GitSnapshotChecks
import qualified GitStoreChecks
import HexProperties (hexProperties)
import JournalProperties (journalProperties)
import qualified JournalRecoveryChecks
import PatternProperties (patternProperties)
import PointerProperties (pointerProperties)
import qualified RecordCommitChecks
import qualified ServiceChecks
import qualified SubprocessChecks
import System.Directory (findExecutable)
import System.Environment (setEnv)
import Test.Tasty (defaultMain, testGroup)
import qualified TransitionChecks
import qualified UpgradeChecks

main :: IO ()
main = do
  configureExecutablePaths
  -- The fixtures commit with plain `git`, which can otherwise start Git's
  -- maintenance detached in the background and repack an archive while a
  -- check reads it. Door's own Git commands turn it off themselves.
  mapM_ (uncurry setEnv)
    [ ("GIT_CONFIG_COUNT", "2")
    , ("GIT_CONFIG_KEY_0", "maintenance.auto"), ("GIT_CONFIG_VALUE_0", "false")
    , ("GIT_CONFIG_KEY_1", "gc.auto"), ("GIT_CONFIG_VALUE_1", "0")
    ]
  -- One group per concept, each from its own module; add a new group here.
  -- Rows of recording checks and release modules are computed while the
  -- tree is built, because tasty fixes its tree before running it and each
  -- row is its own case; 'rowGroup' turns an exception there into that
  -- group's failure instead of stopping the suite before any case runs.
  -- The cases share the process environment (the checks set the Git
  -- executables through 'withEnv'), so they run one at a time: the suite is
  -- built without -threaded.
  tests <- sequence
    [ EnvelopeChecks.tests
    , ContractChecks.tests
    , BootstrapChecks.tests
    , GitSnapshotChecks.tests
    , AdmissionChecks.tests
    , ContractAdmissionChecks.tests
    , UpgradeChecks.tests
    , TransitionChecks.tests
    , SubprocessChecks.tests
    , CheckpointChecks.tests
    , ServiceChecks.tests
    , RecordCommitChecks.tests
    , JournalRecoveryChecks.tests
    , GitStoreChecks.tests
    , pure $ testGroup "properties"
        [ patternProperties
        , pointerProperties
        , canonicalProperties
        , journalProperties
        , hexProperties
        ]
    ]
  defaultMain (testGroup "door-test" tests)

configureExecutablePaths :: IO ()
configureExecutablePaths = do
  gitPath <- findExecutable "git" >>= maybe
    (ioError (userError "tests require an installed Git executable")) pure
  annexPath <- findExecutable "git-annex" >>= maybe
    (ioError (userError "tests require an installed git-annex executable")) pure
  setEnv "ARCHIVE_GIT_EXECUTABLE" gitPath
  setEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE" annexPath
