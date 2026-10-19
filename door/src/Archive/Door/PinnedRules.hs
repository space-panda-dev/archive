{-# LANGUAGE OverloadedStrings #-}

-- | The 0.0.10 profile rules (D-18) that read the records a record pins:
-- what a pinned Claim itself pins, its payload values, and the measures it
-- states. Admission evaluates them, after the reference targets, against the
-- committed records and the earlier records of the batch.
--
-- The rules about who asserts a record are in "Archive.Door.AgentRules", and
-- a judgment's and a mark's scope in "Archive.Door.JudgedScope"; this module
-- dispatches to both.
module Archive.Door.PinnedRules (validatePinnedRules) where

import Archive.Door.AgentRules (validateAgentRules)
import Archive.Door.JudgedScope (validateJudgedScope, validateMarkAbout)
import Archive.Door.RuleJson
import Archive.Door.RuleRecords
import Archive.Door.RuleSyntax
import Archive.Door.Types (DraftEnvelope (..), Reference (..))
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Text (Text)
import Data.Text qualified as Text

-- | Check every pinned-record rule at the root of a record's effective
-- schema (after @x-archive-when-pinned@).
validatePinnedRules :: RuleRecords -> DraftEnvelope -> KeyMap.KeyMap Value -> Either Text ()
validatePinnedRules records draft schema = do
  let with name parse check = ruleAt name parse schema >>= maybe (Right ()) check
  with "x-archive-target-form" targetFormRule (targetForm records draft)
  with "x-archive-pinned-subjects" pinnedSubjectsRule (mapM_ (pinnedSubjects records draft))
  with "x-archive-target-values" targetValuesRule (mapM_ (targetValues records draft))
  with "x-archive-target-span" targetSpanRule (targetSpan records draft)
  with "x-archive-within-range" withinRangeRule (withinRange records draft)
  with "x-archive-tracklist-entry" tracklistEntryRule (tracklistEntry records draft)
  with "x-archive-judged-scope" (roleRule "judged scope rule") (validateJudgedScope records draft)
  with "x-archive-mark-about" (roleRule "mark about rule") (validateMarkAbout records draft)
  validateAgentRules records draft schema

-- | A Claim pinned in a @without@ role pins nothing in @by@; one pinned in a
-- @same@ role pins a reference in @by@ exactly when this record does.
targetForm :: RuleRecords -> DraftEnvelope -> TargetFormRule -> Either Text ()
targetForm records draft rule = mapM_ check (targetFormRoles rule)
  where
    by = targetFormBy rule
    own = not (null (draftRefs by draft))
    check (role, same) = mapM_ (\ref -> do
      target <- pinnedRecord records ref
      let pinned = not (null (draftRefs by target))
      if (same && pinned /= own) || (not same && pinned)
        then Left (role <> " must pin a Claim " <> (if same then "of the same form as this one: with a "
          <> by <> " exactly when this record pins one" else "that pins no " <> by))
        else Right ()) (draftRefs role draft)

-- | Each subject a Claim pinned in the roles names is named by this record
-- in @equals@: the same object, or the same object and revision.
pinnedSubjects :: RuleRecords -> DraftEnvelope -> PinnedSubjects -> Either Text ()
pinnedSubjects records draft rule = mapM_ check (concatMap (`draftRefs` draft) (pinnedRoles rule))
  where
    equals = draftRefs (pinnedEquals rule) draft
    key ref = (refObject ref, if pinnedRevision rule then refRevision ref else Nothing)
    check ref = do
      target <- pinnedRecord records ref
      let missing = [subject | subject <- draftRefs (pinnedSubject rule) target, key subject `notElem` map key equals]
      if null missing then Right ()
        else Left (referenceLabel ref <> " is about a " <> pinnedSubject rule <> " this record's "
          <> pinnedEquals rule <> " does not name" <> (if pinnedRevision rule then " at that revision" else ""))
    referenceLabel ref = "the Claim pinned in " <> referenceRole ref

-- | Each Claim pinned in @role@ has payload @field@ with one of @values@.
targetValues :: RuleRecords -> DraftEnvelope -> TargetValues -> Either Text ()
targetValues records draft rule = mapM_ (\ref -> do
  target <- pinnedRecord records ref
  case textAt (targetValuesField rule) (draftPayload target) of
    Just value | value `elem` targetValuesValues rule -> Right ()
    _ -> Left (targetValuesRole rule <> " must pin a Claim whose " <> targetValuesField rule <> " is "
      <> Text.intercalate " or " (targetValuesValues rule))) (draftRefs (targetValuesRole rule) draft)

-- | A span within what the pinned Claim measures: the same rate, and an end
-- at most its count.
targetSpan :: RuleRecords -> DraftEnvelope -> NamedFields -> Either Text ()
targetSpan records draft named
  | null (draftRefs (named "role") draft) = Right ()
  | Just span' <- objectAt (named "span") own = do
      (rate, count) <- measure records draft (named "role") (named "rate") (named "count")
      if integerAt (named "rate") own /= Just rate
        then Left (named "rate" <> " must equal the " <> named "role" <> " claim's")
        else Right ()
      case integerAt (named "end") span' of
        Just end | end <= count -> Right ()
        _ -> Left (named "span" <> " must end within the " <> named "role" <> " claim's " <> named "count")
  | otherwise = Right ()
  where own = draftPayload draft

-- | With a range, the range ends within the pinned Claim's count and every
-- item's field lies inside it.
withinRange :: RuleRecords -> DraftEnvelope -> NamedFields -> Either Text ()
withinRange records draft named = case objectAt (named "range") own of
  Nothing -> Right ()
  Just range -> do
    (_, count) <- measure records draft (named "role") (named "count") (named "count")
    start <- maybe (Left (named "range" <> " needs an integer " <> named "start")) Right (integerAt (named "start") range)
    end <- maybe (Left (named "range" <> " needs an integer " <> named "end")) Right (integerAt (named "end") range)
    if end <= count then Right ()
      else Left (named "range" <> " must end within the " <> named "role" <> " claim's " <> named "count")
    if all (\item -> maybe False (\value -> start <= value && value < end) (integerAt (named "field") item))
        [fields | Object fields <- arrayAt (named "items") own]
      then Right ()
      else Left (named "items" <> " " <> named "field" <> " must lie within the " <> named "range")
  where own = draftPayload draft

-- | The integer fields @rate@ and @count@ of the one Claim pinned in @role@.
measure :: RuleRecords -> DraftEnvelope -> Text -> Text -> Text -> Either Text (Integer, Integer)
measure records draft role rateName countName = do
  ref <- singleIn role draft
  target <- draftPayload <$> pinnedRecord records ref
  case (integerAt rateName target, integerAt countName target) of
    (Just rate, Just count) -> Right (rate, count)
    _ -> Left (role <> " must pin a Claim with integer " <> rateName <> " and " <> countName)

-- | A recording's audio placed as a track: the tracklist lists the medium
-- and track, and its reference for that track names this record's recording.
tracklistEntry :: RuleRecords -> DraftEnvelope -> NamedFields -> Either Text ()
tracklistEntry records draft named = case draftRefs (named "role") draft of
  [] | placed -> Left (named "medium" <> " and " <> named "track" <> " need a " <> named "role")
     | otherwise -> Right ()
  _ | not (KeyMap.member (Key.fromText (named "medium")) own && KeyMap.member (Key.fromText (named "track")) own) ->
      Left (named "medium" <> " and " <> named "track" <> " are stated with a " <> named "role")
  _ -> do
    ref <- singleIn (named "role") draft
    listed <- pinnedRecord records ref
    let payload = draftPayload listed
        positions = [ (integerAt "position" medium, integerAt "position" track)
                    | Object medium <- arrayAt "media" payload, Object track <- arrayAt "tracks" medium ]
        entries = zip positions (draftRefs (named "track") listed)
        wanted = (integerAt (named "medium") own, integerAt (named "track") own)
    recording <- singleIn (named "recording") draft
    case [pin | (at, pin) <- entries, at == wanted] of
      pin : _ | refObject pin == refObject recording -> Right ()
              | otherwise -> Left ("the " <> named "role" <> " says that track carries another " <> named "recording")
      [] -> Left ("the " <> named "role" <> " does not list that " <> named "medium" <> " and " <> named "track")
  where
    own = draftPayload draft
    placed = KeyMap.member (Key.fromText (named "medium")) own || KeyMap.member (Key.fromText (named "track")) own
