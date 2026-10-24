-- | Catching an operating-system failure as a value, and the one spelling of
-- it in Door's diagnostics.
--
-- Door's writer, its bootstrap and its proposal reader each turn an
-- 'IOException' into their own error type, but the text they carry is the
-- same @show@ of the exception, and the Python suites and door-test compare
-- that text. Catching and rendering live here once so the three cannot drift
-- apart; each caller keeps only its mapping into its own error type.
module Archive.Door.IOFailure
  ( ioFailureText
  , tryIOException
  ) where

import qualified Control.Exception as Exception
import Data.Text (Text)
import qualified Data.Text as Text

-- | Run an action, returning an 'IOException' it throws instead of
-- propagating it. Every other exception, asynchronous ones included, still
-- propagates.
tryIOException :: IO a -> IO (Either Exception.IOException a)
tryIOException = Exception.try

-- | An 'IOException' as diagnostics print it.
ioFailureText :: Exception.IOException -> Text
ioFailureText = Text.pack . show
