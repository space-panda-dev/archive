{-# LANGUAGE OverloadedStrings #-}

-- | ADR 0088's gate for co-written Works, beyond the circle (S-30): every
-- co-written Work the covered revisions touch needs, from each of its
-- writers other than the active operator, a head sharing consent that
-- approves this destination exactly and covers every covered revision.
--
-- World's @circle.sharing_gate@ states the same rule for a reader; the two
-- are kept equal by world/tests/test_door_agreement_beyond_circle.py.
module Archive.Door.SharingGate
  ( worksTouched
  , consentBlockers
  ) where

import Archive.Door.Reach
import Archive.Door.Types (DraftEnvelope (..), Reference (..), objectIdText, revisionIdText)
import Data.Aeson (Value (..))
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Map.Strict qualified as Map
import Data.Set qualified as Set
import Data.Text (Text)

workOrigin, sharingConsent :: (Text, Text)
workOrigin = ("archive.claim/work_origin", "1")
sharingConsent = ("archive.claim/sharing_consent", "1")

performanceWork, workLinks :: [(Text, Text)]
performanceWork = [("archive.claim/performance_work", v) | v <- ["1", "2", "3"]]
-- Records that tie an Artifact, a selection, or an occurrence to a Work.
workLinks = performanceWork
  <> [("archive.claim/audio_selection", v) | v <- ["1", "2"]]
  <> [("archive.claim/song_occurrence", v) | v <- ["1", "2"]]

citesAny :: [(Text, Text)] -> Held -> Bool
citesAny contracts held = any (`heldCites` held) contracts

-- | The musical Works a disclosure of @covered@ revisions touches: every
-- @musical_work@ Entity reached through their exact references, and every
-- Work a performance/work claim ties to an occurrence, selection, or
-- Artifact among them. Objects compare by identifier at any revision, so
-- the rule errs toward finding a Work.
worksTouched :: Index -> [Text] -> Set.Set Text
worksTouched index covered = Set.filter isWork (links (follow Set.empty Set.empty covered))
  where
    byRevision = indexByRevision index
    follow objects _ [] = objects
    follow objects seen (revision : rest)
      | Set.member revision seen = follow objects seen rest
      | otherwise = case Map.lookup revision byRevision of
          Nothing -> follow objects seen rest
          Just held ->
            let refs = envelopeReferences (heldDraft held)
                objects' = foldr Set.insert (Set.insert (heldObject held) objects) (map (objectIdText . referenceObject) refs)
                next = [r | ref <- refs, Just pinned <- [referenceRevision ref], let r = revisionIdText pinned, Map.member r byRevision]
            in follow objects' (Set.insert revision seen) (next <> rest)
    records = Map.elems byRevision
    links objects =
      let added = Set.fromList (concat
            [ [heldObject held | citesAny workLinks held, not (Set.member (heldObject held) objects)
                               , any (`Set.member` objects) (nonWork held)]
              <> [w | citesAny performanceWork held, any (`Set.member` objects) (heldRefIds "performance" held)
                    , w <- heldRefIds "work" held, not (Set.member w objects)]
            | held <- records ])
      in if Set.null added then objects else links (Set.union objects added)
    nonWork held = [objectIdText (referenceObject ref) | ref <- envelopeReferences (heldDraft held), referenceRole ref /= "work"]
    isWork object = any ((== Just "musical_work") . heldKind) (Map.findWithDefault [] object (indexObjectRecords index))

-- | Each (Work, writer, revision) still needing a consent, named.
consentBlockers :: Index -> Text -> Text -> [Text] -> [Text]
consentBlockers index operator destination covered = concatMap work (Set.toAscList (worksTouched index covered))
  where
    heads = indexHeads index
    consents = [held | held <- heads, heldCites sharingConsent held]
    work object =
      let origins = [ held | held <- heads, heldCites workOrigin held, heldRefIds "work" held == [object]
                    , heldAgent held == operator || heldAgent held `elem` heldRefIds "writer" held ]
          coWritten = any ((== Just (String "co_written")) . KeyMap.lookup "origin" . heldPayload) origins
          writers = Set.toAscList (Set.delete operator (Set.fromList (concatMap (heldRefIds "writer") origins)))
          originObjects = Set.fromList (map heldObject origins)
          given writer = [ held | held <- consents, heldAgent held == writer
                         , KeyMap.lookup "decision" (heldPayload held) == Just (String "approve")
                         , KeyMap.lookup "destination" (heldPayload held) == Just (String destination)
                         , any (`Set.member` originObjects) (heldRefIds "origin" held) ]
          coveredBy held = Set.fromList [ revisionIdText r | ref <- envelopeReferences (heldDraft held)
                                        , referenceRole ref == "covers", Just r <- [referenceRevision ref] ]
      in if not coWritten then [] else
           [ object <> " is co-written: " <> writer <> " has given no consent to " <> destination <> " covering " <> revision
           | writer <- writers
           , let covering = Set.unions (map coveredBy (given writer))
           , revision <- covered, not (Set.member revision covering) ]
