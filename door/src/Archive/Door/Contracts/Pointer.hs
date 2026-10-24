{-# LANGUAGE OverloadedStrings #-}

-- | JSON Pointers: local @$ref@ targets inside a schema, and the place in an
-- instance a refusal names.
module Archive.Door.Contracts.Pointer
  ( resolvePointer
  , instancePointer
  ) where

import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Foldable (toList)
import Data.Text (Text)
import Data.Text qualified as Text

-- | A JSON Pointer to a place in the instance, from its reversed path.
instancePointer :: [Text] -> Text
instancePointer [] = "the instance"
instancePointer location =
  "/" <> Text.intercalate "/" (map escape (reverse location))
  where
    escape = Text.replace "/" "~1" . Text.replace "~" "~0"

resolvePointer :: Value -> Text -> Maybe Value
resolvePointer root pointer
  | pointer == "#" = Just root
  | Just path <- Text.stripPrefix "#/" pointer =
      resolveSegments root (map decodePointerSegment (Text.splitOn "/" path))
  | otherwise = Nothing

resolveSegments :: Value -> [Text] -> Maybe Value
resolveSegments value [] = Just value
resolveSegments (Object fields) (segment : rest) =
  KeyMap.lookup (Key.fromText segment) fields >>= (`resolveSegments` rest)
resolveSegments (Array values) (segment : rest) = do
  index <- readNatural segment
  value <- atMay (toList values) index
  resolveSegments value rest
resolveSegments _ _ = Nothing

atMay :: [a] -> Int -> Maybe a
atMay values index
  | index < 0 = Nothing
  | otherwise = case drop index values of
      value : _ -> Just value
      [] -> Nothing

decodePointerSegment :: Text -> Text
decodePointerSegment = replace "~1" "/" . replace "~0" "~"

replace :: Text -> Text -> Text -> Text
replace needle replacement input
  | Text.null needle = input
  | otherwise = case Text.breakOn needle input of
      (before, after)
        | Text.null after -> input
        | otherwise -> before <> replacement <> replace needle replacement (Text.drop (Text.length needle) after)

readNatural :: Text -> Maybe Int
readNatural value
  | Text.null value = Nothing
  | "0" `Text.isPrefixOf` value && Text.length value > 1 = Nothing
  | Text.all (\character -> '0' <= character && character <= '9') value =
      case reads (Text.unpack value) of
        [(index, "")] -> Just index
        _ -> Nothing
  | otherwise = Nothing
