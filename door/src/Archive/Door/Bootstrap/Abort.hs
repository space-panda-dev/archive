-- | How bootstrap steps fail: a typed 'MutationError' thrown as an exception.
--
-- Bootstrap runs a long sequence of file-system and subprocess steps under
-- pinned descriptors. Throwing lets each step stop the sequence at once while
-- 'Control.Exception.onException' and 'Control.Exception.bracket' still run
-- the cleanup that keeps a failed initialization from leaving partial state;
-- 'tryIO' turns the failure back into a value at each boundary.
module Archive.Door.Bootstrap.Abort
  ( failMutation
  , failIO
  , ioEither
  , tryIO
  , safeClose
  , withCleanupFailure
  ) where

import Archive.Door.IOFailure (ioFailureText)
import Archive.Door.MutationError (MutationError (..))
import qualified Control.Exception as Exception
import Data.Text (Text)
import System.IO (Handle, hClose)

ioEither :: Either Text a -> IO a
ioEither (Right value) = pure value
ioEither (Left problem) = failIO problem

tryIO :: forall a. IO a -> IO (Either MutationError a)
tryIO action =
  (Right <$> action)
    `Exception.catch` (\(MutationAbort failure) -> pure (Left failure))
    `Exception.catch` (\(problem :: Exception.IOException) ->
      pure (Left (MutationIoFailure (ioFailureText problem))))

safeClose :: Handle -> IO ()
safeClose = hClose

withCleanupFailure :: MutationError -> Either MutationError () -> MutationError
withCleanupFailure original (Right ()) = original
withCleanupFailure original (Left cleanup) = MutationCleanupFailure original cleanup

newtype MutationAbort = MutationAbort MutationError
  deriving (Show)

instance Exception.Exception MutationAbort

failMutation :: MutationError -> IO a
failMutation = Exception.throwIO . MutationAbort

failIO :: Text -> IO a
failIO = failMutation . MutationRejected
