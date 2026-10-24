{-# LANGUAGE ScopedTypeVariables #-}

-- | How Door's writer turns operating-system exceptions into the diagnostic
-- text its callers report, and back.
--
-- The writer's helpers return 'Either' 'Text' so a failure carries the exact
-- message the Python suites and door-test compare; 'tryIO' is the one place
-- an 'Exception.IOException' becomes that text, and 'failText' the one place
-- text becomes an exception inside a 'tryIO' block.
module Archive.Door.RecordCommit.Failure
  ( failText
  , tryIO
  , unlessEither
  , whenEither
  ) where

import qualified Control.Exception as Exception
import Data.Text (Text)
import qualified Data.Text as Text

whenEither :: Bool -> Text -> Either Text ()
whenEither True _ = Right ()
whenEither False problem = Left problem

unlessEither :: Bool -> Text -> Either Text ()
unlessEither True _ = Right ()
unlessEither False problem = Left problem

tryIO :: forall a. IO a -> IO (Either Text a)
tryIO action = do
  result <- Exception.try action :: IO (Either Exception.IOException a)
  pure (either (Left . Text.pack . show) Right result)

failText :: Text -> IO a
failText = Exception.throwIO . userError . Text.unpack
