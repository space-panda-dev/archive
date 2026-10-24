{-# LANGUAGE OverloadedStrings #-}

-- | How far an object may reach beyond the circle, as Door reads it when it
-- checks a publication approval (specification 0.0.10, "Publication beyond
-- the circle"; S-30, S-32): the sensitivity limits on the object and on
-- everything it pins, transitively, and the rights claims on every
-- Artifact, Work, recording, and release among them.
--
-- World's @documents.reach@ answers the same question for a reader; the two
-- are kept equal by world/tests/test_door_agreement_beyond_circle.py, so a
-- difference in rule here is a difference there.
module Archive.Door.Reach
  ( Held (..)
  , Index
  , indexHeld
  , indexHeads
  , indexByRevision
  , indexObjectRecords
  , heldCites
  , heldRefIds
  , heldPayload
  , heldAgent
  , heldKind
  , classes
  , classIndex
  , reachBlockers
  , rightsLimit
  , partialDate
  ) where

import Archive.Door.RuleJson (textAt)
import Archive.Door.Types
  ( ContractCitation (..)
  , DraftEnvelope (..)
  , RecordBody (..)
  , Reference (..)
  , agentIdText
  , objectIdText
  , revisionIdText
  )
import Data.Aeson (Value (..))
import Data.Aeson.KeyMap qualified as KeyMap
import Data.List (elemIndex)
import Data.Map.Strict qualified as Map
import Data.Maybe (fromMaybe, mapMaybe)
import Data.Set qualified as Set
import Data.Text (Text)
import Data.Text qualified as Text
import Data.Time (Day, fromGregorianValid, gregorianMonthLength)
import Text.Read (readMaybe)

-- | A committed record: its object, revision, the time Door recorded it,
-- and its envelope.
data Held = Held
  { heldObject :: Text
  , heldRevision :: Text
  , heldAt :: Text
  , heldDraft :: DraftEnvelope
  }

data Index = Index
  { indexByRevision :: Map.Map Text Held
  , indexObjectRecords :: Map.Map Text [Held]
  , indexHeads :: [Held]
  , indexPins :: Map.Map Text (Set.Set Text)
  , indexRecords :: [Held]
  }

indexHeld :: [Held] -> Index
indexHeld records = Index
  { indexByRevision = Map.fromList [(heldRevision held, held) | held <- records]
  , indexObjectRecords = Map.fromListWith (flip (<>)) [(heldObject held, [held]) | held <- records]
  , indexHeads = [held | held <- records, not (Set.member (heldRevision held) superseded)]
  , indexPins = Map.fromListWith Set.union (concatMap pins records)
  , indexRecords = records
  }
  where
    superseded = Set.fromList [ revisionIdText revision | held <- records
                              , ref <- envelopeSupersedes (heldDraft held), Just revision <- [referenceRevision ref] ]
    -- Every revision's references, and for a document the Artifacts its
    -- document source claims say it was made from.
    pins held =
      (heldObject held, Set.fromList (map refObject (envelopeReferences (heldDraft held))))
      : [ (document, Set.fromList (heldRefIds "derived_from" held))
        | heldCites documentSource held, document <- heldRefIds "document" held ]

documentSource, sensitivity, rights :: (Text, Text)
documentSource = ("archive.claim/document_source", "1")
sensitivity = ("archive.claim/sensitivity", "1")
rights = ("archive.claim/rights", "1")

refObject :: Reference -> Text
refObject = objectIdText . referenceObject

heldCites :: (Text, Text) -> Held -> Bool
heldCites (identifier, version) held =
  any (\c -> contractId c == identifier && contractVersion c == version) (envelopeContracts (heldDraft held))

heldRefIds :: Text -> Held -> [Text]
heldRefIds role held = [refObject ref | ref <- envelopeReferences (heldDraft held), referenceRole ref == role]

heldPayload :: Held -> KeyMap.KeyMap Value
heldPayload held = case envelopeBody (heldDraft held) of
  ClaimRecord (Object payload) -> payload
  EntityRecord (Object payload) -> payload
  ArtifactRecord (Object payload) -> payload
  AssessmentRecord (Object payload) -> payload
  _ -> KeyMap.empty

heldAgent :: Held -> Text
heldAgent = agentIdText . envelopeAgent . heldDraft

-- | An Entity's kind; Nothing for any other shape.
heldKind :: Held -> Maybe Text
heldKind held = case envelopeBody (heldDraft held) of
  EntityRecord (Object payload) -> textAt "kind" payload
  _ -> Nothing

isArtifact :: Held -> Bool
isArtifact held = case envelopeBody (heldDraft held) of
  ArtifactRecord _ -> True
  _ -> False

-- | The exposure classes, nearest first.
classes :: [Text]
classes = ["private", "circle", "service", "open", "distribution"]

classIndex :: Text -> Int
classIndex name = fromMaybe 0 (elemIndex name classes)

labelClass :: Text -> Text
labelClass label = case label of
  "RED" -> "private"
  "AMBER" -> "circle"
  "GREEN" -> "service"
  _ -> "distribution"

-- | The object and everything it pins, over every revision of each.
closure :: Index -> Text -> Set.Set Text
closure index start = go (Set.singleton start) [start]
  where
    go seen [] = seen
    go seen (next : queue) =
      let found = [o | o <- Set.toList (Map.findWithDefault Set.empty next (indexPins index)), not (Set.member o seen)]
      in go (foldr Set.insert seen found) (queue <> found)

-- | What keeps @object@ from reaching @wanted@: each sensitivity limit and
-- each rights claim (or missing one) nearer than it, named. Empty when it
-- may reach that far on @today@.
--
-- Sensitivity: for each person and object, that person's latest head claim
-- is their limit (ties to the stricter), and every limit holds. Rights:
-- every Artifact and Work, recording, or release Entity in the closure
-- needs a head rights claim, and each of its claims must permit the class:
-- the strictest wins.
reachBlockers :: Index -> Text -> Day -> Text -> Text -> [Text]
reachBlockers index operator today wanted object = labelled <> righted
  where
    want = classIndex wanted
    within = closure index object
    latest = Map.fromListWith newer
      [ ((heldAgent held, subject), held)
      | held <- indexHeads index, heldCites sensitivity held
      , subject <- heldRefIds "subject" held, Set.member subject within ]
    newer a b = if order a >= order b then a else b
    order held = (heldAt held, negate (classIndex (labelClass (label held))))
    label held = fromMaybe "" (textAt "label" (heldPayload held))
    labelled =
      [ agent <> " labels " <> subject <> " " <> label held <> " (" <> heldRevision held <> "): no further than " <> limit
      | ((agent, subject), held) <- Map.toAscList latest
      , let limit = labelClass (label held), classIndex limit < want ]
    claims = [held | held <- indexHeads index, heldCites rights held]
    about subject = [held | held <- claims, subject `elem` heldRefIds "subject" held]
    needsRights subject = case reverse <$> Map.lookup subject (indexObjectRecords index) of
      Just (held : _) ->
        isArtifact held || heldKind held `elem` map Just ["musical_work", "recording", "release"]
      _ -> False
    righted = concat
      [ case about subject of
          [] -> [subject <> " has no rights claim: no further than service" | classIndex "service" < want]
          found ->
            [ subject <> " is " <> statement held <> " (" <> heldRevision held <> "): no further than " <> limit
            | held <- found
            , let limit = rightsLimit (heldPayload held) (heldRefIds "holder" held) operator today
            , classIndex limit < want ]
      | subject <- Set.toAscList within, needsRights subject ]
    statement held = fromMaybe "" (textAt "statement" (heldPayload held))

-- | The furthest class one rights statement lets its subject reach on
-- @today@. Rights never keep a subject from the private circle: a
-- personal-use licence reaches the circle and nothing beyond it; a
-- statement that permits no more, or one outside its dates, reaches service.
rightsLimit :: KeyMap.KeyMap Value -> [Text] -> Text -> Day -> Text
rightsLimit payload holders operator today
  | statement == "personal-use-licence" = "circle"
  | not inForce = "service"
  | "InC" `Text.isPrefixOf` statement && operator `elem` holders = "distribution"
  | statement == "NoC-US" = "distribution"
  | statement == "NoC-NC" = "open"
  | otherwise = "service"
  where
    statement = fromMaybe "" (textAt "statement" payload)
    -- A partial date is read the way that keeps the statement out of force:
    -- its last day for @from@ and its first for @until@.
    inForce = maybe True (\(_, latestDay) -> latestDay <= today) (textAt "from" payload >>= partialDate)
      && maybe True (\(earliest, _) -> earliest >= today) (textAt "until" payload >>= partialDate)
      && all (maybe False (const True) . partialDate) (mapMaybe (`textAt` payload) ["from", "until"])

-- | A partial date (YYYY, YYYY-MM, or YYYY-MM-DD) as the earliest and latest
-- days it can mean.
partialDate :: Text -> Maybe (Day, Day)
partialDate value = case Text.splitOn "-" value of
  [y] | Text.length y == 4 -> do
    year <- number y
    (,) <$> fromGregorianValid year 1 1 <*> fromGregorianValid year 12 31
  [y, m] | Text.length y == 4 && Text.length m == 2 -> do
    year <- number y
    month <- fromInteger <$> number m
    (,) <$> fromGregorianValid year month 1 <*> fromGregorianValid year month (gregorianMonthLength year month)
  [y, m, d] | Text.length y == 4 && Text.length m == 2 && Text.length d == 2 -> do
    day <- fromGregorianValid <$> number y <*> (fromInteger <$> number m) <*> (fromInteger <$> number d)
    exact <- day
    pure (exact, exact)
  _ -> Nothing
  where
    number :: Text -> Maybe Integer
    number digits
      | Text.all (`elem` ['0' .. '9']) digits = readMaybe (Text.unpack digits)
      | otherwise = Nothing
