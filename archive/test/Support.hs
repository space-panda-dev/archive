{-# LANGUAGE OverloadedStrings #-}

-- | Shared helpers of archive-test: the temporary archive each check runs in,
-- the `archive` and `git` commands, admission through Door, the assertion,
-- and string helpers.
module Support
  ( initialized
  , withArchive
  , initialize
  , command
  , gitChecked
  , assert
  , jsonStringField
  , shellQuote
  , contains
  , prefixOf
  , field
  , draft
  , admit
  , admitWith
  , failWith
  ) where

import Control.Exception (bracket)
import Data.Aeson ((.=))
import qualified Data.Aeson as Aeson
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import qualified Data.ByteString.Lazy.Char8 as LazyChar8
import Data.Foldable (toList)
import Data.List (isPrefixOf)
import qualified Data.Text as Text
import GHC.Stack (HasCallStack)
import System.Directory
  ( createDirectory
  , createDirectoryIfMissing
  , getTemporaryDirectory
  , listDirectory
  , removeFile
  , removePathForcibly
  )
import System.Environment (getEnvironment, lookupEnv)
import System.Exit (ExitCode (ExitSuccess))
import System.FilePath ((</>))
import System.FilePath (takeDirectory, takeFileName)
import System.IO (hClose, hPutStrLn, openTempFile, stderr)
import System.Process
  ( CreateProcess (cwd, env)
  , proc
  , readCreateProcessWithExitCode
  , readProcessWithExitCode
  )
import Test.Tasty.HUnit (assertFailure)

-- | A check on a freshly initialized archive of its own.
initialized :: (FilePath -> IO ()) -> IO ()
initialized check = withArchive $ \root -> initialize root >> check root

-- | Runs the action in a fresh temporary directory and removes it, with the
-- siblings the checks make beside it (named after it, such as
-- @ROOT-proposal-NAME@ and @ROOT-repack-git@), however the action ends: a
-- failing case no longer stops the run. With ARCHIVE_TEST_KEEP set to
-- anything non-empty they are kept and the directory's path printed, so a
-- failure can be inspected.
withArchive :: (FilePath -> IO ()) -> IO ()
withArchive action = do
  temporary <- getTemporaryDirectory
  bracket (newDirectory temporary) (release temporary) action
  where
    release temporary directory = do
      keep <- maybe False (not . null) <$> lookupEnv "ARCHIVE_TEST_KEEP"
      if keep
        then hPutStrLn stderr ("ARCHIVE_TEST_KEEP: kept " <> directory)
        else do
          let siblingPrefix = takeFileName directory <> "-"
          siblings <- filter (siblingPrefix `isPrefixOf`) <$> listDirectory temporary
          mapM_ (removePathForcibly . (temporary </>)) siblings
          removePathForcibly directory
    newDirectory directory = do
      (path, handle) <- openTempFile directory "archive-reader"
      hClose handle
      removeFile path
      createDirectory path
      pure path

initialize :: FilePath -> IO ()
initialize root = do
  (code, output, errors) <- command (parent root) ["init", "--operator", "alice", root]
  assert "archive init succeeds" (code == ExitSuccess) output errors

command :: FilePath -> [String] -> IO (ExitCode, String, String)
command root arguments = readCreateProcessWithExitCode
  ((proc "archive" arguments) { cwd = Just root }) ""

gitChecked :: FilePath -> [String] -> IO ()
gitChecked root arguments = do
  (code, output, errors) <- readCreateProcessWithExitCode
    ((proc "git" arguments) { cwd = Just root }) ""
  assert ("git " <> unwords arguments) (code == ExitSuccess) output errors

parent :: FilePath -> FilePath
parent = takeDirectory

-- | Fails the running test case with the label and the command's output.
assert :: HasCallStack => String -> Bool -> String -> String -> IO ()
assert label condition output errors
  | condition = pure ()
  | otherwise = assertFailure (label <> "\nstdout: " <> output <> "\nstderr: " <> errors)

jsonStringField :: String -> String -> Maybe String
jsonStringField name value =
  takeWhile (/= '"') <$> after ("\"" <> name <> "\":\"") value

after :: String -> String -> Maybe String
after needle value = search value
  where
    search [] = if null needle then Just [] else Nothing
    search remaining
      | needle `prefixOf` remaining = Just (drop (length needle) remaining)
      | otherwise = search (drop 1 remaining)

shellQuote :: String -> String
shellQuote value = "'" <> concatMap escape value <> "'"
  where
    escape '\'' = "'\\''"
    escape character = [character]

contains :: String -> String -> Bool
contains needle haystack = any (needle `prefixOf`) (tails haystack)

prefixOf :: String -> String -> Bool
prefixOf [] _ = True
prefixOf _ [] = False
prefixOf (first:rest) (other:remaining) = first == other && prefixOf rest remaining

tails :: [a] -> [[a]]
tails [] = [[]]
tails values@(_:rest) = values : tails rest

field :: Text.Text -> Aeson.Value -> Maybe Aeson.Value
field name value = case value of
  Aeson.Object fields -> KeyMap.lookup (Key.fromText name) fields
  _ -> Nothing

draft :: String -> Text.Text -> Aeson.Value -> [Aeson.Value] -> [Aeson.Value] -> Aeson.Value
draft objectId shape payload refs contracts = Aeson.object
  ([ "spec" .= ("archive/0.0.11" :: Text.Text)
   , "object_id" .= objectId
   , "shape" .= shape
   , "by" .= ("pkg:hackage/archive@0.0.1.0" :: Text.Text)
   , "agent" .= ("urn:uuid:00000000-0000-4000-8000-000000000001" :: Text.Text)
   , "payload" .= payload
   , "refs" .= refs
   , "supersedes" .= ([] :: [Aeson.Value])
   ] <> [ "contracts" .= contracts | not (null contracts) ])

admit :: FilePath -> String -> String -> String -> (String -> Aeson.Value)
      -> IO (String, String)
admit = admitWith []

-- | 'admit' with extra environment for Door, such as a Git wrapper.
admitWith :: [(String, String)] -> FilePath -> String -> String -> String -> (String -> Aeson.Value)
          -> IO (String, String)
admitWith extra root archiveId namespace name draftFor = do
  (allocCode, allocated, allocErrors) <- readProcessWithExitCode "python3"
    [ "-c"
    , "import sys,uuid; print('urn:uuid:'+str(uuid.uuid5(uuid.UUID(sys.argv[1][9:]), sys.argv[2]+'\\0'+sys.argv[3])))"
    , archiveId, namespace, name ] ""
  assert "object identifiers are allocated as UUIDv5" (allocCode == ExitSuccess) allocated allocErrors
  let objectId = takeWhile (/= '\n') allocated
      proposal = root <> "-proposal-" <> name
  createDirectoryIfMissing True (proposal </> "records")
  LazyChar8.writeFile (proposal </> "records" </> "record.json") (Aeson.encode (draftFor objectId))
  LazyChar8.writeFile (proposal </> "manifest.json") (Aeson.encode (Aeson.object
    [ "protocol" .= ("archive-admission/1" :: Text.Text)
    , "records" .= ["records/record.json" :: Text.Text]
    , "identities" .= [Aeson.object
        ["object_id" .= objectId, "namespace" .= namespace, "name" .= name]]
    , "contents" .= ([] :: [Aeson.Value])
    , "expected_heads" .= [Aeson.object
        ["object_id" .= objectId, "revision_id" .= Aeson.Null]]
    ]))
  environment <- getEnvironment
  let doorEnvironment = extra <> filter ((`notElem` map fst extra) . fst) environment
  (code, output, errors) <- readCreateProcessWithExitCode
    ((proc "door" ["admit", "--proposal", proposal]) { cwd = Just root, env = Just doorEnvironment }) ""
  removePathForcibly proposal
  assert ("door admits " <> name) (code == ExitSuccess) output errors
  case Aeson.decode (LazyChar8.pack output) >>= field "revision_ids" of
    Just (Aeson.Array items) | [Aeson.String revision] <- toList items ->
      pure (objectId, Text.unpack revision)
    _ -> failWith ("door reports the revision of " <> name) output

failWith :: HasCallStack => String -> String -> IO a
failWith label output = assertFailure (label <> "\nstdout: " <> output)
