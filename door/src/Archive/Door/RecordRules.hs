{-# LANGUAGE OverloadedStrings #-}

-- | The 0.0.10 and 0.0.11 profile rules (D-18, D-19) that read only the
-- record itself: its payload and its own references. Door evaluates them
-- wherever it checks a contract instance, so a committed record is held to
-- them as it loads.
--
-- The untimed meter, form, and underlay rules are larger and live in their
-- own modules; this one dispatches to them.
module Archive.Door.RecordRules
  ( validateRecordRules
  , exportKey
  , soundsClass
  ) where

import Archive.Door.FormStructure (validateFormStructure)
import Archive.Door.Hex (hexText)
import Archive.Door.PlacedItems (passListsRule, placedItemsRule, validatePassLists, validatePlacedItems)
import Archive.Door.RuleJson
import Archive.Door.RuleSyntax
import Archive.Door.SpanRules (validateContainedSpans)
import Archive.Door.WrittenBars (validateWrittenBars)
import Crypto.Hash.SHA256 qualified as SHA256
import Data.Aeson (Value (..), object, (.=))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Aeson.RFC8785 (encodeCanonical)
import Data.List (nub, sort)
import Data.Maybe (mapMaybe)
import Data.Text (Text)
import Data.Text qualified as Text

-- | Check every record-level 0.0.10 and 0.0.11 rule at the root of @schema@ against a
-- contract instance @{payload, refs}@.
validateRecordRules :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateRecordRules schema instanceValue = do
  let payload = instancePayload instanceValue
      refs = instanceRefs instanceValue
      with name parse check = ruleAt name parse schema >>= maybe (Right ()) check
  with "x-archive-distinct-revisions" distinctRevisionsRule (distinctRevisions refs)
  with "x-archive-ordered-fields" orderedFieldsRule (mapM_ (orderedFields payload))
  with "x-archive-ordered-ranges" orderedRangesRule (orderedRanges payload)
  with "x-archive-ascending-tuples" ascendingTuplesRule (mapM_ (ascendingTuples payload))
  with "x-archive-contained-spans" containedSpansRule (`validateContainedSpans` payload)
  with "x-archive-distinct-values" distinctValuesRule (mapM_ (distinctValues payload))
  with "x-archive-file-names" fileNamesRule (mapM_ (fileName payload))
  with "x-archive-spellings" spellingsRule (spellings payload)
  with "x-archive-ref-index" (refPathRule "ref index rule") (refIndex payload refs)
  with "x-archive-ref-per-item" (refPathRule "ref per item rule") (refPerItem payload refs)
  with "x-archive-named-refs" (refPathRule "named refs rule") (namedRefs payload refs)
  with "x-archive-tracklist-positions" tracklistPositionsRule (tracklistPositions payload)
  with "x-archive-export-key" exportKeyRule (exportKeyCheck payload refs)
  with "x-archive-written-bars" writtenBarsRule (validateWrittenBars payload)
  with "x-archive-form-structure" formStructureRule (validateFormStructure payload)
  with "x-archive-placed-items" placedItemsRule (validatePlacedItems payload)
  with "x-archive-pass-lists" passListsRule (validatePassLists payload)

-- | No revision appears twice among the references in @roles@.
distinctRevisions :: [InstanceRef] -> [Text] -> Either Text ()
distinctRevisions refs roles =
  let revisions = mapMaybe instanceRefRevision (filter ((`elem` roles) . instanceRefRole) refs)
  in if length revisions == length (nub revisions) then Right ()
       else Left ("no revision may appear twice among the references in " <> Text.intercalate ", " roles)

orderedFields :: KeyMap.KeyMap Value -> OrderedField -> Either Text ()
orderedFields payload rule = mapM_ check [fields | Object fields <- elements (orderedPath rule) payload]
  where
    check fields = case (integerAt (orderedLow rule) fields, integerAt (orderedHigh rule) fields) of
      (Just low, Just high)
        | low < high || (not (orderedStrict rule) && low == high) -> Right ()
        | otherwise -> Left (pathLabel (orderedPath rule) <> " " <> orderedLow rule <> " must be "
            <> (if orderedStrict rule then "less than " else "at most ") <> orderedHigh rule)
      _ -> Right ()

-- | Ranges in written order: first <= last, each after the previous one.
orderedRanges :: KeyMap.KeyMap Value -> NamedFields -> Either Text ()
orderedRanges payload named = go Nothing [fields | Object fields <- arrayAt (named "payload") payload]
  where
    go _ [] = Right ()
    go previous (fields : rest) = case (integerAt (named "first") fields, integerAt (named "last") fields) of
      (Just first, Just lastPosition) -> do
        if first <= lastPosition then Right ()
          else Left (named "payload" <> " " <> named "first" <> " must be at most its " <> named "last")
        case previous of
          Just before | first <= before ->
            Left (named "payload" <> " ranges must each start after the previous one's " <> named "last")
          _ -> Right ()
        go (Just lastPosition) rest
      _ -> go previous rest

ascendingTuples :: KeyMap.KeyMap Value -> AscendingTuples -> Either Text ()
ascendingTuples payload rule = mapM_ check [items | Array items <- reached (tuplesPath rule) payload]
  where
    label = pathLabel (tuplesPath rule)
    check items = do
      keys <- mapM key (foldr (:) [] items)
      let ordered = and (zipWith (\a b -> if tuplesStrict rule then a < b else a <= b) keys (drop 1 keys))
      if ordered then Right ()
        else Left (label <> " must be in " <> (if tuplesStrict rule then "strictly " else "")
          <> "ascending order of " <> Text.intercalate ", " (tuplesFields rule))
    key (Object fields) = maybe (Left (label <> " elements must hold integer " <> Text.intercalate ", " (tuplesFields rule)))
      Right (mapM (`integerAt` fields) (tuplesFields rule))
    key _ = Left (label <> " must hold objects")

distinctValues :: KeyMap.KeyMap Value -> [Text] -> Either Text ()
distinctValues payload path =
  let values = reached path payload
  in if length values == length (nub values) then Right ()
       else Left (pathLabel path <> " must hold distinct values")

fileName :: KeyMap.KeyMap Value -> Text -> Either Text ()
fileName payload name = case textAt name payload of
  Just value | value `elem` [".", ".."] || Text.any (`elem` ['/', '\\', '\0']) value ->
    Left (name <> " must be a file name: not . or .., and without /, \\, or NUL")
  _ -> Right ()

spellings :: KeyMap.KeyMap Value -> SpellingsRule -> Either Text ()
spellings payload rule = mapM_ check [fields | Object fields <- elements (spellingsPath rule) payload]
  where
    check fields = case (KeyMap.lookup (Key.fromText (spellingsClasses rule)) fields,
                         KeyMap.lookup (Key.fromText (spellingsNames rule)) fields) of
      (Just (Array classes), Just (Array names))
        | length classes /= length names ->
            Left (spellingsNames rule <> " must name each of the " <> spellingsClasses rule)
        | and (zipWith sounds (foldr (:) [] names) (foldr (:) [] classes)) -> Right ()
        | otherwise -> Left (spellingsNames rule <> " must each sound the class at its position")
      _ -> Right ()
    sounds (String name) value = Just True == ((==) <$> soundsClass name <*> integerOf value)
    sounds _ _ = False

-- | The pitch class a spelled name sounds: a letter A to G and at most two
-- sharps or two flats.
soundsClass :: Text -> Maybe Integer
soundsClass name = case Text.uncons name of
  Just (letter, marks) | Just base <- lookup letter letters
                       , Text.length marks <= 2
                       , Text.all (== '#') marks || Text.all (== 'b') marks ->
      let shift = fromIntegral (Text.length marks) * (if Text.all (== '#') marks then 1 else -1)
      in Just ((base + shift) `mod` 12)
  _ -> Nothing
  where letters = [('C', 0), ('D', 2), ('E', 4), ('F', 5), ('G', 7), ('A', 9), ('B', 11)]

refIndex :: KeyMap.KeyMap Value -> [InstanceRef] -> RefPathRule -> Either Text ()
refIndex payload refs rule =
  let size = fromIntegral (length (refsIn (refRole rule) refs))
  in if all (\value -> maybe False (\n -> n >= 0 && n < size) (integerOf value)) (reached (refPath rule) payload)
       then Right ()
       else Left (pathLabel (refPath rule) <> " must be a position among the " <> refRole rule <> " references")

refPerItem :: KeyMap.KeyMap Value -> [InstanceRef] -> RefPathRule -> Either Text ()
refPerItem payload refs rule
  | length (elements (refPath rule) payload) == length (refsIn (refRole rule) refs) = Right ()
  | otherwise = Left ("the record must pin one " <> refRole rule <> " reference per item of "
      <> pathLabel (refPath rule) <> ", in order")

namedRefs :: KeyMap.KeyMap Value -> [InstanceRef] -> RefPathRule -> Either Text ()
namedRefs payload refs rule =
  let named = sort (nub [value | String value <- reached (refPath rule) payload])
      pinned = sort (nub (mapMaybe instanceRefRevision (refsIn (refRole rule) refs)))
  in if named == pinned && all isText (reached (refPath rule) payload) then Right ()
       else Left (pathLabel (refPath rule) <> " must name exactly the revisions pinned in " <> refRole rule)
  where
    isText (String _) = True
    isText _ = False

-- | Media and their tracks in strictly ascending position; unless partial,
-- media numbered 1, 2, ... and each medium's tracks from 0 or 1 with no gap.
tracklistPositions :: KeyMap.KeyMap Value -> NamedFields -> Either Text ()
tracklistPositions payload named = do
  let partial = KeyMap.lookup (Key.fromText (named "partial")) payload == Just (Bool True)
      media = [fields | Object fields <- arrayAt (named "media") payload]
      position fields = integerAt (named "position") fields
  ascending (named "media") (map position media)
  if partial || map position media == map Just [1 .. fromIntegral (length media)] then Right ()
    else Left (named "media" <> " of a complete tracklist are numbered 1, 2, ...")
  mapM_ (\medium -> do
    let tracks = map position [fields | Object fields <- arrayAt (named "tracks") medium]
    ascending (named "tracks") tracks
    case tracks of
      _ | Just 0 `elem` drop 1 tracks -> Left (named "tracks" <> " position 0, a hidden pregap track, comes first")
      Just first : _ | not partial && first `notElem` [0, 1] ->
        Left (named "tracks" <> " of a complete tracklist start at 0 or 1")
      _ | not partial && or (zipWith (\a b -> fmap (+ 1) a /= b) tracks (drop 1 tracks)) ->
        Left (named "tracks" <> " of a complete tracklist leave no gap")
      _ -> Right ()) media
  where
    ascending label values
      | all (/= Nothing) values && and (zipWith (<) values (drop 1 values)) = Right ()
      | otherwise = Left (label <> " must be in strictly ascending " <> named "position")

-- | The export key the payload and references state (D-18): the SHA-256 of
-- the RFC 8785 JSON of the format, exporter, options, and the rendered and
-- joined revisions in reference order.
exportKeyCheck :: KeyMap.KeyMap Value -> [InstanceRef] -> NamedFields -> Either Text ()
exportKeyCheck payload refs named
  | textAt (named "key") payload == Just (exportKey payload (pins "rendered") (pins "joined")) = Right ()
  | otherwise = Left (named "key" <> " must be the key of the export its payload and references state")
  where
    pins role = [(instanceRefObject ref, maybe "" id (instanceRefRevision ref)) | ref <- refsIn (named role) refs]

exportKey :: KeyMap.KeyMap Value -> [(Text, Text)] -> [(Text, Text)] -> Text
exportKey payload rendered joined =
  let exporter = maybe KeyMap.empty id (objectAt "exporter" payload)
      material = object
        [ "format" .= KeyMap.lookup "format" payload
        , "exporter" .= object ["id" .= KeyMap.lookup "id" exporter, "version" .= KeyMap.lookup "version" exporter]
        , "options" .= maybe (Object KeyMap.empty) id (KeyMap.lookup "options" payload)
        , "rendered" .= [[identifier, revision] | (identifier, revision) <- rendered]
        , "joined" .= [[identifier, revision] | (identifier, revision) <- joined]
        ]
  in "sha256:" <> hexText (SHA256.hashlazy (encodeCanonical material))
