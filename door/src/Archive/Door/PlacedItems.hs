{-# LANGUAGE OverloadedStrings #-}

-- | The profile rules specification 0.0.11 adds (D-19), for an underlay's
-- words under a Work's tune (ADR 0113), as Door parses and checks them.
--
-- @x-archive-placed-items@: across the claim, in document order, the melody
-- items each placed word or syllable names strictly increase, so words never
-- share or cross notes. @x-archive-pass-lists@: a list of passes is distinct
-- ascending pass numbers, optionally followed by the word @last@, or @last@
-- alone. Whether an item is a pitch of the pinned melody, and a pass within
-- the pinned form's repeat, read pinned claims; they are World's checks.
module Archive.Door.PlacedItems
  ( placedItemsRule
  , validatePlacedItems
  , passListsRule
  , validatePassLists
  ) where

import Archive.Door.RuleJson
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Foldable (toList)
import Data.List (nub)
import Data.Maybe (isJust, mapMaybe)
import Data.Text (Text)

placedItemsRule :: Value -> Either Text NamedFields
placedItemsRule = namedFields "placed items rule" ["lines", "words", "syllables", "items"]

-- | A word holding syllables is placed by its syllables, each by its items.
validatePlacedItems :: KeyMap.KeyMap Value -> NamedFields -> Either Text ()
validatePlacedItems payload named =
  let placed = [ unit
               | Object line <- arrayAt (named "lines") payload
               , Object word <- arrayAt (named "words") line
               , unit <- if KeyMap.member (Key.fromText (named "syllables")) word
                   then [syllable | Object syllable <- arrayAt (named "syllables") word]
                   else [word] ]
      numbers = mapMaybe integerOf (concatMap (arrayAt (named "items")) placed)
  in if and (zipWith (<) numbers (drop 1 numbers)) then Right ()
       else Left (named "items" <> " must strictly increase across the claim: words never share or cross notes")

passListsRule :: Value -> Either Text [[Text]]
passListsRule value = do
  fields <- ruleObject label value
  exactKeys label ["paths"] fields
  case KeyMap.lookup "paths" fields of
    Just (Array items) | not (null items) -> do
      paths <- mapM (\item -> pathField label "path" (KeyMap.singleton "path" item)) (toList items)
      if length paths == length (nub paths) then Right paths else Left (label <> " must not repeat a path")
    _ -> Left (label <> " paths must be a non-empty array of paths")
  where label = "pass lists rule"

validatePassLists :: KeyMap.KeyMap Value -> [[Text]] -> Either Text ()
validatePassLists payload = mapM_ (\path -> mapM_ (check path) [toList items | Array items <- reached path payload])
  where
    check path items =
      let numbers = takeWhile (isJust . integerOf) items
          ascending = and (zipWith (<) (mapMaybe integerOf numbers) (drop 1 (mapMaybe integerOf numbers)))
      in if ascending && drop (length numbers) items `elem` [[], [String "last"]] then Right ()
           else Left (pathLabel path <> " must be distinct ascending pass numbers, optionally followed by last")
