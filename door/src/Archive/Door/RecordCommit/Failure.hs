{-# LANGUAGE ScopedTypeVariables #-}

-- | How Door's writer turns operating-system exceptions into the diagnostic
-- text its callers report, and back, and how its steps short-circuit.
--
-- The writer's helpers return 'Either' 'Text' so a failure carries the exact
-- message the Python suites and door-test compare; 'tryIO' is the one place
-- an 'Exception.IOException' becomes that text, and 'failText' the one place
-- text becomes an exception inside a 'tryIO' block.
--
-- A sequence of such steps runs in 'ExceptT': each step's 'Left' ends the
-- sequence with that diagnostic, exactly where the nested @case@ it replaces
-- stopped, and no exception boundary moves, because 'ExceptT' adds none.
module Archive.Door.RecordCommit.Failure
  ( failText
  , orFail
  , require
  , tryIO
  , tryPresent
  , unlessEither
  ) where

import qualified Control.Exception as Exception
import Control.Monad (unless)
import Control.Monad.Trans.Except (ExceptT (..), throwE, withExceptT)
import Data.Text (Text)
import qualified Data.Text as Text
import System.IO.Error (isDoesNotExistError)

unlessEither :: Bool -> Text -> Either Text ()
unlessEither True _ = Right ()
unlessEither False problem = Left problem

-- | Run one step, ending the sequence with its failure rendered by the
-- caller: usually a prefix naming what the step was for.
orFail :: (e -> e') -> IO (Either e a) -> ExceptT e' IO a
orFail describe step = withExceptT describe (ExceptT step)

-- | End the sequence with a refusal unless the condition holds.
require :: Monad m => Bool -> e -> ExceptT e m ()
require condition problem = unless condition (throwE problem)

tryIO :: forall a. IO a -> IO (Either Text a)
tryIO action = do
  result <- Exception.try action :: IO (Either Exception.IOException a)
  pure (either (Left . Text.pack . show) Right result)

-- | 'tryIO' for a step whose missing target is an answer, not a failure:
-- opening an operation journal, index or staging file that a finished
-- operation already removed.
tryPresent :: forall a. IO a -> IO (Either Text (Maybe a))
tryPresent action = do
  result <- Exception.try action :: IO (Either Exception.IOException a)
  pure $ case result of
    Left problem
      | isDoesNotExistError problem -> Right Nothing
      | otherwise -> Left (Text.pack (show problem))
    Right value -> Right (Just value)

failText :: Text -> IO a
failText = Exception.throwIO . userError . Text.unpack
