{-# LANGUAGE OverloadedStrings #-}

-- | A judgment version 2's scope and a span annotation's subjects, checked
-- against what they judge and mark (D-18).
--
-- A scope is read from the judged Claim revision and, for a span, from that
-- Claim's stream: two hops from the judgment. A judged Claim is timed when
-- its payload states an integer @sample_rate@ and it pins exactly one
-- @stream@.
module Archive.Door.JudgedScope
  ( validateJudgedScope
  , validateMarkAbout
  ) where

import Archive.Door.RuleJson
import Archive.Door.RuleRecords
import Archive.Door.Types (DraftEnvelope (..))
import Data.Aeson (Value (..))
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Maybe (isJust, mapMaybe)
import Data.Text (Text)

validateJudgedScope :: RuleRecords -> DraftEnvelope -> Text -> Either Text ()
validateJudgedScope records draft role = do
  let payload = draftPayload draft
  ref <- singleIn role draft
  judged <- pinnedRecord records ref
  let held = draftPayload judged
      timed = isJust (integerAt "sample_rate" held) && length (draftRefs "stream" judged) == 1
      items name = [fields | Object fields <- arrayAt name held]
      present name = KeyMap.member name held
  case (objectAt "span" payload, objectAt "entries" payload, objectAt "reading" payload) of
    (Just span', _, _) -> do
      if timed then Right () else Left "a span scope needs a timed judged claim"
      if integerAt "sample_rate" payload == integerAt "sample_rate" held then Right ()
        else Left "a span scope's sample_rate must be the judged claim's"
      streamRef <- singleIn "stream" judged
      stream <- draftPayload <$> pinnedRecord records streamRef
      case (integerAt "end_sample" span', integerAt "sample_count" stream) of
        (Just end, Just count) | end <= count -> Right ()
        _ -> Left "a span scope must end within the judged claim's stream"
    (_, Just entries, _) -> case integerAt "last" entries of
      Just lastEntry | present "entries", lastEntry < fromIntegral (length (items "entries")) -> Right ()
      _ -> Left "an entries scope must end at an entry of the judged claim"
    (_, _, Just reading) -> do
      (list, position) <- case (integerAt "span" reading, integerAt "entry" reading) of
        (Just position, Nothing)
          | timed -> Right (items "spans", position)
          | otherwise -> Left "a reading scope of a span needs a timed judged claim"
        (Nothing, Just position) -> Right (items "entries", position)
        _ -> Left "a reading scope names exactly one of span or entry"
      index <- maybe (Left "a reading scope names its reading") Right (integerAt "reading" reading)
      case drop (fromIntegral position) list of
        item : _ | position >= 0, index >= 0, index < fromIntegral (length (arrayAt "readings" item)) -> Right ()
        _ -> Left "a reading scope must name a reading the judged claim has"
    _ -> Right ()

-- | A mark's @work@ is the Work revision its sequence pins, and each Claim it
-- is @about@ pins, under some role, the marked recording's @source@
-- revision, or the sequence's @work@ or @document@ revision.
validateMarkAbout :: RuleRecords -> DraftEnvelope -> Text -> Either Text ()
validateMarkAbout records draft role = do
  subjects <- case (draftRefs "source" draft, draftRefs "sequence" draft) of
    (source : _, _) -> Right (mapMaybe refRevision [source])
    ([], sequenceRef : _) -> do
      sequence' <- pinnedRecord records sequenceRef
      case draftRefs "work" draft of
        [] -> Right ()
        [work] | [pinned] <- draftRefs "work" sequence', refRevision pinned == refRevision work -> Right ()
        _ -> Left "a mark's work must be the Work revision its sequence pins"
      Right (mapMaybe refRevision (draftRefs "work" sequence' <> draftRefs "document" sequence'))
    _ -> Right []
  mapM_ (\ref -> do
    other <- pinnedRecord records ref
    if null subjects || any ((`elem` map Just subjects) . refRevision) (envelopeReferences other)
      then Right ()
      else Left ("a mark is " <> role <> " a claim that is not about the same recording, Work, or document"))
    (draftRefs role draft)
