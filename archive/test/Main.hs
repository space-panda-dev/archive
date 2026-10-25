{-# LANGUAGE OverloadedStrings #-}

-- | archive-test: the tasty tree of the check modules. Each concept module
-- holds its checks and exports one group; Support holds what they share.
module Main (main) where

import qualified ExportChecks
import qualified IncompleteChecks
import qualified MaintenanceChecks
import qualified ReaderChecks
import System.Directory (findExecutable)
import System.Environment (setEnv)
import Test.Tasty (defaultMain, testGroup)

main :: IO ()
main = do
  gitPath <- findExecutable "git" >>= maybe
    (ioError (userError "tests require an installed Git executable")) pure
  annexPath <- findExecutable "git-annex" >>= maybe
    (ioError (userError "tests require an installed git-annex executable")) pure
  setEnv "ARCHIVE_GIT_EXECUTABLE" gitPath
  setEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE" annexPath
  -- One group per concept, each in its own module. Each case runs on its own
  -- initialized archive except the reader commands, which share one. The
  -- cases share the process environment, so they run one at a time (no
  -- -threaded). Add a new group here.
  defaultMain $ testGroup "archive-test"
    [ ReaderChecks.tests
    , IncompleteChecks.tests
    , ExportChecks.tests
    , MaintenanceChecks.tests
    ]
