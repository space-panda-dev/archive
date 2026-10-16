{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE LambdaCase #-}

-- | Capture subprocess output with explicit byte limits. The caller chooses
-- whether stdout is bounded; stderr is always bounded because it is only a
-- diagnostic channel.
module Archive.Process
  ( ProcessError (..)
  , ProcessStream (..)
  , processErrorMessage
  , runProcessBounded
  , runProcessBoundedAt
  ) where

import Control.Concurrent (forkFinally, threadDelay)
import Control.Concurrent.MVar (newEmptyMVar, putMVar, readMVar, takeMVar)
import Control.Exception (AsyncException, Exception, IOException, SomeException, finally, fromException, mask, onException, throwIO, try)
import qualified Control.Exception as Exception
import qualified Data.ByteString as ByteString
import Data.Text (Text)
import qualified Data.Text as Text
import System.Exit (ExitCode (..))
import System.IO (Handle, hClose, hSetBinaryMode)
import System.Process
  ( CreateProcess (std_err, std_in, std_out)
  , ProcessHandle
  , StdStream (CreatePipe, NoStream)
  , createProcess
  , terminateProcess
  , waitForProcess
  )
import Foreign.C.String (CString, withCString)
import Foreign.C.Types (CInt (..))
import Foreign.Marshal.Array (withArray0)
import Foreign.Marshal.Alloc (alloca)
import Foreign.Marshal.Utils (withMany)
import Foreign.Ptr (Ptr, nullPtr)
import Foreign.Storable (peek)
import System.Posix.IO (closeFd, fdToHandle)
import System.Posix.Types (Fd (..))
import System.IO.Error (catchIOError)

-- | Failures while starting or collecting a subprocess. A normal nonzero
-- process exit remains part of the successful result: callers such as Git
-- use particular exit codes as control flow.
data ProcessError
  = ProcessSpawnFailure Text
  | ProcessPipeFailure Text
  | ProcessReadFailure Text
  | ProcessOutputLimitFailure ProcessStream Int
  | ProcessWaitFailure Text
  | ProcessTerminationFailure Text
  deriving (Eq, Show)

data ProcessStream = StandardOutput | StandardError
  deriving (Eq, Show)

processErrorMessage :: ProcessError -> Text
processErrorMessage failure = case failure of
  ProcessSpawnFailure message -> message
  ProcessPipeFailure message -> message
  ProcessReadFailure message -> message
  ProcessOutputLimitFailure stream _ -> case stream of
    StandardOutput -> "subprocess stdout exceeded its byte limit"
    StandardError -> "subprocess stderr exceeded its byte limit"
  ProcessWaitFailure message -> message
  ProcessTerminationFailure message -> message

foreign import ccall safe "archive_spawn_pinned"
  c_archiveSpawnPinned
    :: CInt -> CString -> Ptr CString -> Ptr CString
    -> Ptr CInt -> Ptr CInt -> Ptr CInt -> IO CInt

foreign import ccall safe "archive_wait_pinned"
  c_archiveWaitPinned :: CInt -> Ptr CInt -> IO CInt

foreign import ccall unsafe "archive_terminate_pinned"
  c_archiveTerminatePinned :: CInt -> IO CInt

runProcessBounded
  :: CreateProcess
  -> Maybe Int
  -> Int
  -> IO (Either ProcessError (ExitCode, ByteString.ByteString, ByteString.ByteString))
runProcessBounded process stdoutLimit stderrLimit = mask $ \restore -> do
  created <- try (createProcess process
    { std_in = NoStream, std_out = CreatePipe, std_err = CreatePipe })
    :: IO (Either IOException (Maybe Handle, Maybe Handle, Maybe Handle, ProcessHandle))
  case created of
    Left problem -> pure (Left (ProcessSpawnFailure (Text.pack (show problem))))
    Right (input, output, errors, handle) -> case (output, errors) of
      (Just stdoutHandle, Just stderrHandle) -> do
        result <- restore (capture stdoutLimit stderrLimit stdoutHandle stderrHandle
          (waitForProcessTyped handle) (terminateProcessTyped handle))
          `onException` cleanupProcess (input, output, errors, handle)
        closeHandles [input, output, errors]
        pure result
      _ -> do
        cleanupProcess (input, output, errors, handle)
        pure (Left (ProcessPipeFailure "subprocess did not provide output pipes"))

-- | Run an executable with its current directory selected by an open
-- directory descriptor.  The C spawn seam applies fchdir in the child before
-- exec, so a rename or replacement of the original pathname cannot redirect
-- the process into a different directory.  No shell is involved.
runProcessBoundedAt
  :: Fd
  -> FilePath
  -> [String]
  -> [(String, String)]
  -> Maybe Int
  -> Int
  -> IO (Either ProcessError (ExitCode, ByteString.ByteString, ByteString.ByteString))
runProcessBoundedAt (Fd directoryFd) executable arguments environment stdoutLimit stderrLimit =
  Exception.handle handleProcessException $ mask $ \restore -> do
    spawned <- spawn
    case spawned of
      Left problem -> pure (Left problem)
      Right (processId, stdoutFd, stderrFd) -> do
        waitResult <- newEmptyMVar
        _ <- forkFinally (reapChild processId) (putMVar waitResult)
        let waitChild = readMVar waitResult >>= \case
              Left problem -> case fromException problem :: Maybe AsyncException of
                Just asyncException -> throwIO asyncException
                Nothing -> case fromException problem :: Maybe IOException of
                  Just ioException -> pure (Left (ProcessWaitFailure (Text.pack (show ioException))))
                  Nothing -> throwIO problem
              Right status -> pure (Right status)
            stopChild = do
              terminated <- terminateChild processId
              waited <- waitChild
              pure $ case (terminated, waited) of
                (Left problem, _) -> Left problem
                (Right (), Left problem) -> Left problem
                (Right (), Right _) -> Right ()
            cleanupPinned output errors = stopChild
              `finally` closeHandles [Nothing, Just output, Just errors]
        output <- ioToProcessError ProcessPipeFailure (fdToHandle (Fd stdoutFd))
          `onException` (closeRaw stdoutFd >> closeRaw stderrFd >> stopChild)
        errors <- ioToProcessError ProcessPipeFailure (fdToHandle (Fd stderrFd))
          `onException` (hClose output `catchIOError` const (pure ())
            >> closeRaw stderrFd >> stopChild)
        ioToProcessError ProcessPipeFailure
          (hSetBinaryMode output True >> hSetBinaryMode errors True)
          `onException` cleanupPinned output errors
        result <- restore (capture stdoutLimit stderrLimit output errors
          waitChild (terminateChild processId))
          `onException` cleanupPinned output errors
        closeHandles [Nothing, Just output, Just errors]
        pure result
  where
    handleProcessException (ProcessException problem) = pure (Left problem)
    spawn = withCString executable $ \executableCString ->
      withMany withCString (executable : arguments) $ \argumentStrings ->
        withArray0 nullPtr argumentStrings $ \argumentArray ->
          withMany withCString (map renderEnvironment environment) $ \environmentStrings ->
            withArray0 nullPtr environmentStrings $ \environmentArray ->
              alloca3 $ \processId outputFd errorFd -> do
                result <- c_archiveSpawnPinned directoryFd executableCString
                  argumentArray environmentArray processId outputFd errorFd
                if result == 0
                  then do
                    processValue <- peek processId
                    outputValue <- peek outputFd
                    errorValue <- peek errorFd
                    pure (Right (processValue, outputValue, errorValue))
                else pure (Left (ProcessSpawnFailure ("could not spawn pinned subprocess: "
                    <> Text.pack (show (fromIntegral result :: Int)))))
    renderEnvironment (name, value) = name <> "=" <> value
    -- Exit is polled, never awaited in a blocking call, so the pipe readers
    -- keep running in the single-threaded runtime. Polling starts fast and
    -- backs off to 10 ms: most Git calls finish within a few milliseconds,
    -- and a fixed 10 ms wait dominated short ones.
    reapChild processId = alloca $ \status -> waitForExit status 50
      where
        waitForExit status delay = do
          result <- c_archiveWaitPinned processId status
          if result == -1
              then threadDelay delay >> waitForExit status (min 10000 (delay * 2))
            else if result /= 0
              then throwIO (userError ("could not wait for pinned subprocess: "
                <> show (fromIntegral result :: Int)))
              else do
                raw <- peek status
                let code = fromIntegral raw
                pure (if code == 0 then ExitSuccess else ExitFailure code)
    terminateChild processId = tryTerminate 20
      where
        tryTerminate :: Int -> IO (Either ProcessError ())
        tryTerminate retries = do
          result <- c_archiveTerminatePinned processId
          if result == 0
            then pure (Right ())
            else if result == 1 && retries > 0
              then threadDelay 10000 >> tryTerminate (retries - 1)
              else pure (Left (ProcessTerminationFailure
                ("could not terminate pinned process group: "
                  <> Text.pack (show (fromIntegral result :: Int)))))
    closeRaw descriptor = closeFd (Fd descriptor) `catchIOError` const (pure ())

alloca3 :: (Ptr CInt -> Ptr CInt -> Ptr CInt -> IO a) -> IO a
alloca3 action = alloca $ \first -> alloca $ \second -> alloca $ \third ->
  action first second third

capture
  :: Maybe Int
  -> Int
  -> Handle
  -> Handle
  -> IO (Either ProcessError ExitCode)
  -> IO (Either ProcessError ())
  -> IO (Either ProcessError (ExitCode, ByteString.ByteString, ByteString.ByteString))
capture stdoutLimit stderrLimit output errors waitChild terminateChild = do
  outputResult <- newEmptyMVar
  errorResult <- newEmptyMVar
  let reader destination maximumBytes pipe = forkFinally
        (do
          result <- readBounded (fmap (+ 1) maximumBytes) pipe
          case result of
            Right (_, True) -> do
              _ <- terminateChild
              pure ()
            _ -> pure ()
          pure result)
        (putMVar destination)
  _ <- reader outputResult stdoutLimit output
  _ <- reader errorResult (Just stderrLimit) errors
  outputRead <- takeMVar outputResult
  errorRead <- takeMVar errorResult
  outputValue <- unwrapRead outputRead
  errorValue <- unwrapRead errorRead
  case (outputValue, errorValue) of
    (Left problem, _) -> stoppedOr problem
    (_, Left problem) -> stoppedOr problem
    (Right (_, True), _) -> stoppedOr (ProcessOutputLimitFailure StandardOutput (maybe 0 id stdoutLimit))
    (_, Right (_, True)) -> stoppedOr (ProcessOutputLimitFailure StandardError stderrLimit)
    (Right (outputBytes, False), Right (errorBytes, False)) -> do
      waited <- waitChild
      pure $ case waited of
        Left problem -> Left problem
        Right status -> Right (status, outputBytes, errorBytes)
  where
    stopChild = do
      terminated <- terminateChild
      waited <- waitChild
      pure $ case (terminated, waited) of
        (Left problem, _) -> Left problem
        (Right (), Left problem) -> Left problem
        (Right (), Right _) -> Right ()
    stoppedOr original = do
      stopped <- stopChild
      pure $ case stopped of
        Left problem -> Left problem
        Right () -> Left original
    unwrapRead :: Either SomeException (Either IOException (ByteString.ByteString, Bool))
      -> IO (Either ProcessError (ByteString.ByteString, Bool))
    unwrapRead (Left problem) = case fromException problem of
      Just ioProblem -> pure (Left (ProcessReadFailure (Text.pack (show (ioProblem :: IOException)))))
      Nothing -> throwIO problem
    unwrapRead (Right (Left problem)) = pure (Left (ProcessReadFailure (Text.pack (show problem))))
    unwrapRead (Right (Right result)) = pure (Right result)

cleanupProcess :: (Maybe Handle, Maybe Handle, Maybe Handle, ProcessHandle) -> IO ()
cleanupProcess (input, output, errors, handle) = do
  stopProcess handle
  closeHandles [input, output, errors]

stopProcess :: ProcessHandle -> IO ()
stopProcess handle = do
  terminateProcess handle `catchIOError` const (pure ())
  _ <- try (waitForProcess handle) :: IO (Either IOException ExitCode)
  pure ()

closeHandles :: [Maybe Handle] -> IO ()
closeHandles = mapM_ (maybe (pure ()) close)
  where
    close handle = hClose handle `catchIOError` const (pure ())

readBounded
  :: Maybe Int
  -> Handle
  -> IO (Either IOException (ByteString.ByteString, Bool))
readBounded Nothing handle = do
  result <- try (ByteString.hGetContents handle)
  pure ((, False) <$> result)
readBounded (Just maximumBytes) handle = try (loop maximumBytes [])
  where
    loop remaining chunks
      | remaining <= 0 = pure (ByteString.concat (reverse chunks), True)
      | otherwise = do
          chunk <- ByteString.hGetSome handle (min remaining 32768)
          if ByteString.null chunk
            then pure (ByteString.concat (reverse chunks), False)
            else loop (remaining - ByteString.length chunk) (chunk : chunks)

ioToProcessError :: (Text -> ProcessError) -> IO a -> IO a
ioToProcessError makeError action = do
  outcome <- try action
  case outcome of
    Left problem -> throwIO (ProcessException (makeError (Text.pack (show (problem :: IOException)))))
    Right value -> pure value

data ProcessException = ProcessException ProcessError
  deriving (Show)

instance Exception ProcessException

waitForProcessTyped :: ProcessHandle -> IO (Either ProcessError ExitCode)
waitForProcessTyped handle = do
  result <- try (waitForProcess handle)
  pure $ case result of
    Left problem -> Left (ProcessWaitFailure (Text.pack (show (problem :: IOException))))
    Right status -> Right status

terminateProcessTyped :: ProcessHandle -> IO (Either ProcessError ())
terminateProcessTyped handle = do
  result <- try (terminateProcess handle)
  pure $ case result of
    Left problem -> Left (ProcessTerminationFailure (Text.pack (show (problem :: IOException))))
    Right () -> Right ()
