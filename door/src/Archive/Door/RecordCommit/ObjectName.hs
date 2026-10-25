-- | A Git object name as the operation journal records it: a commit or blob
-- in either object format.
--
-- The journal is the writer's only durable claim about what it owned, and
-- recovery passes its names straight to Git and compares them with Git's
-- output. 'parseGitObjectName' is therefore the one check a name passes on
-- its way from journal text to a value recovery may use; the constructor
-- stays private to this module so no other path exists.
module Archive.Door.RecordCommit.ObjectName
  ( GitObjectName
  , gitObjectNameText
  , parseGitObjectName
  ) where

import Data.Text (Text)
import qualified Data.Text as Text

-- | Forty (SHA-1) or sixty-four (SHA-256) lowercase hexadecimal characters,
-- exactly as Git prints a full object name.
newtype GitObjectName = GitObjectName Text
  deriving (Eq, Ord, Show)

parseGitObjectName :: Text -> Maybe GitObjectName
parseGitObjectName value
  | Text.length value `elem` [40, 64]
      && Text.all (\character -> ('0' <= character && character <= '9')
            || ('a' <= character && character <= 'f')) value = Just (GitObjectName value)
  | otherwise = Nothing

gitObjectNameText :: GitObjectName -> Text
gitObjectNameText (GitObjectName value) = value
