{-# LANGUAGE OverloadedStrings #-}

-- | Explicit operator-selected Git and git-annex executables.
--
-- Configuration is supplied through ARCHIVE_GIT_EXECUTABLE and
-- ARCHIVE_GIT_ANNEX_EXECUTABLE. Values are absolute paths; there is no PATH
-- lookup fallback. Each invocation resolves symlinks and checks the
-- executable's filesystem identity before spawning it.
module Archive.Executables
  ( Executable
  , executablePath
  , executableIdentityText
  , resolveGitExecutable
  , resolveAnnexExecutable
  , executableStillMatches
  ) where

import Data.Text (Text)
import qualified Data.Text as Text
import Data.Bits ((.&.))
import System.Directory (canonicalizePath)
import System.Environment (lookupEnv)
import System.FilePath (isAbsolute)
import System.IO.Error (tryIOError)
import System.Posix.Files
  ( FileStatus
  , deviceID
  , fileID
  , fileMode
  , fileSize
  , getFileStatus
  , isRegularFile
  , modificationTime
  , statusChangeTime
  , ownerExecuteMode
  , groupExecuteMode
  , otherExecuteMode
  )

data Executable = Executable
  { executableSetting :: String
  , executablePath :: FilePath
  , executableDevice :: Integer
  , executableInode :: Integer
  , executableSize :: Integer
  , executableMtime :: String
  , executableCtime :: String
  }
  deriving (Eq, Show)

resolveGitExecutable :: IO (Either Text Executable)
resolveGitExecutable = resolveExecutable "ARCHIVE_GIT_EXECUTABLE"

resolveAnnexExecutable :: IO (Either Text Executable)
resolveAnnexExecutable = resolveExecutable "ARCHIVE_GIT_ANNEX_EXECUTABLE"

resolveExecutable :: String -> IO (Either Text Executable)
resolveExecutable setting = do
  configured <- lookupEnv setting
  case configured of
    Nothing -> pure (Left (Text.pack setting <> " must name an absolute executable path"))
    Just path
      | not (isAbsolute path) -> pure (Left (Text.pack setting <> " must be absolute"))
      | otherwise -> do
          canonical <- tryIOError (canonicalizePath path)
          case canonical of
            Left _ -> pure (Left (Text.pack setting <> " does not resolve to an executable"))
            Right resolved -> do
              status <- tryIOError (getFileStatus resolved)
              pure $ case status of
                Left _ -> Left (Text.pack setting <> " does not resolve to an executable")
                Right details
                  | not (isExecutable details) -> Left (Text.pack setting <> " is not a regular executable file")
                  | otherwise -> Right (fromStatus setting resolved details)

executableStillMatches :: Executable -> IO Bool
executableStillMatches expected = do
  current <- resolveExecutable (executableSetting expected)
  pure (current == Right expected)

executableIdentityText :: Executable -> Text
executableIdentityText executable = Text.pack
  (executablePath executable <> ":"
    <> show (executableDevice executable) <> ":"
    <> show (executableInode executable) <> ":"
    <> show (executableSize executable) <> ":"
    <> executableMtime executable <> ":"
    <> executableCtime executable)

fromStatus :: String -> FilePath -> FileStatus -> Executable
fromStatus setting path status = Executable
  { executableSetting = setting
  , executablePath = path
  , executableDevice = fromIntegral (deviceID status)
  , executableInode = fromIntegral (fileID status)
  , executableSize = fromIntegral (fileSize status)
  , executableMtime = show (modificationTime status)
  , executableCtime = show (statusChangeTime status)
  }

isExecutable :: FileStatus -> Bool
isExecutable status = isRegularFile status
  && fileMode status .&. (ownerExecuteMode + groupExecuteMode + otherExecuteMode) /= 0
