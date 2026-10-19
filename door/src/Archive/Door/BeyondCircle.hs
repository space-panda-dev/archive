{-# LANGUAGE OverloadedStrings #-}

-- | @x-archive-beyond-circle@ (specification 0.0.10, "Publication beyond the
-- circle"): Door refuses a publication approval beyond the circle that the
-- archive's own sensitivity, rights, or co-writers' consent forbid on the
-- UTC date of the transition (S-30). It names every object, label,
-- statement, writer, and revision that blocks the approval. No override is
-- defined, so Door makes none.
--
-- Unlike the other rules it reads the whole snapshot, and only when a
-- transition is planned: the date is today's, so a committed approval is
-- never checked again when history replays. A publisher checks again when
-- it publishes (World's @may_publish@ view), because rights can expire.
module Archive.Door.BeyondCircle
  ( BeyondCircleRule (..)
  , beyondCircleRule
  , beyondCircleBlockers
  , validateBeyondCircle
  ) where

import Archive.Door.Reach
import Archive.Door.RuleJson
import Archive.Door.SharingGate (consentBlockers)
import Archive.Door.Types (DraftEnvelope (..), Reference (..), objectIdText, revisionIdText)
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.List (nub)
import Data.Text (Text)
import Data.Text qualified as Text
import Data.Time (Day)

data BeyondCircleRule = BeyondCircleRule
  { beyondClass :: Text
  , beyondClasses :: [Text]
  , beyondDecision :: Text
  , beyondApprove :: Text
  , beyondDestination :: Text
  , beyondCovers :: Text
  } deriving (Eq, Show)

beyondCircleRule :: Value -> Either Text BeyondCircleRule
beyondCircleRule value = do
  fields <- ruleObject label value
  exactKeys label ["class", "beyond", "decision", "approve", "destination", "covers"] fields
  rule <- BeyondCircleRule
    <$> textField label "class" fields
    <*> textsField label "beyond" fields
    <*> textField label "decision" fields
    <*> textField label "approve" fields
    <*> textField label "destination" fields
    <*> textField label "covers" fields
  let named = [beyondClass rule, beyondDecision rule, beyondDestination rule]
  if length named /= length (nub named) then Left (label <> " must name distinct fields") else Right ()
  if all (`elem` classes) (beyondClasses rule) && all (`notElem` ["private", "circle"]) (beyondClasses rule)
    then Right rule
    else Left (label <> " beyond must name exposure classes beyond the circle")
  where label = "beyond circle rule"

-- | Everything that keeps @draft@, a publication approval, from the class it
-- names, on @today@. Empty when the rule does not apply (a hold-back, or a
-- class the rule does not check) or nothing blocks it.
beyondCircleBlockers :: Day -> Text -> [Held] -> BeyondCircleRule -> DraftEnvelope -> [Text]
beyondCircleBlockers today operator records rule draft
  | field (beyondDecision rule) /= Just (beyondApprove rule) = []
  | otherwise = case field (beyondClass rule) of
      Just wanted | wanted `elem` beyondClasses rule -> nub
        (concatMap (reachBlockers index operator today wanted) (nub (map (objectIdText . referenceObject) covers))
          <> maybe [] (\destination -> consentBlockers index operator destination
               [revisionIdText r | ref <- covers, Just r <- [referenceRevision ref]])
               (field (beyondDestination rule)))
      _ -> []
  where
    index = indexHeld records
    payload = heldPayload (Held "" "" "" draft)
    field name = case KeyMap.lookup (Key.fromText name) payload of
      Just (String text) -> Just text
      _ -> Nothing
    covers = [ref | ref <- envelopeReferences draft, referenceRole ref == beyondCovers rule]

-- | Check a transition draft under every cited bundle schema that declares
-- the rule.
validateBeyondCircle :: Day -> Text -> [Held] -> DraftEnvelope -> [Value] -> Either Text ()
validateBeyondCircle today operator records draft schemas = do
  rules <- mapM beyondCircleRule
    [rule | Object schema <- schemas, Just rule <- [KeyMap.lookup "x-archive-beyond-circle" schema]]
  case nub (concatMap (\rule -> beyondCircleBlockers today operator records rule draft) rules) of
    [] -> Right ()
    blockers -> Left ("publication approval beyond the circle is refused: " <> Text.intercalate "; " blockers)
