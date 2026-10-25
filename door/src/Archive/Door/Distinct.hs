-- | Refusing a list whose values repeat.
--
-- Proposals, plans and snapshot loading each refuse repeated paths,
-- revisions or citations, every one with its own refusal value; only the
-- check is shared, so the caller names what it refuses with.
module Archive.Door.Distinct
  ( distinctOr
  ) where

import qualified Data.Set as Set

-- | Refuse with @problem@ unless every value is distinct.
distinctOr :: Ord a => e -> [a] -> Either e ()
distinctOr problem values
  | Set.size (Set.fromList values) == length values = Right ()
  | otherwise = Left problem
